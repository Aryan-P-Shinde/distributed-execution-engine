from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db import get_session
from api.schemas import WorkerOut
from shared.models import Job, Worker
from shared.states import JobStatus
from shared.workers import HealthThresholds, db_now, derive_status, heartbeat_age

router = APIRouter(prefix="/workers", tags=["workers"])

_ACTIVE = (JobStatus.SCHEDULED.value, JobStatus.RUNNING.value)


def get_thresholds() -> HealthThresholds:
    return HealthThresholds.from_env()


def active_jobs(session: Session) -> dict[str, int]:
    """worker_id -> id of the job it currently holds (lowest id if several)."""
    rows = session.execute(
        select(Job.worker_id, Job.id)
        .where(Job.status.in_(_ACTIVE), Job.worker_id.is_not(None))
        .order_by(Job.id.desc())
    ).all()
    return {worker_id: job_id for worker_id, job_id in rows}


def to_worker_out(w: Worker, now, active: dict[str, int], t: HealthThresholds) -> WorkerOut:
    age = heartbeat_age(now, w.last_heartbeat_at)
    job_id = active.get(w.id)
    return WorkerOut(
        id=w.id,
        hostname=w.hostname,
        runtimes=list(w.runtimes),
        status=derive_status(
            stopped=w.stopped_at is not None, age_seconds=age, busy=job_id is not None, thresholds=t
        ),
        started_at=w.started_at,
        last_heartbeat_at=w.last_heartbeat_at,
        seconds_since_heartbeat=round(age, 1),
        current_job_id=job_id,
    )


@router.get("", response_model=list[WorkerOut])
def list_workers(
    session: Session = Depends(get_session),
    thresholds: HealthThresholds = Depends(get_thresholds),
) -> list[WorkerOut]:
    now = db_now(session)
    active = active_jobs(session)
    workers = session.scalars(select(Worker).order_by(Worker.id))
    return [to_worker_out(w, now, active, thresholds) for w in workers]


@router.get("/{worker_id}", response_model=WorkerOut)
def get_worker(
    worker_id: str,
    session: Session = Depends(get_session),
    thresholds: HealthThresholds = Depends(get_thresholds),
) -> WorkerOut:
    w = session.get(Worker, worker_id)
    if w is None:
        raise HTTPException(status_code=404, detail="worker not found")
    return to_worker_out(w, db_now(session), active_jobs(session), thresholds)
