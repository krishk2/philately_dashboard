"""Google / Yahoo sign-in. The OAuth exchange is mocked; the account-matching rules are real."""

from unittest.mock import patch

import pytest

from app.models import OAuthIdentity, User
from app.services.errors import ValidationError
from app.services.sso import user_from_claims

from .conftest import make_user


def claims(**overrides):
    data = {
        "sub": "g-123",
        "email": "new.collector@gmail.com",
        "email_verified": True,
        "name": "New Collector",
    }
    data.update(overrides)
    return data


def test_creates_new_philatelist(app, db):
    user = user_from_claims("google", claims())
    assert (user.role, user.username, user.full_name) == (
        "collector",
        "new.collector",
        "New Collector",
    )
    assert db.session.query(OAuthIdentity).one().subject == "g-123"


def test_same_identity_signs_into_same_account(app, db):
    first = user_from_claims("google", claims())
    again = user_from_claims("google", claims(email="changed@gmail.com"))
    assert again.id == first.id
    assert db.session.query(User).count() == 1


def test_links_to_existing_account_by_verified_email(app, db):
    alice = make_user("alice")
    user = user_from_claims("yahoo", claims(sub="y-9", email="ALICE@example.com"))
    assert user.id == alice.id


def test_unverified_email_is_rejected(app, db):
    make_user("alice")
    with pytest.raises(ValidationError, match="verified email"):
        user_from_claims("google", claims(email="alice@example.com", email_verified=False))


def test_postal_circle_accounts_cannot_use_sso(app, db):
    make_user("ap_circle", "circle", "Andhra Pradesh")
    with pytest.raises(ValidationError, match="Postal circle"):
        user_from_claims("google", claims(email="ap_circle@example.com"))


def test_username_collisions_get_a_suffix(app, db):
    make_user("new.collector")
    user = user_from_claims("google", claims())
    assert user.username == "new.collector2"


def test_login_page_shows_provider_buttons(client):
    page = client.get("/login").get_data(as_text=True)
    assert "Continue with Google" in page and "Continue with Yahoo" in page


def test_unknown_provider_is_refused(client):
    response = client.get("/login/facebook")
    assert response.headers["Location"] == "/login"


class FakeClient:
    def __init__(self, userinfo):
        self._userinfo = userinfo

    def authorize_redirect(self, redirect_uri):
        from flask import redirect

        return redirect(f"https://accounts.example/auth?redirect_uri={redirect_uri}")

    def authorize_access_token(self):
        return {"access_token": "x", "userinfo": self._userinfo}


def test_full_redirect_and_callback(client, db):
    fake = FakeClient(claims())
    with patch("app.auth.oauth.create_client", return_value=fake):
        start = client.get("/login/google?next=/wishlist")
        assert "redirect_uri=http://localhost/login/google/callback" in start.headers["Location"]
        done = client.get("/login/google/callback?code=abc&state=xyz")
    assert done.headers["Location"] == "/wishlist"
    assert client.get("/dashboard").status_code == 200


def test_cancelled_sign_in(client):
    with patch("app.auth.oauth.create_client", return_value=FakeClient(claims())):
        response = client.get("/login/google/callback?error=access_denied", follow_redirects=True)
    assert b"Sign-in was cancelled" in response.data
