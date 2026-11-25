from app.models import User

from .conftest import PASSWORD, login


def register(client, **overrides):
    data = {
        "username": "newbie",
        "email": "newbie@example.com",
        "password": "longenough",
        "role": "collector",
    }
    data.update(overrides)
    return client.post("/auth/register", data=data)


def test_register_hashes_password(client, db):
    response = register(client)
    assert response.status_code == 302
    user = db.session.query(User).filter_by(username="newbie").one()
    assert user.password_hash != "longenough"
    assert user.check_password("longenough")


def test_register_rejects_duplicate_username_case_insensitive(client, alice):
    response = register(client, username="ALICE", email="other@example.com")
    assert response.status_code == 422
    assert b"already registered" in response.data


def test_register_rejects_short_password(client):
    assert register(client, password="short").status_code == 422


def test_circle_registration_requires_circle(client):
    assert register(client, role="circle").status_code == 422
    assert register(client, role="circle", postal_circle="Karnataka").status_code == 302


def test_login_and_logout(client, alice):
    assert login(client, "alice").status_code == 302
    assert client.get("/me/orders").status_code == 200
    client.post("/auth/logout")
    assert client.get("/me/orders").status_code == 302


def test_login_wrong_password(client, alice):
    assert login(client, "alice", "nope").status_code == 401


def test_login_next_param_blocks_open_redirect(client, alice):
    response = client.post(
        "/auth/login?next=//evil.example.com",
        data={"username": "alice", "password": PASSWORD},
    )
    assert response.headers["Location"] == "/"


def test_login_next_param_allows_local_path(client, alice):
    response = client.post(
        "/auth/login?next=/me/wishlist", data={"username": "alice", "password": PASSWORD}
    )
    assert response.headers["Location"] == "/me/wishlist"


def test_change_password(client, alice):
    login(client, "alice")
    response = client.post(
        "/auth/settings",
        data={
            "current_password": PASSWORD,
            "new_password": "brand-new-pass",
            "confirm_password": "brand-new-pass",
        },
    )
    assert response.status_code == 302
    client.post("/auth/logout")
    assert login(client, "alice", "brand-new-pass").status_code == 302


def test_change_password_requires_current(client, alice):
    login(client, "alice")
    response = client.post(
        "/auth/settings",
        data={"current_password": "wrong", "new_password": "x" * 10, "confirm_password": "x" * 10},
    )
    assert response.status_code == 422


def test_collector_cannot_open_circle_dashboard(client, alice):
    login(client, "alice")
    assert client.get("/circle/").status_code == 403


def test_circle_can_open_dashboard(client, circle):
    login(client, "ap_circle")
    assert client.get("/circle/").status_code == 200
