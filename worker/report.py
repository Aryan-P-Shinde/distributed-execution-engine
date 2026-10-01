import logging

from sqlalchemy.orm import Session

from shared.models import Attempt, Job, utcnow
from shared.states import (
    AttemptStatus,
    JobStatus,
    assert_transition,
    decide_after_attempt,
)
from worker.claim import ClaimedJob
from worker.executor import ExecResult

log = logging.getLogger(__name__)

MAX_LOG_CHARS = 1_000_000
_OPEN_ATTEMPT = (AttemptStatus.ASSIGNED, AttemptStatus.RUNNING)


def _cap(text: str) -> str:
    if len(text) <= MAX_LOG_CHARS:
        return text
    return text[:MAX_LOG_CHARS] + "\n[output truncated]"


def _owns(job: Job, attempt: Attempt, worker_id: str, expected: JobStatus) -> bool:
    """Fencing check: is this worker still the current owner of this attempt?

    False means the control plane moved on (cancelled, or recovered the job
    after declaring this worker dead) while we were running.
    """
    return (
        job.status == expected
        and job.worker_id == worker_id
        and job.attempt_count == attempt.attempt_number
    )


def mark_running(session: Session, claimed: ClaimedJob, worker_id: str) -> bool:
    """SCHEDULED -> RUNNING. Returns False if we no longer own the job."""
    job = session.get(Job, claimed.job_id, with_for_update=True)
    attempt = session.get(Attempt, claimed.attempt_id)
    if not _owns(job, attempt, worker_id, JobStatus.SCHEDULED):
        session.rollback()
        return False
    assert_transition(JobStatus.SCHEDULED, JobStatus.RUNNING)
    now = utcnow()
    job.status = JobStatus.RUNNING
    job.started_at = now
    attempt.status = AttemptStatus.RUNNING
    attempt.started_at = now
    session.commit()
    return True


def finish_attempt(
    session: Session, claimed: ClaimedJob, worker_id: str, result: ExecResult
) -> JobStatus | None:
    """Record the outcome and move the job. Returns the job's new status.

    Returns None if the result was discarded because the attempt was already
    closed or the job is no longer ours; in that case the job is untouched.
    """
    job = session.get(Job, claimed.job_id, with_for_update=True)
    attempt = session.get(Attempt, claimed.attempt_id)
    if attempt.status not in _OPEN_ATTEMPT:
        log.warning("job %s attempt %s already closed, discarding result",
                    job.id, attempt.attempt_number)
        session.rollback()
        return None

    now = utcnow()
    attempt.status = result.status
    attempt.exit_code = result.exit_code
    attempt.stdout = _cap(result.stdout)
    attempt.stderr = _cap(result.stderr)
    attempt.error = result.error
    attempt.finished_at = now

    if not _owns(job, attempt, worker_id, JobStatus.RUNNING):
        # attempt history is still recorded, but the job belongs to someone else
        log.warning("job %s no longer owned by %s (status=%s), result recorded on attempt only",
                    job.id, worker_id, job.status)
        session.commit()
        return None

    nxt = decide_after_attempt(result.status, attempt.attempt_number, job.max_retries)
    assert_transition(JobStatus.RUNNING, nxt)
    job.status = nxt
    job.exit_code = result.exit_code
    n = attempt.attempt_number
    job.result_summary = (
        f"attempt {n}: {result.status.value}, exit {result.exit_code}, {result.duration_seconds:.1f}s"
    )
    if result.error:
        job.result_summary += f" ({result.error})"
    if nxt is JobStatus.QUEUED:
        job.worker_id = None
        job.queued_at = now
        job.result_summary += f"; retry {n}/{job.max_retries} queued"
    else:
        job.completed_at = now  # every non-requeue outcome is terminal
        if result.status is AttemptStatus.EXECUTOR_ERROR:
            job.result_summary += (
                f"; retries exhausted ({n} attempt{'s' if n != 1 else ''}, max_retries={job.max_retries})"
            )
    session.commit()
    return nxt
