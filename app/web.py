"""Server-rendered pages for collectors: browse, sell, buy, wishlist, issues and events."""

from datetime import date
from decimal import Decimal

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import select

from .extensions import db
from .models import LISTING_ACTIVE, LISTING_REMOVED, Event, Issue, Listing, Order, WishlistItem
from .services import marketplace as svc
from .services.errors import DomainError

bp = Blueprint("main", __name__)


def _flash_error(exc: DomainError) -> None:
    flash(exc.message, "error")


def _optional(value, parser):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return parser(value)
    except (ValueError, ArithmeticError):
        return None


@bp.route("/")
def index():
    latest = db.session.scalars(svc.search_listings().limit(6)).all()
    issues = db.session.scalars(select(Issue).order_by(Issue.issue_date.desc()).limit(3)).all()
    events = db.session.scalars(
        select(Event).where(Event.date >= date.today()).order_by(Event.date).limit(3)
    ).all()
    return render_template("index.html", latest=latest, issues=issues, events=events)


@bp.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename, max_age=86400)


# ---------- listings ----------


@bp.route("/listings")
def listings():
    filters = {
        "query": request.args.get("q", "").strip(),
        "year": _optional(request.args.get("year"), int),
        "min_price": _optional(request.args.get("min_price"), Decimal),
        "max_price": _optional(request.args.get("max_price"), Decimal),
        "verified_only": request.args.get("verified") == "1",
    }
    page = db.paginate(svc.search_listings(**filters), per_page=12, error_out=False)
    return render_template("listings/list.html", page=page, args=request.args)


