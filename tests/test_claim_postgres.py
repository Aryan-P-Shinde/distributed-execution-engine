"""Concurrency test against real Postgres: SKIP LOCKED must hand each job to
exactly one worker. Skipped when no Postgres is reachable.

    docker compose up -d postgres
    TEST_DATABASE_URL=postgresql+psycopg://engine:engine@localhost:5432/engine_test pytest
"""
import os
import threading

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from shared import models  # noqa: F401
from shared.db import Base
from shared.models import Attempt, Job, utcnow
from shared.spec import JobSpec
from shared.states import JobStatus
from worker.claim import claim_next_job

URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://engine:engine@localhost:5432/engine_test"
)


@pytest.fixture()
def pg():
    engine = create_engine(URL, pool_size=10)
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("no postgres reachable")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def test_concurrent_workers_never_double_claim(pg):
    n_jobs, n_workers = 60, 8
    spec = JobSpec(type="script", runtime="python", command="x").model_dump(mode="json")
    with pg() as s:
        s.add_all(Job(spec=spec, runtime="python", job_type="script",
                      status=JobStatus.QUEUED, queued_at=utcnow()) for _ in range(n_jobs))
        s.commit()

    claimed: dict[str, list[int]] = {}
    barrier = threading.Barrier(n_workers)

    def worker(wid: str):
        mine = []
        barrier.wait()
        while True:
            with pg() as s:
                c = claim_next_job(s, wid, ["python"])
            if c is None:
                break
            mine.append(c.job_id)
        claimed[wid] = mine

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(n_workers)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    all_ids = [j for ids in claimed.values() for j in ids]
    assert len(all_ids) == n_jobs
    assert len(set(all_ids)) == n_jobs, "a job was claimed twice"
    assert sum(1 for ids in claimed.values() if ids) > 1, "work was not spread"
    with pg() as s:
        assert s.query(Attempt).count() == n_jobs
        assert s.query(Job).filter(Job.status == "SCHEDULED").count() == n_jobs
