import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import get_session
from api.main import app
from api.redis_client import get_redis
from shared import models  # noqa: F401
from shared.db import Base


@pytest.fixture()
def session_factory(tmp_path):
    # file-backed (not :memory:) so each thread gets its own connection
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


class FakeRedis:
    """Just enough of redis-py for the wake-up channel."""

    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.down = False

    def _check(self):
        if self.down:
            raise redis.ConnectionError("fake redis is down")

    def lpush(self, key, value):
        self._check()
        self.lists.setdefault(key, []).insert(0, value)

    def ltrim(self, key, start, stop):
        self._check()

    def ping(self):
        self._check()
        return True


@pytest.fixture()
def fake_redis():
    return FakeRedis()


@pytest.fixture()
def client(session_factory, fake_redis):
    def override():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_redis] = lambda: fake_redis
    # no `with`: skip lifespan so create_all doesn't hit the real Postgres URL
    yield TestClient(app)
    app.dependency_overrides.clear()
