"""Dead-worker and overdue-job recovery.

Runs in its own process, not inside workers: recovery must survive the failures
it exists to handle. Any number of replicas may run; correctness comes from row
locks (SKIP LOCKED) plus re-checking every condition under the lock, not from
leader election.

A job is orphaned when it is SCHEDULED or RUNNING and one of these holds:
  - its worker row is missing, or the worker stopped cleanly while holding it
  - its worker has not heartbeated for dead_after seconds
  - RUNNING longer than timeout_seconds + overdue_grace (worker alive but wedged)
  - SCHEDULED for longer than scheduled_grace (claimed, never started)

Recovery closes the open attempt as WORKER_LOST, then either requeues the job or
fails it, per decide_after_attempt and the job's max_retries.
"""
import logging
from dataclasses import dataclass

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from controller.config import ControllerConfig
from shared.models import Attempt, Job, Worker
from shared.states import (
    AttemptStatus,
    JobStatus,
    assert_transition,
    decide_after_attempt,
)
from shared.wakeup import notify
from shared.workers import as_utc, db_now, heartbeat_age

log = logging.getLogger(__name__)

ACTIVE = (JobStatus.SCHEDULED.value, JobStatus.RUNNING.value)
_OPEN_ATTEMPT = (AttemptStatus.ASSIGNED, AttemptStatus.RUNNING)


@dataclass(frozen=True)
class Recovery:
    job_id: int
    attempt_number: int
    reason: str
    outcome: JobStatus  # QUEUED (retry) or FAILED (retries exhausted)


def orphan_reason(job: Job, worker: Worker | None, now, cfg: ControllerConfig) -> str | None:
    """Why this active job should be recovered, or None if it is fine."""
    if worker is None:
        return f"worker {job.worker_id!r} has no record"
    if worker.stopped_at is not None:
        return f"worker {worker.id} stopped while holding the job"
    age = heartbeat_age(now, worker.last_heartbeat_at)
    if age >= cfg.health.dead_after:
        return f"worker {worker.id} dead (no heartbeat for {age:.0f}s)"

    status = JobStatus(job.status)
    if status is JobStatus.RUNNING and job.started_at is not None:
        limit = float(job.spec.get("timeout_seconds", 60))
        elapsed = (as_utc(now) - as_utc(job.started_at)).total_seconds()
        if elapsed > limit + cfg.overdue_grace:
            return (f"overdue: running {elapsed:.0f}s, limit {limit:.0f}s "
                    f"+ {cfg.overdue_grace:.0f}s grace, worker {worker.id} wedged")
    if status is JobStatus.SCHEDULED and job.attempts:
        claimed = (as_utc(now) - as_utc(job.attempts[-1].created_at)).total_seconds()
        if claimed > cfg.scheduled_grace:
            return f"claimed {claimed:.0f}s ago by {worker.id} but never started"
    return None


def find_orphans(session: Session, cfg: ControllerConfig) -> list[tuple[int, str]]:
    """Cheap unlocked scan. Candidates are re-verified under a row lock before
    anything changes."""
    now = db_now(session)
    workers = {w.id: w for w in session.scalars(select(Worker))}
    found = []
    for job in session.scalars(select(Job).where(Job.status.in_(ACTIVE)).order_by(Job.id)):
        reason = orphan_reason(job, workers.get(job.worker_id), now, cfg)
        if reason:
            found.append((job.id, reason))
    return found


def recover_job(
    session_factory: sessionmaker[Session],
    job_id: int,
    cfg: ControllerConfig,
    notifier: redis.Redis | None = None,
) -> Recovery | None:
    """Recover one job. Returns None if another replica has it locked, or it is
    no longer an orphan (finished, cancelled, or its worker came back)."""
    with session_factory() as s:
        job = s.scalars(
            select(Job).where(Job.id == job_id).with_for_update(skip_locked=True)
        ).first()
        if job is None or job.status not in ACTIVE:
            return None
        now = db_now(s)
        worker = s.get(Worker, job.worker_id) if job.worker_id else None
        reason = orphan_reason(job, worker, now, cfg)
        if reason is None:
            return None

        current = JobStatus(job.status)
        attempt: Attempt | None = job.attempts[-1] if job.attempts else None
        if attempt is not None and attempt.status in _OPEN_ATTEMPT:
            attempt.status = AttemptStatus.WORKER_LOST
            attempt.finished_at = now
            attempt.error = reason

        nxt = decide_after_attempt(AttemptStatus.WORKER_LOST, job.attempt_count, job.max_retries)
        assert_transition(current, nxt)
        job.status = nxt
        n = job.attempt_count
        if nxt is JobStatus.QUEUED:
            job.worker_id = None
            job.queued_at = now
            job.result_summary = f"attempt {n} lost ({reason}); retry {n}/{job.max_retries} queued"
        else:
            job.completed_at = now
            job.exit_code = None
            job.result_summary = (
                f"attempt {n} lost ({reason}); retries exhausted "
                f"({n} attempt{'s' if n != 1 else ''}, max_retries={job.max_retries})"
            )
        s.commit()
        outcome = Recovery(job_id=job.id, attempt_number=n, reason=reason, outcome=nxt)

    log.info("controller event=recovered job=%s attempt=%s outcome=%s reason=%s",
             outcome.job_id, outcome.attempt_number, outcome.outcome.value, outcome.reason)
    if outcome.outcome is JobStatus.QUEUED and notifier is not None:
        notify(notifier, outcome.job_id)
    return outcome


def recover_once(
    session_factory: sessionmaker[Session],
    cfg: ControllerConfig,
    notifier: redis.Redis | None = None,
) -> list[Recovery]:
    with session_factory() as s:
        candidates = find_orphans(s, cfg)
    results = []
    for job_id, _reason in candidates:
        rec = recover_job(session_factory, job_id, cfg, notifier)
        if rec is not None:
            results.append(rec)
    return results
