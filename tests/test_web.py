"""End-to-end tests of the original pages: every form posts with the original field names."""

from datetime import date, timedelta
from unittest.mock import patch

from werkzeug.datastructures import FileStorage

from app.models import CircleUpdate, Event, Issue, Listing, Order, Prebooking, User, WishlistItem
from app.services import marketplace as svc

from .conftest import login, make_user, png

AP = "Andhra Pradesh"


def sell(client, seed=1, **fields):
    """Post the original seller.html form."""
    data = {
        "stamp_name": "Taj Mahal",
        "year_of_manufacture": "1975",
        "coated_price": "20",
        "quantity": "2",
        "description": "Mint condition",
        "issue_id": "",
    }
    data.update(fields)
    data["stamp_photo"] = (png(seed), "taj.png")
    return client.post("/submit_stamp", data=data, content_type="multipart/form-data")


def make_issue(circle, prebook=10, seed=50):
    return svc.create_issue(
        circle,
        {
            "title": "Araku Coffee",
            "issue_date": "2026-01-26",
            "denomination": "25",
            "prebook_available": prebook,
        },
        FileStorage(stream=png(seed), filename="a.png"),
    )


# ---------- public pages ----------


def test_public_pages_render(client):
    for path in [
        "/",
        "/login",
        "/register",
        "/home",
        "/stamp_store",
        "/buyer_home",
        "/philately_crash_course",
        "/stamp_museum",
    ]:
        assert client.get(path).status_code == 200, path


def test_missing_page_renders_html_404(client):
    response = client.get("/definitely-not-here")
    assert response.status_code == 404
    assert b"Not Found" in response.data


def test_home_shows_releases_events_and_news(client, circle, db):
    make_issue(circle)
    db.session.add(
        Event(
            circle=circle,
            name="Stamp Mela",
            date=date.today() + timedelta(days=3),
            location="Vijayawada",
        )
    )
    db.session.add(CircleUpdate(circle=circle, kind="fdc", title="New FDC out", content="Details"))
    db.session.commit()
    page = client.get("/home").data
    assert b"Release of Araku Coffee" in page
    assert b"Stamp Mela" in page
    assert b"FDC: New FDC out" in page


def test_stamp_mela_chooser(client):
    assert client.post("/submit", data={"user_type": "buyer"}).headers["Location"] == "/buyer_home"
    assert client.post("/submit", data={"user_type": "seller"}).headers["Location"] == (
        "/seller_home"
    )


def test_chatbot_without_key_gives_fallback(client):
    response = client.post("/ask", json={"message": "How do I pre-book?"})
    assert "Stamp Store" in response.get_json()["response"]


def test_chatbot_with_key_calls_gemini(app, client):
    app.config["GEMINI_API_KEY"] = "k"

    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "Use the Stamp Store."}]}}]}

    with patch("app.services.assistant.requests.post", return_value=Reply()) as post:
        response = client.post("/ask", json={"message": "How do I pre-book?"})
    assert response.get_json()["response"] == "Use the Stamp Store."
    assert post.call_args.kwargs["headers"] == {"x-goog-api-key": "k"}


# ---------- selling ----------


def test_sell_then_view_stamp(client, alice):
    login(client, "alice")
    response = sell(client)
    assert response.status_code == 302
    detail = client.get(response.headers["Location"])
    assert b"Taj Mahal" in detail.data
    assert b"Listing Verification" in detail.data
    assert b"Verified" in detail.data


def test_sell_form_shows_validation_error(client, alice):
    login(client, "alice")
    response = sell(client, coated_price="abc")
    assert response.status_code == 422
    assert b"Price must be a number" in response.data


def test_copied_photo_is_flagged_on_detail_page(client, alice, bob, db):
    login(client, "alice")
    sell(client, seed=7)
    client.post("/logout")
    login(client, "bob")
    detail = client.get(sell(client, seed=7).headers["Location"])
    assert b"Flagged" in detail.data
    assert b"near-copy of listing" in detail.data


def test_seller_home_lists_my_stamps(client, alice):
    login(client, "alice")
    sell(client)
    page = client.get("/seller_home").data
    assert b"Taj Mahal" in page
    assert b"1 stamp listed" in page


def test_edit_and_remove_stamp(client, alice, db):
    login(client, "alice")
    sell(client)
    listing = db.session.query(Listing).one()
    client.post(
        f"/stamp/{listing.id}/edit",
        data={
            "stamp_name": "Taj Mahal (mint)",
            "year_of_manufacture": "1975",
            "coated_price": "25",
            "quantity": "2",
            "description": "",
            "issue_id": "",
        },
    )
    assert db.session.get(Listing, listing.id).title == "Taj Mahal (mint)"
    client.post(f"/stamp/{listing.id}/delete")
    assert db.session.get(Listing, listing.id).status == "removed"


