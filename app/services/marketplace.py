"""Business logic for listings, orders and pre-bookings, shared by the web UI and the REST API."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import or_, select, update

from ..extensions import db
from ..models import (
    LISTING_ACTIVE,
    LISTING_REMOVED,
    LISTING_SOLD_OUT,
    Issue,
    Listing,
    Order,
    Prebooking,
    User,
)
from ..verification import verify_listing
from .errors import Forbidden, OutOfStock, ValidationError
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
):
    """Build a SELECT for active listings matching the filters (newest first)."""
    from ..models import VerificationReport

    stmt = select(Listing).where(Listing.status == LISTING_ACTIVE)
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
    return stmt.order_by(Listing.created_at.desc(), Listing.id.desc())


def _require_owner(listing: Listing, user: User) -> None:
    if listing.seller_id != user.id:
        raise Forbidden("You can only change your own listings.")


# ---------- orders ----------


def place_order(buyer: User, listing_id: int, quantity, shipping_address) -> Order:
    """Buy stock from a listing.

    Stock is decremented with a single conditional UPDATE (`... WHERE quantity >= :qty`), so two
    buyers racing for the last stamp cannot both succeed: the database applies the updates one at
    a time and the second one matches zero rows.
    """
    quantity = parse_int(quantity, "Quantity", minimum=1, maximum=10_000)
    address = parse_text(shipping_address, "Shipping address", max_length=500)

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
    )
    db.session.add(order)
    db.session.commit()
    return order


def prebook_issue(user: User, issue_id: int, quantity) -> Prebooking:
    """Reserve copies of a new official issue, with the same race-safe decrement as orders."""
    quantity = parse_int(quantity, "Quantity", minimum=1, maximum=100)
    if user.is_circle:
        raise Forbidden("Postal circle accounts cannot pre-book issues.")
    if db.session.get(Issue, issue_id) is None:
        raise ValidationError("Issue not found.")

    result = db.session.execute(
        update(Issue)
        .where(Issue.id == issue_id, Issue.prebook_available >= quantity)
        .values(prebook_available=Issue.prebook_available - quantity)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.session.rollback()
        raise OutOfStock("Not enough copies left to pre-book.")

    booking = Prebooking(issue_id=issue_id, user=user, quantity=quantity)
    db.session.add(booking)
    db.session.commit()
    return booking


# ---------- official issues ----------


def create_issue(circle: User, data, image) -> Issue:
    if not circle.is_circle:
        raise Forbidden("Only postal circle accounts can publish issues.")
    try:
        issue_date = date.fromisoformat(str(data.get("issue_date", "")).strip())
    except ValueError as exc:
        raise ValidationError("Issue date must be a date (YYYY-MM-DD).") from exc

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
    return issue


def delete_issue(issue: Issue, user: User) -> None:
    if issue.circle_id != user.id:
        raise Forbidden("You can only delete your own circle's issues.")
    if issue.prebookings:
        raise ValidationError("This issue already has pre-bookings and cannot be deleted.")
    linked = db.session.scalar(select(Listing.id).where(Listing.issue_id == issue.id).limit(1))
    if linked is not None:
        raise ValidationError("Listings refer to this issue, so it cannot be deleted.")
    filename = issue.reference_image
    db.session.delete(issue)
    db.session.commit()
    delete_image(filename)
