"""Feed importer tests. They run offline against fixtures saved from the real sources."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models import FeedItem
from app.services import feeds

FIXTURES = Path(__file__).parent / "fixtures"
INDIA_POST = (FIXTURES / "india_post_news.html").read_text(encoding="utf-8")
RAINBOW = (FIXTURES / "rainbow.rss").read_text(encoding="utf-8")


def fake_fetcher(url):
    if "postagestamps" in url:
        return INDIA_POST
    if "rainbow" in url:
        return RAINBOW
    raise AssertionError(f"unexpected url {url}")


def test_parse_india_post_releases_and_notices():
    items = feeds.parse_india_post(INDIA_POST)
    assert len(items) == 4
    first = items[0]
    assert first.kind == "release"
    assert first.title == "Centenary Year of Bihar Chamber of Commerce & Industries"
    assert first.denomination == Decimal("5")
    assert first.stamp_count == 1
    assert first.published_on == date(2026, 11, 19)
    assert first.url == "https://postagestamps.gov.in/NewsPage.aspx?uid=575"
    assert first.external_id == "575"

    multi = items[1]  # "Rs.5 (4)": a set of four stamps
    assert (multi.title, multi.stamp_count) == ("50 Years of ICMR-NIMR", 4)

    notice = items[2]
    assert notice.kind == "news"
    assert "Dhai Akhar" in notice.title


def test_parse_rss_strips_html_and_scripts():
    items = feeds.parse_rss(RAINBOW)
    assert [i.title for i in items] == ["Rainbow November 2026", "Rainbow October 2026"]
    assert items[0].published_on == date(2026, 11, 3)
    assert "<" not in items[0].summary and "alert" not in items[0].summary
    assert "PCI Stamp Show 2026 & new issues" in items[0].summary


def test_import_is_idempotent(app, db):
    first = feeds.import_all(fetcher=fake_fetcher)
    assert {r.source: r.added for r in first} == {"india_post": 4, "rainbow": 2}
    second = feeds.import_all(fetcher=fake_fetcher)
    assert {r.source: (r.added, r.updated) for r in second} == {
        "india_post": (0, 4),
        "rainbow": (0, 2),
    }
    assert db.session.query(FeedItem).count() == 6


def test_one_failing_source_does_not_stop_others(app, db):
    def flaky(url):
        if "rainbow" in url:
            raise ConnectionError("down")
        return fake_fetcher(url)

    results = {r.source: r for r in feeds.import_all(fetcher=flaky)}
    assert results["rainbow"].error.startswith("ConnectionError")
    assert results["india_post"].added == 4


def test_extra_rss_sources_from_config(app, db):
    app.config["FEED_EXTRA_RSS"] = "event|PCI Shows|https://example.org/events.rss, bogus"
    sources = feeds.configured_sources()
    extra = sources[-1]
    assert (extra.kind, extra.label, extra.url) == (
        "event",
        "PCI Shows",
        "https://example.org/events.rss",
    )
    assert len(sources) == 3  # malformed entry ignored


def test_robots_txt_is_respected(app, monkeypatch):
    class Resp:
        status_code = 200
        text = "User-agent: *\nDisallow: /"

    monkeypatch.setattr(feeds.requests, "get", lambda *a, **k: Resp())
    try:
        feeds.fetch("https://example.org/news")
    except PermissionError as exc:
        assert "robots.txt" in str(exc)
    else:
        raise AssertionError("expected PermissionError")


def test_home_page_shows_imported_headlines_with_source_links(client, app, db):
    feeds.import_all(fetcher=fake_fetcher)
    page = client.get("/home").get_data(as_text=True)
    assert "Centenary Year of Bihar Chamber of Commerce &amp; Industries" in page
    assert "https://postagestamps.gov.in/NewsPage.aspx?uid=575" in page
    assert "× 4 stamps" in page
    assert "Rainbow November 2026" in page
    assert "Last updated" in page
