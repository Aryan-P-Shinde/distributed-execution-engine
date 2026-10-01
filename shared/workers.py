"""Worker health: thresholds and status derivation.

Two rules keep this trustworthy:
1. One clock. Heartbeats are stamped with the database's now(), and age is
   measured against the database's now(). Worker and API host clocks never
   enter the comparison, so skew between machines cannot fake a death.
2. Status is derived at read time, not stored. Nothing has to notice a worker
   died in order for it to read as DEAD.
"""
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.states import WorkerStatus


@dataclass(frozen=True)
class HealthThresholds:
    heartbeat_interval: float = 5.0
    unhealthy_after: float = 15.0
    dead_after: float = 30.0

    def __post_init__(self) -> None:
        if self.heartbeat_interval <= 0:
            raise ValueError("heartbeat interval must be > 0")
        # one or two missed beats must not flip a healthy worker to UNHEALTHY
        if self.unhealthy_after < 2 * self.heartbeat_interval:
            raise ValueError("unhealthy_after must be at least 2x the heartbeat interval")
        if self.dead_after <= self.unhealthy_after:
            raise ValueError("dead_after must be greater than unhealthy_after")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "HealthThresholds":
        env = os.environ if env is None else env
        d = cls()
        return cls(
            heartbeat_interval=float(env.get("WORKER_HEARTBEAT_INTERVAL", d.heartbeat_interval)),
            unhealthy_after=float(env.get("WORKER_UNHEALTHY_AFTER", d.unhealthy_after)),
            dead_after=float(env.get("WORKER_DEAD_AFTER", d.dead_after)),
        )


def as_utc(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; Postgres aware ones. Both mean UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def db_now(session: Session) -> datetime:
    return as_utc(session.scalar(select(func.now())))


def heartbeat_age(now: datetime, last_heartbeat_at: datetime) -> float:
    return max(0.0, (as_utc(now) - as_utc(last_heartbeat_at)).total_seconds())


def derive_status(
    *, stopped: bool, age_seconds: float, busy: bool, thresholds: HealthThresholds
) -> WorkerStatus:
    if stopped:
        return WorkerStatus.STOPPED
    if age_seconds >= thresholds.dead_after:
        return WorkerStatus.DEAD
    if age_seconds >= thresholds.unhealthy_after:
        return WorkerStatus.UNHEALTHY
    return WorkerStatus.BUSY if busy else WorkerStatus.ONLINE
