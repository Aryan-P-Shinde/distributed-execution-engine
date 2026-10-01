"""Job and attempt state machines.

Design rules:
- A *job* is a logical unit of work. An *attempt* is one try at executing it.
- Job status answers "where is the work overall". Attempt status answers
  "what happened on this particular try".
- RETRY is not a job state. A retryable failure sends the job RUNNING -> QUEUED
  and the failed attempt stays in history. FAILED on a job is always terminal.
- Every status change goes through `assert_transition`. Nothing writes
  `job.status = ...` directly.
"""
from enum import StrEnum


class JobStatus(StrEnum):
    CREATED = "CREATED"      # row exists, not yet enqueued
    QUEUED = "QUEUED"        # waiting for a worker
    SCHEDULED = "SCHEDULED"  # claimed by a worker, container not started yet
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"        # terminal: retries exhausted or non-retryable
    TIMED_OUT = "TIMED_OUT"  # terminal
    CANCELLED = "CANCELLED"  # terminal


class AttemptStatus(StrEnum):
    ASSIGNED = "ASSIGNED"          # worker claimed it
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"              # non-zero exit / executor error
    TIMED_OUT = "TIMED_OUT"
    WORKER_LOST = "WORKER_LOST"    # heartbeat lost / overdue, outcome unknown
    EXECUTOR_ERROR = "EXECUTOR_ERROR"  # the execution environment failed, not the workload
    CANCELLED = "CANCELLED"


class WorkerStatus(StrEnum):
    """Derived from heartbeat age and active jobs at read time, never stored, so
    it cannot go stale. STOPPED is the one exception: a graceful shutdown is a
    fact the worker records about itself."""

    ONLINE = "ONLINE"
    BUSY = "BUSY"
    UNHEALTHY = "UNHEALTHY"
    DEAD = "DEAD"
    STOPPED = "STOPPED"


TERMINAL_JOB_STATES: frozenset[JobStatus] = frozenset(
    {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.TIMED_OUT, JobStatus.CANCELLED}
)

_J = JobStatus
JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    _J.CREATED: frozenset({_J.QUEUED, _J.CANCELLED}),
    _J.QUEUED: frozenset({_J.SCHEDULED, _J.CANCELLED}),
    # SCHEDULED -> QUEUED: worker died (or claim expired) before starting
    # SCHEDULED -> FAILED: worker lost before start and no retries left
    _J.SCHEDULED: frozenset({_J.RUNNING, _J.QUEUED, _J.FAILED, _J.CANCELLED}),
    # RUNNING -> QUEUED: retry after a retryable failure or lost worker
    _J.RUNNING: frozenset(
        {_J.SUCCEEDED, _J.FAILED, _J.TIMED_OUT, _J.QUEUED, _J.CANCELLED}
    ),
    _J.SUCCEEDED: frozenset(),
    _J.FAILED: frozenset(),
    _J.TIMED_OUT: frozenset(),
    _J.CANCELLED: frozenset(),
}


class InvalidTransition(Exception):
    def __init__(self, current: JobStatus, target: JobStatus):
        super().__init__(f"illegal job transition {current} -> {target}")
        self.current = current
        self.target = target


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    return target in JOB_TRANSITIONS[current]


def assert_transition(current: JobStatus, target: JobStatus) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(current, target)


def is_terminal(status: JobStatus) -> bool:
    return status in TERMINAL_JOB_STATES


def decide_after_attempt(
    outcome: AttemptStatus, attempts_used: int, max_retries: int
) -> JobStatus:
    """Given how an attempt ended, pick the job's next status.

    attempts_used counts the attempt that just ended. Total allowed attempts
    is max_retries + 1.

    Policy: retry only failures that are not the workload's fault.
    - SUCCEEDED       -> SUCCEEDED
    - CANCELLED       -> CANCELLED
    - TIMED_OUT       -> TIMED_OUT (deterministic, retrying wastes capacity)
    - FAILED          -> FAILED (non-zero exit / OOM is treated as deterministic)
    - WORKER_LOST     -> QUEUED while retries remain, else FAILED
    - EXECUTOR_ERROR  -> QUEUED while retries remain, else FAILED
    """
    if outcome is AttemptStatus.SUCCEEDED:
        return JobStatus.SUCCEEDED
    if outcome is AttemptStatus.CANCELLED:
        return JobStatus.CANCELLED
    if outcome is AttemptStatus.TIMED_OUT:
        return JobStatus.TIMED_OUT
    if outcome in (AttemptStatus.WORKER_LOST, AttemptStatus.EXECUTOR_ERROR):
        return JobStatus.QUEUED if attempts_used <= max_retries else JobStatus.FAILED
    if outcome is AttemptStatus.FAILED:
        return JobStatus.FAILED
    raise ValueError(f"attempt outcome {outcome} is not final")
