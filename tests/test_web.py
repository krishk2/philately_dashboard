"""Smoke tests that every page renders and the main web flows work end to end."""

from app.models import Event, Listing, WishlistItem

from .conftest import login, png


def create_listing_via_form(client, seed=1, **fields):
    data = {"title": "Taj Mahal", "year": "1975", "price": "20", "quantity": "2", "issue_id": ""}
    data.update(fields)
    data["image"] = (png(seed), "taj.png")
    return client.post("/listings/new", data=data, content_type="multipart/form-data")


def test_public_pages_render(client):
    for path in ["/", "/listings", "/issues", "/events", "/auth/login", "/auth/register"]:
        assert client.get(path).status_code == 200, path


def test_missing_page_renders_html_404(client):
    response = client.get("/definitely-not-here")
    assert response.status_code == 404
    assert b"Not Found" in response.data


def test_sell_then_view_listing(client, alice):
    login(client, "alice")
    response = create_listing_via_form(client)
    assert response.status_code == 302
    detail = client.get(response.headers["Location"])
    assert b"Taj Mahal" in detail.data
    assert b"Listing verification" in detail.data
    assert b"Verified" in detail.data


def test_listing_form_shows_validation_error(client, alice):
    login(client, "alice")
    response = create_listing_via_form(client, price="abc")
    assert response.status_code == 422
    assert b"Price must be a number" in response.data


def test_buy_through_web(client, alice, bob, db):
    login(client, "alice")
    create_listing_via_form(client)
    listing = db.session.query(Listing).one()
    client.post("/auth/logout")

    login(client, "bob")
    response = client.post(
        f"/listings/{listing.id}/buy", data={"quantity": "1", "shipping_address": "Pune"}
    )
    assert response.status_code == 302
    orders = client.get("/me/orders")
    assert b"Taj Mahal" in orders.data


def test_wishlist_toggle(client, alice, bob, db):
    login(client, "alice")
    create_listing_via_form(client)
    listing = db.session.query(Listing).one()
    client.post("/auth/logout")

    login(client, "bob")
    client.post(f"/listings/{listing.id}/wishlist")
    assert db.session.query(WishlistItem).count() == 1
    assert b"Taj Mahal" in client.get("/me/wishlist").data
    client.post(f"/listings/{listing.id}/wishlist")
    assert db.session.query(WishlistItem).count() == 0


def test_other_user_cannot_edit_listing(client, alice, bob, db):
    login(client, "alice")
    create_listing_via_form(client)
    listing = db.session.query(Listing).one()
    client.post("/auth/logout")
    login(client, "bob")
    assert client.get(f"/listings/{listing.id}/edit").status_code == 403


def test_circle_publishes_issue_and_event(client, circle, db):
    login(client, "ap_circle")
    response = client.post(
        "/circle/issues",
        data={
            "title": "Lepakshi",
            "issue_date": "2026-08-15",
            "denomination": "5",
            "prebook_available": "100",
            "image": (png(3), "ref.png"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    client.post(
        "/circle/events",
        data={"name": "Stamp Mela", "date": "2099-01-01", "location": "Vijayawada"},
    )
    assert db.session.query(Event).count() == 1
    page = client.get("/circle/")
    assert b"Lepakshi" in page.data and b"Stamp Mela" in page.data
    assert b"Stamp Mela" in client.get("/events").data


def test_circle_cannot_edit_other_circles_event(client, circle, db):
    from .conftest import make_user

    other = make_user("ka_circle", "circle", "Karnataka")
    from datetime import date

    event = Event(circle=other, name="KA fair", date=date(2099, 1, 1), location="Bengaluru")
    db.session.add(event)
    db.session.commit()
    login(client, "ap_circle")
    assert client.post(f"/circle/events/{event.id}/delete").status_code == 403


def test_csrf_is_enforced_when_enabled(app, client, alice):
    app.config["WTF_CSRF_ENABLED"] = True
    response = client.post("/auth/login", data={"username": "alice", "password": "x"})
    assert response.status_code == 400
