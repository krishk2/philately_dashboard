"""Import philately headlines from external sources (run with `flask import-feeds`).

Sources
-------
india_post   postagestamps.gov.in/News.aspx, the Department of Posts' philately news page.
             It has no API or RSS, so the page is parsed. Its robots.txt allows crawling. Each
             entry is a sentence like "A Commemorative Postage Stamp of denomination of Rs.5
             on <title> has been released on 19.11.2026"; those become stamp releases, anything
             else becomes a news item.
rainbow      Rainbow Stamp News, an Indian philately e-bulletin, via its Blogger RSS feed.
extra        Any RSS/Atom feeds listed in FEED_EXTRA_RSS ("kind|label|url,...").

Ground rules: one polite request per source per run, a clear User-Agent, robots.txt honoured,
timeouts, and only headline facts are stored (title, date, denomination, link). Every item
links back to its source; no images or article text are copied. A failing source is logged and
skipped, so one broken site never stops the others.

indiapost.gov.in (where the Stamp Calendar lives) blocks automated requests, so it is not used.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

import feedparser
import requests
from bs4 import BeautifulSoup
from flask import current_app
from requests.utils import requote_uri
from sqlalchemy import select

from ..extensions import db
from ..models import FEED_EVENT, FEED_NEWS, FEED_RELEASE, FeedItem, utcnow

log = logging.getLogger(__name__)

USER_AGENT = "NationalPhilatelicCommunity/2.0 (philately news importer)"

INDIA_POST_URL = "https://postagestamps.gov.in/News.aspx"
RAINBOW_RSS = "https://rainbowstampnews.blogspot.com/feeds/posts/default?alt=rss&max-results=10"

RELEASE_RE = re.compile(
    r"denomination of\s+Rs\.?\s*(?P<value>[\d.]+)\s*(?:\((?P<count>\d+)\))?\s+on\s+"
    r"(?P<title>.+?)\s+has been released on\s+(?P<date>\d{1,2}\.\d{1,2}\.\d{4})",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")


@dataclass
class Item:
    external_id: str
    kind: str
    title: str
    url: str
    published_on: date | None = None
    denomination: Decimal | None = None
    stamp_count: int | None = None
    summary: str = ""
    image_url: str | None = None


@dataclass
class SourceResult:
    source: str
    added: int = 0
    updated: int = 0
    error: str | None = None
    items: list[Item] = field(default_factory=list)


# ---------- fetching ----------


def _allowed_by_robots(url: str, timeout: float) -> bool:
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        response = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    except requests.RequestException:
        return True  # robots.txt unreachable: treat as allowed, like most crawlers
    if response.status_code >= 400:
        return True
    parser = robotparser.RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def fetch(url: str) -> str:
    timeout = current_app.config["FEED_TIMEOUT"]
    if not _allowed_by_robots(url, timeout):
        raise PermissionError(f"robots.txt disallows {url}")
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    return response.text


# ---------- parsing ----------


def _parse_ddmmyyyy(text: str) -> date | None:
    match = DATE_RE.search(text or "")
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_india_post(page: str, base_url: str = INDIA_POST_URL) -> list[Item]:
    soup = BeautifulSoup(page, "html.parser")
    items = []
    for link in soup.select('a[id$="_aTitle"]'):
        text = " ".join(link.get_text().split())
        href = link.get("href") or ""
        if not text or not href:
            continue
        url = urljoin(base_url, href)
        uid = re.search(r"uid=(\d+)", href)
        external_id = uid.group(1) if uid else href
        match = RELEASE_RE.search(text)
        if match:
            try:
                value = Decimal(match["value"].rstrip("."))
            except InvalidOperation:
                value = None
            items.append(
                Item(
                    external_id=external_id,
                    kind=FEED_RELEASE,
                    title=match["title"].strip(),
                    url=url,
                    published_on=_parse_ddmmyyyy(match["date"]),
                    denomination=value,
                    stamp_count=int(match["count"]) if match["count"] else 1,
                    summary=text,
                )
            )
        else:
            items.append(
                Item(
                    external_id=external_id,
                    kind=FEED_NEWS,
                    title=text[:300],
                    url=url,
                    published_on=_parse_ddmmyyyy(text),
                    summary=text,
                )
            )
    return items


def parse_india_post_detail(page: str, base_url: str) -> str | None:
    """Image URL of the stamp on a release page (the 'Stamp' image, else the first upload)."""
    soup = BeautifulSoup(page, "html.parser")
    uploads = [
        img.get("src")
        for img in soup.find_all("img")
        if "uploads/" in (img.get("src") or "").lower()
        and (img.get("alt") or "").lower() != "banner"
    ]
    if not uploads:
        return None
    stamp = next((src for src in uploads if "stamp" in src.lower().rsplit("/", 1)[-1]), uploads[0])
    return requote_uri(urljoin(base_url, stamp))  # encode spaces etc. in file names


def _plain_text(markup: str, limit: int = 280) -> str:
    text = " ".join(BeautifulSoup(markup or "", "html.parser").get_text(" ").split())
    text = html.unescape(text)
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def parse_rss(content: str, kind: str = FEED_NEWS) -> list[Item]:
    feed = feedparser.parse(content)
    items = []
    for entry in feed.entries:
        link = entry.get("link") or ""
        title = " ".join((entry.get("title") or "").split())
        if not link or not title:
            continue
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        published = date(*stamp[:3]) if stamp else None
        items.append(
            Item(
                external_id=entry.get("id") or link,
                kind=kind,
                title=title[:300],
                url=link,
                published_on=published,
                summary=_plain_text(entry.get("summary", "")),
            )
        )
    return items


# ---------- sources ----------


@dataclass(frozen=True)
class Source:
    name: str
    label: str
    url: str
    parser: str  # "india_post" or "rss"
    kind: str = FEED_NEWS


def configured_sources() -> list[Source]:
    sources = [
        Source("india_post", "India Post", INDIA_POST_URL, "india_post"),
        Source("rainbow", "Rainbow Stamp News", RAINBOW_RSS, "rss", FEED_NEWS),
    ]
    for n, spec in enumerate(filter(None, current_app.config.get("FEED_EXTRA_RSS", "").split(","))):
        parts = [p.strip() for p in spec.split("|")]
        if len(parts) == 3 and parts[0] in (FEED_RELEASE, FEED_NEWS, FEED_EVENT):
            kind, label, url = parts
            sources.append(Source(f"extra{n + 1}", label[:64], url, "rss", kind))
        else:
            log.warning("Ignoring malformed FEED_EXTRA_RSS entry: %r", spec)
    return sources


def _save(source: Source, items: list[Item], result: SourceResult) -> None:
    existing = {
        row.external_id: row
        for row in db.session.scalars(select(FeedItem).where(FeedItem.source == source.name))
    }
    for item in items:
        row = existing.get(item.external_id)
        if row is None:
            row = FeedItem(source=source.name, external_id=item.external_id)
            db.session.add(row)
            existing[item.external_id] = row
            result.added += 1
        else:
            result.updated += 1
        row.source_label = source.label
        row.kind = item.kind
        row.title = item.title
        row.url = item.url
        row.published_on = item.published_on
        row.denomination = item.denomination
        row.stamp_count = item.stamp_count
        row.summary = item.summary
        if item.image_url:
            row.image_url = item.image_url
        row.fetched_at = utcnow()


def _add_release_images(source: Source, items: list[Item], fetcher) -> None:
    """Fetch the detail page of *new* releases (capped per run) to record the stamp image URL.
    Pages already imported aren't fetched again, so a run makes few extra requests."""
    known = set(
        db.session.scalars(
            select(FeedItem.external_id).where(
                FeedItem.source == source.name, FeedItem.image_url.is_not(None)
            )
        )
    )
    budget = current_app.config.get("FEED_DETAIL_FETCH_LIMIT", 10)
    for item in items:
        if budget <= 0:
            break
        if item.kind != FEED_RELEASE or item.external_id in known:
            continue
        budget -= 1
        try:
            item.image_url = parse_india_post_detail(fetcher(item.url), item.url)
        except Exception as exc:  # noqa: BLE001 - a missing image never fails the import
            log.info("No image for %s: %s", item.url, exc)


