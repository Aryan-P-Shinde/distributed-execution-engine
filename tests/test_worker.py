import threading
import time

import pytest

from shared.models import Attempt, Job, utcnow
from shared.spec import JobSpec
from shared.states import AttemptStatus, JobStatus
from worker.claim import claim_next_job
from worker.executor import ExecResult
from worker.loop import run_forever, run_once
from worker.report import MAX_LOG_CHARS, finish_attempt, mark_running

A = AttemptStatus


def add_job(session_factory, runtime="python", **spec_kw) -> int:
    spec = JobSpec(type="script", runtime=runtime, command="run", **spec_kw)
    with session_factory() as s:
        job = Job(
            spec=spec.model_dump(mode="json"),
            runtime=runtime,
            job_type="script",
            max_retries=spec.max_retries,
            status=JobStatus.QUEUED,
            queued_at=utcnow(),
        )
        s.add(job)
        s.commit()
        return job.id


def result(status=A.SUCCEEDED, exit_code=0, out="ok", err="", **kw):
    return ExecResult(status=status, exit_code=exit_code, stdout=out, stderr=err,
                      duration_seconds=0.5, **kw)


class FakeExecutor:
    def __init__(self, res=None, exc=None):
        self.res, self.exc, self.calls = res or result(), exc, []

    def run(self, spec, cancel=None):
        self.calls.append(spec)
        if self.exc:
            raise self.exc
        return self.res


def claim(session_factory, worker="w1", runtimes=("python",)):
    with session_factory() as s:
        return claim_next_job(s, worker, list(runtimes))


# ---- claiming ---------------------------------------------------------------

def test_claim_empty_queue(session_factory):
    assert claim(session_factory) is None


def test_claim_moves_job_to_scheduled_and_creates_attempt(session_factory):
    jid = add_job(session_factory)
    c = claim(session_factory)
    assert (c.job_id, c.attempt_number) == (jid, 1)
    with session_factory() as s:
        job = s.get(Job, jid)
        assert job.status == "SCHEDULED" and job.worker_id == "w1"
        assert job.attempt_count == 1
        att = s.get(Attempt, c.attempt_id)
        assert att.status == "ASSIGNED" and att.worker_id == "w1"


def test_claim_is_fifo_and_never_repeats(session_factory):
    ids = [add_job(session_factory) for _ in range(3)]
    got = [claim(session_factory).job_id for _ in range(3)]
    assert got == ids
    assert claim(session_factory) is None


def test_claim_filters_by_runtime(session_factory):
    node_id = add_job(session_factory, runtime="node")
    assert claim(session_factory, runtimes=("python",)) is None
    assert claim(session_factory, runtimes=("python", "node")).job_id == node_id


# ---- reporting --------------------------------------------------------------

def test_success_path(session_factory):
    jid = add_job(session_factory)
    c = claim(session_factory)
    with session_factory() as s:
        assert mark_running(s, c, "w1")
    with session_factory() as s:
        assert finish_attempt(s, c, "w1", result(out="hello")) == JobStatus.SUCCEEDED
    with session_factory() as s:
        job = s.get(Job, jid)
        assert job.status == "SUCCEEDED" and job.exit_code == 0
        assert job.completed_at is not None and job.started_at is not None
        assert "SUCCEEDED" in job.result_summary
        att = s.get(Attempt, c.attempt_id)
        assert att.status == "SUCCEEDED" and att.stdout == "hello"


@pytest.mark.parametrize("status,code,expected", [
    (A.FAILED, 3, JobStatus.FAILED),
    (A.TIMED_OUT, None, JobStatus.TIMED_OUT),
])
def test_terminal_failures(session_factory, status, code, expected):
    jid = add_job(session_factory, max_retries=3)  # retries don't apply to these
    c = claim(session_factory)
    with session_factory() as s:
        mark_running(s, c, "w1")
    with session_factory() as s:
        assert finish_attempt(s, c, "w1", result(status, code, err="boom")) == expected
    with session_factory() as s:
        job = s.get(Job, jid)
        assert job.status == expected and job.exit_code == code
        assert s.get(Attempt, c.attempt_id).stderr == "boom"


def test_output_is_capped(session_factory):
    add_job(session_factory)
    c = claim(session_factory)
    with session_factory() as s:
        mark_running(s, c, "w1")
    with session_factory() as s:
        finish_attempt(s, c, "w1", result(out="x" * (MAX_LOG_CHARS + 500)))
    with session_factory() as s:
        out = s.get(Attempt, c.attempt_id).stdout
        assert len(out) < MAX_LOG_CHARS + 50 and out.endswith("[output truncated]")


