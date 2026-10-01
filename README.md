# Distributed Workload Execution Engine

Accepts jobs over an API, queues them in PostgreSQL, runs each one inside a
locked-down Docker container on one of N workers, and records status, logs and
attempt history. A React dashboard shows jobs, workers and logs, and a separate
controller recovers jobs from dead workers. See "What exists" and "Behaviour worth knowing".

## Layout

```
api/            FastAPI control plane (submit, list, inspect, cancel jobs; workers; stats; health)
frontend/       React + TypeScript dashboard (Vite), polls the API every 2s
shared/         used by API and worker: job spec, state machine, DB models, Redis wake-up
worker/         claim loop, Docker executor, result reporting, heartbeat, process entrypoint
controller/     recovery process: retries or fails jobs held by dead or wedged workers
docker/         Dockerfiles (api, worker, controller) and the runner images jobs execute in
tests/          unit, in-process e2e, and service-backed integration tests
scripts/        smoke_test.py: end-to-end check against a running stack
.github/        CI workflow (workflows/ci.yml), Dependabot config, CI helper scripts
docker-compose.yml
pyproject.toml
```

## Setup

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Windows (Git Bash): the venv script lives in `Scripts/`, not `bin/`, and the command is
`python`, not `python3.12`:

```bash
python --version                  # must be 3.12 or newer
python -m venv .venv && source .venv/Scripts/activate
pip install -e ".[dev]"
```

You only need the venv for backend tests. Running the system needs Docker Desktop (WSL2
backend). Not verified on native Windows Python: `tests/test_docker_executor.py` uses a
shell-script fake docker and is expected to fail there; run the backend suite from WSL.

## Run the whole stack (compose)

```bash
docker compose --profile runners build   # once: the images jobs run inside
docker compose up --build                # postgres, redis, api, 2 workers, 2 controllers, dashboard
```

Dashboard on http://localhost:3000. API on http://localhost:8000 (interactive docs at /docs).

```bash
# submit
curl -s -X POST localhost:8000/jobs -H 'content-type: application/json' -d '{
  "type": "script", "runtime": "python", "command": "python main.py",
  "files": {"main.py": "print(\"hello\")"}}'

curl -s localhost:8000/jobs                # list
curl -s localhost:8000/jobs/1              # status
curl -s localhost:8000/jobs/1/logs         # stdout/stderr of latest attempt
curl -s localhost:8000/jobs/1/attempts     # attempt history
curl -s -X POST localhost:8000/jobs/1/cancel   # cancel (queued or running)
curl -s localhost:8000/workers             # workers: status, heartbeat age, current job

# timeout: infinite loop is killed after 3s -> TIMED_OUT
curl -s -X POST localhost:8000/jobs -H 'content-type: application/json' -d '{
  "type": "script", "runtime": "python", "command": "python main.py",
  "files": {"main.py": "while True: pass"}, "timeout_seconds": 3}'

# pytest job
curl -s -X POST localhost:8000/jobs -H 'content-type: application/json' -d '{
  "type": "test", "runtime": "python", "command": "pytest -q",
  "files": {"test_a.py": "def test_ok():\n    assert 1 + 1 == 2\n"}}'

docker compose up -d --scale worker=4      # more workers
docker compose logs -f worker              # claim/finish events per worker
docker compose down -v                     # stop and wipe the database
```

## Run pieces by hand (API and workers outside compose)

```bash
docker compose up -d postgres redis
docker compose --profile runners build
export DATABASE_URL=postgresql+psycopg://engine:engine@localhost:5432/engine
export REDIS_URL=redis://localhost:6379/0

uvicorn api.main:app --reload                       # terminal 1
WORKER_ID=w1 python -m worker.main                  # terminal 2 (needs the docker CLI)
WORKER_ID=w2 python -m worker.main                  # terminal 3
python -m controller.main                           # terminal 4 (recovery)
```

Worker settings (env): `DATABASE_URL`, `REDIS_URL`, `WORKER_ID` (default: hostname),
`WORKER_RUNTIMES` (default `python,node`), `POLL_INTERVAL` seconds (default 5),
`LOG_LEVEL`.

Health timing (env, **must be identical for the API and every worker**):
`WORKER_HEARTBEAT_INTERVAL` (5s), `WORKER_UNHEALTHY_AFTER` (15s), `WORKER_DEAD_AFTER` (30s).
Invalid combinations (unhealthy < 2 beats, dead <= unhealthy) fail at startup.

## Dashboard (development)

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api to http://localhost:8000
npm run typecheck && npm test
npm run build        # production bundle in frontend/dist
```

Set `API_URL` to point the dev proxy at another API. The dashboard polls every 2s (paused
while the tab is hidden), keeps the last good data through an outage, and says so.

Contract and smoke check against a running stack (API, Postgres, Redis, a worker, the
controller). It compares every field the dashboard reads with what the API really sends,
in both directions, then renders the real pages against real responses:

```bash
CONTRACT_BASE_URL=http://localhost:8000 npm run contract
```

## Tests

```bash
ruff check .                             # lint (config in pyproject.toml)
pytest                                   # tests needing an absent Redis / Postgres /
                                         # Docker skip instead of failing

docker compose up -d postgres redis
docker compose exec postgres createdb -U engine engine_test
pytest -rs                               # -rs lists anything still skipped

