import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import OperationalError

from shared.models import Worker
from shared.states import WorkerStatus as W
from shared.workers import HealthThresholds, db_now, derive_status, heartbeat_age
from worker.heartbeat import DuplicateWorkerId, Heartbeat

T = HealthThresholds()  # 5 / 15 / 30


# ---- thresholds & derivation ------------------------------------------------

def test_default_thresholds():
    assert (T.heartbeat_interval, T.unhealthy_after, T.dead_after) == (5, 15, 30)


@pytest.mark.parametrize("kw", [
    {"heartbeat_interval": 0},
    {"heartbeat_interval": 5, "unhealthy_after": 9},   # < 2 beats of slack
    {"unhealthy_after": 15, "dead_after": 15},
    {"unhealthy_after": 15, "dead_after": 10},
])
def test_invalid_thresholds_rejected(kw):
    with pytest.raises(ValueError):
        HealthThresholds(**kw)


def test_thresholds_from_env():
    t = HealthThresholds.from_env({"WORKER_HEARTBEAT_INTERVAL": "1", "WORKER_UNHEALTHY_AFTER": "3",
                                   "WORKER_DEAD_AFTER": "6"})
    assert (t.heartbeat_interval, t.unhealthy_after, t.dead_after) == (1, 3, 6)
    assert HealthThresholds.from_env({}) == T


def status(age, busy=False, stopped=False):
    return derive_status(stopped=stopped, age_seconds=age, busy=busy, thresholds=T)


@pytest.mark.parametrize("age,expected", [
    (0, W.ONLINE), (14.9, W.ONLINE), (15.0, W.UNHEALTHY), (29.9, W.UNHEALTHY),
    (30.0, W.DEAD), (3600, W.DEAD),
])
def test_status_boundaries(age, expected):
    assert status(age) == expected


def test_busy_only_matters_while_healthy():
    assert status(1, busy=True) == W.BUSY
    assert status(20, busy=True) == W.UNHEALTHY
    assert status(60, busy=True) == W.DEAD


def test_stopped_beats_everything():
    assert status(0, stopped=True) == W.STOPPED
    assert status(999, busy=True, stopped=True) == W.STOPPED


def test_heartbeat_age_handles_naive_aware_and_skew():
    now = datetime(2026, 1, 1, 12, 0, 10, tzinfo=UTC)
    assert heartbeat_age(now, datetime(2026, 1, 1, 12, 0, 0)) == 10          # naive
    assert heartbeat_age(now, datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)) == 10
    assert heartbeat_age(now, datetime(2026, 1, 1, 12, 0, 20, tzinfo=UTC)) == 0  # future clamps


# ---- Heartbeat --------------------------------------------------------------

def hb(session_factory, worker_id="w1", thresholds=T):
    return Heartbeat(session_factory, worker_id, "host-a", ["python"], thresholds)


def age_of(session_factory, worker_id="w1"):
    with session_factory() as s:
        return heartbeat_age(db_now(s), s.get(Worker, worker_id).last_heartbeat_at)


def make_stale(session_factory, seconds, worker_id="w1"):
    with session_factory() as s:
        s.get(Worker, worker_id).last_heartbeat_at = db_now(s) - timedelta(seconds=seconds)
        s.commit()


def test_register_creates_row(session_factory):
    hb(session_factory).register()
    with session_factory() as s:
        w = s.get(Worker, "w1")
        assert (w.hostname, w.runtimes, w.stopped_at) == ("host-a", ["python"], None)
        assert w.started_at is not None and w.last_heartbeat_at is not None


def test_duplicate_live_worker_id_is_refused(session_factory):
    hb(session_factory).register()
    with pytest.raises(DuplicateWorkerId, match="another worker"):
        hb(session_factory).register()


def test_id_is_reusable_after_stop_after_crash_window_or_when_forced(session_factory):
    a = hb(session_factory)
    a.register()
    a.deregister()
    hb(session_factory).register()              # clean stop -> reusable immediately

    make_stale(session_factory, 16)             # crashed: reusable once unhealthy
    hb(session_factory).register()

    hb(session_factory).register(check_duplicate=False)


def test_reregister_clears_stopped(session_factory):
    a = hb(session_factory)
    a.register()
    a.deregister()
    a.register()
    with session_factory() as s:
        assert s.get(Worker, "w1").stopped_at is None


def test_beat_refreshes_timestamp(session_factory):
    h = hb(session_factory)
    h.register()
    make_stale(session_factory, 100)
    assert age_of(session_factory) > 90
    h.beat()
    assert age_of(session_factory) < 3


def test_beat_reregisters_if_row_vanished(session_factory):
    h = hb(session_factory)
    h.register()
    with session_factory() as s:
        s.delete(s.get(Worker, "w1"))
        s.commit()
    h.beat()
    with session_factory() as s:
        assert s.get(Worker, "w1") is not None


def test_deregister_marks_stopped(session_factory):
    h = hb(session_factory)
    h.register()
    h.deregister()
    with session_factory() as s:
        assert s.get(Worker, "w1").stopped_at is not None


FAST = HealthThresholds(heartbeat_interval=0.05, unhealthy_after=0.2, dead_after=0.5)


def test_run_loop_beats_until_stopped(session_factory):
    h = hb(session_factory, thresholds=FAST)
    h.register()
    make_stale(session_factory, 100)
    stop = threading.Event()
    t = threading.Thread(target=h.run, args=(stop,))
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline and age_of(session_factory) > 5:
        time.sleep(0.05)
    assert age_of(session_factory) < 5, "heartbeat thread never refreshed the row"
    stop.set()
    t.join(timeout=2)
    assert not t.is_alive()


def test_run_loop_survives_failures(session_factory):
    h = hb(session_factory, thresholds=FAST)
    calls = {"n": 0}
    real_beat = h.beat

    def flaky():
        calls["n"] += 1
        if calls["n"] <= 3:
            raise OperationalError("stmt", {}, Exception("db down"))
        real_beat()

    h.beat = flaky
    h.register()
    stop = threading.Event()
    t = threading.Thread(target=h.run, args=(stop,))
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline and calls["n"] < 6:
        time.sleep(0.05)
    stop.set()
    t.join(timeout=2)
    assert calls["n"] >= 6 and not t.is_alive()


def test_register_with_retry_waits_for_the_database(session_factory):
    attempts = {"n": 0}

    def flaky_factory():
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise OperationalError("stmt", {}, Exception("not up yet"))
        return session_factory()

    Heartbeat(flaky_factory, "w1", "h", ["python"], T).register_with_retry(give_up_after=5, delay=0.01)
    assert attempts["n"] == 3
    assert age_of(session_factory) < 3


def test_register_with_retry_gives_up(session_factory):
    def broken():
        raise OperationalError("stmt", {}, Exception("down"))

    with pytest.raises(OperationalError):
        Heartbeat(broken, "w1", "h", ["python"], T).register_with_retry(give_up_after=0.05, delay=0.01)


def test_duplicate_id_is_not_retried(session_factory):
    hb(session_factory).register()
    start = time.monotonic()
    with pytest.raises(DuplicateWorkerId):
        hb(session_factory).register_with_retry(give_up_after=30, delay=5)
    assert time.monotonic() - start < 2
