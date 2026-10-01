import threading
import time

import pytest

from controller.config import ControllerConfig
from controller.main import record_heartbeat, run_forever
from shared.models import Controller
from shared.workers import db_now, heartbeat_age


def test_defaults():
    c = ControllerConfig.from_env({})
    assert (c.interval, c.overdue_grace, c.scheduled_grace) == (5, 30, 60)
    assert c.controller_id


@pytest.mark.parametrize("env", [
    {"CONTROLLER_INTERVAL": "0"},
    {"OVERDUE_GRACE": "5"},
    {"SCHEDULED_GRACE": "0"},
    {"OVERDUE_GRACE": "abc"},
])
def test_bad_config_fails_fast(env):
    with pytest.raises(ValueError):
        ControllerConfig.from_env(env)


def test_heartbeat_upserts(session_factory):
    record_heartbeat(session_factory, "c1", "host")
    record_heartbeat(session_factory, "c1", "host")
    with session_factory() as s:
        rows = s.query(Controller).all()
        assert len(rows) == 1 and heartbeat_age(db_now(s), rows[0].last_heartbeat_at) < 3


def test_heartbeat_only_recorded_after_a_successful_pass(session_factory, monkeypatch):
    import controller.main as m
    cfg = ControllerConfig.from_env({"CONTROLLER_INTERVAL": "0.05"})
    calls = {"n": 0}

    def flaky_pass(*a, **k):
        calls["n"] += 1
        raise RuntimeError("recovery is broken")

    monkeypatch.setattr(m, "recover_once", flaky_pass)
    stop = threading.Event()
    t = threading.Thread(target=run_forever, args=(session_factory, cfg, None, stop))
    t.start()
    time.sleep(0.4)
    stop.set()
    t.join(timeout=2)
    assert calls["n"] >= 3 and not t.is_alive()  # loop survived every failure
    with session_factory() as s:
        assert s.query(Controller).count() == 0  # ...but never claimed to be healthy
