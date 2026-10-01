"""Real Docker tests. Skipped when the daemon is unreachable.

    sh docker/build-runners.sh     # once; the fixture also builds if missing
    pytest tests/test_docker_integration.py -v

These are the tests that actually prove the isolation claims. The fake-docker
tests only prove orchestration.
"""
import pathlib
import subprocess
import threading
import time

import pytest

from shared.spec import JobSpec
from shared.states import AttemptStatus as A
from worker.docker_executor import (
    DEFAULT_IMAGES,
    OUTPUT_CAP_BYTES,
    DockerExecutor,
    ExecutorError,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent


def docker(*args, timeout=300):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


@pytest.fixture(scope="module")
def ex():
    try:
        if docker("info", timeout=15).returncode != 0:
            pytest.skip("docker daemon not reachable")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("docker not installed")
    for image in DEFAULT_IMAGES.values():
        if docker("image", "inspect", image).returncode != 0:
            built = subprocess.run(["sh", str(ROOT / "docker" / "build-runners.sh")],
                                   capture_output=True, text=True)
            assert built.returncode == 0, built.stderr
            break
    return DockerExecutor()


def py(code: str, **kw) -> JobSpec:
    base = {"type": "script", "runtime": "python", "command": "python main.py",
            "files": {"main.py": code}, "timeout_seconds": 20}
    base.update(kw)
    return JobSpec(**base)


def leftover_containers() -> list[str]:
    out = docker("ps", "-aq", "--filter", "label=dwee.managed=true").stdout
    return out.split()


def test_hello(ex):
    r = ex.run(py("print('hello')"))
    assert (r.status, r.exit_code, r.stdout) == (A.SUCCEEDED, 0, "hello\n")


def test_nonzero_exit_and_stderr(ex):
    r = ex.run(py("import sys; sys.stderr.write('boom'); sys.exit(3)"))
    assert (r.status, r.exit_code, r.stderr) == (A.FAILED, 3, "boom")


def test_runs_as_nobody(ex):
    assert ex.run(py("import os; print(os.getuid(), os.getgid())")).stdout.strip() == "65534 65534"


def test_root_filesystem_is_read_only(ex):
    r = ex.run(py("open('/etc/pwned', 'w')"))
    assert r.status is A.FAILED
    assert "Read-only" in r.stderr or "Permission denied" in r.stderr


def test_work_and_tmp_are_writable(ex):
    code = "open('/work/a','w').write('x'); open('/tmp/b','w').write('y'); print('ok')"
    assert ex.run(py(code)).stdout.strip() == "ok"


def test_no_network_by_default(ex):
    code = ("import socket\n"
            "s = socket.socket(); s.settimeout(3)\n"
            "s.connect(('1.1.1.1', 53))\n")
    r = ex.run(py(code))
    assert r.status is A.FAILED, "container reached the internet with network disabled"


def test_infinite_loop_times_out_and_container_is_gone(ex):
    start = time.monotonic()
    r = ex.run(py("while True: pass", timeout_seconds=2))
    assert r.status is A.TIMED_OUT
    assert time.monotonic() - start < 15
    assert leftover_containers() == []


def test_memory_limit_kills_the_job(ex):
    r = ex.run(py("x = b'x' * (600 * 1024 * 1024); print(len(x))",
                  resources={"cpu": 1, "memory_mb": 128}))
    assert r.status is A.FAILED
    assert r.exit_code == 137
    assert r.error and "out of memory" in r.error


def test_fork_bomb_is_contained(ex):
    code = "import os\nwhile True:\n    os.fork()\n"
    r = ex.run(py(code, timeout_seconds=10))
    assert r.status in (A.FAILED, A.TIMED_OUT)
    assert leftover_containers() == []


def test_huge_output_is_capped(ex):
    code = "import sys\nfor _ in range(200):\n    sys.stdout.write('x' * 100000)\n"
    r = ex.run(py(code))
    assert r.status is A.SUCCEEDED
    assert len(r.stdout) <= OUTPUT_CAP_BYTES + 50


def test_nested_source_files(ex):
    files = {"main.py": "from pkg.util import X\nprint(X)", "pkg/util.py": "X = 42"}
    assert ex.run(py("", files=files)).stdout.strip() == "42"


def test_pytest_job(ex):
    files = {"test_a.py": "def test_ok():\n    assert 1 + 1 == 2\n"}
    r = ex.run(JobSpec(type="test", runtime="python", command="pytest -q",
                       files=files, timeout_seconds=30))
    assert r.status is A.SUCCEEDED, r.stdout + r.stderr


def test_node_runtime(ex):
    r = ex.run(JobSpec(type="script", runtime="node", command="node index.js",
                       files={"index.js": "console.log('node ok')"}, timeout_seconds=20))
    assert r.stdout.strip() == "node ok"


def test_shell_metacharacters_in_command_stay_inside_container(ex):
    r = ex.run(py("", command='echo "$(id -u)"; echo done', files={}))
    assert r.stdout.split() == ["65534", "done"]


def test_missing_image_is_an_executor_error(ex):
    with pytest.raises(ExecutorError):
        DockerExecutor(images={"python": "dwee-no-such-image:nope"}).run(py("print(1)"))
    assert leftover_containers() == []


def test_cancel_kills_a_running_container_promptly(ex):
    cancel = threading.Event()
    threading.Timer(2.0, cancel.set).start()
    start = time.monotonic()
    r = ex.run(py("while True: pass", timeout_seconds=60), cancel)
    assert r.status is A.CANCELLED
    assert time.monotonic() - start < 15
    assert leftover_containers() == []
