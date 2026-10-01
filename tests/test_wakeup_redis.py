"""Integration test against a real Redis. Skipped if none is reachable."""
import threading
import time

import pytest
import redis

from shared.wakeup import WAKEUP_KEY, make_blocking_client, make_client, notify, wait

URL = "redis://localhost:6379/15"


@pytest.fixture()
def r():
    c = make_client(URL)
    try:
        c.ping()
    except redis.RedisError:
        pytest.skip("no redis on localhost:6379")
    c.delete(WAKEUP_KEY)
    yield c
    c.delete(WAKEUP_KEY)


def test_notify_then_wait_returns_true(r):
    assert notify(r, 42)
    assert wait(r, timeout=1) is True


def test_wait_times_out(r):
    assert wait(r, timeout=1) is False


def test_stale_tokens_are_consumed_one_per_wait(r):
    notify(r, 1)
    notify(r, 2)
    assert wait(r, 1) and wait(r, 1)
    assert wait(r, 1) is False


def test_dead_redis_never_raises():
    dead = make_client("redis://localhost:1/0")
    assert notify(dead, 1) is False
    start = time.monotonic()
    assert wait(dead, 1) is False
    assert time.monotonic() - start >= 0.9  # sleeps, does not spin


def test_long_wait_is_woken_by_a_token_not_a_socket_timeout(r):
    """Regression: wait() longer than the notify client's 1s socket timeout used
    to error out after 1s and then sleep the whole interval."""
    blocking = make_blocking_client(URL, block_seconds=10)
    threading.Timer(1.5, notify, args=(r, 7)).start()
    start = time.monotonic()
    assert wait(blocking, timeout=10) is True
    assert time.monotonic() - start < 4
