import pytest
from pydantic import ValidationError

from shared.spec import JobSpec


def make(**kw):
    base = {"type": "script", "runtime": "python", "command": "python main.py"}
    base.update(kw)
    return JobSpec(**base)


def test_defaults():
    s = make()
    assert s.timeout_seconds == 60
    assert s.resources.cpu == 1.0
    assert s.resources.memory_mb == 512
    assert s.max_retries == 2
    assert s.network_enabled is False


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        make(privileged=True)


@pytest.mark.parametrize("cmd", ["", "   ", "a\x00b"])
def test_bad_command(cmd):
    with pytest.raises(ValidationError):
        make(command=cmd)


@pytest.mark.parametrize("t", [0, -1, 3601])
def test_timeout_bounds(t):
    with pytest.raises(ValidationError):
        make(timeout_seconds=t)


def test_resource_bounds():
    with pytest.raises(ValidationError):
        make(resources={"cpu": 64, "memory_mb": 512})
    with pytest.raises(ValidationError):
        make(resources={"cpu": 1, "memory_mb": 8})


@pytest.mark.parametrize(
    "path", ["/etc/passwd", "../x.py", "a/../b.py", "a//b.py", "a\\b.py", "", "./x.py"]
)
def test_unsafe_paths_rejected(path):
    with pytest.raises(ValidationError):
        make(files={path: "x"})


def test_safe_nested_path_ok():
    assert make(files={"pkg/main.py": "print(1)"}).files


def test_file_size_limits():
    with pytest.raises(ValidationError):
        make(files={"big.py": "x" * (256 * 1024 + 1)})


def test_retry_cap():
    with pytest.raises(ValidationError):
        make(max_retries=6)
