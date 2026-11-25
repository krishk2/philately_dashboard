"""Community pages in the site layout: Forums, collector profiles, Crash Course progress."""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import delete, func, or_, select

from .extensions import db
from .models import (
    COURSE_WEEKS,
    FORUM_CATEGORIES,
    CourseProgress,
    ForumReply,
    ForumThread,
    User,
    utcnow,
)
from .services import collectors as collectors_svc
from .services.errors import DomainError, ValidationError
from .services.marketplace import parse_text
from .services.notifications import notify

bp = Blueprint("community", __name__)

THREADS_PER_PAGE = 15


# ---------- forums ----------


@bp.route("/forums")
def forums():
    category = request.args.get("category")
    query = request.args.get("q", "").strip()
    stmt = select(ForumThread)
    if category in FORUM_CATEGORIES:
        stmt = stmt.where(ForumThread.category == category)
    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(or_(ForumThread.title.ilike(pattern), ForumThread.body.ilike(pattern)))
    page = db.paginate(
        stmt.order_by(ForumThread.last_activity.desc(), ForumThread.id.desc()),
        per_page=THREADS_PER_PAGE,
        error_out=False,
    )
    reply_counts = dict(
        db.session.execute(
            select(ForumReply.thread_id, func.count(ForumReply.id))
            .where(ForumReply.thread_id.in_([t.id for t in page.items]))
            .group_by(ForumReply.thread_id)
        ).all()
    )
    category_counts = dict(
        db.session.execute(
            select(ForumThread.category, func.count(ForumThread.id)).group_by(ForumThread.category)
        ).all()
    )
    return render_template(
        "forums.html",
        page=page,
        categories=FORUM_CATEGORIES,
        category=category,
        query=query,
        reply_counts=reply_counts,
        category_counts=category_counts,
    )


@bp.post("/forums/new")
@login_required
def new_thread():
    try:
        category = request.form.get("category")
        if category not in FORUM_CATEGORIES:
            raise ValidationError("Choose a category.")
        thread = ForumThread(
            author=current_user,
            category=category,
            title=parse_text(request.form.get("title"), "Title", max_length=150),
            body=parse_text(request.form.get("body"), "Message", max_length=5000),
        )
        db.session.add(thread)
        db.session.commit()
    except DomainError as exc:
        flash(exc.message, "error")
        return redirect(url_for("community.forums"))
    flash("Discussion started!", "success")
    return redirect(url_for("community.thread", thread_id=thread.id))


@bp.route("/forums/<int:thread_id>")
def thread(thread_id):
    return render_template(
        "forum_thread.html",
        thread=db.get_or_404(ForumThread, thread_id),
        categories=FORUM_CATEGORIES,
    )


@bp.post("/forums/<int:thread_id>/reply")
@login_required
def reply(thread_id):
    thread = db.get_or_404(ForumThread, thread_id)
    try:
        body = parse_text(request.form.get("body"), "Reply", max_length=5000)
    except DomainError as exc:
        flash(exc.message, "error")
        return redirect(url_for("community.thread", thread_id=thread.id))
    db.session.add(ForumReply(thread=thread, author=current_user, body=body))
    thread.last_activity = utcnow()
    if thread.author_id != current_user.id:
        notify(
            thread.author_id,
            f"{current_user.display_name} replied to your discussion '{thread.title}'.",
            url_for("community.thread", thread_id=thread.id),
        )
    db.session.commit()
    return redirect(url_for("community.thread", thread_id=thread.id) + "#latest")


@bp.post("/forums/<int:thread_id>/delete")
@login_required
def delete_thread(thread_id):
    thread = db.get_or_404(ForumThread, thread_id)
    if thread.author_id != current_user.id:
        abort(403)
    db.session.delete(thread)
    db.session.commit()
    flash("Discussion deleted.", "success")
    return redirect(url_for("community.forums"))


@bp.post("/forums/replies/<int:reply_id>/delete")
@login_required
def delete_reply(reply_id):
    item = db.get_or_404(ForumReply, reply_id)
    if item.author_id != current_user.id:
        abort(403)
    thread_id = item.thread_id
    db.session.delete(item)
    db.session.commit()
    flash("Reply deleted.", "success")
    return redirect(url_for("community.thread", thread_id=thread_id))


# ---------- crash course ----------


def completed_weeks(user) -> set[str]:
    if not user.is_authenticated:
        return set()
    return set(
        db.session.scalars(select(CourseProgress.week).where(CourseProgress.user_id == user.id))
    )


@bp.route("/philately_crash_course")
def crash_course():
    return render_template("philately_crash_course.html", done=completed_weeks(current_user))


@bp.post("/philately_crash_course/<week>")
@login_required
def toggle_week(week):
    if week not in COURSE_WEEKS:
        abort(404)
    removed = db.session.execute(
        delete(CourseProgress).where(
            CourseProgress.user_id == current_user.id, CourseProgress.week == week
        )
    ).rowcount
    if not removed:
        db.session.add(CourseProgress(user_id=current_user.id, week=week))
    db.session.commit()
    return redirect(url_for("community.crash_course") + f"#{week}")


# ---------- collector profiles ----------


@bp.route("/collectors")
def collectors():
    query = request.args.get("q", "").strip()
    return render_template(
        "collectors.html",
        rows=collectors_svc.directory(query),
        query=query,
        following=collectors_svc.following_ids(current_user),
    )


@bp.route("/collectors/<username>")
def collector(username):
    user = db.session.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if user is None:
        abort(404)
    own = current_user.is_authenticated and current_user.id == user.id
    collection = collectors_svc.collection_for(user) if (user.show_collection or own) else None
    badges = collectors_svc.badges_for(user, collection or [])
    followers, following = collectors_svc.follow_counts(user)
    return render_template(
        "collector.html",
        user=user,
        own=own,
        collection=collection,
        badges=badges,
        for_sale=collectors_svc.for_sale(user),
        threads=collectors_svc.recent_threads(user),
        followers=followers,
        following=following,
        is_following=collectors_svc.is_following(current_user, user),
    )


@bp.post("/collectors/<username>/follow")
@login_required
def follow(username):
    user = db.session.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if user is None:
        abort(404)
    now = collectors_svc.toggle_follow(current_user, user)
    flash(
        f"You're now following {user.display_name}." if now else f"Unfollowed {user.display_name}.",
        "success",
    )
    return redirect(request.referrer or url_for("community.collector", username=user.username))


@bp.post("/collectors/me/visibility")
@login_required
def collection_visibility():
    current_user.show_collection = request.form.get("show_collection") == "1"
    bio = (request.form.get("bio") or "").strip()
    if len(bio) > 300:
        flash("Bio must be at most 300 characters.", "error")
        return redirect(url_for("community.collector", username=current_user.username))
    current_user.bio = bio or None
    db.session.commit()
    flash("Public profile updated.", "success")
    return redirect(url_for("community.collector", username=current_user.username))


@bp.app_template_global()
def top_badges(user, limit: int = 3):
    """Earned badges for showing next to a name (e.g. in forum posts)."""
    return collectors_svc.earned_badges(user)[-limit:]
