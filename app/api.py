"""JSON REST API (v1).

Auth: POST /api/v1/auth/token with username and password returns a bearer token. Send it as
`Authorization: Bearer <token>` on write endpoints. The API never uses the session cookie, so it
is exempt from CSRF.

Errors always have the shape {"error": {"code": str, "message": str}}.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import select
from werkzeug.exceptions import HTTPException

from .extensions import csrf, db
from .models import LISTING_REMOVED, Event, Issue, Listing, Order
from .services import marketplace as svc
from .services.accounts import authenticate, issue_token, user_from_token
from .services.errors import DomainError

bp = Blueprint("api", __name__, url_prefix="/api/v1")
csrf.exempt(bp)


def error(status: int, code: str, message: str):
    return jsonify({"error": {"code": code, "message": message}}), status


@bp.errorhandler(DomainError)
def handle_domain_error(exc: DomainError):
    return error(exc.status_code, exc.code, exc.message)


@bp.errorhandler(HTTPException)
def handle_http_error(exc: HTTPException):
    return error(exc.code or 500, (exc.name or "error").lower().replace(" ", "_"), exc.description)


def token_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        user = user_from_token(token.strip()) if scheme.lower() == "bearer" and token else None
        if user is None:
            return error(401, "unauthorized", "A valid bearer token is required.")
        g.api_user = user
        return view(*args, **kwargs)

    return wrapper


def _payload() -> dict:
    """Accept JSON bodies or form/multipart bodies (multipart is needed for image uploads)."""
    if request.is_json:
        body = request.get_json(silent=True)
        return body if isinstance(body, dict) else {}
    return request.form.to_dict()


def _get_listing(listing_id: int) -> Listing:
    listing = db.session.get(Listing, listing_id)
    if listing is None or listing.status == LISTING_REMOVED:
        raise _NotFound("Listing not found.")
    return listing


class _NotFound(DomainError):
    status_code = 404
    code = "not_found"


# ---------- auth ----------


@bp.post("/auth/token")
def create_token():
    body = _payload()
    user = authenticate(body.get("username", ""), body.get("password", ""))
    if user is None:
        return error(401, "invalid_credentials", "Invalid username or password.")
    return jsonify(
        {
            "token": issue_token(user),
            "expires_in": current_app.config["API_TOKEN_MAX_AGE"],
            "user": user.to_dict(),
        }
    )


@bp.get("/me")
@token_required
def me():
    return jsonify(g.api_user.to_dict())


# ---------- listings ----------


def _arg(name: str, parser):
    raw = request.args.get(name, "").strip()
    if not raw:
        return None
    try:
        return parser(raw)
    except (ValueError, InvalidOperation) as exc:
        raise DomainError(f"Query parameter '{name}' is invalid.") from exc


@bp.get("/listings")
def list_listings():
    per_page = min(_arg("per_page", int) or 20, current_app.config["API_PAGE_SIZE_MAX"])
    page_number = max(_arg("page", int) or 1, 1)
    stmt = svc.search_listings(
        query=request.args.get("q", "").strip(),
        year=_arg("year", int),
        min_price=_arg("min_price", Decimal),
        max_price=_arg("max_price", Decimal),
        verified_only=request.args.get("verified") in ("1", "true"),
    )
    page = db.paginate(stmt, page=page_number, per_page=max(per_page, 1), error_out=False)
    return jsonify(
        {
            "items": [listing.to_dict() for listing in page.items],
            "page": page.page,
            "per_page": page.per_page,
            "total": page.total,
            "pages": page.pages,
        }
    )


@bp.get("/listings/<int:listing_id>")
def get_listing(listing_id):
    return jsonify(_get_listing(listing_id).to_dict())


@bp.post("/listings")
@token_required
def create_listing():
    listing = svc.create_listing(g.api_user, request.form, request.files.get("image"))
    return jsonify(listing.to_dict()), 201


@bp.patch("/listings/<int:listing_id>")
@token_required
def update_listing(listing_id):
    listing = svc.update_listing(_get_listing(listing_id), g.api_user, _payload())
    return jsonify(listing.to_dict())


@bp.delete("/listings/<int:listing_id>")
@token_required
def delete_listing(listing_id):
    svc.remove_listing(_get_listing(listing_id), g.api_user)
    return "", 204


@bp.get("/listings/<int:listing_id>/verification")
def get_verification(listing_id):
    listing = _get_listing(listing_id)
    if listing.verification is None:
        raise _NotFound("This listing has not been verified yet.")
    return jsonify(listing.verification.to_dict())


@bp.post("/listings/<int:listing_id>/verification")
@token_required
def rerun_verification(listing_id):
    listing = svc.reverify_listing(_get_listing(listing_id), g.api_user)
    return jsonify(listing.verification.to_dict())


# ---------- orders ----------


@bp.post("/orders")
@token_required
def create_order():
    body = _payload()
    listing_id = svc.parse_int(body.get("listing_id"), "listing_id", minimum=1)
    order = svc.place_order(
        g.api_user, listing_id, body.get("quantity", 1), body.get("shipping_address")
    )
    return jsonify(order.to_dict()), 201


@bp.get("/orders")
@token_required
def list_orders():
    rows = db.session.scalars(
        select(Order).where(Order.buyer_id == g.api_user.id).order_by(Order.created_at.desc())
    ).all()
    return jsonify({"items": [order.to_dict() for order in rows]})


# ---------- issues and events ----------


@bp.get("/issues")
def list_issues():
    rows = db.session.scalars(select(Issue).order_by(Issue.issue_date.desc())).all()
    return jsonify({"items": [issue.to_dict() for issue in rows]})


@bp.post("/issues/<int:issue_id>/prebook")
@token_required
def prebook(issue_id):
    booking = svc.prebook_issue(g.api_user, issue_id, _payload().get("quantity", 1))
    return jsonify(
        {
            "id": booking.id,
            "issue_id": booking.issue_id,
            "quantity": booking.quantity,
            "remaining": booking.issue.prebook_available,
        }
    ), 201


@bp.get("/events")
def list_events():
    stmt = select(Event).order_by(Event.date)
    if request.args.get("upcoming") in ("1", "true"):
        stmt = stmt.where(Event.date >= date.today())
    return jsonify({"items": [event.to_dict() for event in db.session.scalars(stmt)]})
