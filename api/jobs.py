import redis
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db import get_session
from api.redis_client import get_redis
from api.schemas import AttemptOut, JobOut, JobSummary, LogsOut
from shared.models import Attempt, Job, utcnow
from shared.spec import JobSpec
from shared.states import AttemptStatus, JobStatus, assert_transition, is_terminal
from shared.wakeup import notify

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobOut, status_code=201)
def submit_job(
    spec: JobSpec,
    session: Session = Depends(get_session),
    wakeup: redis.Redis = Depends(get_redis),
) -> Job:
    """Persist the job and mark it QUEUED. Never executes anything.

    CREATED -> QUEUED happens in one transaction: a job is either committed
    as QUEUED or not stored at all. Workers claim from Postgres, so QUEUED in
    the DB *is* the enqueue. The Redis wake-up after commit is best-effort: if
    it fails the job is still durably queued and workers find it on their next
    poll, so submission still succeeds.
    """
    job = Job(
        spec=spec.model_dump(mode="json"),
        runtime=spec.runtime.value,
        job_type=spec.type.value,
        max_retries=spec.max_retries,
    )
    session.add(job)
    session.flush()
    assert_transition(job.status, JobStatus.QUEUED)
    job.status = JobStatus.QUEUED
    job.queued_at = utcnow()
    session.commit()
    notify(wakeup, job.id)
    return job


@router.get("", response_model=list[JobSummary])
def list_jobs(
    status: JobStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> list[Job]:
    stmt = select(Job).order_by(Job.id.desc()).limit(limit).offset(offset)
    if status is not None:
        stmt = stmt.where(Job.status == status)
    return list(session.scalars(stmt))


def _get_job(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, session: Session = Depends(get_session)) -> Job:
    return _get_job(session, job_id)


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: int, session: Session = Depends(get_session)) -> Job:
    """Cancel a job. The job becomes CANCELLED immediately.

    - QUEUED: it will simply never be claimed.
    - SCHEDULED: the claim is void; the worker sees it lost the job before start.
    - RUNNING: the owning worker notices within about a second, kills the
      container, and closes the attempt as CANCELLED. Until then the job already
      reads CANCELLED and any late result from the worker is discarded.

    Idempotent for already-cancelled jobs; 409 for jobs that finished otherwise.
    Row-locked so it serialises against a worker claiming or finishing the job.
    """
    job = session.get(Job, job_id, with_for_update=True)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    status = JobStatus(job.status)
    if status is JobStatus.CANCELLED:
        return job
    if is_terminal(status):
        raise HTTPException(status_code=409, detail=f"job already {status.value}")

    assert_transition(status, JobStatus.CANCELLED)
    now = utcnow()
    job.status = JobStatus.CANCELLED
    job.completed_at = now
    job.result_summary = job.result_summary or "cancelled by request"
    if job.attempts:
        latest = job.attempts[-1]
        # A claimed-but-never-started attempt has no worker to close it.
        # A RUNNING attempt is closed by its worker, which keeps partial logs.
        if latest.status == AttemptStatus.ASSIGNED:
            latest.status = AttemptStatus.CANCELLED
            latest.finished_at = now
    session.commit()
    return job


@router.get("/{job_id}/attempts", response_model=list[AttemptOut])
def get_attempts(job_id: int, session: Session = Depends(get_session)) -> list[Attempt]:
    job = _get_job(session, job_id)
    return list(job.attempts)


@router.get("/{job_id}/logs", response_model=LogsOut)
def get_logs(
    job_id: int,
    attempt: int | None = Query(None, ge=1, description="attempt number; default latest"),
    session: Session = Depends(get_session),
) -> LogsOut:
    """stdout/stderr of one attempt. Logs are stored when an attempt finishes,
    so a RUNNING attempt returns empty logs (no live streaming yet)."""
    job = _get_job(session, job_id)
    attempts = list(job.attempts)
    if attempt is None:
        chosen = attempts[-1] if attempts else None
    else:
        chosen = next((a for a in attempts if a.attempt_number == attempt), None)
        if chosen is None:
            raise HTTPException(status_code=404, detail="attempt not found")
    if chosen is None:
        return LogsOut(job_id=job.id, attempt_number=None, attempt_status=None, stdout="", stderr="")
    return LogsOut(
        job_id=job.id,
        attempt_number=chosen.attempt_number,
        attempt_status=chosen.status,
        stdout=chosen.stdout,
        stderr=chosen.stderr,
    )
