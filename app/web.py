"""Public pages and the marketplace (Stamp Mela), served with the original templates.

URLs and form field names follow the original app, so the templates keep their markup. The
handlers translate those field names into the service layer's arguments.
"""

from datetime import date
from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import func, select

from .extensions import csrf, db, limiter
from .models import (
    FEED_EVENT,
    FEED_NEWS,
    FEED_RELEASE,
    LISTING_ACTIVE,
    LISTING_REMOVED,
    CircleUpdate,
    Event,
    Issue,
    Listing,
    Order,
    Prebooking,
    User,
    WishlistItem,
)
from .services import assistant, feeds
from .services import marketplace as svc
from .services.errors import DomainError
from .services.storage import get_storage

bp = Blueprint("main", __name__)


def collector_required(view):
    """Logged-in philatelist accounts only. Postal circles are sent to their dashboard."""

    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.is_circle:
            flash("Postal circle accounts can't buy or sell stamps.", "error")
            return redirect(url_for("circle.dashboard"))
        return view(*args, **kwargs)

    return wrapper


def _wishlist_ids() -> set[int]:
    if not current_user.is_authenticated:
        return set()
    return set(
        db.session.scalars(
            select(WishlistItem.listing_id).where(WishlistItem.user_id == current_user.id)
        )
    )


def _visible_listing(listing_id: int) -> Listing:
    listing = db.get_or_404(Listing, listing_id)
    if listing.status == LISTING_REMOVED and (
        not current_user.is_authenticated or current_user.id != listing.seller_id
    ):
        abort(404)
    return listing


# ---------- community pages ----------


# Shown in the carousel only when there's no live data yet (the original v1 showcase).
SHOWCASE = [
    (
        "wild.jpg",
        "Commemorative Stamp on Wildlife Conservation",
        "Celebrating wildlife preservation.",
    ),
    ("fest.jpg", "First Day Cover: Indian Festivals", "India's festivals on a First Day Cover."),
    (
        "cancellation.jpg",
        "Special Cancellation: Freedom Fighters",
        "Honouring India's freedom fighters.",
    ),
    ("monuments.jpg", "Historic Monuments", "The Taj Mahal, Qutub Minar and more."),
    ("bird.jpg", "Birds of India", "India's diverse bird species."),
    ("yuri.jpg", "Space Exploration", "India's achievements in space."),
]


def _fmt_value(value) -> str:
    if value is None:
        return ""
    return f"₹{int(value)}" if value == int(value) else f"₹{value}"


def carousel_items(limit: int = 12) -> list[dict]:
    """Latest releases for the home carousel: circles' own issues plus India Post's releases.

    India Post's stamp images are used only when FEED_SHOW_SOURCE_IMAGES is enabled (it needs
    Department of Posts permission); otherwise those releases are drawn as stamp cards.
    """
    show_source_images = current_app.config.get("FEED_SHOW_SOURCE_IMAGES")
    items = []
    for issue in db.session.scalars(select(Issue).order_by(Issue.issue_date.desc()).limit(6)):
        items.append(
            {
                "kicker": f"{issue.circle.postal_circle} Circle",
                "title": issue.title,
                "desc": issue.description or "Official issue from the postal circle.",
                "value": _fmt_value(issue.denomination),
                "date": issue.issue_date,
                "image": url_for("main.uploaded_file", filename=issue.reference_image),
                "link": url_for("main.pre_book", issue_id=issue.id),
                "cta": "Pre-book" if issue.prebook_available else "View",
                "external": False,
            }
        )
    for item in feeds.latest(FEED_RELEASE, limit):
        count = item.stamp_count or 1
        items.append(
            {
                "kicker": "India Post release",
                "title": item.title,
                "desc": (f"Set of {count} stamps" if count > 1 else "Commemorative postage stamp")
                + (
                    f" · released {item.published_on.strftime('%d %b %Y')}"
                    if item.published_on
                    else ""
                ),
                "value": _fmt_value(item.denomination),
                "date": item.published_on,
                "image": item.image_url if (show_source_images and item.image_url) else None,
                "link": item.url,
                "cta": "View on India Post",
                "external": True,
            }
        )
    items.sort(key=lambda i: i["date"] or date.min, reverse=True)
    if not items:
        items = [
            {
                "kicker": "Showcase",
                "title": title,
                "desc": desc,
                "value": "",
                "date": None,
                "image": url_for("static", filename=image),
                "link": url_for("main.buyer_home"),
                "cta": "Browse the Mela",
                "external": False,
            }
            for image, title, desc in SHOWCASE
        ]
    return items[:limit]


