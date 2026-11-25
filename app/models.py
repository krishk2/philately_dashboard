"""Database models.

Roles are plain strings (not DB enums) so migrations stay portable between SQLite and Postgres.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from flask import current_app
from flask_login import UserMixin
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db

ROLE_COLLECTOR = "collector"
ROLE_CIRCLE = "circle"
ROLES = (ROLE_COLLECTOR, ROLE_CIRCLE)

POSTAL_CIRCLES = (
    "Andhra Pradesh",
    "Delhi",
    "Karnataka",
    "Kerala",
    "Maharashtra",
    "Tamil Nadu",
    "Telangana",
    "West Bengal",
)

LISTING_ACTIVE = "active"
LISTING_SOLD_OUT = "sold_out"
LISTING_REMOVED = "removed"


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default=ROLE_COLLECTOR)
    postal_circle: Mapped[str | None] = mapped_column(String(64))

    listings: Mapped[list[Listing]] = relationship(back_populates="seller")
    orders: Mapped[list[Order]] = relationship(back_populates="buyer")

    __table_args__ = (CheckConstraint(f"role IN {ROLES}", name="ck_users_role"),)

    def set_password(self, password: str) -> None:
        method = current_app.config.get("PASSWORD_HASH_METHOD", "scrypt")
        self.password_hash = generate_password_hash(password, method=method)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_circle(self) -> bool:
        return self.role == ROLE_CIRCLE

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "postal_circle": self.postal_circle,
        }


class Issue(TimestampMixin, db.Model):
    """An official stamp issue published by a postal circle. Collectors can pre-book it,
    and its reference image is what seller listings are checked against."""

    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    circle_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(150))
    issue_date: Mapped[date] = mapped_column(Date)
    denomination: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    description: Mapped[str] = mapped_column(Text, default="")
    reference_image: Mapped[str] = mapped_column(String(255))
    reference_hash: Mapped[str] = mapped_column(String(16))
    prebook_available: Mapped[int] = mapped_column(Integer, default=0)

    circle: Mapped[User] = relationship()
    prebookings: Mapped[list[Prebooking]] = relationship(
        back_populates="issue", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("prebook_available >= 0", name="ck_issues_prebook_nonneg"),
        CheckConstraint("denomination > 0", name="ck_issues_denomination_pos"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "issue_date": self.issue_date.isoformat(),
            "denomination": str(self.denomination),
            "description": self.description,
            "postal_circle": self.circle.postal_circle,
            "prebook_available": self.prebook_available,
        }


class Prebooking(TimestampMixin, db.Model):
    __tablename__ = "prebookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)

    issue: Mapped[Issue] = relationship(back_populates="prebookings")
    user: Mapped[User] = relationship()

    __table_args__ = (CheckConstraint("quantity > 0", name="ck_prebookings_qty_pos"),)


class Listing(TimestampMixin, db.Model):
    """A stamp a collector is selling."""

    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    issue_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id"), index=True)
    title: Mapped[str] = mapped_column(String(150), index=True)
    year: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str] = mapped_column(Text, default="")
    image_filename: Mapped[str] = mapped_column(String(255))
    image_hash: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), default=LISTING_ACTIVE, index=True)

    seller: Mapped[User] = relationship(back_populates="listings")
    issue: Mapped[Issue | None] = relationship()
    verification: Mapped[VerificationReport | None] = relationship(
        back_populates="listing", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_listings_qty_nonneg"),
        CheckConstraint("price > 0", name="ck_listings_price_pos"),
    )

    def to_dict(self, include_verification: bool = True) -> dict:
        data = {
            "id": self.id,
            "title": self.title,
            "year": self.year,
            "price": str(self.price),
            "quantity": self.quantity,
            "description": self.description,
            "status": self.status,
            "seller": self.seller.username,
            "issue_id": self.issue_id,
            "image_url": f"/uploads/{self.image_filename}",
            "created_at": self.created_at.isoformat(),
        }
        if include_verification:
            data["verification"] = self.verification.to_dict() if self.verification else None
        return data


class Order(TimestampMixin, db.Model):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    shipping_address: Mapped[str] = mapped_column(Text)

    buyer: Mapped[User] = relationship(back_populates="orders")
    listing: Mapped[Listing] = relationship()

    __table_args__ = (CheckConstraint("quantity > 0", name="ck_orders_qty_pos"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "listing_id": self.listing_id,
            "title": self.listing.title,
            "quantity": self.quantity,
            "unit_price": str(self.unit_price),
            "total": str(self.total),
            "created_at": self.created_at.isoformat(),
        }


class Event(TimestampMixin, db.Model):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    circle_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    date: Mapped[date] = mapped_column(Date, index=True)
    location: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")

    circle: Mapped[User] = relationship()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "date": self.date.isoformat(),
            "location": self.location,
            "description": self.description,
            "postal_circle": self.circle.postal_circle,
        }


class WishlistItem(TimestampMixin, db.Model):
    __tablename__ = "wishlist_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))

    listing: Mapped[Listing] = relationship()

    __table_args__ = (UniqueConstraint("user_id", "listing_id", name="uq_wishlist_user_listing"),)


class VerificationReport(TimestampMixin, db.Model):
    """Result of running the verification checks on a listing. Re-running replaces it."""

    __tablename__ = "verification_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), unique=True)
    status: Mapped[str] = mapped_column(String(16))
    score: Mapped[int] = mapped_column(Integer)
    checks: Mapped[list] = mapped_column(JSON)

    listing: Mapped[Listing] = relationship(back_populates="verification")

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "score": self.score,
            "checks": self.checks,
            "checked_at": self.created_at.isoformat(),
        }
