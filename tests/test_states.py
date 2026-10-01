import itertools

import pytest

from shared.states import (
    JOB_TRANSITIONS,
    TERMINAL_JOB_STATES,
    AttemptStatus,
    InvalidTransition,
    JobStatus,
    assert_transition,
    can_transition,
    decide_after_attempt,
)

J, A = JobStatus, AttemptStatus


def test_every_status_has_a_transition_entry():
    assert set(JOB_TRANSITIONS) == set(JobStatus)


def test_terminal_states_have_no_exits():
    for s in TERMINAL_JOB_STATES:
        assert JOB_TRANSITIONS[s] == frozenset()


@pytest.mark.parametrize("terminal", sorted(TERMINAL_JOB_STATES))
def test_terminal_never_moves(terminal):
    for target in JobStatus:
        assert not can_transition(terminal, target)


def test_succeeded_cannot_become_running():
    with pytest.raises(InvalidTransition):
        assert_transition(J.SUCCEEDED, J.RUNNING)


def test_no_self_transitions():
    for s in JobStatus:
        assert not can_transition(s, s)


def test_happy_path():
    path = [J.CREATED, J.QUEUED, J.SCHEDULED, J.RUNNING, J.SUCCEEDED]
    for a, b in itertools.pairwise(path):
        assert_transition(a, b)


def test_cannot_skip_scheduling():
    assert not can_transition(J.QUEUED, J.RUNNING)
    assert not can_transition(J.CREATED, J.RUNNING)


def test_cancel_from_every_live_state():
    for s in (J.CREATED, J.QUEUED, J.SCHEDULED, J.RUNNING):
        assert can_transition(s, J.CANCELLED)


def test_requeue_paths():
    assert can_transition(J.RUNNING, J.QUEUED)
    assert can_transition(J.SCHEDULED, J.QUEUED)


def test_decide_success_and_cancel():
    assert decide_after_attempt(A.SUCCEEDED, 1, 0) == J.SUCCEEDED
    assert decide_after_attempt(A.CANCELLED, 1, 3) == J.CANCELLED


def test_timeout_is_not_retried():
    assert decide_after_attempt(A.TIMED_OUT, 1, 5) == J.TIMED_OUT


def test_nonzero_exit_is_not_retried():
    assert decide_after_attempt(A.FAILED, 1, 5) == J.FAILED


RETRYABLE = [A.WORKER_LOST, A.EXECUTOR_ERROR]


@pytest.mark.parametrize("outcome", RETRYABLE)
def test_retryable_outcomes_retry_until_exhausted(outcome):
    # max_retries=2 -> 3 total attempts
    assert decide_after_attempt(outcome, 1, 2) == J.QUEUED
    assert decide_after_attempt(outcome, 2, 2) == J.QUEUED
    assert decide_after_attempt(outcome, 3, 2) == J.FAILED


@pytest.mark.parametrize("outcome", RETRYABLE)
def test_zero_retries_fails_immediately(outcome):
    assert decide_after_attempt(outcome, 1, 0) == J.FAILED


@pytest.mark.parametrize("outcome", [A.FAILED, A.TIMED_OUT])
def test_workload_failures_never_retry(outcome):
    assert decide_after_attempt(outcome, 1, 5) != J.QUEUED


def test_scheduled_can_fail_or_requeue_when_its_worker_is_lost():
    assert can_transition(J.SCHEDULED, J.FAILED)
    assert can_transition(J.SCHEDULED, J.QUEUED)


@pytest.mark.parametrize("non_final", [A.ASSIGNED, A.RUNNING])
def test_non_final_outcome_rejected(non_final):
    with pytest.raises(ValueError):
        decide_after_attempt(non_final, 1, 0)


def test_every_decision_is_a_legal_transition_from_running():
    for outcome in (A.SUCCEEDED, A.FAILED, A.TIMED_OUT, A.WORKER_LOST, A.EXECUTOR_ERROR, A.CANCELLED):
        for used in range(1, 5):
            nxt = decide_after_attempt(outcome, used, 2)
            assert can_transition(J.RUNNING, nxt), (outcome, used, nxt)


def test_recovery_decisions_are_legal_from_scheduled_too():
    for used in range(1, 5):
        assert can_transition(J.SCHEDULED, decide_after_attempt(A.WORKER_LOST, used, 2))
