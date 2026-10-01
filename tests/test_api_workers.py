from datetime import timedelta

from shared.models import Job, Worker
from shared.states import JobStatus
from shared.workers import db_now


def add_worker(session_factory, wid, age=0.0, runtimes=("python",), stopped=False):
    with session_factory() as s:
        now = db_now(s)
        s.add(Worker(id=wid, hostname=f"host-{wid}", runtimes=list(runtimes),
                     started_at=now - timedelta(minutes=5),
                     last_heartbeat_at=now - timedelta(seconds=age),
                     stopped_at=now if stopped else None))
        s.commit()


def add_active_job(session_factory, wid, status=JobStatus.RUNNING):
    with session_factory() as s:
        j = Job(spec={}, runtime="python", job_type="script", status=status, worker_id=wid)
        s.add(j)
        s.commit()
        return j.id


def by_id(client):
    return {w["id"]: w for w in client.get("/workers").json()}


def test_no_workers(client):
    assert client.get("/workers").json() == []


def test_statuses_across_the_lifecycle(client, session_factory):
    add_worker(session_factory, "fresh", age=1)
    add_worker(session_factory, "slow", age=20)
    add_worker(session_factory, "gone", age=45)
    add_worker(session_factory, "bye", age=1, stopped=True)
    w = by_id(client)
    assert w["fresh"]["status"] == "ONLINE"
    assert w["slow"]["status"] == "UNHEALTHY"
    assert w["gone"]["status"] == "DEAD"
    assert w["bye"]["status"] == "STOPPED"
    assert 0 <= w["fresh"]["seconds_since_heartbeat"] < 5
    assert w["gone"]["seconds_since_heartbeat"] >= 44


def test_busy_worker_reports_its_current_job(client, session_factory):
    add_worker(session_factory, "w1", age=1)
    add_worker(session_factory, "w2", age=1)
    jid = add_active_job(session_factory, "w1")
    w = by_id(client)
    assert w["w1"]["status"] == "BUSY" and w["w1"]["current_job_id"] == jid
    assert w["w2"]["status"] == "ONLINE" and w["w2"]["current_job_id"] is None


def test_scheduled_counts_as_holding_a_job(client, session_factory):
    add_worker(session_factory, "w1", age=1)
    jid = add_active_job(session_factory, "w1", status=JobStatus.SCHEDULED)
    assert by_id(client)["w1"]["current_job_id"] == jid


def test_finished_jobs_do_not_make_a_worker_busy(client, session_factory):
    add_worker(session_factory, "w1", age=1)
    add_active_job(session_factory, "w1", status=JobStatus.SUCCEEDED)
    add_active_job(session_factory, "w1", status=JobStatus.CANCELLED)
    assert by_id(client)["w1"]["status"] == "ONLINE"


def test_dead_worker_still_shows_the_job_it_was_holding(client, session_factory):
    """The inconsistent state dead-worker recovery (step 14) exists to resolve."""
    add_worker(session_factory, "w1", age=60)
    jid = add_active_job(session_factory, "w1")
    w = by_id(client)["w1"]
    assert w["status"] == "DEAD" and w["current_job_id"] == jid


def test_get_one_and_404(client, session_factory):
    add_worker(session_factory, "w1", age=1, runtimes=("python", "node"))
    body = client.get("/workers/w1").json()
    assert body["runtimes"] == ["python", "node"] and body["hostname"] == "host-w1"
    assert client.get("/workers/nope").status_code == 404
