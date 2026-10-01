import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass

from shared.spec import Runtime
from shared.workers import HealthThresholds


@dataclass(frozen=True)
class WorkerConfig:
    database_url: str
    redis_url: str
    worker_id: str
    runtimes: list[str]
    poll_interval: float
    health: HealthThresholds

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "WorkerConfig":
        env = os.environ if env is None else env
        runtimes = [r.strip() for r in env.get("WORKER_RUNTIMES", "python,node").split(",") if r.strip()]
        valid = {r.value for r in Runtime}
        unknown = [r for r in runtimes if r not in valid]
        if unknown or not runtimes:
            raise ValueError(f"WORKER_RUNTIMES must be a non-empty subset of {sorted(valid)}, got {runtimes}")
        poll = float(env.get("POLL_INTERVAL", "5"))
        if poll <= 0:
            raise ValueError("POLL_INTERVAL must be > 0")
        return cls(
            database_url=env.get("DATABASE_URL", "postgresql+psycopg://engine:engine@localhost:5432/engine"),
            redis_url=env.get("REDIS_URL", "redis://localhost:6379/0"),
            # hostname is the container id under compose, so replicas get unique ids
            worker_id=env.get("WORKER_ID") or socket.gethostname(),
            runtimes=runtimes,
            poll_interval=poll,
            health=HealthThresholds.from_env(env),
        )
