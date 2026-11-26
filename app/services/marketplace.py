"""Business logic for listings, orders and pre-bookings, shared by the web UI and the REST API."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from flask import current_app
from sqlalchemy import or_, select, update
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import (
    LISTING_ACTIVE,
    LISTING_REMOVED,
    LISTING_SOLD_OUT,
    PREBOOK_CANCELLED,
    PREBOOK_EXPIRED,
    PREBOOK_PAID,
    PREBOOK_PENDING,
    Issue,
    Listing,
    Order,
    Prebooking,
    User,
    VerificationReport,
    utcnow,
)
from ..verification import verify_listing
from .errors import Forbidden, OutOfStock, ValidationError
from .notifications import notify
from .uploads import delete_image, save_image

MIN_YEAR = 1840  # the Penny Black, the first postage stamp


# ---------- input parsing ----------


def parse_int(value, field: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field} must be a whole number.") from exc
    if minimum is not None and number < minimum:
        raise ValidationError(f"{field} must be at least {minimum}.")
    if maximum is not None and number > maximum:
        raise ValidationError(f"{field} must be at most {maximum}.")
    return number


def parse_price(value, field: str = "Price") -> Decimal:
    try:
        price = Decimal(str(value).strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"{field} must be a number.") from exc
    if price <= 0:
        raise ValidationError(f"{field} must be greater than 0.")
    if price >= Decimal("100000000"):
        raise ValidationError(f"{field} is too large.")
    return price


def parse_text(value, field: str, *, max_length: int, required: bool = True) -> str:
    text = (value or "").strip()
    if required and not text:
        raise ValidationError(f"{field} is required.")
    if len(text) > max_length:
        raise ValidationError(f"{field} must be at most {max_length} characters.")
    return text


def _parse_issue_id(value) -> int | None:
    if value in (None, "", "0", 0):
        return None
    issue_id = parse_int(value, "Official issue", minimum=1)
    if db.session.get(Issue, issue_id) is None:
        raise ValidationError("Official issue not found.")
    return issue_id


# ---------- listings ----------


def create_listing(seller: User, data, image) -> Listing:
    if seller.is_circle:
        raise Forbidden("Postal circles publish issues; only collectors can list stamps for sale.")

    listing = Listing(
        seller=seller,
        title=parse_text(data.get("title"), "Title", max_length=150),
        year=parse_int(data.get("year"), "Year", minimum=MIN_YEAR, maximum=date.today().year),
        price=parse_price(data.get("price")),
        quantity=parse_int(data.get("quantity", 1), "Quantity", minimum=1, maximum=10_000),
        description=parse_text(
            data.get("description"), "Description", max_length=2000, required=False
        ),
        issue_id=_parse_issue_id(data.get("issue_id")),
    )
    listing.image_filename, listing.image_hash = save_image(image)
    db.session.add(listing)
    db.session.flush()  # assigns listing.id so the duplicate check can exclude it
    verify_listing(listing)
    db.session.commit()
    return listing


def update_listing(listing: Listing, user: User, data) -> Listing:
    _require_owner(listing, user)
    if "title" in data:
        listing.title = parse_text(data.get("title"), "Title", max_length=150)
    if "year" in data:
        listing.year = parse_int(
            data.get("year"), "Year", minimum=MIN_YEAR, maximum=date.today().year
        )
    if "price" in data:
        listing.price = parse_price(data.get("price"))
    if "quantity" in data:
        listing.quantity = parse_int(data.get("quantity"), "Quantity", minimum=0, maximum=10_000)
        if listing.status != LISTING_REMOVED:
            listing.status = LISTING_ACTIVE if listing.quantity > 0 else LISTING_SOLD_OUT
    if "description" in data:
        listing.description = parse_text(
            data.get("description"), "Description", max_length=2000, required=False
        )
    if "issue_id" in data:
        listing.issue_id = _parse_issue_id(data.get("issue_id"))
        db.session.flush()
        db.session.refresh(listing, ["issue"])
    # Claims changed, so the old verification no longer applies.
    verify_listing(listing)
    db.session.commit()
    return listing


def remove_listing(listing: Listing, user: User) -> None:
    """Soft delete: past orders still reference the listing."""
    _require_owner(listing, user)
    listing.status = LISTING_REMOVED
    db.session.commit()


def reverify_listing(listing: Listing, user: User) -> Listing:
    _require_owner(listing, user)
    verify_listing(listing)
    db.session.commit()
    return listing


def search_listings(
    *,
    query: str = "",
    year: int | None = None,
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
    verified_only: bool = False,
    sort: str | None = None,
):
    """Build a SELECT for active listings matching the filters (newest first by default)."""
    # Load the related rows in a few batched queries instead of one per card (N+1).
    stmt = (
        select(Listing)
        .where(Listing.status == LISTING_ACTIVE)
        .options(
            selectinload(Listing.seller),
            selectinload(Listing.verification),
            selectinload(Listing.issue),
        )
    )
    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(or_(Listing.title.ilike(pattern), Listing.description.ilike(pattern)))
    if year is not None:
        stmt = stmt.where(Listing.year == year)
    if min_price is not None:
        stmt = stmt.where(Listing.price >= min_price)
    if max_price is not None:
        stmt = stmt.where(Listing.price <= max_price)
    if verified_only:
        stmt = stmt.join(VerificationReport).where(VerificationReport.status == "verified")
    order = SORTS.get(sort or "", ())
    return stmt.order_by(*order, Listing.created_at.desc(), Listing.id.desc())


# The four options in the marketplace's "Filter" dropdown.
SORTS = {
    "price_asc": (Listing.price.asc(),),
    "price_desc": (Listing.price.desc(),),
    "year_asc": (Listing.year.asc(),),
    "year_desc": (Listing.year.desc(),),
}


def _require_owner(listing: Listing, user: User) -> None:
    if listing.seller_id != user.id:
        raise Forbidden("You can only change your own listings.")


# ---------- orders ----------


PHONE_RE = re.compile(r"^\+?[0-9][0-9 -]{6,18}[0-9]$")


def place_order(
    buyer: User, listing_id: int, quantity, shipping_address, contact: dict | None = None
) -> Order:
    """Buy stock from a listing.

    Stock is decremented with a single conditional UPDATE (`... WHERE quantity >= :qty`), so two
    buyers racing for the last stamp cannot both succeed: the database applies the updates one at
    a time and the second one matches zero rows.

    `contact` holds the name, email and phone the buyer typed on the purchase form. When it is
    missing (e.g. API calls) the buyer's account details are used.
    """
    quantity = parse_int(quantity, "Quantity", minimum=1, maximum=10_000)
    address = parse_text(shipping_address, "Shipping address", max_length=500)
    contact = contact or {}
    name = parse_text(
        contact.get("name") or buyer.full_name or buyer.username, "Name", max_length=120
    )
    email = parse_text(contact.get("email") or buyer.email, "Email", max_length=255)
    phone = (contact.get("phone") or "").strip()
    if phone and not PHONE_RE.match(phone):
        raise ValidationError("Enter a valid phone number.")

    listing = db.session.get(Listing, listing_id)
    if listing is None or listing.status == LISTING_REMOVED:
        raise ValidationError("Listing not found.")
    if listing.seller_id == buyer.id:
        raise Forbidden("You cannot buy your own listing.")

    result = db.session.execute(
        update(Listing)
        .where(
            Listing.id == listing_id,
            Listing.status == LISTING_ACTIVE,
            Listing.quantity >= quantity,
        )
        .values(quantity=Listing.quantity - quantity)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.session.rollback()
        raise OutOfStock("Not enough stock left for that quantity.")

    db.session.refresh(listing)
    if listing.quantity == 0:
        listing.status = LISTING_SOLD_OUT

    order = Order(
        buyer=buyer,
        listing=listing,
        quantity=quantity,
        unit_price=listing.price,
        total=listing.price * quantity,
        shipping_address=address,
        contact_name=name,
        contact_email=email,
        contact_phone=phone,
    )
    db.session.add(order)
    notify(
        listing.seller_id,
        f"Sold! {name} bought {quantity} × '{listing.title}' (₹{order.total}). "
        "Delivery details are on your Orders page.",
        "/orders",
    )
    db.session.commit()
    return order


def prebook_issue(user: User, issue_id: int, quantity) -> Prebooking:
    """Hold copies of a new official issue while the collector pays.

    Stock is taken with the same race-safe conditional UPDATE as orders. The booking starts as
    *pending* and expires after PREBOOK_HOLD_MINUTES if unpaid, which returns the copies, so
    nobody can hoard an issue without paying.
    """
    quantity = parse_int(quantity, "Quantity", minimum=1, maximum=100)
    if user.is_circle:
        raise Forbidden("Postal circle accounts cannot pre-book issues.")
    if db.session.get(Issue, issue_id) is None:
        raise ValidationError("Issue not found.")
    expire_stale_prebookings()  # free any lapsed holds first, so their copies are available

    result = db.session.execute(
        update(Issue)
        .where(Issue.id == issue_id, Issue.prebook_available >= quantity)
        .values(prebook_available=Issue.prebook_available - quantity)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.session.rollback()
        raise OutOfStock("Not enough copies left to pre-book.")

    hold = timedelta(minutes=current_app.config["PREBOOK_HOLD_MINUTES"])
    booking = Prebooking(
        issue_id=issue_id,
        user=user,
        quantity=quantity,
        status=PREBOOK_PENDING,
        expires_at=utcnow() + hold,
    )
    db.session.add(booking)
    db.session.commit()
    return booking


def _release(booking_ids: list[int], new_status: str) -> int:
    """Move pending bookings to expired/cancelled and return their copies. Race-safe: only the
    process whose UPDATE flips a row from pending gets to return that row's stock."""
    released = 0
    for booking_id in booking_ids:
        flipped = db.session.execute(
            update(Prebooking)
            .where(Prebooking.id == booking_id, Prebooking.status == PREBOOK_PENDING)
            .values(status=new_status)
            .execution_options(synchronize_session=False)
        ).rowcount
        if flipped:
            booking = db.session.get(Prebooking, booking_id)
            db.session.execute(
                update(Issue)
                .where(Issue.id == booking.issue_id)
                .values(prebook_available=Issue.prebook_available + booking.quantity)
                .execution_options(synchronize_session=False)
            )
            released += 1
    db.session.commit()
    db.session.expire_all()
    return released


