#!/usr/bin/env python3
"""End-to-end smoke test against a RUNNING stack. Standard library only.

    docker compose --profile runners build && docker compose up -d --build
    python scripts/smoke_test.py                  # success, failing exit code, timeout
    python scripts/smoke_test.py --kill-worker    # also: kill a busy worker, expect recovery
    python scripts/smoke_test.py --only hello

--kill-worker needs the docker CLI on this machine (override with $DOCKER) and workers
that run as containers whose id is their worker id (the compose default). It takes
about a minute: the controller declares the worker dead after WORKER_DEAD_AFTER (30s).
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"}
DOCKER = os.environ.get("DOCKER", "docker")


def call(base: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict | list]:
    req = urllib.request.Request(
        base + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait_for(what: str, fn, timeout: float, every: float = 1.0):
    """Poll fn() until it returns something truthy; raise with `what` on timeout."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = fn()
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            last = None
        if last:
            return last
        time.sleep(every)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what}")


def python_job(code: str, **overrides) -> dict:
    spec = {"type": "script", "runtime": "python", "command": "python main.py",
            "files": {"main.py": code}, "timeout_seconds": 30}
    spec.update(overrides)
    return spec


def submit(base: str, spec: dict) -> int:
    status, body = call(base, "POST", "/jobs", spec)
    assert status == 201, f"POST /jobs -> {status}: {body}"
    return body["id"]


def finish(base: str, job_id: int, timeout: float) -> dict:
    def done():
        _, job = call(base, "GET", f"/jobs/{job_id}")
        return job if job["status"] in TERMINAL else None
    return wait_for(f"job {job_id} to reach a terminal state", done, timeout)


def attempts(base: str, job_id: int) -> list[dict]:
    _, atts = call(base, "GET", f"/jobs/{job_id}/attempts")
    return sorted(atts, key=lambda a: a["attempt_number"])


# --- scenarios ---------------------------------------------------------------------

def hello(base: str) -> None:
    jid = submit(base, python_job("print('hello-from-smoke')"))
    job = finish(base, jid, 60)
    assert job["status"] == "SUCCEEDED", job
    _, logs = call(base, "GET", f"/jobs/{jid}/logs")
    assert "hello-from-smoke" in logs["stdout"], logs
    assert [a["status"] for a in attempts(base, jid)] == ["SUCCEEDED"]


def failing(base: str) -> None:
    jid = submit(base, python_job("import sys; sys.stderr.write('boom'); sys.exit(3)"))
    job = finish(base, jid, 60)
    assert (job["status"], job["exit_code"]) == ("FAILED", 3), job
    _, logs = call(base, "GET", f"/jobs/{jid}/logs")
    assert "boom" in logs["stderr"], logs
    # a non-zero exit is the workload's fault: terminal, never retried
    assert len(attempts(base, jid)) == 1


def timeout(base: str) -> None:
    jid = submit(base, python_job("while True: pass", timeout_seconds=3))
    job = finish(base, jid, 60)
    assert job["status"] == "TIMED_OUT", job
    assert len(attempts(base, jid)) == 1  # a timeout is deterministic: not retried


def worker_loss(base: str) -> None:
    """The point of the project: a worker dies mid-job and the job still completes."""
    def two_workers():
        _, w = call(base, "GET", "/workers")
        return len([x for x in w if x["status"] in ("ONLINE", "BUSY")]) >= 2
    wait_for("two live workers (the retry needs a second one)", two_workers, 60)

    jid = submit(base, python_job("import time; print('start', flush=True); time.sleep(20); print('done')",
                                  timeout_seconds=120, max_retries=2))

    def running():
        _, job = call(base, "GET", f"/jobs/{jid}")
        return job if job["status"] == "RUNNING" and job["worker_id"] else None
    victim = wait_for("job to start RUNNING", running, 60)["worker_id"]

    ids = subprocess.run([DOCKER, "ps", "--format", "{{.ID}}"], capture_output=True, text=True,
                         check=True).stdout.split()
    match = [c for c in ids if c.startswith(victim) or victim.startswith(c)]
    assert len(match) == 1, f"no single container for worker {victim!r} among {ids}"
    subprocess.run([DOCKER, "kill", match[0]], capture_output=True, text=True, check=True)
    print(f"      killed worker {victim} mid-job; waiting for the controller to recover it")

    job = finish(base, jid, 180)
    atts = attempts(base, jid)
    assert job["status"] == "SUCCEEDED", (job, atts)
    assert [a["status"] for a in atts] == ["WORKER_LOST", "SUCCEEDED"], atts
    assert atts[0]["worker_id"] == victim and atts[1]["worker_id"] != victim, atts


SCENARIOS = {"hello": hello, "failing": failing, "timeout": timeout, "worker-loss": worker_loss}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get("API_URL", "http://localhost:8000"))
    ap.add_argument("--kill-worker", action="store_true", help="also run the worker-loss scenario")
    ap.add_argument("--only", choices=sorted(SCENARIOS), help="run a single scenario")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    names = [args.only] if args.only else ["hello", "failing", "timeout"] + (
        ["worker-loss"] if args.kill_worker else [])

    def ready():
        status, body = call(base, "GET", "/ready")
        if status != 200 or body.get("status") != "ready":
            return None
        _, stats = call(base, "GET", "/stats")
        return stats["workers"].get("ONLINE", 0) + stats["workers"].get("BUSY", 0) >= 1
    try:
        wait_for("API ready with a live worker", ready, 90)
    except AssertionError as e:
        print(f"FAIL  stack not ready: {e}")
        return 1

    failed = 0
    for name in names:
        t0 = time.monotonic()
        try:
            SCENARIOS[name](base)
            print(f"PASS  {name} ({time.monotonic() - t0:.1f}s)")
        except Exception as e:  # noqa: BLE001 - report every scenario, not just the first failure
            failed += 1
            print(f"FAIL  {name} ({time.monotonic() - t0:.1f}s): {e}")
    print(f"{len(names) - failed}/{len(names)} scenarios passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
