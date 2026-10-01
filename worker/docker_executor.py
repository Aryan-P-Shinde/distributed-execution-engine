"""Docker executor: runs one workload in one throwaway, locked-down container.

Flow per job:
    docker create   (limits + hardening flags, image must already exist locally)
    docker start -a -i   (source files streamed in as a tar on stdin)
    docker inspect  (authoritative exit code / OOM flag)
    docker rm -f    (always, in `finally`)

Why the CLI and tar-over-stdin instead of the SDK and bind mounts:
- no extra dependency;
- no host-path assumptions, so it behaves the same when the worker itself runs
  in a container talking to a host Docker socket (a bind mount would resolve
  paths on the host, not in the worker);
- works with a read-only root filesystem: /work is a tmpfs filled at start.

The host-side timeout is authoritative. `timeout -s KILL` inside the container
is a backstop with a grace period, so a container cannot outlive a crashed
worker forever.

NOT a hostile-code sandbox. Docker's default isolation shares the host kernel.
"""
import io
import json
import logging
import subprocess
import tarfile
import threading
import time
import uuid
from dataclasses import dataclass

from shared.spec import EXECUTOR_BACKSTOP_SECONDS as BACKSTOP_GRACE_SECONDS
from shared.spec import JobSpec
from shared.states import AttemptStatus
from worker.executor import ExecResult

log = logging.getLogger(__name__)

LABEL = "dwee.managed=true"
NOBODY = "65534:65534"
OUTPUT_CAP_BYTES = 1_000_000
HELPER_TIMEOUT_SECONDS = 30
PIDS_LIMIT = 128

DEFAULT_IMAGES = {
    "python": "dwee-runner-python:latest",
    "node": "dwee-runner-node:latest",
}


class ExecutorError(Exception):
    """Infrastructure failure (Docker down, image missing, container never
    started). Not a statement about the workload."""


@dataclass
class _Captured:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    cancelled: bool = False


