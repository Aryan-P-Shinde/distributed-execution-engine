"""API -> Postgres/SQLite -> worker -> result -> API, in one process, with a
fake executor. The multi-process version with real services is in README."""
from shared.states import AttemptStatus as A
from worker.executor import ExecResult
from worker.loop import run_once

SPEC = {"type": "script", "runtime": "python", "command": "python main.py",
        "files": {"main.py": "print('x')"}}


class Scripted:
    def __init__(self, *results):
        self.results = list(results)

    def run(self, spec, cancel=None):
        return self.results.pop(0)


def res(status=A.SUCCEEDED, code=0, out="hello\n", err=""):
    return ExecResult(status=status, exit_code=code, stdout=out, stderr=err, duration_seconds=0.2)


def test_submit_execute_observe(client, session_factory):
    jid = client.post("/jobs", json=SPEC).json()["id"]
    assert client.get(f"/jobs/{jid}").json()["status"] == "QUEUED"
    assert client.get(f"/jobs/{jid}/logs").json()["attempt_number"] is None

    assert run_once(session_factory, Scripted(res()), "w1", ["python"])

    job = client.get(f"/jobs/{jid}").json()
    assert job["status"] == "SUCCEEDED" and job["exit_code"] == 0 and job["worker_id"] == "w1"
    assert job["completed_at"] is not None and "SUCCEEDED" in job["result_summary"]

    logs = client.get(f"/jobs/{jid}/logs").json()
    assert (logs["attempt_number"], logs["stdout"], logs["attempt_status"]) == (1, "hello\n", "SUCCEEDED")

    attempts = client.get(f"/jobs/{jid}/attempts").json()
    assert [(a["attempt_number"], a["status"], a["worker_id"]) for a in attempts] == [(1, "SUCCEEDED", "w1")]


def test_failure_and_timeout_visible_through_api(client, session_factory):
    failed = client.post("/jobs", json=SPEC).json()["id"]
    timed = client.post("/jobs", json=SPEC).json()["id"]
    ex = Scripted(res(A.FAILED, 2, out="", err="Traceback"), res(A.TIMED_OUT, None, out="partial"))
    run_once(session_factory, ex, "w1", ["python"])
    run_once(session_factory, ex, "w1", ["python"])

    assert client.get(f"/jobs/{failed}").json()["status"] == "FAILED"
    assert client.get(f"/jobs/{failed}/logs").json()["stderr"] == "Traceback"
    assert client.get(f"/jobs/{timed}").json()["status"] == "TIMED_OUT"
    assert client.get(f"/jobs/{timed}/logs").json()["stdout"] == "partial"


def test_logs_by_attempt_and_404s(client, session_factory):
    jid = client.post("/jobs", json=SPEC).json()["id"]
    run_once(session_factory, Scripted(res(out="one")), "w1", ["python"])
    assert client.get(f"/jobs/{jid}/logs", params={"attempt": 1}).json()["stdout"] == "one"
    assert client.get(f"/jobs/{jid}/logs", params={"attempt": 2}).status_code == 404
    assert client.get("/jobs/9999/logs").status_code == 404
