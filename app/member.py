"""Philatelist dashboard pages (dashboard-1 sidebar): dashboard, profile, collection, settings."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from .extensions import db
from .models import Order, Prebooking
from .services.accounts import MEMBERSHIPS, change_email, change_password, update_profile
from .services.errors import ValidationError
from .web import collector_required

bp = Blueprint("member", __name__)


@bp.route("/dashboard")
@collector_required
def dashboard():
    return render_template("dashboard-1.html")


@bp.route("/myprofile", methods=["GET", "POST"])
@collector_required
def myprofile():
    if request.method == "POST":
        try:
            update_profile(current_user, request.form, request.files.get("profilePic"))
        except ValidationError as exc:
            db.session.rollback()
            flash(exc.message, "error")
            return render_template("myprofile.html", memberships=MEMBERSHIPS), 422
        flash("Profile updated successfully!", "success")
        return redirect(url_for("member.myprofile"))
    return render_template("myprofile.html", memberships=MEMBERSHIPS)


@bp.route("/my-collection")
@collector_required
def my_collection():
    orders = db.session.scalars(
        select(Order).where(Order.buyer_id == current_user.id).order_by(Order.created_at.desc())
    ).all()
    prebookings = db.session.scalars(
        select(Prebooking)
        .where(Prebooking.user_id == current_user.id)
        .order_by(Prebooking.created_at.desc())
    ).all()
    return render_template("my_collection.html", orders=orders, prebookings=prebookings)


@bp.route("/settings2")
@collector_required
def settings():
    return render_template("settings2.html")


@bp.post("/settings2/profile")
@collector_required
def update_email():
    try:
        change_email(current_user, request.form.get("email", ""))
        db.session.commit()
        flash("Profile updated successfully!", "success")
    except ValidationError as exc:
        db.session.rollback()
        flash(exc.message, "error")
    return redirect(url_for("member.settings"))


@bp.post("/settings2/password")
@collector_required
def update_password():
    try:
        change_password(
            current_user,
            request.form.get("currentPassword", ""),
            request.form.get("newPassword", ""),
            request.form.get("confirmPassword", ""),
        )
        flash("Password updated successfully!", "success")
    except ValidationError as exc:
        flash(exc.message, "error")
    return redirect(url_for("member.settings"))