def expire_stale_prebookings() -> int:
    stale = db.session.scalars(
        select(Prebooking.id).where(
            Prebooking.status == PREBOOK_PENDING, Prebooking.expires_at < utcnow()
        )
    ).all()
    return _release(list(stale), PREBOOK_EXPIRED) if stale else 0


def cancel_prebooking(user: User, booking: Prebooking) -> None:
    if booking.user_id != user.id:
        raise Forbidden("That isn't your pre-booking.")
    if booking.status != PREBOOK_PENDING:
        raise ValidationError("Only unpaid pre-bookings can be cancelled.")
    _release([booking.id], PREBOOK_CANCELLED)


def pay_prebooking(user: User, booking: Prebooking, card: dict) -> Prebooking:
    """Confirm a pending pre-booking through the (simulated) payment gateway."""
    from .payments import charge

    if booking.user_id != user.id:
        raise Forbidden("That isn't your pre-booking.")
    expire_stale_prebookings()
    db.session.refresh(booking)
    if booking.status == PREBOOK_PAID:
        return booking
    if booking.status != PREBOOK_PENDING:
        raise ValidationError(
            "This pre-booking has expired and the copies were released. Please book again."
        )
    reference = charge(booking.total, card)  # raises PaymentDeclined
    paid = db.session.execute(
        update(Prebooking)
        .where(Prebooking.id == booking.id, Prebooking.status == PREBOOK_PENDING)
        .values(status=PREBOOK_PAID, paid_at=utcnow(), payment_ref=reference)
        .execution_options(synchronize_session=False)
    ).rowcount
    db.session.commit()
    db.session.refresh(booking)
    if not paid:
        raise ValidationError("This pre-booking is no longer awaiting payment.")
    return booking


