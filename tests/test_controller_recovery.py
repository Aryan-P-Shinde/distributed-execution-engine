from datetime import timedelta

import pytest

from controller.config import ControllerConfig
from controller.recovery import find_orphans, orphan_reason, recover_job, recover_once
from shared.models import Attempt, Job, Worker
from shared.spec import JobSpec
from shared.states import AttemptStatus as A
from shared.states import JobStatus as J
from shared.workers import HealthThresholds, db_now
from worker.claim import claim_next_job
from worker.executor import ExecResult
from worker.loop import run_once
from worker.report import finish_attempt, mark_running


def spec_json(timeout=60):
    spec = JobSpec(type="script", runtime="python", command="x", timeout_seconds=timeout)
    return spec.model_dump(mode="json")


CFG = ControllerConfig(
    database_url="x", redis_url="x", controller_id="c1", interval=1,
    overdue_grace=30, scheduled_grace=60, health=HealthThresholds(),  # 5 / 15 / 30
)


def add_worker(sf, wid, age=1.0, stopped=False):
    with sf() as s:
        now = db_now(s)
        s.add(Worker(id=wid, hostname="h", runtimes=["python"], started_at=now - timedelta(hours=1),
                     last_heartbeat_at=now - timedelta(seconds=age),
                     stopped_at=now if stopped else None))
        s.commit()


def add_job(sf, worker="w1", status=J.RUNNING, max_retries=2, timeout=60, running_for=5.0,
            claimed_for=5.0, attempt_status=None):
    """A job as a worker would have left it, with timestamps relative to DB now."""
    with sf() as s:
        now = db_now(s)
        job = Job(spec=spec_json(timeout), runtime="python", job_type="script",
                  status=status, worker_id=worker, max_retries=max_retries, attempt_count=1,
                  queued_at=now - timedelta(minutes=5),
                  started_at=now - timedelta(seconds=running_for) if status == J.RUNNING else None)
        s.add(job)
        s.flush()
        s.add(Attempt(job_id=job.id, attempt_number=1, worker_id=worker,
                      status=attempt_status or (A.RUNNING if status == J.RUNNING else A.ASSIGNED),
                      created_at=now - timedelta(seconds=claimed_for)))
        s.commit()
        return job.id


def reason_for(sf, jid, cfg=CFG):
    with sf() as s:
        job = s.get(Job, jid)
        return orphan_reason(job, s.get(Worker, job.worker_id), db_now(s), cfg)


# ---- what counts as an orphan ------------------------------------------------

def test_healthy_running_job_is_left_alone(session_factory):
    add_worker(session_factory, "w1", age=1)
    assert reason_for(session_factory, add_job(session_factory)) is None


def test_unhealthy_worker_is_given_time(session_factory):
    add_worker(session_factory, "w1", age=20)  # UNHEALTHY, not yet DEAD
    assert reason_for(session_factory, add_job(session_factory)) is None


def test_dead_worker_orphans_its_job(session_factory):
    add_worker(session_factory, "w1", age=45)
    assert "dead" in reason_for(session_factory, add_job(session_factory))


def test_missing_worker_row_orphans_the_job(session_factory):
    assert "no record" in reason_for(session_factory, add_job(session_factory, worker="ghost"))


def test_stopped_worker_still_holding_a_job_is_an_orphan(session_factory):
    add_worker(session_factory, "w1", age=1, stopped=True)
    assert "stopped" in reason_for(session_factory, add_job(session_factory))


def test_overdue_needs_timeout_plus_grace(session_factory):
    add_worker(session_factory, "w1", age=1)  # heartbeating fine: worker is wedged
    just_inside = add_job(session_factory, timeout=60, running_for=60 + 25)
    past = add_job(session_factory, timeout=60, running_for=60 + 35)
    assert reason_for(session_factory, just_inside) is None
    assert "overdue" in reason_for(session_factory, past)


def test_grace_exceeds_the_container_backstop():
    with pytest.raises(ValueError, match="backstop"):
        ControllerConfig(database_url="x", redis_url="x", controller_id="c", interval=1,
                         overdue_grace=5, scheduled_grace=60, health=HealthThresholds())


def test_scheduled_but_never_started(session_factory):
    add_worker(session_factory, "w1", age=1)
    fresh = add_job(session_factory, status=J.SCHEDULED, claimed_for=10)
    stuck = add_job(session_factory, status=J.SCHEDULED, claimed_for=90)
    assert reason_for(session_factory, fresh) is None
    assert "never started" in reason_for(session_factory, stuck)


# ---- recovery actions --------------------------------------------------------

def outcome(sf, jid):
    with sf() as s:
        job = s.get(Job, jid)
        atts = s.query(Attempt).filter_by(job_id=jid).order_by(Attempt.attempt_number).all()
        return job, [(a.attempt_number, a.status) for a in atts]


def test_dead_worker_job_is_requeued_when_retries_remain(session_factory, fake_redis):
    add_worker(session_factory, "w1", age=45)
    jid = add_job(session_factory, max_retries=2)
    recs = recover_once(session_factory, CFG, fake_redis)
    assert [(r.job_id, r.outcome) for r in recs] == [(jid, J.QUEUED)]
    job, atts = outcome(session_factory, jid)
    assert job.status == "QUEUED" and job.worker_id is None and job.queued_at is not None
    assert atts == [(1, "WORKER_LOST")]
    assert "retry 1/2 queued" in job.result_summary and "dead" in job.result_summary
    assert fake_redis.lists["engine:wakeup"] == [str(jid)]


