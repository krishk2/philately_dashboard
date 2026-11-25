"""Public collector profiles: what someone collects, and the badges they've earned.

Badges are computed from real activity each time (nothing to award or revoke by hand), so they
can never get out of sync with the data. Each rule returns progress toward a target, which the
profile shows as "3 / 10" for badges not yet earned.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from ..extensions import db
from ..models import (
    COURSE_WEEKS,
    LISTING_ACTIVE,
    LISTING_REMOVED,
    PREBOOK_PAID,
    CourseProgress,
    Follow,
    ForumReply,
    ForumThread,
    Listing,
    NpdaAccount,
    Order,
    Prebooking,
    User,
    VerificationReport,
)
from .notifications import notify


@dataclass
class CollectionItem:
    title: str
    image_url: str
    detail: str
    year: int | None
    circle: str | None
    how: str  # "Bought" or "Pre-booked"


@dataclass
class Badge:
    key: str
    icon: str
    name: str
    description: str
    progress: int
    target: int

    @property
    def earned(self) -> bool:
        return self.progress >= self.target


def collection_for(user: User) -> list[CollectionItem]:
    """Stamps the user owns: bought in the Stamp Mela or pre-booked from postal circles."""
    items: list[CollectionItem] = []
    for order in db.session.scalars(
        select(Order).where(Order.buyer_id == user.id).order_by(Order.created_at.desc())
    ):
        listing = order.listing
        circle = listing.issue.circle.postal_circle if listing.issue else None
        items.append(
            CollectionItem(
                title=listing.title,
                image_url=f"/uploads/{listing.image_filename}",
                detail=f"{listing.year} · × {order.quantity}",
                year=listing.year,
                circle=circle,
                how="Bought",
            )
        )
    for booking in db.session.scalars(
        select(Prebooking)
        .where(Prebooking.user_id == user.id, Prebooking.status == PREBOOK_PAID)
        .order_by(Prebooking.created_at.desc())
    ):
        issue = booking.issue
        items.append(
            CollectionItem(
                title=issue.title,
                image_url=f"/uploads/{issue.reference_image}",
                detail=(
                    f"{issue.circle.postal_circle} · {issue.issue_date.year} · × {booking.quantity}"
                ),
                year=issue.issue_date.year,
                circle=issue.circle.postal_circle,
                how="Pre-booked",
            )
        )
    return items


def _count(stmt) -> int:
    return int(db.session.scalar(stmt) or 0)


def badges_for(user: User, collection: list[CollectionItem] | None = None) -> list[Badge]:
    if user.is_circle:
        return []
    collection = collection if collection is not None else collection_for(user)

    stamps_owned = _count(
        select(func.sum(Order.quantity)).where(Order.buyer_id == user.id)
    ) + _count(
        select(func.sum(Prebooking.quantity)).where(
            Prebooking.user_id == user.id, Prebooking.status == PREBOOK_PAID
        )
    )
    circles = {item.circle for item in collection if item.circle}
    oldest = min((item.year for item in collection if item.year), default=None)
    sales = _count(
        select(func.sum(Order.quantity)).join(Listing).where(Listing.seller_id == user.id)
    )
    verified_listings = _count(
        select(func.count(Listing.id))
        .join(VerificationReport)
        .where(
            Listing.seller_id == user.id,
            Listing.status != LISTING_REMOVED,
            VerificationReport.status == "verified",
        )
    )
    npda_active = _count(
        select(func.count(NpdaAccount.id)).where(
            NpdaAccount.user_id == user.id, NpdaAccount.status == "active"
        )
    )
    course = _count(select(func.count(CourseProgress.id)).where(CourseProgress.user_id == user.id))
    posts = _count(
        select(func.count(ForumThread.id)).where(ForumThread.author_id == user.id)
    ) + _count(select(func.count(ForumReply.id)).where(ForumReply.author_id == user.id))
    followers = _count(select(func.count(Follow.id)).where(Follow.followed_id == user.id))

    return [
        Badge("first", "🌱", "First Stamp", "Own your first stamp.", min(stamps_owned, 1), 1),
        Badge("collector", "📒", "Collector", "Own 10 stamps.", stamps_owned, 10),
        Badge("curator", "🏛️", "Curator", "Own 50 stamps.", stamps_owned, 50),
        Badge(
            "explorer",
            "🗺️",
            "Circle Explorer",
            "Collect issues from 3 different postal circles.",
            len(circles),
            3,
        ),
        Badge(
            "vintage",
            "⏳",
            "Vintage Hunter",
            "Own a stamp from before 1950.",
            1 if oldest and oldest < 1950 else 0,
            1,
        ),
        # Sales only count once the seller has at least one listing that passed verification.
        Badge(
            "seller",
            "🤝",
            "Trusted Seller",
            "Sell 3 stamps, with a verified listing.",
            sales if verified_listings else 0,
            3,
        ),
        Badge("npda", "📬", "Standing Order", "Hold an active NPDA account.", npda_active, 1),
        Badge(
            "scholar",
            "🎓",
            "Scholar",
            "Finish the Philately Crash Course.",
            course,
            len(COURSE_WEEKS),
        ),
        Badge("voice", "💬", "Community Voice", "Write 5 forum posts or replies.", posts, 5),
        Badge("popular", "⭐", "Well Known", "Be followed by 5 collectors.", followers, 5),
    ]


def earned_badges(user: User) -> list[Badge]:
    return [b for b in badges_for(user) if b.earned]


def for_sale(user: User) -> list[Listing]:
    return db.session.scalars(
        select(Listing)
        .where(Listing.seller_id == user.id, Listing.status == LISTING_ACTIVE)
        .order_by(Listing.created_at.desc())
    ).all()


def recent_threads(user: User, limit: int = 5) -> list[ForumThread]:
    return db.session.scalars(
        select(ForumThread)
        .where(ForumThread.author_id == user.id)
        .order_by(ForumThread.created_at.desc())
        .limit(limit)
    ).all()


def follow_counts(user: User) -> tuple[int, int]:
    followers = _count(select(func.count(Follow.id)).where(Follow.followed_id == user.id))
    following = _count(select(func.count(Follow.id)).where(Follow.follower_id == user.id))
    return followers, following


def is_following(follower: User, followed: User) -> bool:
    if not follower.is_authenticated:
        return False
    return (
        db.session.scalar(
            select(Follow.id).where(
                Follow.follower_id == follower.id, Follow.followed_id == followed.id
            )
        )
        is not None
    )


def toggle_follow(follower: User, followed: User) -> bool:
    """Follow or unfollow. Returns True when now following."""
    if follower.id == followed.id:
        return False
    existing = db.session.scalar(
        select(Follow).where(Follow.follower_id == follower.id, Follow.followed_id == followed.id)
    )
    if existing:
        db.session.delete(existing)
        db.session.commit()
        return False
    db.session.add(Follow(follower_id=follower.id, followed_id=followed.id))
    notify(
        followed.id,
        f"{follower.display_name} started following your collection.",
        f"/collectors/{follower.username}",
    )
    db.session.commit()
    return True


def following_ids(user: User) -> set[int]:
    if not user.is_authenticated:
        return set()
    return set(db.session.scalars(select(Follow.followed_id).where(Follow.follower_id == user.id)))


def directory(query: str = "") -> list[tuple[User, list[Badge], int]]:
    """Collectors with their earned badges and collection size, most badges first."""
    stmt = select(User).where(User.role == "collector")
    if query:
        pattern = f"%{query}%"
        stmt = stmt.where((User.username.ilike(pattern)) | (User.full_name.ilike(pattern)))
    rows = []
    for user in db.session.scalars(stmt.limit(200)):
        collection = collection_for(user) if user.show_collection else []
        earned = [b for b in badges_for(user, collection) if b.earned]
        rows.append((user, earned, len(collection)))
    rows.sort(key=lambda r: (len(r[1]), r[2]), reverse=True)
    return rows