def test_cancelled_while_running_result_does_not_touch_job(session_factory):
    jid = add_job(session_factory)
    c = claim(session_factory)
    with session_factory() as s:
        mark_running(s, c, "w1")
    with session_factory() as s:  # control plane cancels mid-run
        s.get(Job, jid).status = JobStatus.CANCELLED
        s.commit()
    with session_factory() as s:
        assert finish_attempt(s, c, "w1", result()) is None
    with session_factory() as s:
        assert s.get(Job, jid).status == "CANCELLED"
        assert s.get(Attempt, c.attempt_id).status == "SUCCEEDED"  # history kept


def test_late_result_after_worker_declared_lost_is_discarded(session_factory):
    jid = add_job(session_factory, max_retries=1)
    c = claim(session_factory)
    with session_factory() as s:
        mark_running(s, c, "w1")
    with session_factory() as s:  # recovery closes the attempt and requeues
        s.get(Attempt, c.attempt_id).status = AttemptStatus.WORKER_LOST
        job = s.get(Job, jid)
        job.status, job.worker_id = JobStatus.QUEUED, None
        s.commit()
    with session_factory() as s:
        assert finish_attempt(s, c, "w1", result()) is None
    with session_factory() as s:
        assert s.get(Job, jid).status == "QUEUED"
        assert s.get(Attempt, c.attempt_id).status == "WORKER_LOST"


def test_mark_running_refused_if_not_owner(session_factory):
    jid = add_job(session_factory)
    c = claim(session_factory)
    with session_factory() as s:
        s.get(Job, jid).worker_id = "someone-else"
        s.commit()
    with session_factory() as s:
        assert mark_running(s, c, "w1") is False


def test_executor_cannot_report_worker_lost():
    with pytest.raises(ValueError):
        result(status=A.WORKER_LOST)


# ---- loop -------------------------------------------------------------------

def test_run_once_end_to_end(session_factory):
    jid = add_job(session_factory)
    ex = FakeExecutor(result(out="done"))
    assert run_once(session_factory, ex, "w1", ["python"]) is True
    assert run_once(session_factory, ex, "w1", ["python"]) is False
    assert len(ex.calls) == 1
    with session_factory() as s:
        assert s.get(Job, jid).status == "SUCCEEDED"


def test_executor_error_with_no_retries_fails_the_job(session_factory):
    jid = add_job(session_factory, max_retries=0)
    run_once(session_factory, FakeExecutor(exc=RuntimeError("docker down")), "w1", ["python"])
    with session_factory() as s:
        job = s.get(Job, jid)
        assert job.status == "FAILED" and job.completed_at is not None
        att = s.query(Attempt).filter_by(job_id=jid).one()
        assert att.status == "EXECUTOR_ERROR" and "docker down" in att.error


def test_executor_error_requeues_then_exhausts(session_factory, fake_redis):
    jid = add_job(session_factory, max_retries=1)
    ex = FakeExecutor(exc=RuntimeError("docker down"))

    run_once(session_factory, ex, "w1", ["python"], notifier=fake_redis)
    with session_factory() as s:
        job = s.get(Job, jid)
        assert job.status == "QUEUED" and job.worker_id is None and job.attempt_count == 1
        assert "retry 1/1 queued" in job.result_summary
    assert fake_redis.lists["engine:wakeup"] == [str(jid)]  # workers told to look

    run_once(session_factory, ex, "w2", ["python"], notifier=fake_redis)
    with session_factory() as s:
        job = s.get(Job, jid)
        assert job.status == "FAILED" and job.attempt_count == 2
        assert "docker down" in job.result_summary and "retries exhausted" in job.result_summary
        atts = s.query(Attempt).filter_by(job_id=jid).order_by(Attempt.attempt_number).all()
        assert [(a.attempt_number, a.worker_id, a.status) for a in atts] == [
            (1, "w1", "EXECUTOR_ERROR"), (2, "w2", "EXECUTOR_ERROR")]
    assert len(fake_redis.lists["engine:wakeup"]) == 1  # no wake-up for a terminal failure


def test_retry_can_succeed_on_another_worker(session_factory):
    jid = add_job(session_factory, max_retries=2)
    run_once(session_factory, FakeExecutor(exc=RuntimeError("image missing here")), "w1", ["python"])
    run_once(session_factory, FakeExecutor(), "w2", ["python"])
    with session_factory() as s:
        assert s.get(Job, jid).status == "SUCCEEDED"
        attempts = s.query(Attempt).filter_by(job_id=jid).order_by(Attempt.attempt_number)
        assert [a.status for a in attempts] == ["EXECUTOR_ERROR", "SUCCEEDED"]


