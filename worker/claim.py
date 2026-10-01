from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.models import Attempt, Job
from shared.spec import JobSpec
from shared.states import AttemptStatus, JobStatus, assert_transition


@dataclass(frozen=True)
class ClaimedJob:
    job_id: int
    attempt_id: int
    attempt_number: int
    spec: JobSpec


def claim_next_job(
    session: Session, worker_id: str, runtimes: list[str]
) -> ClaimedJob | None:
    """Atomically claim the oldest QUEUED job this worker can run.

    QUEUED -> SCHEDULED and the new Attempt row are committed together.
    FOR UPDATE SKIP LOCKED means concurrent workers never block on each other
    and never receive the same row: a locked row is skipped, not waited on.
    """
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.QUEUED, Job.runtime.in_(runtimes))
        .order_by(Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = session.scalars(stmt).first()
    if job is None:
        session.rollback()
        return None

    assert_transition(JobStatus(job.status), JobStatus.SCHEDULED)
    job.status = JobStatus.SCHEDULED
    job.worker_id = worker_id
    job.attempt_count += 1
    attempt = Attempt(
        job_id=job.id,
        attempt_number=job.attempt_count,
        worker_id=worker_id,
        status=AttemptStatus.ASSIGNED,
    )
    session.add(attempt)
    session.flush()
    claimed = ClaimedJob(
        job_id=job.id,
        attempt_id=attempt.id,
        attempt_number=attempt.attempt_number,
        spec=JobSpec.model_validate(job.spec),
    )
    session.commit()
    return claimed
