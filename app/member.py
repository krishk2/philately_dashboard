"""Philatelist profile area (dashboard-1 sidebar): overview, profile, NPDA, collection, settings.

Notifications live here too but are open to every logged-in account (postal circles get forum
replies), and use the site layout so they're reachable from the profile icon.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select, update

from .community import completed_weeks
from .extensions import db
from .models import (
    COURSE_WEEKS,
    LISTING_REMOVED,
    POSTAL_CIRCLES,
    Listing,
    Notification,
    Order,
    Prebooking,
    WishlistItem,
)
from .services import collectors as collectors_svc
from .services import npda as npda_svc
from .services.accounts import MEMBERSHIPS, change_email, change_password, update_profile
from .services.errors import DomainError, ValidationError
from .web import collector_required

bp = Blueprint("member", __name__)


def _stats(user) -> dict:
    """Numbers for the profile overview cards."""

    def scalar(stmt):
        return db.session.scalar(stmt) or 0

    sold = (
        select(func.coalesce(func.sum(Order.quantity), 0), func.coalesce(func.sum(Order.total), 0))
        .join(Listing)
        .where(Listing.seller_id == user.id)
    )
    sold_qty, earned = db.session.execute(sold).one()
    return {
        "bought": scalar(select(func.sum(Order.quantity)).where(Order.buyer_id == user.id)),
        "spent": scalar(select(func.sum(Order.total)).where(Order.buyer_id == user.id)),
        "prebooked": scalar(
            select(func.sum(Prebooking.quantity)).where(Prebooking.user_id == user.id)
        ),
        "listed": scalar(
            select(func.count(Listing.id)).where(
                Listing.seller_id == user.id, Listing.status != LISTING_REMOVED
            )
        ),
        "sold": sold_qty,
        "earned": earned,
        "wishlist": scalar(
            select(func.count(WishlistItem.id)).where(WishlistItem.user_id == user.id)
        ),
    }


@bp.route("/dashboard")
@collector_required
def dashboard():
    notifications = db.session.scalars(
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(5)
    ).all()
    return render_template(
        "dashboard-1.html",
        stats=_stats(current_user),
        notifications=notifications,
        course_done=len(completed_weeks(current_user)),
        course_total=len(COURSE_WEEKS),
        badges=collectors_svc.badges_for(current_user),
    )


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
        # Coming from the NPDA page's "complete your profile" link: go straight back.
        if request.args.get("next") == "npda":
            return redirect(url_for("member.npda"))
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


# ---------- NPDA ----------


@bp.route("/npda")
@collector_required
def npda():
    return render_template(
        "npda.html",
        account=current_user.npda,
        circles=POSTAL_CIRCLES,
        min_deposit=npda_svc.MIN_OPENING_DEPOSIT,
    )


def _npda_action(action, success: str):
    try:
        action()
        flash(success, "success")
    except DomainError as exc:
        db.session.rollback()
        flash(exc.message, "error")
    return redirect(url_for("member.npda"))


@bp.post("/npda/open")
@collector_required
def npda_open():
    return _npda_action(
        lambda: npda_svc.open_account(current_user, request.form),
        "Your NPDA account is open! New issues will now be reserved for you automatically.",
    )


@bp.post("/npda/deposit")
@collector_required
def npda_deposit():
    return _npda_action(
        lambda: npda_svc.deposit(current_user, request.form.get("amount")),
        "Deposit added to your NPDA balance.",
    )


@bp.post("/npda/preferences")
@collector_required
def npda_preferences():
    return _npda_action(
        lambda: npda_svc.update_preferences(current_user, request.form),
        "Standing order preferences saved.",
    )


@bp.post("/npda/close")
@collector_required
def npda_close():
    return _npda_action(
        lambda: npda_svc.close_account(current_user), "NPDA account closed and balance refunded."
    )


# ---------- notifications ----------


@bp.route("/notifications")
@login_required
def notifications():
    items = db.session.scalars(
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(100)
    ).all()
    return render_template("notifications.html", items=items)


@bp.post("/notifications/read")
@login_required
def notifications_read():
    db.session.execute(
        update(Notification)
        .where(Notification.user_id == current_user.id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    db.session.commit()
    return redirect(url_for("member.notifications"))


# ---------- settings ----------


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
