"""Deploy blockers: payment holds, rate limits, object storage, proxy/HTTPS, plus the carousel."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from werkzeug.datastructures import FileStorage

from app import create_app
from app.extensions import db as _db
from app.models import FeedItem, Issue, Prebooking, utcnow
from app.services import feeds
from app.services import marketplace as svc
from app.services.errors import ValidationError
from app.services.payments import PaymentDeclined, charge, luhn_valid
from app.services.storage import S3Storage

from .conftest import login, make_user, png

GOOD_CARD = {"number": "4111 1111 1111 1111", "expiry": "12/99", "cvv": "123"}


def make_issue(circle, stock=5):
    return svc.create_issue(
        circle,
        {
            "title": "Araku Coffee",
            "issue_date": "2026-01-26",
            "denomination": "25",
            "prebook_available": stock,
        },
        FileStorage(stream=png(50), filename="a.png"),
    )


# ---------- simulated payment gateway ----------


def test_luhn():
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("4111111111111112")


@pytest.mark.parametrize(
    "card, message",
    [
        ({"number": "1234", "expiry": "12/99", "cvv": "123"}, "isn't valid"),
        ({"number": "4111111111111111", "expiry": "01/20", "cvv": "123"}, "expiry"),
        ({"number": "4111111111111111", "expiry": "12/99", "cvv": "12"}, "CVV"),
        ({"number": "4000 0000 0000 0002", "expiry": "12/99", "cvv": "123"}, "declined"),
    ],
)
def test_charge_rejects_bad_cards(card, message):
    with pytest.raises(PaymentDeclined, match=message):
        charge(Decimal("10"), card)


def test_charge_returns_reference():
    assert charge(Decimal("10"), GOOD_CARD, today=date(2026, 1, 1)).startswith("SIM-")


# ---------- pre-booking holds ----------


def test_prebook_is_pending_until_paid(app, circle, alice):
    issue = make_issue(circle, stock=5)
    booking = svc.prebook_issue(alice, issue.id, 2)
    assert booking.status == "pending" and booking.expires_at is not None
    assert _db.session.get(Issue, issue.id).prebook_available == 3
    svc.pay_prebooking(alice, booking, GOOD_CARD)
    assert booking.status == "paid" and booking.payment_ref.startswith("SIM-")


def test_unpaid_holds_expire_and_release_stock(app, circle, alice):
    issue = make_issue(circle, stock=5)
    booking = svc.prebook_issue(alice, issue.id, 5)
    booking.expires_at = utcnow() - timedelta(minutes=1)
    _db.session.commit()
    assert svc.expire_stale_prebookings() == 1
    assert _db.session.get(Prebooking, booking.id).status == "expired"
    assert _db.session.get(Issue, issue.id).prebook_available == 5
    with pytest.raises(ValidationError, match="expired"):
        svc.pay_prebooking(alice, _db.session.get(Prebooking, booking.id), GOOD_CARD)


def test_expiry_is_idempotent(app, circle, alice):
    issue = make_issue(circle, stock=5)
    booking = svc.prebook_issue(alice, issue.id, 2)
    booking.expires_at = utcnow() - timedelta(minutes=1)
    _db.session.commit()
    svc.expire_stale_prebookings()
    svc.expire_stale_prebookings()
    assert _db.session.get(Issue, issue.id).prebook_available == 5  # returned once, not twice


def test_cancel_releases_stock(app, circle, alice):
    issue = make_issue(circle, stock=5)
    booking = svc.prebook_issue(alice, issue.id, 3)
    svc.cancel_prebooking(alice, booking)
    assert _db.session.get(Issue, issue.id).prebook_available == 5


def test_declined_payment_keeps_hold(app, circle, alice):
    issue = make_issue(circle)
    booking = svc.prebook_issue(alice, issue.id, 1)
    with pytest.raises(PaymentDeclined):
        svc.pay_prebooking(alice, booking, {**GOOD_CARD, "number": "4000000000000002"})
    assert booking.status == "pending"


def test_payment_page_flow(client, circle, alice):
    issue = make_issue(circle)
    login(client, "alice")
    response = client.post("/submit_order", data={"stampType": str(issue.id), "quantity": "1"})
    payment_url = response.headers["Location"]
    declined = client.post(
        payment_url, data={"cardNumber": "4000000000000002", "expiryDate": "12/99", "cvv": "123"}
    )
    assert declined.status_code == 402
    paid = client.post(
        payment_url, data={"cardNumber": "4111111111111111", "expiryDate": "12/99", "cvv": "123"}
    )
    assert paid.headers["Location"] == "/my-collection"
    assert b"Payment received" in client.get(payment_url).data


# ---------- rate limiting ----------


@pytest.fixture
def limited_client(tmp_path):
    app = create_app("testing", {"RATELIMIT_ENABLED": True, "UPLOAD_FOLDER": str(tmp_path)})
    with app.app_context():
        _db.create_all()
        make_user("alice")
        yield app.test_client()
        _db.session.remove()
        _db.drop_all()
        _db.engine.dispose()


def test_login_is_rate_limited(limited_client):
    codes = [login(limited_client, "alice", "wrong").status_code for _ in range(11)]
    assert codes[:10] == [401] * 10
    assert codes[10] == 429
    assert b"Too many attempts" in login(limited_client, "alice", "wrong").data


def test_chatbot_is_rate_limited_with_json_reply(limited_client):
    responses = [limited_client.post("/ask", json={"message": "hi"}) for _ in range(11)]
    assert responses[-1].status_code == 429
    assert "too quickly" in responses[-1].get_json()["response"]


# ---------- object storage ----------


@mock_aws
def test_s3_storage_round_trip():
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="stamps")
    storage = S3Storage("stamps", prefix="uploads/", client=client)
    storage.save("a.png", b"data", "image/png")
    assert storage.read("a.png") == b"data"
    head = client.head_object(Bucket="stamps", Key="uploads/a.png")
    assert head["ContentType"] == "image/png"
    assert "Signature" in storage.url("a.png")  # private bucket -> signed URL
    storage.delete("a.png")
    assert client.list_objects_v2(Bucket="stamps").get("KeyCount") == 0


def test_uploads_go_to_s3_and_route_redirects(app, client, alice):
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="stamps")
        local = app.extensions["storage"]
        app.extensions["storage"] = S3Storage(
            "stamps", prefix="u/", public_url="https://cdn.example.com", client=s3
        )
        try:
            listing = svc.create_listing(
                alice,
                {"title": "Tiger", "year": 1983, "price": "15"},
                FileStorage(stream=png(4), filename="t.png"),
            )
            assert s3.head_object(Bucket="stamps", Key=f"u/{listing.image_filename}")
            response = client.get(f"/uploads/{listing.image_filename}")
            assert response.status_code == 302
            assert response.headers["Location"] == (
                f"https://cdn.example.com/u/{listing.image_filename}"
            )
        finally:
            app.extensions["storage"] = local


def test_uploads_strip_metadata_and_cap_size(app, alice):
    from PIL import Image

    big = Image.new("RGB", (5000, 3000), "red")
    import io

    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # camera make, stands in for GPS and other metadata
    big.save(buffer, format="JPEG", exif=exif)
    buffer.seek(0)
    listing = svc.create_listing(
        alice,
        {"title": "Big", "year": 2000, "price": "1"},
        FileStorage(stream=buffer, filename="big.jpg"),
    )
    stored = Image.open(Path(app.config["UPLOAD_FOLDER"]) / listing.image_filename)
    assert max(stored.size) == 2400
    assert not stored.getexif()


# ---------- proxy / HTTPS ----------


def test_proxy_headers_give_https_urls(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x")
    app = create_app("testing")
    app.config["TRUST_PROXY"] = True
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    @app.get("/_scheme")
    def scheme():
        from flask import url_for

        return url_for("auth.sso_callback", provider="google", _external=True)

    response = app.test_client().get(
        "/_scheme", headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "stamps.example.com"}
    )
    assert response.get_data(as_text=True) == "https://stamps.example.com/login/google/callback"


def test_production_config_is_secure():
    from app.config import ProductionConfig

    assert ProductionConfig.SESSION_COOKIE_SECURE and ProductionConfig.REMEMBER_COOKIE_SECURE
    assert ProductionConfig.PREFERRED_URL_SCHEME == "https"


def test_security_headers(client):
    response = client.get("/home")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


# ---------- carousel, favicon, release images ----------

DETAIL = (Path(__file__).parent / "fixtures" / "india_post_release_575.html").read_text(
    encoding="utf-8"
)


def test_release_detail_image_parsed():
    url = feeds.parse_india_post_detail(
        DETAIL, "https://postagestamps.gov.in/NewsPage.aspx?uid=575"
    )
    assert url.endswith("Industries%20Stamp.png") or url.endswith("Industries Stamp.png")


def test_source_images_hidden_unless_permitted(app, client):
    _db.session.add(
        FeedItem(
            source="india_post",
            source_label="India Post",
            external_id="1",
            kind="release",
            title="MiG-21",
            url="https://example.org/1",
            published_on=date(2026, 10, 8),
            denomination=Decimal("5"),
            image_url="https://example.org/mig.png",
        )
    )
    _db.session.commit()
    page = client.get("/home").get_data(as_text=True)
    assert "MiG-21" in page and "https://example.org/mig.png" not in page
    assert "stamp-art" in page  # drawn stamp instead
    app.config["FEED_SHOW_SOURCE_IMAGES"] = True
    assert "https://example.org/mig.png" in client.get("/home").get_data(as_text=True)


def test_carousel_falls_back_to_showcase(client):
    page = client.get("/home").get_data(as_text=True)
    assert "data-carousel" in page and "Birds of India" in page


def test_every_page_has_nav_and_favicon(client, alice):
    login(client, "alice")
    for path in ["/home", "/buyer_home", "/forums", "/dashboard", "/npda", "/orders", "/login"]:
        page = client.get(path, follow_redirects=True).get_data(as_text=True)
        assert 'class="site-nav"' in page, path
        assert "favicon.svg" in page, path
    assert client.get("/static/img/favicon.svg").status_code == 200


# ---------- SSO users can set a password ----------


def test_sso_user_can_set_password(client, db):
    from app.services.sso import user_from_claims

    user = user_from_claims("google", {"sub": "g1", "email": "g@gmail.com", "email_verified": True})
    assert user.password_set is False
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True
    assert b"Set a password" in client.get("/settings2").data
    client.post(
        "/settings2/password",
        data={
            "currentPassword": "",
            "newPassword": "brand-new-pw",
            "confirmPassword": "brand-new-pw",
        },
    )
    client.post("/logout")
    assert login(client, user.username, "brand-new-pw").status_code == 302
