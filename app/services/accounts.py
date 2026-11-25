"""User registration, login and API tokens."""

from __future__ import annotations

import re

from email_validator import EmailNotValidError, validate_email
from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, or_, select

from ..extensions import db
from ..models import POSTAL_CIRCLES, ROLE_CIRCLE, ROLES, User
from .errors import ValidationError

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
MIN_PASSWORD_LENGTH = 8


def register_user(data) -> User:
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    role = (data.get("role") or "").strip()
    circle = (data.get("postal_circle") or "").strip() or None

    if not USERNAME_RE.match(username):
        raise ValidationError(
            "Username must be 3-64 characters: letters, numbers, dot, dash or underscore."
        )
    try:
        email = validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise ValidationError("Enter a valid email address.") from exc
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if role not in ROLES:
        raise ValidationError("Choose an account type.")
    if role == ROLE_CIRCLE and circle not in POSTAL_CIRCLES:
        raise ValidationError("Choose your postal circle.")
    if role != ROLE_CIRCLE:
        circle = None

    taken = db.session.scalar(
        select(User.id).where(
            or_(
                func.lower(User.username) == username.lower(),
                func.lower(User.email) == email.lower(),
            )
        )
    )
    if taken is not None:
        raise ValidationError("That username or email is already registered.")

    user = User(username=username, email=email, role=role, postal_circle=circle)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def authenticate(username: str, password: str) -> User | None:
    user = db.session.scalar(
        select(User).where(func.lower(User.username) == (username or "").strip().lower())
    )
    if user is None or not user.check_password(password or ""):
        return None
    return user


# ---------- API tokens ----------
# Signed (not stored) tokens: the server can verify them with SECRET_KEY alone.


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="api-token")


def issue_token(user: User) -> str:
    # Embedding a slice of the password hash means changing the password revokes old tokens.
    return _serializer().dumps({"uid": user.id, "pw": user.password_hash[-12:]})


def user_from_token(token: str) -> User | None:
    try:
        data = _serializer().loads(token, max_age=current_app.config["API_TOKEN_MAX_AGE"])
    except (BadSignature, SignatureExpired):
        return None
    user = db.session.get(User, data.get("uid"))
    if user is None or user.password_hash[-12:] != data.get("pw"):
        return None
    return user


def change_password(user: User, current: str, new: str, confirm: str) -> None:
    if not user.check_password(current or ""):
        raise ValidationError("Current password is incorrect.")
    if len(new or "") < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if new != confirm:
        raise ValidationError("New passwords do not match.")
    user.set_password(new)
    db.session.commit()


MEMBERSHIPS = ("Basic", "Premium")


def _normalise_email(value: str) -> str:
    try:
        return validate_email((value or "").strip(), check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise ValidationError("Enter a valid email address.") from exc


def change_email(user: User, email: str) -> None:
    email = _normalise_email(email)
    if email.lower() == user.email.lower():
        return
    taken = db.session.scalar(
        select(User.id).where(func.lower(User.email) == email.lower(), User.id != user.id)
    )
    if taken is not None:
        raise ValidationError("That email is already registered.")
    user.email = email


def update_profile(user: User, data, picture=None) -> None:
    """Save the My Profile form: full name, email, membership and an optional picture."""
    from .uploads import delete_image, save_image

    full_name = (data.get("name") or "").strip()
    if not full_name or len(full_name) > 120:
        raise ValidationError("Full name is required (up to 120 characters).")
    membership = data.get("membership") or "Basic"
    if membership not in MEMBERSHIPS:
        raise ValidationError("Choose a membership type.")
    change_email(user, data.get("email", ""))
    user.full_name = full_name
    user.membership = membership
    if picture is not None and picture.filename:
        old = user.avatar_filename
        user.avatar_filename, _ = save_image(picture)
        if old:
            delete_image(old)
    db.session.commit()
