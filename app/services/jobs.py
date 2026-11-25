"""A small database-backed job queue and the periodic tasks.

Why not Celery/RQ? They need Redis or RabbitMQ. This app only needs a handful of background
jobs, and the database is already there, so jobs are rows in the `jobs` table.

* `enqueue()` adds a job. With JOBS_INLINE on (development, tests, or a deployment without a
  worker) the job also runs immediately, so nothing waits forever.
* `flask worker` loops: claims due jobs and runs the periodic tasks (expire unpaid pre-bookings,
  refresh imported feeds).
* `flask run-jobs` does one pass and exits, for platforms that prefer a scheduled task (cron,
  AWS EventBridge running an ECS task).
"""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import func, select, update

from ..extensions import db
from ..models import JOB_DONE, JOB_FAILED, JOB_QUEUED, JOB_RUNNING, Issue, Job, utcnow

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


# ---------- handlers ----------


def _npda_fulfil(payload: dict) -> dict:
    from .npda import fulfil_new_issue

    issue = db.session.get(Issue, payload["issue_id"])
    if issue is None:
        return {"skipped": "issue deleted"}
    return fulfil_new_issue(issue)


def _import_feeds(payload: dict) -> dict:
    from .feeds import import_all

    results = import_all()
    failed = [r.source for r in results if r.error]
    if failed:
        # Raise so the job is retried and shows up as failed if it keeps happening.
        raise RuntimeError(f"feed import failed for: {', '.join(failed)}")
    return {r.source: {"added": r.added, "updated": r.updated} for r in results}


def _expire_prebookings(payload: dict) -> dict:
    from .marketplace import expire_stale_prebookings

    return {"expired": expire_stale_prebookings()}


HANDLERS: dict[str, Callable[[dict], dict]] = {
    "npda_fulfil": _npda_fulfil,
    "import_feeds": _import_feeds,
    "expire_prebookings": _expire_prebookings,
}


# ---------- queue ----------


def enqueue(kind: str, payload: dict | None = None) -> Job:
    if kind not in HANDLERS:
        raise ValueError(f"unknown job kind {kind!r}")
    job = Job(kind=kind, payload=payload or {}, status=JOB_QUEUED, run_after=utcnow())
    db.session.add(job)
    db.session.commit()
    if current_app.config.get("JOBS_INLINE"):
        run_job(job.id)
        db.session.refresh(job)
    return job


def _claim(job_id: int) -> bool:
    claimed = db.session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JOB_QUEUED)
        .values(status=JOB_RUNNING, attempts=Job.attempts + 1)
        .execution_options(synchronize_session=False)
    ).rowcount
    db.session.commit()
    return bool(claimed)


def run_job(job_id: int) -> bool:
    """Run one job if this process manages to claim it. Returns True if it ran."""
    if not _claim(job_id):
        return False
    job = db.session.get(Job, job_id)
    db.session.refresh(job)
    try:
        result = HANDLERS[job.kind](job.payload or {})
    except Exception as exc:  # noqa: BLE001 - any failure is recorded and retried
        db.session.rollback()
        job = db.session.get(Job, job_id)
        job.last_error = "".join(traceback.format_exception_only(exc)).strip()[:2000]
        if job.attempts >= MAX_ATTEMPTS:
            job.status = JOB_FAILED
            job.finished_at = utcnow()
            log.error("Job %s (%s) failed permanently: %s", job.id, job.kind, job.last_error)
        else:
            job.status = JOB_QUEUED
            job.run_after = utcnow() + timedelta(seconds=30 * 2**job.attempts)
            log.warning("Job %s (%s) failed, will retry: %s", job.id, job.kind, job.last_error)
        db.session.commit()
        return True
    job.status = JOB_DONE
    job.result = result
    job.finished_at = utcnow()
    db.session.commit()
    return True


def run_due(limit: int = 20) -> int:
    """Run up to `limit` jobs whose time has come. Returns how many ran."""
    due = db.session.scalars(
        select(Job.id)
        .where(Job.status == JOB_QUEUED, Job.run_after <= utcnow())
        .order_by(Job.run_after, Job.id)
        .limit(limit)
    ).all()
    return sum(run_job(job_id) for job_id in due)


def queue_depth() -> int:
    return db.session.scalar(select(func.count(Job.id)).where(Job.status == JOB_QUEUED)) or 0


# ---------- periodic tasks ----------


def _aware(value: datetime | None) -> datetime | None:
    # SQLite returns naive datetimes; treat them as UTC.
    from datetime import UTC

    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def run_periodic() -> list[str]:
    """Queue the periodic tasks that are due. Safe to call often and from several workers:
    feed imports are idempotent and expiring holds is race-safe."""
    from .feeds import last_import

    queued = []
    enqueue("expire_prebookings")
    queued.append("expire_prebookings")

    hours = current_app.config.get("FEED_REFRESH_HOURS", 6)
    last = _aware(last_import())
    pending = db.session.scalar(
        select(func.count(Job.id)).where(
            Job.kind == "import_feeds", Job.status.in_((JOB_QUEUED, JOB_RUNNING))
        )
    )
    if hours and not pending and (last is None or utcnow() - last > timedelta(hours=hours)):
        enqueue("import_feeds")
        queued.append("import_feeds")
    return queued


def run_once() -> dict:
    queued = run_periodic()
    ran = run_due()
    return {"queued": queued, "ran": ran}


def work_forever(
    poll_seconds: float = 5.0, periodic_seconds: float = 60.0
) -> None:  # pragma: no cover
    log.info("Worker started")
    next_periodic = 0.0
    while True:
        try:
            if time.monotonic() >= next_periodic:
                run_periodic()
                next_periodic = time.monotonic() + periodic_seconds
            if not run_due():
                time.sleep(poll_seconds)
        except Exception:  # noqa: BLE001 - keep the worker alive, log and continue
            log.exception("Worker loop error")
            db.session.rollback()
            time.sleep(poll_seconds)