def test_workload_failure_is_not_retried_even_with_budget(session_factory):
    jid = add_job(session_factory, max_retries=5)
    run_once(session_factory, FakeExecutor(result(A.FAILED, 1)), "w1", ["python"])
    with session_factory() as s:
        assert s.get(Job, jid).status == "FAILED" and s.get(Job, jid).attempt_count == 1


def test_run_forever_drains_then_stops(session_factory):
    ids = [add_job(session_factory) for _ in range(3)]
    stop = threading.Event()
    t = threading.Thread(
        target=run_forever,
        args=(session_factory, FakeExecutor(), "w1", ["python"], None, 0.05, stop),
    )
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        with session_factory() as s:
            if all(s.get(Job, i).status == "SUCCEEDED" for i in ids):
                break
        time.sleep(0.05)
    stop.set()
    t.join(timeout=2)
    assert not t.is_alive()
    with session_factory() as s:
        assert all(s.get(Job, i).status == "SUCCEEDED" for i in ids)


# ---- cancellation ---------------------------------------------------------

class BlockingExecutor:
    """Runs until told to cancel (or a safety timeout), like a long workload."""

    def __init__(self):
        self.started = threading.Event()
        self.saw_cancel = False

    def run(self, spec, cancel):
        self.started.set()
        self.saw_cancel = cancel.wait(timeout=10)
        status = A.CANCELLED if self.saw_cancel else A.SUCCEEDED
        return ExecResult(status=status, exit_code=None if self.saw_cancel else 0,
                          stdout="partial", stderr="", duration_seconds=0.1,
                          error="cancelled; container killed" if self.saw_cancel else None)


def run_in_thread(session_factory, executor, poll=0.05):
    out = {}
    def target():
        out["worked"] = run_once(session_factory, executor, "w1", ["python"], cancel_poll_seconds=poll)
    t = threading.Thread(target=target)
    t.start()
    return t, out


def test_cancel_while_running_stops_the_workload(client, session_factory):
    jid = client.post("/jobs", json={"type": "script", "runtime": "python", "command": "x"}).json()["id"]
    ex = BlockingExecutor()
    t, out = run_in_thread(session_factory, ex)
    assert ex.started.wait(5)

    assert client.post(f"/jobs/{jid}/cancel").json()["status"] == "CANCELLED"
    t.join(timeout=5)

    assert not t.is_alive(), "worker did not stop the workload after cancel"
    assert ex.saw_cancel
    with session_factory() as s:
        job = s.get(Job, jid)
        assert job.status == "CANCELLED"
        att = s.query(Attempt).filter_by(job_id=jid).one()
        assert att.status == "CANCELLED" and att.stdout == "partial"  # partial logs kept
        assert att.finished_at is not None


def test_watcher_stops_when_workload_finishes_normally(session_factory):
    add_job(session_factory)
    run_once(session_factory, FakeExecutor(), "w1", ["python"], cancel_poll_seconds=0.05)
    time.sleep(0.2)
    assert [t for t in threading.enumerate() if t.name == "cancel-watch"] == []


def test_reassigned_job_stops_the_old_workers_workload(session_factory):
    jid = add_job(session_factory)
    ex = BlockingExecutor()
    t, _ = run_in_thread(session_factory, ex)
    assert ex.started.wait(5)
    with session_factory() as s:  # recovery hands the job to someone else
        s.get(Job, jid).worker_id = "w2"
        s.commit()
    t.join(timeout=5)
    assert not t.is_alive() and ex.saw_cancel
    with session_factory() as s:
        assert s.get(Job, jid).worker_id == "w2"  # old worker did not touch the job


def test_executor_may_report_cancelled():
    assert result(status=A.CANCELLED, exit_code=None).status is A.CANCELLED


@pytest.mark.parametrize("res,expected", [
    (result(A.SUCCEEDED, 0), "SUCCEEDED"),
    (result(A.FAILED, 1), "FAILED"),
    (result(A.TIMED_OUT, None), "TIMED_OUT"),
    (result(A.EXECUTOR_ERROR, None, error="boom"), "FAILED"),
])
def test_every_terminal_outcome_stamps_completed_at(session_factory, res, expected):
    jid = add_job(session_factory, max_retries=0)
    run_once(session_factory, FakeExecutor(res), "w1", ["python"])
    with session_factory() as s:
        job = s.get(Job, jid)
        assert job.status == expected and job.completed_at is not None