# ---------- official issues ----------


def create_issue(circle: User, data, image) -> Issue:
    if not circle.is_circle:
        raise Forbidden("Only postal circle accounts can publish issues.")
    issue_date = _parse_date(data.get("issue_date"), "Issue date")

    issue = Issue(
        circle=circle,
        title=parse_text(data.get("title"), "Title", max_length=150),
        issue_date=issue_date,
        denomination=parse_price(data.get("denomination"), "Denomination"),
        description=parse_text(
            data.get("description"), "Description", max_length=2000, required=False
        ),
        prebook_available=parse_int(
            data.get("prebook_available", 0), "Copies for pre-booking", minimum=0, maximum=1_000_000
        ),
    )
    issue.reference_image, issue.reference_hash = save_image(image)
    db.session.add(issue)
    db.session.commit()

    # NPDA standing orders: subscribers get their copies reserved as soon as the issue is out.
    # Queued as a background job so the circle doesn't wait while thousands of subscribers are
    # served; with JOBS_INLINE it runs right away and the result is available here.
    from .jobs import enqueue  # local import: jobs depends on this module

    job = enqueue("npda_fulfil", {"issue_id": issue.id})
    issue.npda_result = job.result if job.status == "done" else None
    db.session.refresh(issue)
    return issue


def _parse_date(value, field: str) -> date:
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError as exc:
        raise ValidationError(f"{field} must be a date (YYYY-MM-DD).") from exc


def update_issue(issue: Issue, user: User, data) -> Issue:
    """Partial update: blank fields keep their current value (as on the Manage Stamps form)."""
    if issue.circle_id != user.id:
        raise Forbidden("You can only change your own circle's issues.")
    if (data.get("title") or "").strip():
        issue.title = parse_text(data.get("title"), "Title", max_length=150)
    if (data.get("issue_date") or "").strip():
        issue.issue_date = _parse_date(data.get("issue_date"), "Issue date")
    if str(data.get("denomination") or "").strip():
        issue.denomination = parse_price(data.get("denomination"), "Denomination")
    db.session.commit()
    return issue


def set_issue_stock(issue: Issue, user: User, quantity) -> Issue:
    """Set how many copies of an issue are left for pre-booking."""
    if issue.circle_id != user.id:
        raise Forbidden("You can only change your own circle's issues.")
    issue.prebook_available = parse_int(quantity, "Quantity", minimum=0, maximum=1_000_000)
    db.session.commit()
    return issue


def delete_issue(issue: Issue, user: User) -> None:
    if issue.circle_id != user.id:
        raise Forbidden("You can only delete your own circle's issues.")
    if any(b.status in (PREBOOK_PAID, PREBOOK_PENDING) for b in issue.prebookings):
        raise ValidationError("This issue already has pre-bookings and cannot be deleted.")
    linked = db.session.scalar(select(Listing.id).where(Listing.issue_id == issue.id).limit(1))
    if linked is not None:
        raise ValidationError("Listings refer to this issue, so it cannot be deleted.")
    filename = issue.reference_image
    db.session.delete(issue)
    db.session.commit()
    delete_image(filename)