pytest tests/test_docker_integration.py -v   # real isolation tests (Docker + runner images)
```

`tests/test_claim_postgres.py` drops and recreates tables in `engine_test`
(override with `TEST_DATABASE_URL`). Never point it at a database you care about.

End-to-end check against a running compose stack (about a minute with `--kill-worker`,
which kills a busy worker and expects the controller to recover its job):

```bash
python scripts/smoke_test.py                 # success, failing exit code, timeout
python scripts/smoke_test.py --kill-worker   # plus worker loss and recovery
```

## CI

`.github/workflows/ci.yml` runs on every push to `main` and every pull request, as four
independent jobs so one failure does not hide another:

| Job | What it runs | What it proves |
|---|---|---|
| `backend` | `ruff check`, then pytest against Postgres 16 and Redis 7 service containers | logic, DB claim/recovery SQL and Redis wake-up on the real engines |
| `frontend` | `npm ci`, typecheck, vitest, production build | dashboard compiles, tests pass, bundle builds |
| `docker-isolation` | builds the runner images, runs `tests/test_docker_integration.py` on the runner's Docker daemon | non-root, read-only rootfs, no network, memory/timeout kills, fork-bomb containment, no leftover containers |
| `stack` | builds every image, `docker compose up --wait`, `scripts/smoke_test.py --kill-worker`, checks the dashboard and its `/api` proxy, runs the live contract test | the Dockerfiles and compose file work, and a worker killed mid-job still ends in a finished job |

Two guards against a green build that checked nothing. The `backend` and `docker-isolation`
jobs fail if **any** test was skipped (`.github/scripts/assert_no_skips.py`), because tests
skip themselves when Postgres, Redis or Docker is unreachable. The `stack` job fails if
compose cannot bring every service up healthy.

Dependabot proposes weekly updates for GitHub Actions, pip and npm.

Not wired yet: image publishing and deployment (see Not yet, below).

## What exists

- Job spec validation, job/attempt state machines, retry decision (`shared/`)
- Submit/list/get/cancel jobs, attempts, logs, `/health`, `/ready` (`api/`)
- Postgres-claimed queue (`FOR UPDATE SKIP LOCKED`), Redis wake-up, poll fallback
- Docker executor: non-root, read-only rootfs, no network by default, CPU/memory/pid
  limits, timeout kill, output cap, cleanup
- Worker process with fenced result reporting
- Recovery controller (own process, any number of replicas): jobs held by dead or
  missing workers, overdue jobs on wedged workers, and claimed-but-never-started
  jobs are retried or failed, with every attempt and reason recorded
- Retries for failures that are not the workload's fault (worker lost, executor
  error); workload failures (non-zero exit, OOM, timeout) are never retried
- Dashboard: totals, jobs (filter, paging), workers, job detail (lifecycle, attempts, logs,
  cancel), job submission with examples
- Worker registration, heartbeats on a dedicated thread, `GET /workers` with derived
  status (ONLINE / BUSY / UNHEALTHY / DEAD / STOPPED)

Not yet: AWS deployment, auth, live log streaming.

## Security model (short)

Docker isolation shares the host kernel. This is not a hostile-code sandbox.
In compose, workers mount `/var/run/docker.sock`, which is effectively root on the
host. Fine for local dev; a production design would isolate workers on dedicated
hosts or use a stronger runtime (gVisor, microVMs).

## Behaviour worth knowing

- The dashboard shows what the API reports and nothing it cannot know. Per-worker CPU and
  memory usage are not collected, so they are not shown. Logs appear when an attempt
  finishes, not while it runs. There is no authentication.
- Running durations use the server's clock (taken from response `Date` headers), not the
  browser's.

- Worker health is derived when you read it, from heartbeat age measured against the
  database clock (worker and API clocks never enter the comparison). Only STOPPED,
  a clean shutdown, is stored.
- A worker id can be reused after a clean stop, or once a crashed worker has gone
  unhealthy. Starting a second worker with a live id exits with an error, because
  job ownership is fenced on the id.
- Recovery: a job whose worker is DEAD (30s without heartbeat) is taken from it. The
  attempt is closed as WORKER_LOST, then the job is requeued if `max_retries` allows,
  else marked FAILED with the reason in `result_summary`. A RUNNING job past
  `timeout_seconds + OVERDUE_GRACE` (30s) on a worker that still heartbeats is treated
  the same way. If the old worker turns out to be alive, it notices it lost the job,
  kills its container, and its late result is discarded.
- `max_retries` defaults to **2** (max 5). A retry can run the workload a second time,
  so set `max_retries: 0` for jobs with non-idempotent side effects.
- Retrying is bounded on purpose: a job that wedges every worker it lands on can take
  out at most `max_retries + 1` of them.
- Controller settings (env): `CONTROLLER_INTERVAL` (5s), `OVERDUE_GRACE` (30s, must
  exceed the 5s in-container backstop), `SCHEDULED_GRACE` (60s).
- `/ready` reports `controller: ok | stale | absent`. The controller heartbeats only
  after a successful pass, so `stale` means recovery is failing, not just slow.
- Heartbeats prove the process is alive, not that it is making progress.

- Cancel: the job reads CANCELLED immediately. For a running job the owning worker
  polls Postgres about once a second, kills the container, and closes the attempt
  as CANCELLED (partial logs kept). A result that arrives after cancel never
  changes the job. Cancelling a finished job returns 409; cancelling twice is a no-op.

- Logs are stored when an attempt finishes; there is no live streaming yet.
- `/work` inside job containers is a tmpfs (half the memory limit), so jobs that
  write hundreds of MB (for example `npm install`) will not fit.
