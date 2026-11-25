"""Login, registration and logout, using the original Login.html / register.html pages.

Both pages have one form for philatelists and one for postal circles, so each gets its own POST
route. The login routes check that the account actually has that role.
"""

from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from .models import POSTAL_CIRCLES, ROLE_CIRCLE, ROLE_COLLECTOR
from .services.accounts import authenticate, register_user
from .services.errors import ValidationError

bp = Blueprint("auth", __name__)


def _safe_next(target: str | None) -> str | None:
    # Only allow relative redirects, so ?next= can't send users to another site.
    if (
        target
        and target.startswith("/")
        and not target.startswith(("//", "/\\"))
        and not urlsplit(target).netloc
    ):
        return target
    return None


def home_for(user) -> str:
    return url_for("circle.dashboard") if user.is_circle else url_for("member.dashboard")


@bp.route("/")
@bp.route("/login")
def login():
    if current_user.is_authenticated:
        return redirect(home_for(current_user))
    return render_template("Login.html", circles=POSTAL_CIRCLES, next=request.args.get("next"))


def _login(role: str):
    user = authenticate(request.form.get("username", ""), request.form.get("password", ""))
    ok = user is not None and user.role == role
    if ok and role == ROLE_CIRCLE:
        ok = user.postal_circle == request.form.get("postal_circle")
    if not ok:
        flash("Invalid username or password.", "error")
        return render_template(
            "Login.html", circles=POSTAL_CIRCLES, next=request.args.get("next"), open_tab=role
        ), 401
    login_user(user)
    flash("Login successful!", "success")
    return redirect(_safe_next(request.args.get("next")) or home_for(user))


@bp.post("/login_philatelists")
def login_philatelists():
    return _login(ROLE_COLLECTOR)


@bp.post("/login_postalcircles")
def login_postalcircles():
    return _login(ROLE_CIRCLE)


@bp.route("/register")
def register():
    if current_user.is_authenticated:
        return redirect(home_for(current_user))
    return render_template("register.html", circles=POSTAL_CIRCLES, form={})


def _register(role: str):
    data = request.form.to_dict()
    data["role"] = role
    try:
        user = register_user(data)
    except ValidationError as exc:
        flash(exc.message, "error")
        return render_template(
            "register.html", circles=POSTAL_CIRCLES, form=data, open_tab=role
        ), 422
    login_user(user)
    flash("Registered successfully!", "success")
    return redirect(home_for(user))


@bp.post("/register_philatelist")
def register_philatelist():
    return _register(ROLE_COLLECTOR)


@bp.post("/register_postalcircle")
def register_postalcircle():
    return _register(ROLE_CIRCLE)


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
