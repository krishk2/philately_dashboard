from werkzeug.datastructures import FileStorage

from app.models import Follow, Notification
from app.services import collectors
from app.services import marketplace as svc

from .conftest import login, make_user, png


def listing(seller, seed=1, **overrides):
    data = {"title": "Tiger", "year": 1983, "price": "15", "quantity": 5}
    data.update(overrides)
    return svc.create_listing(seller, data, FileStorage(stream=png(seed), filename="t.png"))


def test_collection_and_badges(app, db, alice, bob, circle):
    vintage = listing(alice, seed=1, title="Gandhi 1948", year=1948)
    svc.place_order(bob, vintage.id, 1, "Pune")
    issue = svc.create_issue(
        circle,
        {
            "title": "Lepakshi",
            "issue_date": "2026-08-15",
            "denomination": "5",
            "prebook_available": 50,
        },
        FileStorage(stream=png(9), filename="l.png"),
    )
    svc.prebook_issue(bob, issue.id, 2)

    items = collectors.collection_for(bob)
    assert {(i.title, i.how) for i in items} == {
        ("Gandhi 1948", "Bought"),
        ("Lepakshi", "Pre-booked"),
    }

    badges = {b.key: b for b in collectors.badges_for(bob)}
    assert badges["first"].earned
    assert badges["vintage"].earned
    assert not badges["collector"].earned and badges["collector"].progress == 3
    assert badges["explorer"].progress == 1


def test_trusted_seller_needs_verified_listing_and_sales(app, db, alice, bob):
    item = listing(alice, quantity=5)
    for _ in range(3):
        svc.place_order(bob, item.id, 1, "Pune")
    seller = {b.key: b for b in collectors.badges_for(alice)}["seller"]
    assert seller.earned


def test_profile_page_and_privacy(client, db, alice, bob):
    item = listing(alice)
    svc.place_order(bob, item.id, 1, "Pune")
    page = client.get("/collectors/bob").get_data(as_text=True)
    assert "Tiger" in page and "First Stamp" in page

    login(client, "bob")
    client.post("/collectors/me/visibility", data={"bio": "Birds on stamps"})  # unchecked = hide
    client.post("/logout")
    page = client.get("/collectors/bob").get_data(as_text=True)
    assert "keeps their collection private" in page
    assert "Birds on stamps" in page


def test_follow_toggle_and_notification(client, db, alice, bob):
    login(client, "alice")
    client.post("/collectors/bob/follow")
    assert db.session.query(Follow).count() == 1
    note = db.session.query(Notification).filter_by(user_id=bob.id).one()
    assert "started following" in note.message
    assert b"Following" in client.get("/collectors/bob").data
    client.post("/collectors/bob/follow")
    assert db.session.query(Follow).count() == 0


def test_cannot_follow_self(client, db, alice):
    login(client, "alice")
    client.post("/collectors/alice/follow")
    assert db.session.query(Follow).count() == 0


def test_directory_lists_collectors_not_circles(client, db, alice, circle):
    make_user("carol")
    page = client.get("/collectors").get_data(as_text=True)
    assert "@alice" in page and "@carol" in page and "@ap_circle" not in page
    assert "@carol" in client.get("/collectors?q=car").get_data(as_text=True)


def test_forum_posts_link_to_profiles(client, db, alice):
    login(client, "alice")
    client.post("/forums/new", data={"category": "general", "title": "Hello", "body": "Hi all"})
    page = client.get("/forums").get_data(as_text=True)
    assert "/collectors/alice" in page


def test_unknown_collector_404(client):
    assert client.get("/collectors/nobody").status_code == 404
