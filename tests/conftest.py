import io

import pytest

from app import create_app
from app.cli import draw_stamp
from app.extensions import db as _db
from app.models import ROLE_CIRCLE, ROLE_COLLECTOR, User
from app.services.storage import init_storage

PASSWORD = "correct-horse"


@pytest.fixture
def app(tmp_path):
    app = create_app("testing")
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    init_storage(app)  # re-point local storage at the per-test folder
    with app.app_context():
        _db.create_all()
        yield app
        _db.session.remove()
        _db.drop_all()
        _db.engine.dispose()  # close the in-memory SQLite connection


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    return _db


def make_user(username, role=ROLE_COLLECTOR, circle=None):
    user = User(username=username, email=f"{username}@example.com", role=role, postal_circle=circle)
    user.set_password(PASSWORD)
    _db.session.add(user)
    _db.session.commit()
    return user


@pytest.fixture
def alice(app):
    return make_user("alice")


@pytest.fixture
def bob(app):
    return make_user("bob")


@pytest.fixture
def circle(app):
    return make_user("ap_circle", ROLE_CIRCLE, "Andhra Pradesh")


def png(seed=1, label="Stamp", value="Rs 5", size=None):
    """PNG bytes of a generated stamp. Same seed -> same picture."""
    image = draw_stamp(seed, label, value)
    if size:
        image = image.resize(size)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def login(client, username, password=PASSWORD, circle=None):
    """Log in through the original Login.html forms (one per account type)."""
    if circle:
        return client.post(
            "/login_postalcircles",
            data={"username": username, "password": password, "postal_circle": circle},
        )
    return client.post("/login_philatelists", data={"username": username, "password": password})


def token_for(client, username):
    response = client.post("/api/v1/auth/token", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['token']}"}
