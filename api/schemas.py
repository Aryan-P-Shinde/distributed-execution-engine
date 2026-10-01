from datetime import datetime

from pydantic import BaseModel, ConfigDict

from shared.spec import JobSpec
from shared.states import AttemptStatus, JobStatus, WorkerStatus


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: JobStatus
    spec: JobSpec
    attempt_count: int
    worker_id: str | None
    exit_code: int | None
    result_summary: str | None
    created_at: datetime
    queued_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None


class JobSummary(BaseModel):
    """List view: no spec body, no source files."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: JobStatus
    job_type: str
    runtime: str
    worker_id: str | None
    attempt_count: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class AttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    attempt_number: int
    worker_id: str | None
    status: AttemptStatus
    exit_code: int | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class LogsOut(BaseModel):
    job_id: int
    attempt_number: int | None
    attempt_status: AttemptStatus | None
    stdout: str
    stderr: str


class WorkerOut(BaseModel):
    id: str
    hostname: str
    runtimes: list[str]
    status: WorkerStatus
    started_at: datetime
    last_heartbeat_at: datetime
    seconds_since_heartbeat: float
    current_job_id: int | None


class StatsOut(BaseModel):
    """Aggregates for the dashboard. Every status key is always present."""

    total_jobs: int
    jobs: dict[JobStatus, int]
    workers: dict[WorkerStatus, int]
