import threading
from dataclasses import dataclass
from typing import Protocol

from shared.spec import JobSpec
from shared.states import AttemptStatus

# What an executor is allowed to report. CANCELLED means "I was told to stop and
# did". WORKER_LOST is decided by the control plane, never by the process that
# ran the workload.
EXECUTOR_OUTCOMES = frozenset(
    {
        AttemptStatus.SUCCEEDED,
        AttemptStatus.FAILED,
        AttemptStatus.TIMED_OUT,
        AttemptStatus.CANCELLED,
        AttemptStatus.EXECUTOR_ERROR,
    }
)


@dataclass(frozen=True)
class ExecResult:
    status: AttemptStatus
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in EXECUTOR_OUTCOMES:
            raise ValueError(f"executor cannot report {self.status}")


class Executor(Protocol):
    def run(self, spec: JobSpec, cancel: threading.Event) -> ExecResult:
        """Run the workload to completion, timeout or cancellation.

        When `cancel` is set the executor must stop the workload promptly and
        return CANCELLED with whatever output it has.
        """
        ...
