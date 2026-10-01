from shared.models import Attempt, Job

VALID = {"type": "script", "runtime": "python", "command": "python main.py",
         "files": {"main.py": "print('hi')"}}


def test_submit_returns_queued_job(client):
    r = client.post("/jobs", json=VALID)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "QUEUED"
    assert body["queued_at"] is not None
    assert body["attempt_count"] == 0
    assert body["spec"]["command"] == "python main.py"
    assert body["spec"]["timeout_seconds"] == 60  # default applied


def test_submit_persists(client, session_factory):
    job_id = client.post("/jobs", json=VALID).json()["id"]
    with session_factory() as s:
        job = s.get(Job, job_id)
        assert job is not None
        assert job.status == "QUEUED"
        assert job.runtime == "python"
        assert job.spec["files"] == {"main.py": "print('hi')"}


def test_submit_does_not_execute(client, session_factory):
    job_id = client.post("/jobs", json=VALID).json()["id"]
    with session_factory() as s:
        assert s.get(Job, job_id).worker_id is None
        assert s.query(Attempt).count() == 0


def test_invalid_spec_rejected_and_not_stored(client, session_factory):
    for bad in (
        {**VALID, "timeout_seconds": 0},
        {**VALID, "files": {"../evil": "x"}},
        {**VALID, "runtime": "ruby"},
        {**VALID, "privileged": True},
        {"type": "script"},
    ):
        assert client.post("/jobs", json=bad).status_code == 422
    with session_factory() as s:
        assert s.query(Job).count() == 0


def test_get_job_and_404(client):
    job_id = client.post("/jobs", json=VALID).json()["id"]
    assert client.get(f"/jobs/{job_id}").json()["id"] == job_id
    assert client.get("/jobs/9999").status_code == 404


def test_list_newest_first_with_filter_and_paging(client, session_factory):
    ids = [client.post("/jobs", json=VALID).json()["id"] for _ in range(3)]
    with session_factory() as s:
        s.get(Job, ids[0]).status = "SUCCEEDED"
        s.commit()

    listed = client.get("/jobs").json()
    assert [j["id"] for j in listed] == ids[::-1]
    assert "spec" not in listed[0]

    only_queued = client.get("/jobs", params={"status": "QUEUED"}).json()
    assert [j["id"] for j in only_queued] == [ids[2], ids[1]]

    page = client.get("/jobs", params={"limit": 1, "offset": 1}).json()
    assert [j["id"] for j in page] == [ids[1]]

    assert client.get("/jobs", params={"status": "NOPE"}).status_code == 422
    assert client.get("/jobs", params={"limit": 0}).status_code == 422


def test_attempts_empty_then_listed(client, session_factory):
    job_id = client.post("/jobs", json=VALID).json()["id"]
    assert client.get(f"/jobs/{job_id}/attempts").json() == []
    with session_factory() as s:
        s.add(Attempt(job_id=job_id, attempt_number=1, worker_id="w1", status="WORKER_LOST"))
        s.commit()
    got = client.get(f"/jobs/{job_id}/attempts").json()
    assert got[0]["worker_id"] == "w1" and got[0]["status"] == "WORKER_LOST"
    assert client.get("/jobs/9999/attempts").status_code == 404