def import_source(source: Source, fetcher=fetch) -> SourceResult:
    result = SourceResult(source.name)
    try:
        content = fetcher(source.url)
        if source.parser == "india_post":
            items = parse_india_post(content, source.url)
            if not items:
                # The page loaded but nothing parsed: the site's layout probably changed.
                raise ValueError("parsed 0 items; the page layout may have changed")
            _add_release_images(source, items, fetcher)
        else:
            items = parse_rss(content, source.kind)
        _save(source, items, result)
        db.session.commit()
        result.items = items
    except Exception as exc:  # noqa: BLE001 - one bad source must not stop the others
        db.session.rollback()
        result.error = f"{exc.__class__.__name__}: {exc}"[:300]
        log.warning("Feed import failed for %s: %s", source.name, result.error)
    return result


def import_all(fetcher=fetch) -> list[SourceResult]:
    return [import_source(source, fetcher) for source in configured_sources()]


def latest(kind: str, limit: int = 5) -> list[FeedItem]:
    return db.session.scalars(
        select(FeedItem)
        .where(FeedItem.kind == kind)
        .order_by(FeedItem.published_on.desc().nulls_last(), FeedItem.id.desc())
        .limit(limit)
    ).all()


def last_import() -> datetime | None:
    from sqlalchemy import func

    return db.session.scalar(select(func.max(FeedItem.fetched_at)))
