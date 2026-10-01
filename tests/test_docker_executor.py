"""Orchestration tests using a fake docker CLI (see fake_docker.py)."""
import io
import json
import pathlib
import sys
import tarfile
import threading
import time

import pytest

from shared.spec import JobSpec
from shared.states import AttemptStatus as A
from worker.docker_executor import (
    OUTPUT_CAP_BYTES,
    DockerExecutor,
    ExecutorError,
    build_tar,
)

FAKE_SCRIPT = pathlib.Path(__file__).parent / "fake_docker.py"


@pytest.fixture()
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_DIR", str(tmp_path))
    monkeypatch.setenv("FAKE_MODE", "ok")

    # wrapper so the fake works regardless of file mode bits (downloads and
    # some filesystems drop the executable bit)
    wrapper = tmp_path / "docker"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_SCRIPT}" "$@"\n')
    wrapper.chmod(0o755)

    class Fake:
        dir = tmp_path
        bin = str(wrapper)

        def mode(self, m):
            monkeypatch.setenv("FAKE_MODE", m)

        def calls(self):
            p = tmp_path / "calls.jsonl"
            return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

        def verbs(self):
            return [c[0] for c in self.calls()]

    return Fake()


def spec(**kw):
    base = {"type": "script", "runtime": "python", "command": "python main.py",
            "files": {"main.py": "print('hi')"}}
    base.update(kw)
    return JobSpec(**base)


def executor(fake):
    return DockerExecutor(docker_bin=fake.bin)


def test_success(fake):
    r = executor(fake).run(spec())
    assert (r.status, r.exit_code) == (A.SUCCEEDED, 0)
    assert r.stdout == "hello\n" and r.stderr == "warn\n"
    assert fake.verbs() == ["create", "start", "inspect", "rm"]


def test_nonzero_exit_is_failed(fake):
    fake.mode("fail")
    r = executor(fake).run(spec())
    assert (r.status, r.exit_code) == (A.FAILED, 3)
    assert "rm" in fake.verbs()


def test_timeout_kills_container_and_cleans_up(fake):
    fake.mode("hang")
    start = time.monotonic()
    r = executor(fake).run(spec(timeout_seconds=1))
    assert r.status is A.TIMED_OUT and r.exit_code is None
    assert time.monotonic() - start < 6
    verbs = fake.verbs()
    assert "kill" in verbs and verbs[-1] == "rm"
    assert verbs.index("kill") < verbs.index("rm")


def test_oom_is_reported(fake):
    fake.mode("oom")
    r = executor(fake).run(spec())
    assert r.status is A.FAILED and r.exit_code == 137
    assert "out of memory" in r.error and "512" in r.error


def test_output_is_capped_in_memory(fake):
    fake.mode("flood")
    r = executor(fake).run(spec())
    assert r.status is A.SUCCEEDED
    assert len(r.stdout) <= OUTPUT_CAP_BYTES + 50
    assert r.stdout.endswith("[output truncated]")


def test_create_failure_is_executor_error_and_still_cleans_up(fake):
    fake.mode("create_fail")
    with pytest.raises(ExecutorError, match="build-runners"):
        executor(fake).run(spec())
    assert "rm" in fake.verbs()


def test_container_that_never_started_is_executor_error(fake):
    fake.mode("start_error")
    with pytest.raises(ExecutorError, match="failed to start"):
        executor(fake).run(spec())
    assert "rm" in fake.verbs()


def test_inspect_failure_is_executor_error(fake):
    fake.mode("no_inspect")
    with pytest.raises(ExecutorError, match="inspect"):
        executor(fake).run(spec())
    assert "rm" in fake.verbs()


def test_missing_docker_binary():
    with pytest.raises(ExecutorError, match="not found"):
        DockerExecutor(docker_bin="/nonexistent/docker").run(spec())


def test_unknown_runtime_has_no_image(fake):
    with pytest.raises(ExecutorError, match="no runner image"):
        DockerExecutor(images={"node": "x"}, docker_bin=fake.bin).run(spec())


def flag_value(args, flag):
    return args[args.index(flag) + 1]


def test_create_args_are_locked_down(fake):
    executor(fake).run(spec(resources={"cpu": 0.5, "memory_mb": 256}, timeout_seconds=30))
    a = next(c for c in fake.calls() if c[0] == "create")
    assert flag_value(a, "--user") == "65534:65534"
    assert "--read-only" in a
    assert flag_value(a, "--cap-drop") == "ALL"
    assert flag_value(a, "--security-opt") == "no-new-privileges"
    assert flag_value(a, "--memory") == "256m"
    assert flag_value(a, "--memory-swap") == "256m"
    assert flag_value(a, "--cpus") == "0.5"
    assert flag_value(a, "--pids-limit") == "128"
    assert flag_value(a, "--network") == "none"
    assert flag_value(a, "--pull") == "never"
    assert flag_value(a, "--label") == "dwee.managed=true"
    assert "--privileged" not in a
    assert "JOB_CMD=python main.py" in a
    assert "dwee-runner-python:latest" in a
    assert "timeout -s KILL 35" in a[-1]  # 30s + 5s grace backstop


def test_network_only_when_requested(fake):
    executor(fake).run(spec(network_enabled=True))
    a = next(c for c in fake.calls() if c[0] == "create")
    assert flag_value(a, "--network") == "bridge"


def test_command_is_passed_as_env_not_interpolated_into_shell(fake):
    evil = 'echo "$(whoami)"; rm -rf / #'
    executor(fake).run(spec(command=evil))
    a = next(c for c in fake.calls() if c[0] == "create")
    assert f"JOB_CMD={evil}" in a
    assert evil not in a[-1]  # boot script never contains the user's command text


def test_source_files_delivered_as_tar_on_stdin(fake):
    executor(fake).run(spec(files={"main.py": "print(1)", "pkg/util.py": "X = 2"}))
    tar = tarfile.open(fileobj=io.BytesIO((fake.dir / "stdin.tar").read_bytes()))
    got = {m.name: tar.extractfile(m).read().decode() for m in tar.getmembers()}
    assert got == {"main.py": "print(1)", "pkg/util.py": "X = 2"}


def test_build_tar_with_no_files_is_valid():
    assert tarfile.open(fileobj=io.BytesIO(build_tar({}))).getmembers() == []


def test_cancel_kills_running_container_and_cleans_up(fake):
    fake.mode("hang")
    cancel = threading.Event()
    threading.Timer(0.6, cancel.set).start()
    start = time.monotonic()
    r = executor(fake).run(spec(timeout_seconds=30), cancel)
    assert r.status is A.CANCELLED and r.exit_code is None
    assert time.monotonic() - start < 5
    verbs = fake.verbs()
    assert "kill" in verbs and verbs[-1] == "rm"


def test_cancel_before_start_never_starts_the_container(fake):
    cancel = threading.Event()
    cancel.set()
    r = executor(fake).run(spec(), cancel)
    assert r.status is A.CANCELLED
    assert "start" not in fake.verbs() and fake.verbs()[-1] == "rm"


def test_timeout_still_wins_when_not_cancelled(fake):
    fake.mode("hang")
    r = executor(fake).run(spec(timeout_seconds=1), threading.Event())
    assert r.status is A.TIMED_OUT
