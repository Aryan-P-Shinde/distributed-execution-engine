"""Redis wake-up channel.

Redis is NOT the queue and holds no job state. Postgres is the source of truth
and workers claim jobs from it. This list only tells idle workers "something
may be claimable, look now" so they don't have to wait out a poll interval.

Consequences, all deliberate:
- A lost token is harmless: workers also poll Postgres on a timer.
- A duplicate or stale token is harmless: the claim query finds nothing.
- A token does not name a job the worker must run. It just ends the wait.
"""
import logging
import time

import redis

WAKEUP_KEY = "engine:wakeup"

log = logging.getLogger(__name__)


def make_client(url: str) -> redis.Redis:
    """Client for notify(): short timeouts, a hung Redis must never stall job
    submission. Do NOT use it for wait(); see make_blocking_client."""
    return redis.Redis.from_url(
        url, decode_responses=True, socket_timeout=1, socket_connect_timeout=1
    )


def make_blocking_client(url: str, block_seconds: float) -> redis.Redis:
    """Client for wait(). Its socket timeout must exceed the longest BRPOP, or
    the read times out mid-block and looks like an outage."""
    return redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=block_seconds + 5,
        socket_connect_timeout=1,
    )


def notify(client: redis.Redis, job_id: int) -> bool:
    """Best-effort wake-up. Returns False (and logs) instead of raising."""
    try:
        client.lpush(WAKEUP_KEY, str(job_id))
        # bound growth if no worker is consuming
        client.ltrim(WAKEUP_KEY, 0, 999)
        return True
    except redis.RedisError:
        log.warning("wakeup notify failed for job %s", job_id, exc_info=True)
        return False


def wait(client: redis.Redis, timeout: float) -> bool:
    """Block up to `timeout` seconds for a wake-up token.

    `client` must come from make_blocking_client with block_seconds >= timeout.

    Returns True if a token arrived, False on timeout. If Redis is unreachable
    it sleeps out the timeout and returns False, so callers that poll Postgres
    afterwards never spin in a tight loop.
    """
    try:
        return client.brpop([WAKEUP_KEY], timeout=timeout) is not None
    except redis.RedisError:
        log.warning("wakeup wait failed", exc_info=True)
        time.sleep(timeout)
        return False
