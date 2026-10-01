from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db import Base
from shared.states import AttemptStatus, JobStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[JobStatus] = mapped_column(
        String(16), default=JobStatus.CREATED, index=True
    )
    spec: Mapped[dict] = mapped_column(JSON)
    # denormalised from spec: the scheduler filters on these without parsing JSON
    runtime: Mapped[str] = mapped_column(String(16))
    job_type: Mapped[str] = mapped_column(String(16))
    max_retries: Mapped[int] = mapped_column(Integer, default=0)

    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    attempts: Mapped[list["Attempt"]] = relationship(
        back_populates="job", order_by="Attempt.attempt_number"
    )


class Worker(Base):
    """One row per worker id. Holds facts only (when it started, when it last
    checked in, whether it shut down cleanly). Health is derived at read time
    from these plus the DB clock; see shared/workers.py."""

    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255))
    runtimes: Mapped[list] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Controller(Base):
    """Heartbeat of a recovery controller replica. Written only after a
    successful recovery pass, so a stale row means recovery is not working."""

    __tablename__ = "controllers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    # plain string, not an FK: the workers table arrives with heartbeats (step 12)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[AttemptStatus] = mapped_column(String(16), default=AttemptStatus.ASSIGNED)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout: Mapped[str] = mapped_column(Text, default="")
    stderr: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[Job] = relationship(back_populates="attempts")