@bp.route("/home")
def home():
    today = date.today()
    events = db.session.scalars(
        select(Event).where(Event.date >= today).order_by(Event.date).limit(10)
    ).all()
    releases = db.session.scalars(select(Issue).order_by(Issue.issue_date.desc()).limit(5)).all()
    news = db.session.scalars(
        select(CircleUpdate).order_by(CircleUpdate.created_at.desc()).limit(5)
    ).all()
    counts = {
        "listings": db.session.scalar(
            select(func.count(Listing.id)).where(Listing.status == LISTING_ACTIVE)
        ),
        "collectors": db.session.scalar(
            select(func.count(User.id)).where(User.role == "collector")
        ),
        "issues": db.session.scalar(select(func.count(Issue.id))),
    }
    # Headlines imported by `flask import-feeds` (India Post, Rainbow Stamp News, extra RSS).
    return render_template(
        "landing.html",
        carousel=carousel_items(),
        latest_listings=db.session.scalars(svc.search_listings().limit(4)).all(),
        counts=counts,
        events=events,
        releases=releases,
        news=news,
        feed_releases=feeds.latest(FEED_RELEASE, 8),
        feed_news=feeds.latest(FEED_NEWS, 8),
        feed_events=feeds.latest(FEED_EVENT, 8),
        feeds_updated=feeds.last_import(),
    )


@bp.route("/about")
def about():
    return render_template("about.html")


@bp.route("/stamp_museum")
def stamp_museum():
    # The museum's archive wing: every official issue the circles have published, oldest first.
    archive = db.session.scalars(select(Issue).order_by(Issue.issue_date, Issue.id)).all()
    return render_template("stamp_museum.html", archive=archive)


@bp.post("/ask")
@limiter.limit("10 per minute;100 per day")
@csrf.exempt  # read-only JSON endpoint: it changes no state, so there is nothing to forge
def ask():
    body = request.get_json(silent=True) or {}
    return jsonify({"response": assistant.reply(str(body.get("message", "")))})


@bp.route("/healthz")
def healthz():
    """Load balancer / container health check: is the app up and can it reach the database?"""
    from sqlalchemy import text

    from .services.jobs import queue_depth

    try:
        db.session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Health check: database unreachable")
        return jsonify({"status": "error", "database": "unreachable"}), 503
    last = feeds.last_import()
    return jsonify(
        {
            "status": "ok",
            "database": "ok",
            "queued_jobs": queue_depth(),
            "feeds_last_import": last.isoformat() if last else None,
        }
    )


@bp.route("/uploads/<path:filename>")
def uploaded_file(filename):
    storage = get_storage()
    if storage.is_remote:  # S3 / R2 / B2: send the browser to the bucket (or CDN)
        return redirect(storage.url(filename), code=302)
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename, max_age=86400)


# ---------- stamp store: pre-booking official issues ----------


@bp.route("/stamp_store")
def stamp_store():
    svc.expire_stale_prebookings()
    issues = db.session.scalars(select(Issue).order_by(Issue.issue_date.desc())).all()
    npda = current_user.npda if current_user.is_authenticated else None
    return render_template("stamp_store.html", issues=issues, npda=npda)


@bp.route("/pre_book")
@bp.route("/pre_book/<int:issue_id>")
@collector_required
def pre_book(issue_id=None):
    svc.expire_stale_prebookings()
    issues = db.session.scalars(
        select(Issue).where(Issue.prebook_available > 0).order_by(Issue.issue_date.desc())
    ).all()
    return render_template("pre_book.html", issues=issues, selected=issue_id)


@bp.post("/submit_order")
@collector_required
def submit_order():
    try:
        issue_id = svc.parse_int(request.form.get("stampType"), "Stamp type", minimum=1)
        booking = svc.prebook_issue(current_user, issue_id, request.form.get("quantity"))
    except DomainError as exc:
        flash(exc.message, "error")
        return redirect(url_for("main.pre_book"))
    return redirect(url_for("main.payment", booking_id=booking.id))


