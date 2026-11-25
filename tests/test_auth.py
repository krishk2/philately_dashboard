from app.models import User

from .conftest import PASSWORD, login


def register(client, role="philatelist", **overrides):
    data = {"username": "newbie", "email": "newbie@example.com", "password": "longenough"}
    data.update(overrides)
    path = "/register_postalcircle" if role == "circle" else "/register_philatelist"
    return client.post(path, data=data)


def test_register_philatelist_hashes_password(client, db):
    response = register(client)
    assert response.status_code == 302
    assert response.headers["Location"] == "/dashboard"
    user = db.session.query(User).filter_by(username="newbie").one()
    assert user.role == "collector"
    assert user.password_hash != "longenough"
    assert user.check_password("longenough")


def test_register_postal_circle(client, db):
    response = register(client, role="circle", postal_circle="Karnataka")
    assert response.headers["Location"] == "/postal-circle-dashboard"
    assert db.session.query(User).filter_by(username="newbie").one().postal_circle == "Karnataka"


def test_circle_registration_requires_circle(client):
    assert register(client, role="circle").status_code == 422


def test_register_rejects_duplicate_username_case_insensitive(client, alice):
    response = register(client, username="ALICE", email="other@example.com")
    assert response.status_code == 422
    assert b"already registered" in response.data


def test_register_rejects_short_password(client):
    assert register(client, password="short").status_code == 422


def test_login_and_logout(client, alice):
    response = login(client, "alice")
    assert response.headers["Location"] == "/dashboard"
    assert client.get("/dashboard").status_code == 200
    client.post("/logout")
    assert client.get("/dashboard").status_code == 302


def test_login_wrong_password(client, alice):
    assert login(client, "alice", "nope").status_code == 401


def test_philatelist_cannot_use_postal_circle_login(client, alice):
    assert login(client, "alice", circle="Andhra Pradesh").status_code == 401


def test_circle_login_checks_the_selected_circle(client, circle):
    assert login(client, "ap_circle", circle="Karnataka").status_code == 401
    response = login(client, "ap_circle", circle="Andhra Pradesh")
    assert response.headers["Location"] == "/postal-circle-dashboard"


def test_login_next_param_blocks_open_redirect(client, alice):
    response = client.post(
        "/login_philatelists?next=//evil.example.com",
        data={"username": "alice", "password": PASSWORD},
    )
    assert response.headers["Location"] == "/dashboard"


def test_login_next_param_allows_local_path(client, alice):
    response = client.post(
        "/login_philatelists?next=/wishlist", data={"username": "alice", "password": PASSWORD}
    )
    assert response.headers["Location"] == "/wishlist"


def test_change_password_from_settings(client, alice):
    login(client, "alice")
    client.post(
        "/settings2/password",
        data={
            "currentPassword": PASSWORD,
            "newPassword": "brand-new-pass",
            "confirmPassword": "brand-new-pass",
        },
    )
    client.post("/logout")
    assert login(client, "alice", "brand-new-pass").status_code == 302


def test_change_password_requires_current(client, alice):
    login(client, "alice")
    response = client.post(
        "/settings2/password",
        data={"currentPassword": "wrong", "newPassword": "x" * 10, "confirmPassword": "x" * 10},
        follow_redirects=True,
    )
    assert b"Current password is incorrect" in response.data


def test_circle_changes_email(client, circle, db):
    login(client, "ap_circle", circle="Andhra Pradesh")
    client.post("/update_profile", data={"email": "ap@indiapost.example"})
    assert db.session.get(User, circle.id).email == "ap@indiapost.example"


def test_collector_cannot_open_circle_pages(client, alice):
    login(client, "alice")
    for path in ["/postal-circle-dashboard", "/manage_stamps", "/events", "/settings"]:
        assert client.get(path).status_code == 403, path


def test_circle_is_sent_away_from_collector_pages(client, circle):
    login(client, "ap_circle", circle="Andhra Pradesh")
    response = client.get("/dashboard")
    assert response.headers["Location"] == "/postal-circle-dashboard"


def test_logged_out_user_is_sent_to_login(client):
    response = client.get("/sell_stamp")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")
