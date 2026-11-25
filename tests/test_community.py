from app.models import CourseProgress, ForumReply, ForumThread, Notification, User

from .conftest import login, png


def start_thread(client, **fields):
    data = {
        "category": "identification",
        "title": "Is this a 1948 Gandhi?",
        "body": "Found it in my grandfather's album.",
    }
    data.update(fields)
    return client.post("/forums/new", data=data)


# ---------- site layout ----------


def test_community_pages_share_the_site_header(client):
    for path in ["/home", "/stamp_store", "/forums", "/philately_crash_course", "/stamp_museum"]:
        page = client.get(path).get_data(as_text=True)
        assert 'class="site-nav"' in page, path
        assert "Uniting Philatelists Across the Nation" in page, path


def test_profile_icon_shows_when_logged_in(client, alice, db):
    db.session.add(Notification(user_id=alice.id, message="hi"))
    db.session.commit()
    assert b"profile-menu" not in client.get("/home").data
    login(client, "alice")
    page = client.get("/home").get_data(as_text=True)
    assert "profile-menu" in page
    assert 'class="dot">1<' in page
    assert "NPDA account" in page


def test_crash_course_has_no_third_party_embed(client):
    page = client.get("/philately_crash_course").get_data(as_text=True)
    assert "chatbase" not in page and "bootdey" not in page


# ---------- forums ----------


def test_forums_require_login_to_post(client):
    response = start_thread(client)
    assert response.headers["Location"].startswith("/login")


def test_thread_reply_and_notification(client, alice, bob, db):
    login(client, "alice")
    response = start_thread(client)
    thread = db.session.query(ForumThread).one()
    assert response.headers["Location"] == f"/forums/{thread.id}"
    client.post("/logout")

    login(client, "bob")
    client.post(f"/forums/{thread.id}/reply", data={"body": "Looks like the 1.5 anna value."})
    assert db.session.query(ForumReply).count() == 1
    note = db.session.query(Notification).filter_by(user_id=alice.id).one()
    assert "replied to your discussion" in note.message

    page = client.get(f"/forums/{thread.id}").data
    assert b"grandfather" in page and b"1.5 anna" in page


def test_forum_list_filters_and_search(client, alice, db):
    login(client, "alice")
    start_thread(client, category="trading", title="Selling Tiger set")
    start_thread(client, category="events", title="Who is going to the Stamp Mela?")
    assert b"Selling Tiger set" in client.get("/forums?category=trading").data
    assert b"Selling Tiger set" not in client.get("/forums?category=events").data
    assert b"Stamp Mela?" in client.get("/forums?q=mela").data


def test_forum_rejects_bad_category(client, alice, db):
    login(client, "alice")
    start_thread(client, category="spam")
    assert db.session.query(ForumThread).count() == 0


def test_only_author_can_delete(client, alice, bob, db):
    login(client, "alice")
    start_thread(client)
    thread = db.session.query(ForumThread).one()
    client.post("/logout")
    login(client, "bob")
    assert client.post(f"/forums/{thread.id}/delete").status_code == 403
    client.post("/logout")
    login(client, "alice")
    client.post(f"/forums/{thread.id}/delete")
    assert db.session.query(ForumThread).count() == 0


def test_circle_posts_show_circle_badge(client, circle, db):
    login(client, "ap_circle", circle="Andhra Pradesh")
    start_thread(client, category="new-issues", title="Lepakshi stamp on sale")
    assert b"Andhra Pradesh Postal Circle" in client.get("/forums").data


# ---------- crash course progress ----------


def test_course_progress_toggle(client, alice, db):
    login(client, "alice")
    client.post("/philately_crash_course/week1")
    client.post("/philately_crash_course/week2")
    assert db.session.query(CourseProgress).count() == 2
    assert b"2 of 5 sections complete" in client.get("/philately_crash_course").data
    assert b"2 of 5 sections complete" in client.get("/dashboard").data
    client.post("/philately_crash_course/week1")
    assert db.session.query(CourseProgress).count() == 1
    assert client.post("/philately_crash_course/nope").status_code == 404


# ---------- profile ----------


def test_profile_saves_phone_and_address_and_prefills_checkout(client, alice, bob, db):
    login(client, "bob")
    client.post(
        "/myprofile",
        data={
            "name": "Bob Kumar",
            "email": "bob@example.com",
            "membership": "Basic",
            "phone": "+91 98765 43210",
            "address": "Balewadi, Pune 411045",
        },
    )
    user = db.session.get(User, bob.id)
    assert (user.phone, user.address) == ("+91 98765 43210", "Balewadi, Pune 411045")
    assert user.profile_complete

    from werkzeug.datastructures import FileStorage

    from app.services import marketplace as svc

    listing = svc.create_listing(
        alice,
        {"title": "Tiger", "year": 1983, "price": "15", "quantity": 1},
        FileStorage(stream=png(4), filename="t.png"),
    )
    page = client.get(f"/purchase/{listing.id}").data
    assert b"Balewadi, Pune 411045" in page and b"+91 98765 43210" in page


def test_profile_rejects_bad_phone(client, alice):
    login(client, "alice")
    response = client.post(
        "/myprofile",
        data={
            "name": "Alice",
            "email": "alice@example.com",
            "membership": "Basic",
            "phone": "not a phone",
        },
    )
    assert response.status_code == 422


def test_dashboard_shows_stats_and_checklist(client, alice):
    login(client, "alice")
    page = client.get("/dashboard").get_data(as_text=True)
    assert "stamps bought" in page
    assert "Complete your profile" in page
    assert "Create NPDA Account" in page
