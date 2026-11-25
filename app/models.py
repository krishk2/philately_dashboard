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

# India Post's 23 postal circles.
POSTAL_CIRCLES = (
    "Andhra Pradesh",
    "Assam",
    "Bihar",
    "Chhattisgarh",
    "Delhi",
    "Gujarat",
    "Haryana",
    "Himachal Pradesh",
    "Jammu & Kashmir",
    "Jharkhand",
    "Karnataka",
    "Kerala",
    "Madhya Pradesh",
    "Maharashtra",
    "North East",
    "Odisha",
    "Punjab",
    "Rajasthan",
    "Tamil Nadu",
    "Telangana",
    "Uttar Pradesh",
    "Uttarakhand",
    "West Bengal",
)

PREBOOK_PENDING = "pending"
PREBOOK_PAID = "paid"
PREBOOK_EXPIRED = "expired"
PREBOOK_CANCELLED = "cancelled"
PREBOOK_STATUSES = (PREBOOK_PENDING, PREBOOK_PAID, PREBOOK_EXPIRED, PREBOOK_CANCELLED)

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
    # False for accounts created through Google/Yahoo until the user sets a password.
    password_set: Mapped[bool] = mapped_column(default=True, server_default="1")
    role: Mapped[str] = mapped_column(String(16), default=ROLE_COLLECTOR)
    postal_circle: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str | None] = mapped_column(String(120))
    membership: Mapped[str] = mapped_column(String(16), default="Basic", server_default="Basic")
    avatar_filename: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(Text)
    bio: Mapped[str | None] = mapped_column(String(300))
    # Public collector profile: whether others can see what this user owns.
    show_collection: Mapped[bool] = mapped_column(default=True, server_default="1")

    listings: Mapped[list[Listing]] = relationship(back_populates="seller")
    orders: Mapped[list[Order]] = relationship(back_populates="buyer")
    npda: Mapped[NpdaAccount | None] = relationship(back_populates="user", uselist=False)

    __table_args__ = (CheckConstraint(f"role IN {ROLES}", name="ck_users_role"),)

    def set_password(self, password: str) -> None:
        method = current_app.config.get("PASSWORD_HASH_METHOD", "scrypt")
        self.password_hash = generate_password_hash(password, method=method)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_circle(self) -> bool:
        return self.role == ROLE_CIRCLE

    @property
    def display_name(self) -> str:
        return self.full_name or self.username

    @property
    def initials(self) -> str:
        parts = self.display_name.split()
        return "".join(p[0] for p in parts[:2]).upper() or "?"

    @property
    def profile_complete(self) -> bool:
        """NPDA accounts need a name, phone and dispatch address."""
        return bool(self.full_name and self.phone and self.address)

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
    # pending: copies held while the collector pays; paid: confirmed;
    # expired / cancelled: the copies went back to the issue's stock.
    status: Mapped[str] = mapped_column(
        String(10), default=PREBOOK_PAID, server_default=PREBOOK_PAID, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_ref: Mapped[str | None] = mapped_column(String(32))

    issue: Mapped[Issue] = relationship(back_populates="prebookings")
    user: Mapped[User] = relationship()

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_prebookings_qty_pos"),
        CheckConstraint(f"status IN {PREBOOK_STATUSES}", name="ck_prebookings_status"),
    )

    @property
    def total(self) -> Decimal:
        return self.issue.denomination * self.quantity


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
    contact_name: Mapped[str] = mapped_column(String(120), default="", server_default="")
    contact_email: Mapped[str] = mapped_column(String(255), default="", server_default="")
    contact_phone: Mapped[str] = mapped_column(String(20), default="", server_default="")

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


UPDATE_KINDS = {
    "newRelease": "New Stamp Release",
    "cancellation": "Cancellation",
    "fdc": "FDC",
    "souvenir": "Souvenir Sheet",
    "event": "Event",
}


class CircleUpdate(TimestampMixin, db.Model):
    """A news post from a postal circle, shown in the home page's news tab."""

    __tablename__ = "circle_updates"

    id: Mapped[int] = mapped_column(primary_key=True)
    circle_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(150))
    content: Mapped[str] = mapped_column(Text)

    circle: Mapped[User] = relationship()

    @property
    def kind_label(self) -> str:
        return UPDATE_KINDS.get(self.kind, self.kind)


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


# ---------- NPDA (National Philatelic Deposit Account) ----------

NPDA_ACTIVE = "active"
NPDA_CLOSED = "closed"
NPDA_SCOPE_HOME = "home"  # new issues from the account's home circle only
NPDA_SCOPE_ALL = "all"  # new issues from every circle