def _own_booking(booking_id: int) -> Prebooking:
    booking = db.get_or_404(Prebooking, booking_id)
    if booking.user_id != current_user.id:
        abort(404)
    return booking


@bp.route("/payment/<int:booking_id>", methods=["GET", "POST"])
@collector_required
@limiter.limit("10 per minute", methods=["POST"])
def payment(booking_id):
    booking = _own_booking(booking_id)
    svc.expire_stale_prebookings()
    db.session.refresh(booking)
    if request.method == "POST":
        card = {
            "number": request.form.get("cardNumber"),
            "expiry": request.form.get("expiryDate"),
            "cvv": request.form.get("cvv"),
        }
        try:
            svc.pay_prebooking(current_user, booking, card)
        except DomainError as exc:
            flash(exc.message, "error")
            return render_template("payment.html", booking=booking), exc.status_code
        flash(
            f"Payment successful! {booking.quantity} × '{booking.issue.title}' is confirmed "
            f"(ref {booking.payment_ref}).",
            "success",
        )
        return redirect(url_for("member.my_collection"))
    return render_template("payment.html", booking=booking)


@bp.post("/payment/<int:booking_id>/cancel")
@collector_required
def cancel_booking(booking_id):
    booking = _own_booking(booking_id)
    try:
        svc.cancel_prebooking(current_user, booking)
        flash("Pre-booking cancelled and the copies released.", "success")
    except DomainError as exc:
        flash(exc.message, "error")
    return redirect(url_for("main.stamp_store"))


# ---------- Stamp Mela marketplace ----------


@bp.route("/stamp_mela")
def stamp_mela():
    return redirect(url_for("main.buyer_home"))


@bp.post("/submit")
def submit():
    if request.form.get("user_type") == "seller":
        return redirect(url_for("main.seller_home"))
    return redirect(url_for("main.buyer_home"))


@bp.route("/buyer_home")
def buyer_home():
    sort = request.args.get("sort")
    verified = request.args.get("verified") == "1"
    stamps = db.session.scalars(
        svc.search_listings(sort=sort, verified_only=verified).limit(60)
    ).all()
    recent = db.session.scalars(svc.search_listings().limit(5)).all()
    return render_template(
        "buyer_home.html",
        stamps=stamps,
        recent=recent,
        wishlist_ids=_wishlist_ids(),
        sort=sort,
        verified=verified,
    )


@bp.route("/search")
def search():
    query = request.args.get("search", "").strip()
    if not query:
        return redirect(url_for("main.buyer_home"))
    stamps = db.session.scalars(svc.search_listings(query=query).limit(30)).all()
    message = None if stamps else "No stamps found matching your search."
    return render_template(
        "search.html", stamps=stamps, query=query, message=message, wishlist_ids=_wishlist_ids()
    )


@bp.route("/stamp/<int:listing_id>")
def stamp_detail(listing_id):
    listing = _visible_listing(listing_id)
    return render_template("stamp_detail.html", stamp=listing, wishlist_ids=_wishlist_ids())


@bp.route("/purchase/<int:stamp_id>", methods=["GET", "POST"])
@collector_required
def purchase(stamp_id):
    stamp = _visible_listing(stamp_id)
    if request.method == "POST":
        contact = {
            "name": request.form.get("name"),
            "email": request.form.get("email"),
            "phone": request.form.get("phone"),
        }
        try:
            order = svc.place_order(
                current_user,
                stamp.id,
                request.form.get("quantity", 1),
                request.form.get("address"),
                contact,
            )
        except DomainError as exc:
            flash(exc.message, "error")
            return render_template("purchase.html", stamp=stamp, form=request.form), (
                exc.status_code
            )
        flash(f"Purchase completed! Order #{order.id}, total ₹{order.total}.", "success")
        return redirect(url_for("main.orders"))
    # Prefill from the saved profile so checkout is one click for returning buyers.
    form = {
        "name": current_user.display_name,
        "email": current_user.email,
        "phone": current_user.phone or "",
        "address": current_user.address or "",
    }
    return render_template("purchase.html", stamp=stamp, form=form)


@bp.route("/orders")
@collector_required
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


@bp.route("/wishlist")
@collector_required
def wishlist():
    items = db.session.scalars(
        select(WishlistItem)
        .where(WishlistItem.user_id == current_user.id)
        .order_by(WishlistItem.created_at.desc())
    ).all()
    return render_template("wishlist.html", wishlist=[item.listing for item in items])