@bp.route("/listings/<int:listing_id>")
def listing_detail(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    if listing.status == LISTING_REMOVED and (
        not current_user.is_authenticated or current_user.id != listing.seller_id
    ):
        abort(404)
    in_wishlist = current_user.is_authenticated and db.session.scalar(
        select(WishlistItem.id).where(
            WishlistItem.user_id == current_user.id, WishlistItem.listing_id == listing.id
        )
    )
    return render_template("listings/detail.html", listing=listing, in_wishlist=in_wishlist)


def _issue_choices():
    return db.session.scalars(select(Issue).order_by(Issue.issue_date.desc())).all()


@bp.route("/listings/new", methods=["GET", "POST"])
@login_required
def listing_new():
    if current_user.is_circle:
        flash("Postal circle accounts publish official issues instead of listings.", "info")
        return redirect(url_for("circle.dashboard"))
    if request.method == "POST":
        try:
            listing = svc.create_listing(current_user, request.form, request.files.get("image"))
        except DomainError as exc:
            _flash_error(exc)
            return render_template(
                "listings/form.html", form=request.form, issues=_issue_choices(), listing=None
            ), exc.status_code
        flash("Listing published and verification run.", "success")
        return redirect(url_for("main.listing_detail", listing_id=listing.id))
    return render_template("listings/form.html", form={}, issues=_issue_choices(), listing=None)


@bp.route("/listings/<int:listing_id>/edit", methods=["GET", "POST"])
@login_required
def listing_edit(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    if listing.seller_id != current_user.id:
        abort(403)
    if request.method == "POST":
        try:
            svc.update_listing(listing, current_user, request.form)
        except DomainError as exc:
            _flash_error(exc)
            return render_template(
                "listings/form.html", form=request.form, issues=_issue_choices(), listing=listing
            ), exc.status_code
        flash("Listing updated and re-verified.", "success")
        return redirect(url_for("main.listing_detail", listing_id=listing.id))
    form = {
        "title": listing.title,
        "year": listing.year,
        "price": listing.price,
        "quantity": listing.quantity,
        "description": listing.description,
        "issue_id": listing.issue_id or "",
    }
    return render_template(
        "listings/form.html", form=form, issues=_issue_choices(), listing=listing
    )


@bp.post("/listings/<int:listing_id>/delete")
@login_required
def listing_delete(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    try:
        svc.remove_listing(listing, current_user)
    except DomainError as exc:
        _flash_error(exc)
        return redirect(url_for("main.listing_detail", listing_id=listing_id))
    flash("Listing removed.", "info")
    return redirect(url_for("main.my_listings"))


@bp.post("/listings/<int:listing_id>/verify")
@login_required
def listing_verify(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    try:
        svc.reverify_listing(listing, current_user)
        flash("Verification re-run.", "success")
    except DomainError as exc:
        _flash_error(exc)
    return redirect(url_for("main.listing_detail", listing_id=listing_id))


@bp.route("/me/listings")
@login_required
def my_listings():
    rows = db.session.scalars(
        select(Listing)
        .where(Listing.seller_id == current_user.id, Listing.status != LISTING_REMOVED)
        .order_by(Listing.created_at.desc())
    ).all()
    return render_template("listings/mine.html", listings=rows)


# ---------- orders ----------


@bp.post("/listings/<int:listing_id>/buy")
@login_required
def buy(listing_id):
    try:
        order = svc.place_order(
            current_user,
            listing_id,
            request.form.get("quantity", 1),
            request.form.get("shipping_address"),
        )
    except DomainError as exc:
        _flash_error(exc)
        return redirect(url_for("main.listing_detail", listing_id=listing_id))
    flash(f"Order #{order.id} placed. Total: ₹{order.total}.", "success")
    return redirect(url_for("main.orders"))


@bp.route("/me/orders")
@login_required
def orders():
    bought = db.session.scalars(
        select(Order).where(Order.buyer_id == current_user.id).order_by(Order.created_at.desc())
    ).all()
    sold = db.session.scalars(
        select(Order)
        .join(Listing)
        .where(Listing.seller_id == current_user.id)
        .order_by(Order.created_at.desc())
    ).all()
    return render_template("orders.html", bought=bought, sold=sold)


# ---------- wishlist ----------


@bp.route("/me/wishlist")
@login_required
def wishlist():
    items = db.session.scalars(
        select(WishlistItem)
        .where(WishlistItem.user_id == current_user.id)
        .order_by(WishlistItem.created_at.desc())
    ).all()
    return render_template("wishlist.html", items=items)


@bp.post("/listings/<int:listing_id>/wishlist")
@login_required
def wishlist_toggle(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    item = db.session.scalar(
        select(WishlistItem).where(
            WishlistItem.user_id == current_user.id, WishlistItem.listing_id == listing.id
        )
    )
    if item:
        db.session.delete(item)
        flash("Removed from wishlist.", "info")
    elif listing.status == LISTING_ACTIVE:
        db.session.add(WishlistItem(user_id=current_user.id, listing_id=listing.id))
        flash("Added to wishlist.", "success")
    db.session.commit()
    return redirect(request.referrer or url_for("main.listing_detail", listing_id=listing.id))


# ---------- official issues and events ----------


@bp.route("/issues")
def issues():
    rows = db.session.scalars(select(Issue).order_by(Issue.issue_date.desc())).all()
    return render_template("issues.html", issues=rows)


@bp.post("/issues/<int:issue_id>/prebook")
@login_required
def prebook(issue_id):
    try:
        booking = svc.prebook_issue(current_user, issue_id, request.form.get("quantity", 1))
        flash(f"Pre-booked {booking.quantity} of '{booking.issue.title}'.", "success")
    except DomainError as exc:
        _flash_error(exc)
    return redirect(url_for("main.issues"))


@bp.route("/events")
def events():
    upcoming = db.session.scalars(
        select(Event).where(Event.date >= date.today()).order_by(Event.date)
    ).all()
    past = db.session.scalars(
        select(Event).where(Event.date < date.today()).order_by(Event.date.desc()).limit(10)
    ).all()
    return render_template("events.html", upcoming=upcoming, past=past)