def test_job_fails_when_retries_are_exhausted(session_factory, fake_redis):
    add_worker(session_factory, "w1", age=45)
    jid = add_job(session_factory, max_retries=0)
    recover_once(session_factory, CFG, fake_redis)
    job, atts = outcome(session_factory, jid)
    assert job.status == "FAILED" and job.completed_at is not None
    assert "retries exhausted" in job.result_summary and "max_retries=0" in job.result_summary
    assert atts == [(1, "WORKER_LOST")]
    assert fake_redis.lists == {}  # nothing to wake workers for


def test_scheduled_job_with_dead_worker_can_fail_directly(session_factory):
    add_worker(session_factory, "w1", age=45)
    jid = add_job(session_factory, status=J.SCHEDULED, max_retries=0)
    recover_once(session_factory, CFG)
    job, atts = outcome(session_factory, jid)
    assert job.status == "FAILED" and atts == [(1, "WORKER_LOST")]


def test_overdue_job_on_a_live_worker_is_recovered(session_factory):
    add_worker(session_factory, "w1", age=1)
    add_job(session_factory, timeout=10, running_for=100)
    [rec] = recover_once(session_factory, CFG)
    assert rec.outcome == J.QUEUED and "wedged" in rec.reason


def test_recovery_is_idempotent(session_factory):
    add_worker(session_factory, "w1", age=45)
    add_job(session_factory)
    assert len(recover_once(session_factory, CFG)) == 1
    assert recover_once(session_factory, CFG) == []


def test_cancelled_job_is_not_resurrected(session_factory):
    add_worker(session_factory, "w1", age=45)
    jid = add_job(session_factory, status=J.CANCELLED)
    assert recover_once(session_factory, CFG) == []
    assert outcome(session_factory, jid)[0].status == "CANCELLED"


def test_job_recovered_only_if_still_orphaned_under_the_lock(session_factory):
    """The scan found it dead; by the time we lock it, the worker is back."""
    add_worker(session_factory, "w1", age=45)
    jid = add_job(session_factory)
    with session_factory() as s:
        assert find_orphans(s, CFG)[0][0] == jid
    with session_factory() as s:
        s.get(Worker, "w1").last_heartbeat_at = db_now(s)
        s.commit()
    assert recover_job(session_factory, jid, CFG) is None
    assert outcome(session_factory, jid)[0].status == "RUNNING"


def test_repeated_loss_ends_in_failure_with_full_history(session_factory):
    """max_retries=2: three workers die in a row -> FAILED, three WORKER_LOST attempts."""
    add_worker(session_factory, "w1", age=1)
    with session_factory() as s:
        s.add(Job(spec=spec_json(60), runtime="python", job_type="script",
                  status=J.QUEUED, max_retries=2, queued_at=db_now(s)))
        s.commit()
    jid = 1
    for n in (1, 2, 3):
        with session_factory() as s:
            c = claim_next_job(s, "w1", ["python"])
        assert c.attempt_number == n
        with session_factory() as s:
            mark_running(s, c, "w1")
        with session_factory() as s:  # worker dies
            s.get(Worker, "w1").last_heartbeat_at = db_now(s) - timedelta(seconds=60)
            s.commit()
        [rec] = recover_once(session_factory, CFG)
        assert rec.outcome == (J.QUEUED if n < 3 else J.FAILED)
        with session_factory() as s:  # worker restarts
            s.get(Worker, "w1").last_heartbeat_at = db_now(s)
            s.commit()
    job, atts = outcome(session_factory, jid)
    assert job.status == "FAILED" and job.attempt_count == 3
    assert atts == [(1, "WORKER_LOST"), (2, "WORKER_LOST"), (3, "WORKER_LOST")]


def test_late_result_from_the_presumed_dead_worker_is_discarded(session_factory):
    add_worker(session_factory, "w1", age=1)
    jid = add_job(session_factory, max_retries=2)
    with session_factory() as s:
        s.get(Worker, "w1").last_heartbeat_at = db_now(s) - timedelta(seconds=60)
        s.commit()
    recover_once(session_factory, CFG)  # requeued; attempt 1 closed WORKER_LOST

    from worker.claim import ClaimedJob
    stale = ClaimedJob(job_id=jid, attempt_id=1, attempt_number=1,
                       spec=JobSpec(type="script", runtime="python", command="x"))
    result = ExecResult(status=A.SUCCEEDED, exit_code=0, stdout="zombie", stderr="", duration_seconds=1)
    with session_factory() as s:
        assert finish_attempt(s, stale, "w1", result) is None
    job, atts = outcome(session_factory, jid)
    assert job.status == "QUEUED" and atts == [(1, "WORKER_LOST")]


def test_recovered_job_completes_on_another_worker(session_factory, fake_redis):
    add_worker(session_factory, "w1", age=45)
    jid = add_job(session_factory, max_retries=1)
    recover_once(session_factory, CFG, fake_redis)

    class Ok:
        def run(self, spec, cancel=None):
            return ExecResult(status=A.SUCCEEDED, exit_code=0, stdout="done", stderr="", duration_seconds=1)

    assert run_once(session_factory, Ok(), "w2", ["python"])
    job, atts = outcome(session_factory, jid)
    assert job.status == "SUCCEEDED" and job.worker_id == "w2"
    assert atts == [(1, "WORKER_LOST"), (2, "SUCCEEDED")]
