"""Listing verification: runs independent checks on a listing and stores a report.

Checks
------
duplicate_photo   Is this photo a near-copy of another seller's listing? (stolen photos are a
                  common marketplace scam)
official_issue    If the listing claims to be an official issue, does the photo match the postal
                  circle's reference image, and does the year match the issue date?
ai_vision         Optional. Does an AI vision model read the same year off the stamp, and does
                  it think the photo shows a postage stamp at all?

Each check returns pass / warn / fail / skipped. Any fail flags the listing, any warn sends it
for review, and a listing is "verified" only if at least one check actually passed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from flask import current_app
from sqlalchemy import select

from ..extensions import db
from ..models import LISTING_REMOVED, Listing, VerificationReport
from .imagehash import hamming_distance
from .vision import VisionUnavailable, analyse_stamp_image

PASS, WARN, FAIL, SKIPPED = "pass", "warn", "fail", "skipped"

STATUS_VERIFIED = "verified"
STATUS_REVIEW = "needs_review"
STATUS_FLAGGED = "flagged"
STATUS_UNVERIFIED = "unverified"


@dataclass
class CheckResult:
    name: str
    result: str
    detail: str


def check_duplicate_photo(listing: Listing) -> CheckResult:
    threshold = current_app.config["DUPLICATE_HASH_DISTANCE"]
    others = db.session.scalars(
        select(Listing).where(
            Listing.id != listing.id,
            Listing.seller_id != listing.seller_id,
            Listing.status != LISTING_REMOVED,
        )
    )
    # A linear scan is fine at this scale. For a large catalogue, a BK-tree or
    # multi-index hashing would find near neighbours without comparing every row.
    closest: tuple[int, Listing] | None = None
    for other in others:
        distance = hamming_distance(listing.image_hash, other.image_hash)
        if closest is None or distance < closest[0]:
            closest = (distance, other)

    if closest and closest[0] <= threshold:
        distance, other = closest
        return CheckResult(
            "duplicate_photo",
            FAIL,
            f"Photo is a near-copy of listing #{other.id} by another seller "
            f"(distance {distance}/64).",
        )
    return CheckResult("duplicate_photo", PASS, "No other seller uses this photo.")


def check_official_issue(listing: Listing) -> CheckResult:
    issue = listing.issue
    if issue is None:
        return CheckResult("official_issue", SKIPPED, "Listing is not linked to an official issue.")

    problems = []
    if listing.year != issue.issue_date.year:
        problems.append(f"year {listing.year} does not match issue year {issue.issue_date.year}")

    distance = hamming_distance(listing.image_hash, issue.reference_hash)
    match = current_app.config["REFERENCE_MATCH_DISTANCE"]
    warn = current_app.config["REFERENCE_WARN_DISTANCE"]

    if problems or distance > warn:
        if distance > warn:
            problems.append(f"photo differs from the reference image (distance {distance}/64)")
        return CheckResult("official_issue", FAIL, "; ".join(problems).capitalize() + ".")
    if distance > match:
        return CheckResult(
            "official_issue",
            WARN,
            f"Photo only loosely matches the reference image (distance {distance}/64).",
        )
    return CheckResult(
        "official_issue",
        PASS,
        f"Matches '{issue.title}' reference image (distance {distance}/64) and issue year.",
    )


def check_ai_vision(listing: Listing) -> CheckResult:
    config = current_app.config
    if not config.get("GEMINI_API_KEY"):
        return CheckResult(
            "ai_vision", SKIPPED, "AI check not run: GEMINI_API_KEY is not configured."
        )
    try:
        from ..services.uploads import read_image  # local import: services import this module

        seen = analyse_stamp_image(
            read_image(listing.image_filename),
            api_key=config.get("GEMINI_API_KEY"),
            model=config["GEMINI_MODEL"],
            timeout=config["GEMINI_TIMEOUT"],
            filename=listing.image_filename,
        )
    except VisionUnavailable as exc:
        return CheckResult("ai_vision", SKIPPED, f"AI check not run: {exc}.")

    if not seen.is_postage_stamp:
        return CheckResult(
            "ai_vision", FAIL, f"Image does not look like a postage stamp. {seen.notes}"
        )
    if seen.year is not None and seen.year != listing.year:
        return CheckResult(
            "ai_vision", WARN, f"Model reads year {seen.year}, listing says {listing.year}."
        )
    found = ", ".join(
        f"{label} {value}"
        for label, value in (
            ("country", seen.country),
            ("year", seen.year),
            ("denomination", seen.denomination),
        )
        if value is not None
    )
    return CheckResult(
        "ai_vision", PASS, f"Consistent with listing ({found or 'no details read'})."
    )


CHECKS = (check_duplicate_photo, check_official_issue, check_ai_vision)


def summarise(results: list[CheckResult]) -> tuple[str, int]:
    """Turn check results into an overall status and a 0-100 score."""
    ran = [r for r in results if r.result != SKIPPED]
    if not ran:
        return STATUS_UNVERIFIED, 0

    points = {PASS: 1.0, WARN: 0.5, FAIL: 0.0}
    score = round(100 * sum(points[r.result] for r in ran) / len(ran))

    if any(r.result == FAIL for r in ran):
        return STATUS_FLAGGED, score
    if any(r.result == WARN for r in ran):
        return STATUS_REVIEW, score
    return STATUS_VERIFIED, score


def verify_listing(listing: Listing) -> VerificationReport:
    """Run every check and store (or replace) the listing's report. Caller commits."""
    results = [check(listing) for check in CHECKS]
    status, score = summarise(results)

    report = listing.verification or VerificationReport(listing=listing)
    report.status = status
    report.score = score
    report.checks = [asdict(r) for r in results]
    db.session.add(report)
    return report
