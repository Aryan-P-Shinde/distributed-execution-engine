from functools import lru_cache

import redis

from api.config import REDIS_URL
from shared.wakeup import make_client


@lru_cache(maxsize=1)
def _client() -> redis.Redis:
    return make_client(REDIS_URL)


def get_redis() -> redis.Redis:
    return _client()
