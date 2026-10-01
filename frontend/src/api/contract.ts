import type { Attempt, AttemptStatus, Job, JobSpec, JobStatus, JobSummary, Logs, Ready, Stats, Worker, WorkerStatus } from './types';

// Key lists derived from the TypeScript interfaces. `Record<keyof T, true>` makes
// the compiler reject a list that is missing a field or names one that does not
// exist, so these cannot drift from types.ts. contract.live.test.ts compares
// them to a running API in both directions (no missing, no extra fields).
const keys = <T,>(o: Record<keyof T, true>): string[] => Object.keys(o).sort();

export const SHAPES = {
  job: keys<Job>({ id: true, status: true, spec: true, attempt_count: true, worker_id: true, exit_code: true,
    result_summary: true, created_at: true, queued_at: true, started_at: true, completed_at: true }),
  spec: keys<JobSpec>({ type: true, runtime: true, command: true, files: true, timeout_seconds: true,
    resources: true, max_retries: true, network_enabled: true }),
  summary: keys<JobSummary>({ id: true, status: true, job_type: true, runtime: true, worker_id: true,
    attempt_count: true, created_at: true, started_at: true, completed_at: true }),
  attempt: keys<Attempt>({ id: true, attempt_number: true, worker_id: true, status: true, exit_code: true,
    error: true, created_at: true, started_at: true, finished_at: true }),
  logs: keys<Logs>({ job_id: true, attempt_number: true, attempt_status: true, stdout: true, stderr: true }),
  worker: keys<Worker>({ id: true, hostname: true, runtimes: true, status: true, started_at: true,
    last_heartbeat_at: true, seconds_since_heartbeat: true, current_job_id: true }),
  stats: keys<Stats>({ total_jobs: true, jobs: true, workers: true }),
  ready: keys<Ready>({ status: true, database: true, redis: true, controller: true }),
} as const;

export const JOB_STATUSES = keys<Record<JobStatus, unknown>>({
  CREATED: true, QUEUED: true, SCHEDULED: true, RUNNING: true, SUCCEEDED: true, FAILED: true, TIMED_OUT: true, CANCELLED: true });
export const ATTEMPT_STATUSES = keys<Record<AttemptStatus, unknown>>({
  ASSIGNED: true, RUNNING: true, SUCCEEDED: true, FAILED: true, TIMED_OUT: true, WORKER_LOST: true,
  EXECUTOR_ERROR: true, CANCELLED: true });
export const WORKER_STATUSES = keys<Record<WorkerStatus, unknown>>({
  ONLINE: true, BUSY: true, UNHEALTHY: true, DEAD: true, STOPPED: true });
