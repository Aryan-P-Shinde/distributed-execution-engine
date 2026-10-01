import pytest

from shared.workers import HealthThresholds
from worker.config import WorkerConfig


def test_defaults():
    c = WorkerConfig.from_env({})
    assert c.runtimes == ["python", "node"]
    assert c.poll_interval == 5.0
    assert c.worker_id  # falls back to hostname


def test_overrides():
    c = WorkerConfig.from_env({
        "WORKER_ID": "w9", "WORKER_RUNTIMES": " python ", "POLL_INTERVAL": "0.5",
        "DATABASE_URL": "postgresql+psycopg://x", "REDIS_URL": "redis://r:1/2",
    })
    assert (c.worker_id, c.runtimes, c.poll_interval) == ("w9", ["python"], 0.5)
    assert c.database_url == "postgresql+psycopg://x" and c.redis_url == "redis://r:1/2"


@pytest.mark.parametrize("env", [
    {"WORKER_RUNTIMES": "ruby"},
    {"WORKER_RUNTIMES": ""},
    {"WORKER_RUNTIMES": "python,,go"},
    {"POLL_INTERVAL": "0"},
    {"POLL_INTERVAL": "abc"},
])
def test_bad_config_fails_fast(env):
    with pytest.raises(ValueError):
        WorkerConfig.from_env(env)


def test_health_thresholds_default_and_override():
    assert WorkerConfig.from_env({}).health == HealthThresholds()
    c = WorkerConfig.from_env({"WORKER_HEARTBEAT_INTERVAL": "1", "WORKER_UNHEALTHY_AFTER": "3",
                               "WORKER_DEAD_AFTER": "6"})
    assert c.health.heartbeat_interval == 1


def test_inconsistent_health_thresholds_fail_fast():
    with pytest.raises(ValueError):
        WorkerConfig.from_env({"WORKER_HEARTBEAT_INTERVAL": "10", "WORKER_UNHEALTHY_AFTER": "12"})
