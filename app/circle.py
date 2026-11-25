"""Postal circle pages (dashboard-2 sidebar): dashboard, manage stamps, events, settings.

"Manage Stamps" manages the circle's official issues, the stamps collectors pre-book and that
seller listings are verified against.
"""

from datetime import date
from functools import wraps

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select

from .extensions import db
from .models import (
    PREBOOK_PAID,
    PREBOOK_PENDING,
    UPDATE_KINDS,
    CircleUpdate,
    Event,
    Issue,
    Prebooking,
)
from .services import marketplace as svc
from .services.accounts import change_email, change_password
from .services.errors import DomainError, ValidationError
from .services.marketplace import parse_text

bp = Blueprint("circle", __name__)


def circle_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_circle:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def _own_issues():
    return db.session.scalars(
        select(Issue).where(Issue.circle_id == current_user.id).order_by(Issue.issue_date.desc())
    ).all()


def _own(model, object_id):
    obj = db.get_or_404(model, object_id)
    if obj.circle_id != current_user.id:
        abort(403)
    return obj


# ---------- dashboard: post updates, update stock ----------


@bp.route("/postal-circle-dashboard")
@circle_required
def dashboard():
    updates = db.session.scalars(
        select(CircleUpdate)
        .where(CircleUpdate.circle_id == current_user.id)
        .order_by(CircleUpdate.created_at.desc())
        .limit(5)
    ).all()
    return render_template(
        "dashboard-2.html", kinds=UPDATE_KINDS, issues=_own_issues(), updates=updates
    )


@bp.post("/post_update")
@circle_required
def post_update():
    try:
        kind = request.form.get("updateType", "")
        if kind not in UPDATE_KINDS:
            raise ValidationError("Choose an update type.")
        db.session.add(
            CircleUpdate(
                circle=current_user,
                kind=kind,
                title=parse_text(request.form.get("updateTitle"), "Title", max_length=150),
                content=parse_text(request.form.get("updateContent"), "Content", max_length=4000),
            )
        )
        db.session.commit()
        flash("Update posted successfully! It now appears in the news on the home page.", "success")
    except DomainError as exc:
        flash(exc.message, "error")
    return redirect(url_for("circle.dashboard"))


@bp.post("/update_stock")
@circle_required
def update_stock():
    try:
        issue_id = svc.parse_int(request.form.get("item"), "Item", minimum=1)
        issue = _own(Issue, issue_id)
        svc.set_issue_stock(issue, current_user, request.form.get("quantity"))
        flash(f"Stock updated: {issue.prebook_available} of '{issue.title}' available.", "success")
    except DomainError as exc:
        flash(exc.message, "error")
    return redirect(url_for("circle.dashboard"))


# ---------- manage stamps (official issues) ----------


@bp.route("/manage_stamps")
@circle_required
def manage_stamps():
    booked = dict(
        db.session.execute(
            select(Prebooking.issue_id, func.sum(Prebooking.quantity))
            .join(Issue)
            .where(
                Issue.circle_id == current_user.id,
                Prebooking.status.in_((PREBOOK_PAID, PREBOOK_PENDING)),
            )
            .group_by(Prebooking.issue_id)
        ).all()
    )
    return render_template("stamps.html", issues=_own_issues(), booked=booked)


@bp.post("/manage_stamps/add")
@circle_required
def add_stamp():
    data = {
        "title": request.form.get("stampName"),
        "issue_date": request.form.get("stampDate"),
        "denomination": request.form.get("stampPrice"),
        "description": request.form.get("stampDescription", ""),
        "prebook_available": request.form.get("stampStock") or 0,
    }
    try:
        issue = svc.create_issue(current_user, data, request.files.get("stampImage"))
        r = issue.npda_result
        message = "Stamp added successfully!"
        if r is None:
            message += " NPDA subscribers are being booked in the background."
        elif any(r.values()):
            message += (
                f" NPDA standing orders: {r['booked']} subscriber(s) booked automatically, "
                f"{r['low_balance']} skipped for low balance, {r['sold_out']} missed (sold out)."
            )
        flash(message, "success")
    except DomainError as exc:
        flash(exc.message, "error")
    return redirect(url_for("circle.manage_stamps"))


