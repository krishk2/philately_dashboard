from datetime import timedelta

from werkzeug.datastructures import FileStorage

from app.models import FeedItem, Job, Prebooking, utcnow
from app.services import feeds, jobs, npda
from app.services import marketplace as svc

from .conftest import png


def subscriber(user, db):
    user.full_name, user.phone, user.address = "A B", "9876543210", "Pune"
    db.session.commit()
    npda.open_account(
        user,
        {
            "home_circle": "Andhra Pradesh",
            "copies_per_issue": "1",
            "scope": "home",
            "initial_deposit": "200",
        },
    )


def publish(circle):
    return svc.create_issue(
        circle,
        {
            "title": "Lepakshi",
            "issue_date": "2026-08-15",
            "denomination": "5",
            "prebook_available": 10,
        },
        FileStorage(stream=png(9), filename="l.png"),
    )


def test_publishing_queues_npda_job_when_not_inline(app, db, circle, alice):
    app.config["JOBS_INLINE"] = False
    subscriber(alice, db)
    issue = publish(circle)
    assert issue.npda_result is None
    job = db.session.query(Job).one()
    assert (job.kind, job.status, job.payload) == ("npda_fulfil", "queued", {"issue_id": issue.id})
    assert db.session.query(Prebooking).count() == 0

    assert jobs.run_due() == 1
    job = db.session.get(Job, job.id)
    assert job.status == "done" and job.result["booked"] == 1
    assert db.session.query(Prebooking).count() == 1


def test_inline_mode_runs_immediately(app, db, circle, alice):
    subscriber(alice, db)
    issue = publish(circle)
    assert issue.npda_result == {"booked": 1, "low_balance": 0, "sold_out": 0}


def test_a_job_is_only_claimed_once(app, db):
    app.config["JOBS_INLINE"] = False
    job = jobs.enqueue("expire_prebookings")
    assert jobs.run_job(job.id) is True
    assert jobs.run_job(job.id) is False  # already done


def test_failed_job_retries_with_backoff_then_fails(app, db, monkeypatch):
    app.config["JOBS_INLINE"] = False
    calls = []

    def boom(payload):
        calls.append(1)
        raise RuntimeError("source down")

    monkeypatch.setitem(jobs.HANDLERS, "import_feeds", boom)
    job = jobs.enqueue("import_feeds")
    jobs.run_job(job.id)
    job = db.session.get(Job, job.id)
    assert (
        job.status == "queued"
        and jobs._aware(job.run_after) > utcnow()
        and "source down" in job.last_error
    )
    for _ in range(jobs.MAX_ATTEMPTS - 1):
        db.session.get(Job, job.id).run_after = utcnow() - timedelta(seconds=1)
        db.session.commit()
        jobs.run_due()
    assert db.session.get(Job, job.id).status == "failed"
    assert len(calls) == jobs.MAX_ATTEMPTS


def test_periodic_imports_feeds_only_when_stale(app, db, monkeypatch):
    app.config["JOBS_INLINE"] = False
    app.config["FEED_REFRESH_HOURS"] = 6
    assert "import_feeds" in jobs.run_periodic()  # never imported
    assert "import_feeds" not in jobs.run_periodic()  # one already queued
    db.session.query(Job).delete()
    db.session.add(
        FeedItem(
            source="x",
            source_label="X",
            external_id="1",
            kind="news",
            title="t",
            url="https://e.org",
            fetched_at=utcnow(),
        )
    )
    db.session.commit()
    assert "import_feeds" not in jobs.run_periodic()  # fresh


def test_zero_parsed_items_is_an_error(app, db):
    source = feeds.Source("india_post", "India Post", feeds.INDIA_POST_URL, "india_post")
    result = feeds.import_source(source, fetcher=lambda url: "<html><body>redesigned</body></html>")
    assert result.error and "0 items" in result.error


def test_healthz(client):
    body = client.get("/healthz").get_json()
    assert body["status"] == "ok" and body["database"] == "ok" and body["queued_jobs"] == 0


def test_run_jobs_cli(app, db):
    result = app.test_cli_runner().invoke(args=["run-jobs"])
    assert result.exit_code == 0 and "expire_prebookings" in result.output


def test_badge_cache(app, db, alice, monkeypatch):
    from app.services import collectors

    app.config["BADGE_CACHE_SECONDS"] = 60
    collectors._BADGE_CACHE.clear()
    calls = []
    real = collectors.badges_for
    monkeypatch.setattr(collectors, "badges_for", lambda *a, **k: calls.append(1) or real(*a, **k))
    collectors.earned_badges(alice)
    collectors.earned_badges(alice)
    assert len(calls) == 1
    collectors._BADGE_CACHE.clear()
