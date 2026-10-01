import logging
import threading

import redis
from sqlalchemy.orm import Session, sessionmaker

from shared.models import Job
from shared.states import AttemptStatus, JobStatus
from shared.wakeup import notify, wait
from worker.claim import ClaimedJob, claim_next_job
from worker.executor import ExecResult, Executor
from worker.report import finish_attempt, mark_running

log = logging.getLogger(__name__)

CANCEL_POLL_SECONDS = 1.0


def _watch_ownership(
    session_factory: sessionmaker[Session],
    claimed: ClaimedJob,
    worker_id: str,
    cancel: threading.Event,
    done: threading.Event,
    interval: float,
) -> None:
    """While the workload runs, poll Postgres and set `cancel` if this worker no
    longer owns the attempt (job cancelled, or reassigned by recovery)."""
    while not done.wait(interval):
        try:
            with session_factory() as s:
                job = s.get(Job, claimed.job_id)
                owned = (
                    job is not None
                    and job.status == JobStatus.RUNNING
                    and job.worker_id == worker_id
                    and job.attempt_count == claimed.attempt_number
                )
        except Exception:
            log.warning("worker=%s job=%s event=ownership_check_failed", worker_id,
                        claimed.job_id, exc_info=True)
            continue
        if not owned:
            log.info("worker=%s job=%s event=ownership_lost stopping workload",
                     worker_id, claimed.job_id)
            cancel.set()
            return


def run_once(
    session_factory: sessionmaker[Session],
    executor: Executor,
    worker_id: str,
    runtimes: list[str],
    cancel_poll_seconds: float = CANCEL_POLL_SECONDS,
    notifier: redis.Redis | None = None,
) -> bool:
    """Claim and process at most one job. Returns True if a job was claimed.

    Each phase uses its own short transaction. No transaction is held open
    while the workload runs.
    """
    with session_factory() as s:
        claimed = claim_next_job(s, worker_id, runtimes)
    if claimed is None:
        return False
    log.info("worker=%s job=%s attempt=%s event=claimed",
             worker_id, claimed.job_id, claimed.attempt_number)

    with session_factory() as s:
        if not mark_running(s, claimed, worker_id):
            log.warning("worker=%s job=%s event=lost_before_start", worker_id, claimed.job_id)
            return True

    cancel, done = threading.Event(), threading.Event()
    watcher = threading.Thread(
        target=_watch_ownership,
        args=(session_factory, claimed, worker_id, cancel, done, cancel_poll_seconds),
        name="cancel-watch",
        daemon=True,
    )
    watcher.start()
    try:
        result = executor.run(claimed.spec, cancel)
    except Exception as exc:  # executor bug or infra failure, not a workload failure
        log.exception("worker=%s job=%s event=executor_error", worker_id, claimed.job_id)
        # EXECUTOR_ERROR (not FAILED): the environment broke, not the workload,
        # so it is eligible for retry on another worker.
        result = ExecResult(
            status=AttemptStatus.EXECUTOR_ERROR,
            exit_code=None,
            stdout="",
            stderr="",
            duration_seconds=0.0,
            error=f"executor error: {exc!r}",
        )
    finally:
        done.set()
        watcher.join(timeout=5)

    with session_factory() as s:
        final = finish_attempt(s, claimed, worker_id, result)
    log.info("worker=%s job=%s attempt=%s event=finished attempt_status=%s job_status=%s",
             worker_id, claimed.job_id, claimed.attempt_number, result.status.value,
             final.value if final else "unchanged")
    if final is JobStatus.QUEUED and notifier is not None:
        notify(notifier, claimed.job_id)  # a retry is claimable now
    return True


def run_forever(
    session_factory: sessionmaker[Session],
    executor: Executor,
    worker_id: str,
    runtimes: list[str],
    wakeup: redis.Redis | None,
    poll_interval: float,
    stop: threading.Event,
    notifier: redis.Redis | None = None,
) -> None:
    """Drain the queue, then sleep until a wake-up token or the poll interval."""
    while not stop.is_set():
        try:
            worked = run_once(session_factory, executor, worker_id, runtimes, notifier=notifier)
        except Exception:
            log.exception("worker=%s event=loop_error", worker_id)
            stop.wait(poll_interval)
            continue
        if worked:
            continue
        if wakeup is not None:
            wait(wakeup, poll_interval)
        else:
            stop.wait(poll_interval)
