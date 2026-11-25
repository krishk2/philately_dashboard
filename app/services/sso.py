"""Sign in with Google or Yahoo (OpenID Connect).

Both providers publish an OpenID Connect discovery document, so Authlib configures endpoints and
validates the ID token (signature, issuer, audience, expiry, nonce) for us. This module only
decides which local account a verified identity belongs to:

1. A known (provider, subject) pair logs into its linked account.
2. Otherwise a *verified* email that matches an existing account is linked to it, so people who
   registered with a password can switch to Google/Yahoo without a second account.
3. Otherwise a new philatelist account is created.

Postal circle accounts are official: they are never created or signed into through SSO.
"""

from __future__ import annotations

import re
import secrets

from flask import Flask
from sqlalchemy import func, select

from ..extensions import db, oauth
from ..models import ROLE_COLLECTOR, OAuthIdentity, User
from .errors import ValidationError

PROVIDERS = {
    "google": {
        "label": "Google",
        "metadata": "https://accounts.google.com/.well-known/openid-configuration",
    },
    "yahoo": {
        "label": "Yahoo",
        "metadata": "https://api.login.yahoo.com/.well-known/openid-configuration",
    },
}


def register_providers(app: Flask) -> None:
    oauth.init_app(app)
    for name, info in PROVIDERS.items():
        client_id = app.config.get(f"{name.upper()}_CLIENT_ID")
        secret = app.config.get(f"{name.upper()}_CLIENT_SECRET")
        if client_id and secret:
            oauth.register(
                name,
                client_id=client_id,
                client_secret=secret,
                server_metadata_url=info["metadata"],
                client_kwargs={"scope": "openid email profile"},
            )


def enabled_providers(app: Flask) -> list[tuple[str, str]]:
    """[(name, label)] for the providers that have credentials configured."""
    return [
        (name, info["label"])
        for name, info in PROVIDERS.items()
        if app.config.get(f"{name.upper()}_CLIENT_ID")
        and app.config.get(f"{name.upper()}_CLIENT_SECRET")
    ]


def _unique_username(email: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]", "", email.split("@")[0])[:50] or "collector"
    if len(base) < 3:
        base = f"{base}user"
    candidate, n = base, 1
    while db.session.scalar(select(User.id).where(func.lower(User.username) == candidate.lower())):
        n += 1
        candidate = f"{base}{n}"
    return candidate


def user_from_claims(provider: str, claims: dict) -> User:
    """Find, link or create the local user for verified OpenID Connect claims."""
    subject = str(claims.get("sub") or "")
    email = (claims.get("email") or "").strip()
    if not subject:
        raise ValidationError("The sign-in provider didn't return an account id.")

    identity = db.session.scalar(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == provider, OAuthIdentity.subject == subject
        )
    )
    if identity is not None:
        return identity.user

    verified = claims.get("email_verified") in (True, "true", "True", 1)
    if not email or not verified:
        raise ValidationError(
            f"Your {PROVIDERS[provider]['label']} account has no verified email address, "
            "so we can't sign you in with it."
        )

    user = db.session.scalar(select(User).where(func.lower(User.email) == email.lower()))
    if user is not None and user.is_circle:
        raise ValidationError(
            "Postal circle accounts sign in with their username and password, not "
            f"{PROVIDERS[provider]['label']}."
        )
    if user is None:
        user = User(
            username=_unique_username(email),
            email=email,
            role=ROLE_COLLECTOR,
            full_name=(claims.get("name") or "").strip()[:120] or None,
        )
        # No password: this account signs in through the provider. A random, never-shown
        # password keeps the column non-null without making password login possible.
        user.set_password(secrets.token_urlsafe(32))
        db.session.add(user)
        db.session.flush()

    db.session.add(OAuthIdentity(user=user, provider=provider, subject=subject, email=email))
    db.session.commit()
    return user