class NpdaAccount(TimestampMixin, db.Model):
    """An in-app deposit account modelled on India Post's Philatelic Deposit Account.

    The holder keeps a balance; whenever a matching postal circle publishes a new issue, copies
    are reserved automatically and the balance is debited (a standing order).
    """

    __tablename__ = "npda_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    account_number: Mapped[str | None] = mapped_column(String(32), unique=True)
    home_circle: Mapped[str] = mapped_column(String(64))
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    copies_per_issue: Mapped[int] = mapped_column(Integer, default=1)
    scope: Mapped[str] = mapped_column(String(8), default=NPDA_SCOPE_HOME)
    status: Mapped[str] = mapped_column(String(8), default=NPDA_ACTIVE, index=True)

    user: Mapped[User] = relationship(back_populates="npda")
    transactions: Mapped[list[NpdaTransaction]] = relationship(
        back_populates="account", order_by="NpdaTransaction.id.desc()"
    )

    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_npda_balance_nonneg"),
        CheckConstraint("copies_per_issue BETWEEN 1 AND 10", name="ck_npda_copies_range"),
    )

    @property
    def is_active(self) -> bool:
        return self.status == NPDA_ACTIVE


class NpdaTransaction(TimestampMixin, db.Model):
    __tablename__ = "npda_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("npda_accounts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # deposit / debit / refund
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    description: Mapped[str] = mapped_column(String(255))
    issue_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id"))

    account: Mapped[NpdaAccount] = relationship(back_populates="transactions")


class Notification(TimestampMixin, db.Model):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    message: Mapped[str] = mapped_column(String(500))
    link: Mapped[str | None] = mapped_column(String(255))
    is_read: Mapped[bool] = mapped_column(default=False, server_default="0")


# ---------- learning progress (Philately Crash Course) ----------

COURSE_WEEKS = ("week1", "week2", "week3", "week4", "bonus")


class CourseProgress(TimestampMixin, db.Model):
    __tablename__ = "course_progress"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    week: Mapped[str] = mapped_column(String(16))

    __table_args__ = (UniqueConstraint("user_id", "week", name="uq_course_user_week"),)


# ---------- forums ----------

FORUM_CATEGORIES = {
    "general": "General Discussion",
    "identification": "Stamp Identification",
    "trading": "Buying & Selling",
    "new-issues": "New Issues & Releases",
    "events": "Events & Exhibitions",
    "help": "Help & Support",
}


class ForumThread(TimestampMixin, db.Model):
    __tablename__ = "forum_threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(150))
    body: Mapped[str] = mapped_column(Text)
    last_activity: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    author: Mapped[User] = relationship()
    replies: Mapped[list[ForumReply]] = relationship(
        back_populates="thread", cascade="all, delete-orphan", order_by="ForumReply.id"
    )

    @property
    def category_label(self) -> str:
        return FORUM_CATEGORIES.get(self.category, self.category)


class ForumReply(TimestampMixin, db.Model):
    __tablename__ = "forum_replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("forum_threads.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    body: Mapped[str] = mapped_column(Text)

    thread: Mapped[ForumThread] = relationship(back_populates="replies")
    author: Mapped[User] = relationship()


# ---------- single sign-on ----------


class OAuthIdentity(TimestampMixin, db.Model):
    """Links a Google / Yahoo account (provider + stable subject id) to a local user."""

    __tablename__ = "oauth_identities"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255))

    user: Mapped[User] = relationship()

    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_oauth_provider_subject"),)


# ---------- imported philately news (India Post, RSS feeds) ----------

FEED_RELEASE = "release"
FEED_NEWS = "news"
FEED_EVENT = "event"


class FeedItem(db.Model):
    """A headline imported from an external source, always shown with a link back to it."""

    __tablename__ = "feed_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_label: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(8), index=True)
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(500))
    published_on: Mapped[date | None] = mapped_column(Date, index=True)
    denomination: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    stamp_count: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text, default="")
    # The source's own image of the stamp. Only displayed when FEED_SHOW_SOURCE_IMAGES is on,
    # because India Post requires permission for any use of stamp images.
    image_url: Mapped[str | None] = mapped_column(String(500))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_feed_source_item"),)


# ---------- collector network ----------


class Follow(TimestampMixin, db.Model):
    __tablename__ = "follows"

    id: Mapped[int] = mapped_column(primary_key=True)
    follower_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    followed_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    follower: Mapped[User] = relationship(foreign_keys=[follower_id])
    followed: Mapped[User] = relationship(foreign_keys=[followed_id])

    __table_args__ = (
        UniqueConstraint("follower_id", "followed_id", name="uq_follow_pair"),
        CheckConstraint("follower_id <> followed_id", name="ck_follow_not_self"),
    )