def test_other_user_cannot_edit_stamp(client, alice, bob, db):
    login(client, "alice")
    sell(client)
    listing = db.session.query(Listing).one()
    client.post("/logout")
    login(client, "bob")
    assert client.get(f"/stamp/{listing.id}/edit").status_code == 403


# ---------- buying ----------


def _alice_listing(client, db):
    login(client, "alice")
    sell(client)
    client.post("/logout")
    return db.session.query(Listing).one()


def test_purchase_through_original_form(client, alice, bob, db):
    listing = _alice_listing(client, db)
    login(client, "bob")
    response = client.post(
        f"/purchase/{listing.id}",
        data={
            "name": "Bob Kumar",
            "email": "bob@example.com",
            "phone": "+91 98765 43210",
            "quantity": "1",
            "address": "Balewadi, Pune",
        },
    )
    assert response.headers["Location"] == "/orders"
    order = db.session.query(Order).one()
    assert (order.contact_name, order.contact_phone) == ("Bob Kumar", "+91 98765 43210")
    assert b"Taj Mahal" in client.get("/orders").data
    # the seller sees the buyer's delivery details, like the original orders table
    client.post("/logout")
    login(client, "alice")
    orders_page = client.get("/orders").data
    assert b"Bob Kumar" in orders_page and b"Balewadi, Pune" in orders_page


def test_purchase_rejects_bad_phone(client, alice, bob, db):
    listing = _alice_listing(client, db)
    login(client, "bob")
    response = client.post(
        f"/purchase/{listing.id}",
        data={
            "name": "Bob",
            "email": "bob@example.com",
            "phone": "call me",
            "quantity": "1",
            "address": "Pune",
        },
    )
    assert response.status_code == 422
    assert db.session.query(Order).count() == 0


def test_purchase_more_than_stock_shows_error(client, alice, bob, db):
    listing = _alice_listing(client, db)
    login(client, "bob")
    response = client.post(
        f"/purchase/{listing.id}",
        data={
            "name": "Bob",
            "email": "b@example.com",
            "phone": "9876543210",
            "quantity": "5",
            "address": "Pune",
        },
    )
    assert response.status_code == 409
    assert b"Not enough stock" in response.data


def test_buyer_home_sort_and_verified_filter(client, alice, bob, db):
    login(client, "alice")
    sell(client, seed=1, stamp_name="Cheap", coated_price="5")
    sell(client, seed=2, stamp_name="Pricey", coated_price="500")
    client.post("/logout")
    login(client, "bob")
    sell(client, seed=1, stamp_name="Copycat", coated_price="50")  # flagged duplicate

    page = client.get("/buyer_home?sort=price_desc").get_data(as_text=True)
    collection = page.split("Stamp Collection</h2>")[1]
    assert collection.index("Pricey") < collection.index("Copycat") < collection.index("Cheap")

    verified = client.get("/buyer_home?verified=1").get_data(as_text=True)
    assert "Copycat" not in verified.split("Stamp Collection</h2>")[1]


def test_search(client, alice, db):
    _alice_listing(client, db)
    assert b"Taj Mahal" in client.get("/search?search=taj").data
    assert b"No stamps found" in client.get("/search?search=zebra").data


def test_wishlist_heart_toggle_json(client, alice, bob, db):
    listing = _alice_listing(client, db)
    login(client, "bob")
    response = client.post(f"/wishlist/{listing.id}", headers={"Accept": "application/json"})
    assert response.get_json() == {"liked": True}
    assert b"Taj Mahal" in client.get("/wishlist").data
    response = client.post(f"/wishlist/{listing.id}", headers={"Accept": "application/json"})
    assert response.get_json() == {"liked": False}
    assert db.session.query(WishlistItem).count() == 0


# ---------- stamp store: pre-booking ----------


def test_prebook_flow(client, circle, alice, db):
    issue = make_issue(circle, prebook=5)
    login(client, "alice")
    assert b"Araku Coffee" in client.get(f"/pre_book/{issue.id}").data
    response = client.post("/submit_order", data={"stampType": str(issue.id), "quantity": "2"})
    booking = db.session.query(Prebooking).one()
    assert response.headers["Location"] == f"/payment/{booking.id}"
    payment = client.get(response.headers["Location"]).data
    assert "₹50.00".encode() in payment
    assert db.session.get(Issue, issue.id).prebook_available == 3
    assert b"Araku Coffee" in client.get("/my-collection").data


def test_cannot_see_someone_elses_payment_page(client, circle, alice, bob, db):
    issue = make_issue(circle)
    login(client, "alice")
    client.post("/submit_order", data={"stampType": str(issue.id), "quantity": "1"})
    booking = db.session.query(Prebooking).one()
    client.post("/logout")
    login(client, "bob")
    assert client.get(f"/payment/{booking.id}").status_code == 404


