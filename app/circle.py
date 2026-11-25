"""Dashboard for postal circle accounts: publish official issues and run events."""

from datetime import date
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select

from .extensions import db
from .models import Event, Issue, Prebooking
from .services import marketplace as svc
from .services.errors import DomainError, ValidationError
from .services.marketplace import parse_text

bp = Blueprint("circle", __name__, url_prefix="/circle")


def circle_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_circle:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


@bp.route("/")
@circle_required
def dashboard():
    issues = db.session.execute(
        select(Issue, func.coalesce(func.sum(Prebooking.quantity), 0))
        .outerjoin(Prebooking)
        .where(Issue.circle_id == current_user.id)
        .group_by(Issue.id)
        .order_by(Issue.issue_date.desc())
    ).all()
    events = db.session.scalars(
        select(Event).where(Event.circle_id == current_user.id).order_by(Event.date.desc())
    ).all()
    return render_template(
        "circle/dashboard.html", issues=issues, events=events, today=date.today()
    )


@bp.post("/issues")
@circle_required
def issue_create():
    try:
        svc.create_issue(current_user, request.form, request.files.get("image"))
        flash("Issue published.", "success")
    except DomainError as exc:
        flash(exc.message, "error")
    return redirect(url_for("circle.dashboard"))


@bp.post("/issues/<int:issue_id>/delete")
@circle_required
def issue_delete(issue_id):
    issue = db.get_or_404(Issue, issue_id)
    try:
        svc.delete_issue(issue, current_user)
        flash("Issue deleted.", "info")
    except DomainError as exc:
        flash(exc.message, "error")
    return redirect(url_for("circle.dashboard"))


def _event_fields(form) -> dict:
    try:
        event_date = date.fromisoformat(form.get("date", "").strip())
    except ValueError as exc:
        raise ValidationError("Event date must be a date (YYYY-MM-DD).") from exc
    return {
        "name": parse_text(form.get("name"), "Event name", max_length=150),
        "date": event_date,
        "location": parse_text(form.get("location"), "Location", max_length=200),
        "description": parse_text(
            form.get("description"), "Description", max_length=2000, required=False
        ),
    }


@bp.post("/events")
@circle_required
def event_create():
    try:
        db.session.add(Event(circle=current_user, **_event_fields(request.form)))
        db.session.commit()
        flash("Event added.", "success")
    except DomainError as exc:
        flash(exc.message, "error")
    return redirect(url_for("circle.dashboard"))


def _own_event(event_id: int) -> Event:
    event = db.get_or_404(Event, event_id)
    if event.circle_id != current_user.id:
        abort(403)
    return event


@bp.post("/events/<int:event_id>")
@circle_required
def event_update(event_id):
    event = _own_event(event_id)
    try:
        for key, value in _event_fields(request.form).items():
            setattr(event, key, value)
        db.session.commit()
        flash("Event updated.", "success")
    except DomainError as exc:
        db.session.rollback()
        flash(exc.message, "error")
    return redirect(url_for("circle.dashboard"))


@bp.post("/events/<int:event_id>/delete")
@circle_required
def event_delete(event_id):
    db.session.delete(_own_event(event_id))
    db.session.commit()
    flash("Event deleted.", "info")
    return redirect(url_for("circle.dashboard"))
