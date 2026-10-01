from datetime import timedelta

from shared.models import Job, Worker
from shared.states import JobStatus
from shared.workers import db_now


def add_jobs(sf, **counts):
    with sf() as s:
        for status, n in counts.items():
            s.add_all(Job(spec={}, runtime="python", job_type="script", status=status) for _ in range(n))
        s.commit()


def add_worker(sf, wid, age=1.0, stopped=False):
    with sf() as s:
        now = db_now(s)
        s.add(Worker(id=wid, hostname="h", runtimes=["python"], started_at=now,
                     last_heartbeat_at=now - timedelta(seconds=age),
                     stopped_at=now if stopped else None))
        s.commit()


def test_empty_system_has_every_key_at_zero(client):
    body = client.get("/stats").json()
    assert body["total_jobs"] == 0
    assert set(body["jobs"]) == {s.value for s in JobStatus}
    assert set(body["workers"]) == {"ONLINE", "BUSY", "UNHEALTHY", "DEAD", "STOPPED"}
    assert all(v == 0 for v in [*body["jobs"].values(), *body["workers"].values()])


def test_job_counts_by_status_are_not_limited_by_pagination(client, session_factory):
    add_jobs(session_factory, QUEUED=3, RUNNING=2, SUCCEEDED=120, FAILED=4, TIMED_OUT=1, CANCELLED=5)
    body = client.get("/stats").json()
    assert body["total_jobs"] == 135
    assert (body["jobs"]["QUEUED"], body["jobs"]["RUNNING"], body["jobs"]["SUCCEEDED"]) == (3, 2, 120)
    assert len(client.get("/jobs").json()) == 50  # the list endpoint is paged; stats are not


def test_worker_counts_use_derived_health(client, session_factory):
    add_worker(session_factory, "a", age=1)
    add_worker(session_factory, "b", age=1)
    add_worker(session_factory, "slow", age=20)
    add_worker(session_factory, "gone", age=60)
    add_worker(session_factory, "bye", age=1, stopped=True)
    with session_factory() as s:
        s.add(Job(spec={}, runtime="python", job_type="script", status=JobStatus.RUNNING, worker_id="a"))
        s.commit()
    w = client.get("/stats").json()["workers"]
    assert w == {"ONLINE": 1, "BUSY": 1, "UNHEALTHY": 1, "DEAD": 1, "STOPPED": 1}