# ---------- philatelist dashboard ----------


def test_profile_saves_to_database(client, alice, db):
    login(client, "alice")
    client.post(
        "/myprofile",
        data={
            "name": "Alice Rao",
            "email": "alice.rao@example.com",
            "membership": "Premium",
            "profilePic": (png(3), "me.png"),
        },
        content_type="multipart/form-data",
    )
    user = db.session.get(User, alice.id)
    assert (user.full_name, user.email, user.membership) == (
        "Alice Rao",
        "alice.rao@example.com",
        "Premium",
    )
    assert user.avatar_filename
    assert b"Alice Rao" in client.get("/dashboard").data


def test_profile_rejects_taken_email(client, alice, bob, db):
    login(client, "alice")
    response = client.post(
        "/myprofile", data={"name": "Alice", "email": "bob@example.com", "membership": "Basic"}
    )
    assert response.status_code == 422
    assert db.session.get(User, alice.id).email == "alice@example.com"


# ---------- postal circle pages ----------


def circle_login(client):
    return login(client, "ap_circle", circle=AP)


def test_circle_manages_stamps(client, circle, db):
    circle_login(client)
    client.post(
        "/manage_stamps/add",
        data={
            "stampName": "Lepakshi",
            "stampDate": "2026-08-15",
            "stampPrice": "5",
            "stampStock": "100",
            "stampImage": (png(3), "ref.png"),
        },
        content_type="multipart/form-data",
    )
    issue = db.session.query(Issue).one()
    assert (issue.title, issue.prebook_available) == ("Lepakshi", 100)

    client.post(
        "/manage_stamps/update",
        data={
            "existingStamps": str(issue.id),
            "updateStampName": "Lepakshi Temple",
            "updateStampDate": "",
            "updateStampPrice": "6",
        },
    )
    issue = db.session.get(Issue, issue.id)
    assert (issue.title, str(issue.denomination)) == ("Lepakshi Temple", "6.00")
    assert b"Lepakshi Temple" in client.get("/manage_stamps").data

    client.post("/manage_stamps/delete", data={"deleteStampId": str(issue.id)})
    assert db.session.query(Issue).count() == 0


def test_circle_dashboard_posts_update_and_stock(client, circle, db):
    issue = make_issue(circle, prebook=0)
    circle_login(client)
    client.post(
        "/post_update",
        data={
            "updateType": "souvenir",
            "updateTitle": "Souvenir sheet",
            "updateContent": "Out now",
        },
    )
    assert db.session.query(CircleUpdate).one().kind_label == "Souvenir Sheet"
    client.post("/update_stock", data={"item": str(issue.id), "quantity": "250"})
    assert db.session.get(Issue, issue.id).prebook_available == 250
    assert b"Souvenir Sheet: Souvenir sheet" in client.get("/home").data


def test_circle_cannot_touch_other_circles_stamps(client, circle, db):
    other = make_user("ka_circle", "circle", "Karnataka")
    issue = make_issue(other)
    circle_login(client)
    client.post("/update_stock", data={"item": str(issue.id), "quantity": "1"})
    assert db.session.get(Issue, issue.id).prebook_available == 10


def test_circle_events_add_update_delete(client, circle, db):
    circle_login(client)
    client.post(
        "/add_event",
        data={
            "eventName": "Stamp Mela",
            "eventDate": "2099-01-01",
            "eventLocation": "Vijayawada",
            "eventDescription": "Annual fair",
        },
    )
    event = db.session.query(Event).one()
    assert client.get("/get-events").get_json()[0]["event_name"] == "Stamp Mela"

    client.post(
        "/update_event",
        data={
            "existingEvents": str(event.id),
            "updateEventName": "",
            "updateEventDate": "",
            "updateEventLocation": "Guntur",
            "updateEventDescription": "",
        },
    )
    event = db.session.get(Event, event.id)
    assert (event.name, event.location) == ("Stamp Mela", "Guntur")

    client.post("/delete_event", data={"eventId": str(event.id)})
    assert db.session.query(Event).count() == 0


def test_circle_cannot_delete_other_circles_event(client, circle, db):
    other = make_user("ka_circle", "circle", "Karnataka")
    event = Event(circle=other, name="KA fair", date=date(2099, 1, 1), location="Bengaluru")
    db.session.add(event)
    db.session.commit()
    circle_login(client)
    assert client.post("/delete_event", data={"eventId": str(event.id)}).status_code == 403


def test_csrf_is_enforced_when_enabled(app, client, alice):
    app.config["WTF_CSRF_ENABLED"] = True
    response = client.post("/login_philatelists", data={"username": "alice", "password": "x"})
    assert response.status_code == 400
