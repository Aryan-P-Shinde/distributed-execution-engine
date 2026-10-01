"""Concurrent controller replicas against real Postgres: every orphan is
recovered exactly once. Skipped when no Postgres is reachable."""
import os
import threading
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from controller.config import ControllerConfig
from controller.recovery import recover_once
from shared import models  # noqa: F401
from shared.db import Base
from shared.models import Attempt, Job, Worker
from shared.spec import JobSpec
from shared.states import AttemptStatus, JobStatus
from shared.workers import HealthThresholds, db_now

URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://engine:engine@localhost:5432/engine_test"
)
CFG = ControllerConfig(database_url="x", redis_url="x", controller_id="c", interval=1,
                       overdue_grace=30, scheduled_grace=60, health=HealthThresholds())


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


def test_concurrent_controllers_recover_each_orphan_once(pg):
    n_jobs, n_controllers = 40, 4
    spec = JobSpec(type="script", runtime="python", command="x").model_dump(mode="json")
    with pg() as s:
        now = db_now(s)
        s.add(Worker(id="dead", hostname="h", runtimes=["python"], started_at=now,
                     last_heartbeat_at=now - timedelta(seconds=120)))
        for _ in range(n_jobs):
            job = Job(spec=spec, runtime="python", job_type="script", status=JobStatus.RUNNING,
                      worker_id="dead", max_retries=2, attempt_count=1, started_at=now)
            s.add(job)
            s.flush()
            s.add(Attempt(job_id=job.id, attempt_number=1, worker_id="dead", status=AttemptStatus.RUNNING))
        s.commit()

    recovered: list[list[int]] = []
    barrier = threading.Barrier(n_controllers)

    def controller():
        barrier.wait()
        recovered.append([r.job_id for r in recover_once(pg, CFG)])

    threads = [threading.Thread(target=controller) for _ in range(n_controllers)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    all_ids = [j for ids in recovered for j in ids]
    assert len(all_ids) == n_jobs, "some orphans were missed"
    assert len(set(all_ids)) == n_jobs, "an orphan was recovered twice"
    assert sum(1 for ids in recovered if ids) > 1, "work was not shared between controllers"
    with pg() as s:
        assert s.query(Job).filter(Job.status == "QUEUED").count() == n_jobs
        assert s.query(Attempt).filter(Attempt.status == "WORKER_LOST").count() == n_jobs
        assert s.query(Attempt).count() == n_jobs  # recovery never invents attempts
