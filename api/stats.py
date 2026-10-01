from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.db import get_session
from api.schemas import StatsOut
from api.workers import active_jobs, get_thresholds, to_worker_out
from shared.models import Job, Worker
from shared.states import JobStatus, WorkerStatus
from shared.workers import HealthThresholds, db_now

router = APIRouter(tags=["stats"])


@router.get("/stats", response_model=StatsOut)
def stats(
    session: Session = Depends(get_session),
    thresholds: HealthThresholds = Depends(get_thresholds),
) -> StatsOut:
    """Counts computed in the database (jobs) and from derived health (workers).

    Clients must not count from GET /jobs: that endpoint is paginated.
    """
    jobs = {s: 0 for s in JobStatus}
    for status, n in session.execute(select(Job.status, func.count()).group_by(Job.status)):
        jobs[JobStatus(status)] = n

    now, active = db_now(session), active_jobs(session)
    workers = {s: 0 for s in WorkerStatus}
    for w in session.scalars(select(Worker)):
        workers[to_worker_out(w, now, active, thresholds).status] += 1

    return StatsOut(total_jobs=sum(jobs.values()), jobs=jobs, workers=workers)