def build_tar(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    now = time.time()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            info.mode = 0o644
            info.uid = info.gid = 65534
            info.mtime = now
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _decode(buf: bytearray, truncated: bool) -> str:
    text = bytes(buf).decode("utf-8", errors="replace")
    return text + "\n[output truncated]" if truncated else text


CANCEL_POLL_SECONDS = 0.2


def _run_capped(
    cmd: list[str],
    stdin_bytes: bytes,
    timeout: float,
    cancel: threading.Event,
    cap: int = OUTPUT_CAP_BYTES,
) -> _Captured:
    """Run a process, feed it stdin, keep at most `cap` bytes per stream.

    Streams are always drained even past the cap so the child never blocks on a
    full pipe, but worker memory stays bounded however much the job prints.
    """
    # argv list, no shell; cmd comes from create_args()/fixed docker subcommands, never a shell string
    proc = subprocess.Popen(  # noqa: S603
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        bufsize=0,
    )
    bufs = {"out": bytearray(), "err": bytearray()}
    truncated = {"out": False, "err": False}

    def reader(stream, key: str) -> None:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            room = cap - len(bufs[key])
            if room > 0:
                bufs[key] += chunk[:room]
            if len(chunk) > room:
                truncated[key] = True

    def writer() -> None:
        try:
            view = memoryview(stdin_bytes)
            while view:
                view = view[proc.stdin.write(view):]
        except OSError:
            pass  # process exited before reading everything
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    threads = [
        threading.Thread(target=reader, args=(proc.stdout, "out"), daemon=True),
        threading.Thread(target=reader, args=(proc.stderr, "err"), daemon=True),
        threading.Thread(target=writer, daemon=True),
    ]
    for t in threads:
        t.start()

    timed_out = cancelled = False
    deadline = time.monotonic() + timeout
    while True:
        try:
            proc.wait(timeout=CANCEL_POLL_SECONDS)
            break
        except subprocess.TimeoutExpired:
            if cancel.is_set():
                cancelled = True
            elif time.monotonic() >= deadline:
                timed_out = True
            if cancelled or timed_out:
                proc.kill()  # kills the docker CLI client only; the caller kills the container
                proc.wait()
                break
    for t in threads:
        t.join(timeout=5)

    stopped = timed_out or cancelled
    return _Captured(
        returncode=None if stopped else proc.returncode,
        stdout=_decode(bufs["out"], truncated["out"]),
        stderr=_decode(bufs["err"], truncated["err"]),
        timed_out=timed_out,
        cancelled=cancelled,
    )


class DockerExecutor:
    def __init__(self, images: dict[str, str] | None = None, docker_bin: str = "docker"):
        self._images = images or DEFAULT_IMAGES
        self._docker = docker_bin

    # ---- public ---------------------------------------------------------

    def run(self, spec: JobSpec, cancel: threading.Event | None = None) -> ExecResult:
        cancel = cancel or threading.Event()
        image = self._images.get(spec.runtime.value)
        if image is None:
            raise ExecutorError(f"no runner image configured for runtime {spec.runtime.value!r}")

        name = f"dwee-{uuid.uuid4().hex[:16]}"
        started = time.monotonic()
        try:
            self._create(name, image, spec)
            if cancel.is_set():  # cancelled while the container was being created
                return ExecResult(
                    status=AttemptStatus.CANCELLED, exit_code=None, stdout="", stderr="",
                    duration_seconds=time.monotonic() - started,
                    error="cancelled before start",
                )
            cap = self._start(name, spec, cancel)
            if cap.cancelled:
                self._kill(name)
                return ExecResult(
                    status=AttemptStatus.CANCELLED,
                    exit_code=None,
                    stdout=cap.stdout,
                    stderr=cap.stderr,
                    duration_seconds=time.monotonic() - started,
                    error="cancelled; container killed",
                )
            if cap.timed_out:
                self._kill(name)
                return ExecResult(
                    status=AttemptStatus.TIMED_OUT,
                    exit_code=None,
                    stdout=cap.stdout,
                    stderr=cap.stderr,
                    duration_seconds=time.monotonic() - started,
                    error=f"exceeded {spec.timeout_seconds}s timeout; container killed",
                )
            state = self._inspect(name)
            duration = time.monotonic() - started
        finally:
            self._remove(name)

        if state.get("Error"):
            raise ExecutorError(f"container failed to start: {state['Error']}")
        exit_code = int(state.get("ExitCode", -1))
        error = None
        if state.get("OOMKilled"):
            error = f"killed: out of memory (limit {spec.resources.memory_mb} MB)"
        status = AttemptStatus.SUCCEEDED if exit_code == 0 else AttemptStatus.FAILED
        return ExecResult(
            status=status,
            exit_code=exit_code,
            stdout=cap.stdout,
            stderr=cap.stderr,
            duration_seconds=duration,
            error=error,
        )

    # ---- building blocks ------------------------------------------------

    def create_args(self, name: str, image: str, spec: JobSpec) -> list[str]:
        mem = spec.resources.memory_mb
        boot = (
            "tar -x -f - -C /work && "
            f'exec timeout -s KILL {spec.timeout_seconds + BACKSTOP_GRACE_SECONDS} '
            'sh -c "$JOB_CMD"'
        )
        return [
            self._docker, "create", "-i",
            "--name", name,
            "--label", LABEL,
            "--pull", "never",
            "--user", NOBODY,
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--memory", f"{mem}m",
            "--memory-swap", f"{mem}m",  # equal to memory: no swap
            "--cpus", f"{spec.resources.cpu:g}",
            "--pids-limit", str(PIDS_LIMIT),
            "--network", "bridge" if spec.network_enabled else "none",
            "--tmpfs", "/tmp:rw,nosuid,size=64m,mode=1777",  # noqa: S108 - path inside the container, not the host
            "--tmpfs", f"/work:rw,nosuid,size={max(16, mem // 2)}m,mode=1777",
            "--workdir", "/work",
            "--env", f"JOB_CMD={spec.command}",
            "--env", "HOME=/tmp",
            image, "sh", "-c", boot,
        ]

    def _helper(self, *args: str) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(  # noqa: S603 - argv list, no shell, fixed docker subcommands
                [self._docker, *args], capture_output=True, text=True,
                timeout=HELPER_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise ExecutorError(f"docker binary not found: {self._docker!r}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ExecutorError(f"docker {args[0]} timed out") from exc

    def _create(self, name: str, image: str, spec: JobSpec) -> None:
        cmd = self.create_args(name, image, spec)
        try:
            p = subprocess.run(  # noqa: S603 - argv list from create_args(); the job command rides in an env var
                cmd, capture_output=True, text=True, timeout=HELPER_TIMEOUT_SECONDS
            )
        except FileNotFoundError as exc:
            raise ExecutorError(f"docker binary not found: {self._docker!r}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ExecutorError("docker create timed out") from exc
        if p.returncode != 0:
            raise ExecutorError(
                f"docker create failed (image {image!r} built? run docker/build-runners.sh): "
                f"{p.stderr.strip()}"
            )

    def _start(self, name: str, spec: JobSpec, cancel: threading.Event) -> _Captured:
        return _run_capped(
            [self._docker, "start", "-a", "-i", name],
            stdin_bytes=build_tar(spec.files),
            timeout=spec.timeout_seconds,
            cancel=cancel,
        )

    def _inspect(self, name: str) -> dict:
        p = self._helper("inspect", "--format", "{{json .State}}", name)
        if p.returncode != 0:
            raise ExecutorError(f"docker inspect failed: {p.stderr.strip()}")
        try:
            return json.loads(p.stdout)
        except json.JSONDecodeError as exc:
            raise ExecutorError("docker inspect returned invalid JSON") from exc

    def _kill(self, name: str) -> None:
        try:
            self._helper("kill", name)  # may fail if it just exited; that's fine
        except ExecutorError:
            log.warning("docker kill failed for %s", name, exc_info=True)

    def _remove(self, name: str) -> None:
        try:
            p = self._helper("rm", "-f", "-v", name)
            if p.returncode != 0 and "No such container" not in p.stderr:
                log.warning("docker rm failed for %s: %s", name, p.stderr.strip())
        except ExecutorError:
            log.warning("docker rm errored for %s", name, exc_info=True)
