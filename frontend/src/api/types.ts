// Mirrors the FastAPI response models (api/schemas.py, shared/spec.py).
// scripts/contract-check.mjs verifies these against a running API.

export type JobStatus =
  | 'CREATED' | 'QUEUED' | 'SCHEDULED' | 'RUNNING'
  | 'SUCCEEDED' | 'FAILED' | 'TIMED_OUT' | 'CANCELLED';

export type AttemptStatus =
  | 'ASSIGNED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'TIMED_OUT'
  | 'WORKER_LOST' | 'EXECUTOR_ERROR' | 'CANCELLED';

export type WorkerStatus = 'ONLINE' | 'BUSY' | 'UNHEALTHY' | 'DEAD' | 'STOPPED';

export type JobType = 'script' | 'test' | 'build';
export type Runtime = 'python' | 'node';

export interface JobSpec {
  type: JobType;
  runtime: Runtime;
  command: string;
  files: Record<string, string>;
  timeout_seconds: number;
  resources: { cpu: number; memory_mb: number };
  max_retries: number;
  network_enabled: boolean;
}

export interface Job {
  id: number;
  status: JobStatus;
  spec: JobSpec;
  attempt_count: number;
  worker_id: string | null;
  exit_code: number | null;
  result_summary: string | null;
  created_at: string;
  queued_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface JobSummary {
  id: number;
  status: JobStatus;
  job_type: string;
  runtime: string;
  worker_id: string | null;
  attempt_count: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface Attempt {
  id: number;
  attempt_number: number;
  worker_id: string | null;
  status: AttemptStatus;
  exit_code: number | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Logs {
  job_id: number;
  attempt_number: number | null;
  attempt_status: AttemptStatus | null;
  stdout: string;
  stderr: string;
}

export interface Worker {
  id: string;
  hostname: string;
  runtimes: string[];
  status: WorkerStatus;
  started_at: string;
  last_heartbeat_at: string;
  seconds_since_heartbeat: number;
  current_job_id: number | null;
}

export interface Stats {
  total_jobs: number;
  jobs: Record<JobStatus, number>;
  workers: Record<WorkerStatus, number>;
}

export interface Ready {
  status: 'ready' | 'degraded';
  database: string;
  redis: 'ok' | 'unavailable';
  controller: 'ok' | 'stale' | 'absent';
}
