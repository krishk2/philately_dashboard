import pytest
from werkzeug.datastructures import FileStorage

from app.models import LISTING_SOLD_OUT, Order
from app.services import marketplace as svc
from app.services.errors import Forbidden, OutOfStock, ValidationError

from .conftest import png


def upload(seed=1):
    return FileStorage(stream=png(seed), filename="s.png")


def new_listing(seller, seed=1, **overrides):
    data = {"title": "Gandhi 1948", "year": 1948, "price": "100.50", "quantity": 3}
    data.update(overrides)
    return svc.create_listing(seller, data, upload(seed))


# ---------- validation ----------


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"title": ""}, "Title is required"),
        ({"year": "1700"}, "at least 1840"),
        ({"year": "abc"}, "whole number"),
        ({"price": "-1"}, "greater than 0"),
        ({"price": "ten"}, "must be a number"),
        ({"quantity": "0"}, "at least 1"),
        ({"issue_id": "999"}, "issue not found"),
    ],
)
def test_listing_validation(alice, overrides, message):
    data = {"title": "x", "year": 2000, "price": "1", "quantity": 1, **overrides}
    with pytest.raises(ValidationError, match=message):
        svc.create_listing(alice, data, upload())


def test_listing_requires_image(alice):
    with pytest.raises(ValidationError, match="image is required"):
        svc.create_listing(alice, {"title": "x", "year": 2000, "price": "1"}, None)


def test_listing_rejects_non_image_file(alice):
    import io

    bad = FileStorage(stream=io.BytesIO(b"not an image"), filename="evil.png")
    with pytest.raises(ValidationError, match="not a readable image"):
        svc.create_listing(alice, {"title": "x", "year": 2000, "price": "1"}, bad)


def test_listing_rejects_wrong_extension(alice):
    bad = FileStorage(stream=png(), filename="script.exe")
    with pytest.raises(ValidationError, match="PNG, JPEG or WebP"):
        svc.create_listing(alice, {"title": "x", "year": 2000, "price": "1"}, bad)


def test_circle_cannot_create_listing(circle):
    with pytest.raises(Forbidden):
        new_listing(circle)


def test_only_owner_can_update(alice, bob):
    listing = new_listing(alice)
    with pytest.raises(Forbidden):
        svc.update_listing(listing, bob, {"price": "1"})


# ---------- orders ----------


def test_order_decrements_stock_and_records_price(alice, bob):
    listing = new_listing(alice, quantity=3)
    order = svc.place_order(bob, listing.id, 2, "12 MG Road, Pune")
    assert listing.quantity == 1
    assert str(order.total) == "201.00"
    assert order.unit_price == listing.price


def test_buying_last_copy_marks_sold_out(alice, bob):
    listing = new_listing(alice, quantity=1)
    svc.place_order(bob, listing.id, 1, "addr")
    assert listing.quantity == 0
    assert listing.status == LISTING_SOLD_OUT


def test_cannot_oversell(alice, bob, db):
    listing = new_listing(alice, quantity=2)
    with pytest.raises(OutOfStock):
        svc.place_order(bob, listing.id, 3, "addr")
    assert db.session.get(type(listing), listing.id).quantity == 2
    assert db.session.query(Order).count() == 0


def test_sequential_buyers_cannot_both_take_last_copy(alice, bob, db):
    from tests.conftest import make_user

    carol = make_user("carol")
    listing = new_listing(alice, quantity=1)
    svc.place_order(bob, listing.id, 1, "addr")
    with pytest.raises(OutOfStock):
        svc.place_order(carol, listing.id, 1, "addr")
    assert db.session.query(Order).count() == 1


def test_cannot_buy_own_listing(alice):
    listing = new_listing(alice)
    with pytest.raises(Forbidden):
        svc.place_order(alice, listing.id, 1, "addr")


def test_cannot_buy_removed_listing(alice, bob):
    listing = new_listing(alice)
    svc.remove_listing(listing, alice)
    with pytest.raises(ValidationError, match="not found"):
        svc.place_order(bob, listing.id, 1, "addr")


def test_order_requires_address(alice, bob):
    listing = new_listing(alice)
    with pytest.raises(ValidationError, match="Shipping address"):
        svc.place_order(bob, listing.id, 1, "  ")


# ---------- pre-booking ----------


def make_issue(circle, prebook=5):
    return svc.create_issue(
        circle,
        {
            "title": "Araku Coffee",
            "issue_date": "2026-01-26",
            "denomination": "25",
            "prebook_available": prebook,
        },
        upload(50),
    )


def test_prebook_reduces_availability(circle, alice):
    issue = make_issue(circle, prebook=5)
    svc.prebook_issue(alice, issue.id, 3)
    assert issue.prebook_available == 2


def test_prebook_cannot_exceed_availability(circle, alice):
    issue = make_issue(circle, prebook=2)
    with pytest.raises(OutOfStock):
        svc.prebook_issue(alice, issue.id, 3)


def test_circle_cannot_prebook(circle):
    issue = make_issue(circle)
    with pytest.raises(Forbidden):
        svc.prebook_issue(circle, issue.id, 1)


def test_collector_cannot_publish_issue(alice):
    with pytest.raises(Forbidden):
        make_issue(alice)


def test_issue_with_prebookings_cannot_be_deleted(circle, alice):
    issue = make_issue(circle)
    svc.prebook_issue(alice, issue.id, 1)
    with pytest.raises(ValidationError, match="pre-bookings"):
        svc.delete_issue(issue, circle)


# ---------- search ----------


def test_search_filters(alice, db):
    new_listing(alice, seed=1, title="Gandhi 1948", year=1948, price="100")
    new_listing(alice, seed=2, title="Taj Mahal", year=1975, price="20")
    new_listing(alice, seed=3, title="Tiger", year=1983, price="5")

    def titles(**filters):
        return sorted(row.title for row in db.session.scalars(svc.search_listings(**filters)))

    assert titles(query="gandhi") == ["Gandhi 1948"]
    assert titles(year=1975) == ["Taj Mahal"]
    assert titles(max_price=50) == ["Taj Mahal", "Tiger"]
    assert titles(min_price=10, max_price=50) == ["Taj Mahal"]
