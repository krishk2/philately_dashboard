"""Web routes for sign-up, login, logout and account settings."""

from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from .models import POSTAL_CIRCLES
from .services.accounts import authenticate, change_password, register_user
from .services.errors import ValidationError

bp = Blueprint("auth", __name__, url_prefix="/auth")


def _safe_next(target: str | None) -> str:
    # Only allow relative redirects, so ?next= can't send users to another site.
    if (
        target
        and target.startswith("/")
        and not target.startswith(("//", "/\\"))
        and not urlsplit(target).netloc
    ):
        return target
    return url_for("main.index")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        try:
            user = register_user(request.form)
        except ValidationError as exc:
            flash(exc.message, "error")
            return render_template(
                "auth/register.html", circles=POSTAL_CIRCLES, form=request.form
            ), 422
        login_user(user)
        flash(f"Welcome, {user.username}!", "success")
        return redirect(url_for("main.index"))
    return render_template("auth/register.html", circles=POSTAL_CIRCLES, form={})


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        user = authenticate(request.form.get("username", ""), request.form.get("password", ""))
        if user is None:
            flash("Invalid username or password.", "error")
            return render_template("auth/login.html"), 401
        login_user(user, remember=bool(request.form.get("remember")))
        return redirect(_safe_next(request.args.get("next")))
    return render_template("auth/login.html")


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        try:
            change_password(
                current_user,
                request.form.get("current_password", ""),
                request.form.get("new_password", ""),
                request.form.get("confirm_password", ""),
            )
        except ValidationError as exc:
            flash(exc.message, "error")
            return render_template("auth/settings.html"), 422
        flash("Password updated.", "success")
        return redirect(url_for("auth.settings"))
    return render_template("auth/settings.html")
