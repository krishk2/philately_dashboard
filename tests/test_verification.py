from datetime import date
from unittest.mock import patch

import requests
from werkzeug.datastructures import FileStorage

from app.services import marketplace as svc
from app.verification import service as verification
from app.verification.service import CheckResult, summarise
from app.verification.vision import VisionResult, VisionUnavailable, analyse_stamp_image

from .conftest import png


def upload(seed=1, size=None, label="Stamp"):
    return FileStorage(stream=png(seed, label=label, size=size), filename="s.png")


def make_issue(circle, seed=100, year=2024, prebook=10):
    return svc.create_issue(
        circle,
        {
            "title": "Lepakshi Temple",
            "issue_date": f"{year}-08-15",
            "denomination": "5",
            "prebook_available": prebook,
        },
        upload(seed),
    )


def listing_data(**overrides):
    data = {"title": "A stamp", "year": 2024, "price": "40", "quantity": 2}
    data.update(overrides)
    return data


def results(listing):
    return {c["name"]: c["result"] for c in listing.verification.checks}


def test_unique_listing_without_issue_is_verified(alice):
    listing = svc.create_listing(alice, listing_data(), upload(1))
    assert results(listing) == {
        "duplicate_photo": "pass",
        "official_issue": "skipped",
        "ai_vision": "skipped",
    }
    assert listing.verification.status == "verified"
    assert listing.verification.score == 100


def test_reused_photo_from_another_seller_is_flagged(alice, bob):
    svc.create_listing(alice, listing_data(), upload(7))
    copy = svc.create_listing(bob, listing_data(), upload(7, size=(200, 240)))
    assert results(copy)["duplicate_photo"] == "fail"
    assert copy.verification.status == "flagged"
    assert "near-copy of listing" in copy.verification.checks[0]["detail"]


def test_same_seller_reusing_own_photo_is_not_flagged(alice):
    svc.create_listing(alice, listing_data(), upload(7))
    again = svc.create_listing(alice, listing_data(), upload(7))
    assert results(again)["duplicate_photo"] == "pass"


def test_removed_listings_are_ignored_by_duplicate_check(alice, bob):
    original = svc.create_listing(alice, listing_data(), upload(7))
    svc.remove_listing(original, alice)
    copy = svc.create_listing(bob, listing_data(), upload(7))
    assert results(copy)["duplicate_photo"] == "pass"


def test_listing_matching_official_issue_passes(alice, circle):
    issue = make_issue(circle, seed=100, year=2024)
    listing = svc.create_listing(alice, listing_data(issue_id=issue.id, year=2024), upload(100))
    assert results(listing)["official_issue"] == "pass"
    assert listing.verification.status == "verified"


def test_wrong_year_for_official_issue_fails(alice, circle):
    issue = make_issue(circle, seed=100, year=2024)
    listing = svc.create_listing(alice, listing_data(issue_id=issue.id, year=2019), upload(100))
    assert results(listing)["official_issue"] == "fail"
    assert "does not match issue year" in listing.verification.checks[1]["detail"]


def test_different_photo_for_official_issue_fails(alice, circle):
    issue = make_issue(circle, seed=100)
    listing = svc.create_listing(alice, listing_data(issue_id=issue.id), upload(555))
    assert results(listing)["official_issue"] == "fail"
    assert listing.verification.status == "flagged"


def test_updating_claims_reruns_verification(alice, circle):
    issue = make_issue(circle, seed=100, year=2024)
    listing = svc.create_listing(alice, listing_data(issue_id=issue.id, year=2024), upload(100))
    assert listing.verification.status == "verified"
    svc.update_listing(listing, alice, {"year": "2020"})
    assert listing.verification.status == "flagged"


# ---------- summarising ----------


def test_summary_rules():
    def r(*outcomes):
        return [CheckResult(f"c{i}", o, "") for i, o in enumerate(outcomes)]

    assert summarise(r("skipped", "skipped")) == ("unverified", 0)
    assert summarise(r("pass", "skipped")) == ("verified", 100)
    assert summarise(r("pass", "warn")) == ("needs_review", 75)
    assert summarise(r("pass", "warn", "fail")) == ("flagged", 50)


# ---------- AI vision check ----------


def test_vision_mismatch_on_year_warns(app, alice):
    seen = VisionResult(True, "India", 1999, "Rs 5", "Clear photo.")
    with patch.object(verification, "analyse_stamp_image", return_value=seen):
        listing = svc.create_listing(alice, listing_data(year=2024), upload(1))
    assert results(listing)["ai_vision"] == "warn"
    assert listing.verification.status == "needs_review"


def test_vision_not_a_stamp_fails(app, alice):
    seen = VisionResult(False, None, None, None, "This is a coin.")
    with patch.object(verification, "analyse_stamp_image", return_value=seen):
        listing = svc.create_listing(alice, listing_data(), upload(1))
    assert results(listing)["ai_vision"] == "fail"


def test_vision_consistent_passes(app, alice):
    seen = VisionResult(True, "India", 2024, "Rs 5", "")
    with patch.object(verification, "analyse_stamp_image", return_value=seen):
        listing = svc.create_listing(alice, listing_data(year=2024), upload(1))
    assert results(listing)["ai_vision"] == "pass"


def test_vision_outage_is_skipped_not_fatal(app, alice):
    with patch.object(
        verification, "analyse_stamp_image", side_effect=VisionUnavailable("timeout")
    ):
        listing = svc.create_listing(alice, listing_data(), upload(1))
    assert results(listing)["ai_vision"] == "skipped"
    assert listing.id is not None


def test_vision_client_without_key_raises(tmp_path):
    try:
        analyse_stamp_image(tmp_path / "x.png", api_key=None, model="m")
    except VisionUnavailable as exc:
        assert "not configured" in str(exc)
    else:
        raise AssertionError("expected VisionUnavailable")


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._payload


def _gemini_reply(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_vision_client_parses_gemini_response(tmp_path):
    image = tmp_path / "s.png"
    image.write_bytes(png(1).getvalue())
    reply = _gemini_reply(
        '{"is_postage_stamp": true, "country": "India", "year": "2024",'
        ' "denomination": "Rs 5", "notes": "ok"}'
    )
    with patch("app.verification.vision.requests.post", return_value=FakeResponse(reply)) as post:
        result = analyse_stamp_image(image, api_key="k", model="gemini-test")
    assert result == VisionResult(True, "India", 2024, "Rs 5", "ok")
    url = post.call_args.args[0]
    assert "gemini-test:generateContent" in url
    assert post.call_args.kwargs["headers"] == {"x-goog-api-key": "k"}


def test_vision_client_handles_http_error(tmp_path):
    image = tmp_path / "s.png"
    image.write_bytes(png(1).getvalue())
    with patch("app.verification.vision.requests.post", return_value=FakeResponse({}, 500)):
        try:
            analyse_stamp_image(image, api_key="k", model="m")
        except VisionUnavailable:
            pass
        else:
            raise AssertionError("expected VisionUnavailable")


def test_vision_client_handles_garbage(tmp_path):
    image = tmp_path / "s.png"
    image.write_bytes(png(1).getvalue())
    with patch(
        "app.verification.vision.requests.post",
        return_value=FakeResponse(_gemini_reply("not json")),
    ):
        try:
            analyse_stamp_image(image, api_key="k", model="m")
        except VisionUnavailable:
            pass
        else:
            raise AssertionError("expected VisionUnavailable")


def test_issue_date_year_used(circle):
    issue = make_issue(circle, year=2021)
    assert issue.issue_date == date(2021, 8, 15)
