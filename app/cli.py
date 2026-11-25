"""`flask seed`: fill an empty database with demo accounts, issues, events and listings.

Stamp images are drawn with Pillow, so the demo needs no external files.
"""

from __future__ import annotations

import io
import random
from datetime import date, timedelta

import click
from flask import Flask
from PIL import Image, ImageDraw
from sqlalchemy import select
from werkzeug.datastructures import FileStorage

from .extensions import db
from .models import (
    ROLE_CIRCLE,
    ROLE_COLLECTOR,
    CircleUpdate,
    CourseProgress,
    Event,
    Follow,
    ForumReply,
    ForumThread,
    User,
)
from .services import marketplace as svc
from .services import npda as npda_svc

DEMO_PASSWORD = "demo-password"


def draw_stamp(seed: int, label: str, value: str) -> Image.Image:
    """Draw a simple, distinctive 'stamp' with a perforated border."""
    rng = random.Random(seed)
    image = Image.new("RGB", (300, 360), "white")
    draw = ImageDraw.Draw(image)
    colour = tuple(rng.randint(40, 200) for _ in range(3))
    draw.rectangle((20, 20, 280, 340), fill=colour)
    for _ in range(6):
        x, y = rng.randint(30, 230), rng.randint(40, 260)
        r = rng.randint(15, 55)
        shade = tuple(min(255, c + rng.randint(30, 90)) for c in colour)
        draw.ellipse((x, y, x + r, y + r), fill=shade)
    for x in range(10, 300, 20):  # perforations
        draw.ellipse((x - 6, -6, x + 6, 6), fill="white")
        draw.ellipse((x - 6, 354, x + 6, 366), fill="white")
    for y in range(10, 360, 20):
        draw.ellipse((-6, y - 6, 6, y + 6), fill="white")
        draw.ellipse((294, y - 6, 306, y + 6), fill="white")
    draw.text((32, 300), label[:30], fill="white")
    draw.text((230, 30), value, fill="white")
    return image


def as_upload(image: Image.Image, name: str = "stamp.png") -> FileStorage:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return FileStorage(stream=buffer, filename=name, content_type="image/png")


def _user(username: str, role: str, circle: str | None = None) -> User:
    user = User(username=username, email=f"{username}@example.com", role=role, postal_circle=circle)
    user.set_password(DEMO_PASSWORD)
    db.session.add(user)
    return user


