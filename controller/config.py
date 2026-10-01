import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass

from shared.spec import EXECUTOR_BACKSTOP_SECONDS
from shared.workers import HealthThresholds


@dataclass(frozen=True)
class ControllerConfig:
    database_url: str
    redis_url: str
    controller_id: str
    interval: float          # seconds between recovery passes
    overdue_grace: float     # RUNNING past timeout + this => worker is wedged
    scheduled_grace: float   # SCHEDULED for longer than this => never started
    health: HealthThresholds

    def __post_init__(self) -> None:
        if self.interval <= 0:
            raise ValueError("CONTROLLER_INTERVAL must be > 0")
        # the container's own backstop kills the workload at timeout + backstop;
        # declaring the job overdue earlier would race it
        if self.overdue_grace <= EXECUTOR_BACKSTOP_SECONDS:
            raise ValueError(f"OVERDUE_GRACE must exceed the {EXECUTOR_BACKSTOP_SECONDS}s container backstop")
        if self.scheduled_grace <= 0:
            raise ValueError("SCHEDULED_GRACE must be > 0")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ControllerConfig":
        env = os.environ if env is None else env
        return cls(
            database_url=env.get("DATABASE_URL", "postgresql+psycopg://engine:engine@localhost:5432/engine"),
            redis_url=env.get("REDIS_URL", "redis://localhost:6379/0"),
            controller_id=env.get("CONTROLLER_ID") or socket.gethostname(),
            interval=float(env.get("CONTROLLER_INTERVAL", "5")),
            overdue_grace=float(env.get("OVERDUE_GRACE", "30")),
            scheduled_grace=float(env.get("SCHEDULED_GRACE", "60")),
            health=HealthThresholds.from_env(env),
        )