@bp.post("/wishlist/<int:listing_id>")
@collector_required
def wishlist_toggle(listing_id):
    """Called by the ❤️ button (fetch, JSON response) or a plain form post (redirect)."""
    listing = db.get_or_404(Listing, listing_id)
    item = db.session.scalar(
        select(WishlistItem).where(
            WishlistItem.user_id == current_user.id, WishlistItem.listing_id == listing.id
        )
    )
    if item:
        db.session.delete(item)
        liked = False
    elif listing.status == LISTING_ACTIVE:
        db.session.add(WishlistItem(user_id=current_user.id, listing_id=listing.id))
        liked = True
    else:
        liked = False
    db.session.commit()
    if request.accept_mimetypes.best == "application/json":
        return jsonify({"liked": liked})
    flash("Added to wishlist." if liked else "Removed from wishlist.", "success")
    return redirect(request.referrer or url_for("main.stamp_detail", listing_id=listing.id))


# ---------- seller pages ----------


def _listing_form_data(form) -> dict:
    """Map the original seller.html field names onto the service's names."""
    mapping = {
        "stamp_name": "title",
        "year_of_manufacture": "year",
        "coated_price": "price",
        "description": "description",
        "quantity": "quantity",
        "issue_id": "issue_id",
    }
    return {new: form[old] for old, new in mapping.items() if old in form}


def _issue_choices():
    return db.session.scalars(select(Issue).order_by(Issue.issue_date.desc())).all()


@bp.route("/seller_home")
@collector_required
def seller_home():
    listings = db.session.scalars(
        select(Listing)
        .where(Listing.seller_id == current_user.id, Listing.status != LISTING_REMOVED)
        .order_by(Listing.created_at.desc())
    ).all()
    sold_count = db.session.scalar(
        select(func.coalesce(func.sum(Order.quantity), 0))
        .join(Listing)
        .where(Listing.seller_id == current_user.id)
    )
    return render_template("seller_home.html", listings=listings, sold_count=sold_count)


@bp.route("/sell_stamp")
@collector_required
def sell_stamp():
    return render_template("seller.html", form={}, issues=_issue_choices(), stamp=None)


@bp.post("/submit_stamp")
@collector_required
def submit_stamp():
    try:
        listing = svc.create_listing(
            current_user, _listing_form_data(request.form), request.files.get("stamp_photo")
        )
    except DomainError as exc:
        flash(exc.message, "error")
        return render_template(
            "seller.html", form=request.form, issues=_issue_choices(), stamp=None
        ), exc.status_code
    flash("Stamp uploaded successfully! Verification checks have run.", "success")
    return redirect(url_for("main.stamp_detail", listing_id=listing.id))


@bp.route("/stamp/<int:listing_id>/edit", methods=["GET", "POST"])
@collector_required
def edit_stamp(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    if listing.seller_id != current_user.id:
        abort(403)
    if request.method == "POST":
        try:
            svc.update_listing(listing, current_user, _listing_form_data(request.form))
        except DomainError as exc:
            flash(exc.message, "error")
            return render_template(
                "seller.html", form=request.form, issues=_issue_choices(), stamp=listing
            ), exc.status_code
        flash("Stamp updated and re-verified.", "success")
        return redirect(url_for("main.stamp_detail", listing_id=listing.id))
    form = {
        "stamp_name": listing.title,
        "year_of_manufacture": listing.year,
        "coated_price": listing.price,
        "quantity": listing.quantity,
        "description": listing.description,
        "issue_id": listing.issue_id or "",
    }
    return render_template("seller.html", form=form, issues=_issue_choices(), stamp=listing)


@bp.post("/stamp/<int:listing_id>/delete")
@collector_required
def delete_stamp(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    try:
        svc.remove_listing(listing, current_user)
        flash("Stamp removed from sale.", "success")
    except DomainError as exc:
        flash(exc.message, "error")
    return redirect(url_for("main.seller_home"))


@bp.post("/stamp/<int:listing_id>/verify")
@collector_required
def verify_stamp(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    try:
        svc.reverify_listing(listing, current_user)
        flash("Verification re-run.", "success")
    except DomainError as exc:
        flash(exc.message, "error")
    return redirect(url_for("main.stamp_detail", listing_id=listing_id))