def register_cli(app: Flask) -> None:
    @app.cli.command("import-feeds")
    def import_feeds():
        """Fetch new stamp releases and philately news from India Post and RSS feeds."""
        from .services.feeds import import_all

        failed = False
        for r in import_all():
            if r.error:
                failed = True
                click.echo(f"{r.source}: FAILED ({r.error})")
            else:
                click.echo(f"{r.source}: {r.added} new, {r.updated} updated")
        if failed:
            raise SystemExit(1)

    @app.cli.command("seed")
    def seed():
        """Insert demo data (only into an empty database)."""
        if db.session.scalar(select(User.id).limit(1)) is not None:
            raise click.ClickException(
                "Database already has users; seed only runs on an empty one."
            )

        circle = _user("ap_circle", ROLE_CIRCLE, "Andhra Pradesh")
        alice = _user("alice", ROLE_COLLECTOR)
        bob = _user("bob", ROLE_COLLECTOR)
        alice.full_name, alice.phone = "Alice Rao", "+91 98480 12345"
        alice.address = "4-12 Benz Circle, Vijayawada 520010"
        alice.bio = "Collecting Andhra Pradesh commemoratives and temple architecture."
        bob.full_name, bob.phone = "Bob Kumar", "+91 90000 54321"
        bob.address = "Balewadi High Street, Pune 411045"
        db.session.commit()

        # Alice holds an NPDA account before the issues come out, so each new issue below is
        # reserved for her automatically (the standing order in action).
        npda_svc.open_account(
            alice,
            {
                "home_circle": "Andhra Pradesh",
                "copies_per_issue": "1",
                "scope": "home",
                "initial_deposit": "500",
            },
        )

        today = date.today()
        issues = []
        for i, (title, value) in enumerate(
            [("Lepakshi Temple", "5.00"), ("Kuchipudi Dance", "10.00"), ("Araku Coffee", "25.00")]
        ):
            stamp = draw_stamp(100 + i, title, f"Rs {value}")
            issues.append(
                svc.create_issue(
                    circle,
                    {
                        "title": title,
                        "issue_date": (today - timedelta(days=400 * i)).isoformat(),
                        "denomination": value,
                        "description": f"Commemorative issue: {title}.",
                        "prebook_available": 500,
                    },
                    as_upload(stamp),
                )
            )

        db.session.add_all(
            [
                Event(
                    circle=circle,
                    name="Stamp Mela 2027",
                    date=today + timedelta(days=30),
                    location="Vijayawada GPO",
                    description="Exhibition and swap meet.",
                ),
                Event(
                    circle=circle,
                    name="Philately workshop for schools",
                    date=today + timedelta(days=12),
                    location="Tirupati HPO",
                    description="Intro to collecting for students.",
                ),
                CircleUpdate(
                    circle=circle,
                    kind="newRelease",
                    title="Lepakshi Temple commemorative stamp released",
                    content="Available at all head post offices in the circle and for "
                    "pre-booking in the Stamp Store.",
                ),
                CircleUpdate(
                    circle=circle,
                    kind="cancellation",
                    title="Special cancellation for Araku Coffee day",
                    content="A pictorial cancellation will be used at Araku Valley SO.",
                ),
            ]
        )

        # 1. Matches its official issue exactly -> verified
        lepakshi = issues[0]
        lepakshi_listing = svc.create_listing(
            alice,
            {
                "title": "Lepakshi Temple, mint",
                "year": lepakshi.issue_date.year,
                "price": "40",
                "quantity": 3,
                "issue_id": lepakshi.id,
                "description": "Mint, never hinged.",
            },
            as_upload(draw_stamp(100, lepakshi.title, "Rs 5.00")),
        )
        # 2. Claims an issue but the photo is a different stamp -> flagged
        kuchipudi = issues[1]
        svc.create_listing(
            alice,
            {
                "title": "Kuchipudi Dance, used",
                "year": kuchipudi.issue_date.year,
                "price": "15",
                "quantity": 1,
                "issue_id": kuchipudi.id,
            },
            as_upload(draw_stamp(999, "Kuchipudi?", "Rs 10")),
        )
        # 3. Bob re-uses Alice's photo -> flagged as a duplicate
        svc.create_listing(
            bob,
            {
                "title": "Lepakshi Temple",
                "year": lepakshi.issue_date.year,
                "price": "20",
                "quantity": 1,
            },
            as_upload(draw_stamp(100, lepakshi.title, "Rs 5.00").resize((240, 288))),
        )
        # 4. Ordinary listing with no official issue -> duplicate check only
        svc.create_listing(
            bob,
            {
                "title": "1948 Gandhi 1.5 anna (replica)",
                "year": 1948,
                "price": "120",
                "quantity": 2,
                "description": "Album replica for display.",
            },
            as_upload(draw_stamp(7, "Gandhi", "1.5a")),
        )

        # Bob buys the Lepakshi stamp from Alice (a sale, a notification, and a collection item).
        svc.place_order(
            bob, lepakshi_listing.id, 1, bob.address, {"name": bob.full_name, "phone": bob.phone}
        )

        # Forums, follows and course progress, so the community pages aren't empty.
        alice_id = alice.id  # read before building objects, to avoid a mid-build autoflush
        thread = ForumThread(
            author=bob,
            category="identification",
            title="Is my 1948 Gandhi 1.5 anna genuine?",
            body="Found it in my grandfather's album. The perforations look a little uneven. "
            "What should I check?",
        )
        db.session.add(thread)
        db.session.add_all(
            [
                ForumReply(
                    thread=thread,
                    author=alice,
                    body="Compare the watermark and perforation gauge with a catalogue. "
                    "The 1948 set was printed by Courvoisier in Switzerland, so the print "
                    "quality should be very sharp.",
                ),
                ForumThread(
                    author=circle,
                    category="new-issues",
                    title="Lepakshi Temple stamp now available",
                    body="Pre-book it in the Stamp Store, or open an NPDA account to get "
                    "every new Andhra Pradesh issue automatically.",
                ),
                Follow(follower=bob, followed=alice),
                CourseProgress(user_id=alice_id, week="week1"),
                CourseProgress(user_id=alice_id, week="week2"),
            ]
        )
        db.session.commit()

        click.echo(
            "Seeded. Log in as ap_circle (postal circle), alice or bob "
            f"(collectors) with password '{DEMO_PASSWORD}'."
        )