def test_health_and_ready(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").status_code == 200


def test_submit_sends_wakeup(client, fake_redis):
    job_id = client.post("/jobs", json=VALID).json()["id"]
    assert fake_redis.lists["engine:wakeup"] == [str(job_id)]


def test_invalid_submit_sends_no_wakeup(client, fake_redis):
    client.post("/jobs", json={**VALID, "timeout_seconds": 0})
    assert fake_redis.lists == {}


def test_submit_succeeds_when_redis_is_down(client, fake_redis, session_factory):
    fake_redis.down = True
    r = client.post("/jobs", json=VALID)
    assert r.status_code == 201
    with session_factory() as s:
        assert s.get(Job, r.json()["id"]).status == "QUEUED"


def test_ready_degraded_when_redis_down(client, fake_redis):
    fake_redis.down = True
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
    assert r.json()["redis"] == "unavailable"


def test_ready_reports_redis_ok(client):
    assert client.get("/ready").json()["redis"] == "ok"


# ---- cancel ---------------------------------------------------------------

def test_cancel_queued_job(client, session_factory):
    jid = client.post("/jobs", json=VALID).json()["id"]
    r = client.post(f"/jobs/{jid}/cancel")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "CANCELLED" and body["completed_at"] is not None
    assert body["result_summary"] == "cancelled by request"


def test_cancelled_job_is_never_claimed(client, session_factory):
    from worker.claim import claim_next_job
    jid = client.post("/jobs", json=VALID).json()["id"]
    client.post(f"/jobs/{jid}/cancel")
    with session_factory() as s:
        assert claim_next_job(s, "w1", ["python"]) is None


def test_cancel_is_idempotent(client):
    jid = client.post("/jobs", json=VALID).json()["id"]
    first = client.post(f"/jobs/{jid}/cancel").json()
    second = client.post(f"/jobs/{jid}/cancel")
    assert second.status_code == 200
    # SQLite drops tzinfo on reload; Postgres does not
    assert second.json()["completed_at"].rstrip("Z") == first["completed_at"].rstrip("Z")


def test_cancel_finished_job_is_409(client, session_factory):
    for terminal in ("SUCCEEDED", "FAILED", "TIMED_OUT"):
        jid = client.post("/jobs", json=VALID).json()["id"]
        with session_factory() as s:
            s.get(Job, jid).status = terminal
            s.commit()
        r = client.post(f"/jobs/{jid}/cancel")
        assert r.status_code == 409 and terminal in r.json()["detail"]
        with session_factory() as s:
            assert s.get(Job, jid).status == terminal


def test_cancel_unknown_job_is_404(client):
    assert client.post("/jobs/9999/cancel").status_code == 404


def test_cancel_scheduled_job_closes_its_unstarted_attempt(client, session_factory):
    from worker.claim import claim_next_job
    jid = client.post("/jobs", json=VALID).json()["id"]
    with session_factory() as s:
        claim_next_job(s, "w1", ["python"])
    assert client.post(f"/jobs/{jid}/cancel").json()["status"] == "CANCELLED"
    att = client.get(f"/jobs/{jid}/attempts").json()[0]
    assert att["status"] == "CANCELLED" and att["finished_at"] is not None


def test_cancel_running_job_leaves_attempt_for_its_worker(client, session_factory):
    from worker.claim import claim_next_job
    from worker.report import mark_running
    jid = client.post("/jobs", json=VALID).json()["id"]
    with session_factory() as s:
        c = claim_next_job(s, "w1", ["python"])
    with session_factory() as s:
        mark_running(s, c, "w1")
    assert client.post(f"/jobs/{jid}/cancel").json()["status"] == "CANCELLED"
    assert client.get(f"/jobs/{jid}/attempts").json()[0]["status"] == "RUNNING"


def test_ready_reports_controller_states(client, session_factory):
    from datetime import timedelta

    from shared.models import Controller
    from shared.workers import db_now

    assert client.get("/ready").json()["controller"] == "absent"
    assert client.get("/ready").json()["status"] == "degraded"

    with session_factory() as s:
        s.add(Controller(id="c1", hostname="h", started_at=db_now(s),
                         last_heartbeat_at=db_now(s) - timedelta(seconds=60)))
        s.commit()
    assert client.get("/ready").json()["controller"] == "stale"

    with session_factory() as s:
        s.get(Controller, "c1").last_heartbeat_at = db_now(s)
        s.commit()
    body = client.get("/ready").json()
    assert body["controller"] == "ok" and body["status"] == "ready"