@bp.post("/manage_stamps/update")
@circle_required
def update_stamp():
    try:
        issue = _own(Issue, svc.parse_int(request.form.get("existingStamps"), "Stamp", minimum=1))
        svc.update_issue(
            issue,
            current_user,
            {
                "title": request.form.get("updateStampName"),
                "issue_date": request.form.get("updateStampDate"),
                "denomination": request.form.get("updateStampPrice"),
            },
        )
        flash("Stamp updated successfully!", "success")
    except DomainError as exc:
        db.session.rollback()
        flash(exc.message, "error")
    return redirect(url_for("circle.manage_stamps"))


@bp.post("/manage_stamps/delete")
@circle_required
def delete_stamp():
    try:
        issue = _own(Issue, svc.parse_int(request.form.get("deleteStampId"), "Stamp", minimum=1))
        svc.delete_issue(issue, current_user)
        flash("Stamp deleted.", "success")
    except DomainError as exc:
        flash(exc.message, "error")
    return redirect(url_for("circle.manage_stamps"))


# ---------- events ----------


def _parse_event_date(value) -> date:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError as exc:
        raise ValidationError("Event date must be a date (YYYY-MM-DD).") from exc


@bp.route("/events")
@circle_required
def events():
    own = db.session.scalars(
        select(Event).where(Event.circle_id == current_user.id).order_by(Event.date.desc())
    ).all()
    return render_template("events.html", events=own, today=date.today())


@bp.get("/get-events")
@circle_required
def get_events():
    """JSON used by script_for_events.js to prefill the update form."""
    own = db.session.scalars(
        select(Event).where(Event.circle_id == current_user.id).order_by(Event.date.desc())
    ).all()
    return jsonify(
        [
            {
                "id": e.id,
                "event_name": e.name,
                "date": e.date.isoformat(),
                "location": e.location,
                "description": e.description,
            }
            for e in own
        ]
    )


@bp.post("/add_event")
@circle_required
def add_event():
    form = request.form
    try:
        db.session.add(
            Event(
                circle=current_user,
                name=parse_text(form.get("eventName"), "Event name", max_length=150),
                date=_parse_event_date(form.get("eventDate")),
                location=parse_text(form.get("eventLocation"), "Location", max_length=200),
                description=parse_text(
                    form.get("eventDescription"), "Description", max_length=2000, required=False
                ),
            )
        )
        db.session.commit()
        flash("Event added successfully!", "success")
    except DomainError as exc:
        flash(exc.message, "error")
    return redirect(url_for("circle.events"))


@bp.post("/update_event")
@circle_required
def update_event():
    """Blank fields keep their current value, as the form's placeholders suggest."""
    form = request.form
    try:
        event = _own(Event, svc.parse_int(form.get("existingEvents"), "Event", minimum=1))
        if (form.get("updateEventName") or "").strip():
            event.name = parse_text(form.get("updateEventName"), "Event name", max_length=150)
        if (form.get("updateEventDate") or "").strip():
            event.date = _parse_event_date(form.get("updateEventDate"))
        if (form.get("updateEventLocation") or "").strip():
            event.location = parse_text(form.get("updateEventLocation"), "Location", max_length=200)
        if (form.get("updateEventDescription") or "").strip():
            event.description = parse_text(
                form.get("updateEventDescription"), "Description", max_length=2000
            )
        db.session.commit()
        flash("Event updated successfully!", "success")
    except DomainError as exc:
        db.session.rollback()
        flash(exc.message, "error")
    return redirect(url_for("circle.events"))


@bp.post("/delete_event")
@circle_required
def delete_event():
    try:
        event = _own(Event, svc.parse_int(request.form.get("eventId"), "Event", minimum=1))
        db.session.delete(event)
        db.session.commit()
        flash("Event deleted successfully.", "success")
    except DomainError as exc:
        flash(exc.message, "error")
    return redirect(url_for("circle.events"))


# ---------- settings ----------


@bp.route("/settings")
@circle_required
def settings():
    return render_template("settings.html")


@bp.post("/update_profile")
@circle_required
def update_profile():
    try:
        change_email(current_user, request.form.get("email", ""))
        db.session.commit()
        flash("Profile updated successfully!", "success")
    except ValidationError as exc:
        db.session.rollback()
        flash(exc.message, "error")
    return redirect(url_for("circle.settings"))


@bp.post("/update_password")
@circle_required
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
    return redirect(url_for("circle.settings"))
