import type { Attempt, Job, JobSummary, Logs, Stats, Worker } from '../api/types';

export const T0 = '2026-01-01T12:00:00Z';

export const job = (patch: Partial<Job> = {}): Job => ({
  id: 7, status: 'RUNNING', attempt_count: 1, worker_id: 'w1', exit_code: null, result_summary: null,
  created_at: T0, queued_at: T0, started_at: T0, completed_at: null,
  spec: {
    type: 'script', runtime: 'python', command: 'python main.py', files: { 'main.py': "print('hi')" },
    timeout_seconds: 60, resources: { cpu: 1, memory_mb: 512 }, max_retries: 2, network_enabled: false,
  },
  ...patch,
});

export const attempt = (patch: Partial<Attempt> = {}): Attempt => ({
  id: 1, attempt_number: 1, worker_id: 'w1', status: 'RUNNING', exit_code: null, error: null,
  created_at: T0, started_at: T0, finished_at: null, ...patch,
});

export const logs = (patch: Partial<Logs> = {}): Logs => ({
  job_id: 7, attempt_number: 1, attempt_status: 'RUNNING', stdout: '', stderr: '', ...patch,
});

export const summary = (patch: Partial<JobSummary> = {}): JobSummary => ({
  id: 7, status: 'RUNNING', job_type: 'script', runtime: 'python', worker_id: 'w1', attempt_count: 1,
  created_at: T0, started_at: T0, completed_at: null, ...patch,
});

export const worker = (patch: Partial<Worker> = {}): Worker => ({
  id: 'w1', hostname: 'host-1', runtimes: ['python', 'node'], status: 'ONLINE', started_at: T0,
  last_heartbeat_at: T0, seconds_since_heartbeat: 2.1, current_job_id: null, ...patch,
});

export const stats = (patch: Partial<Stats> = {}): Stats => ({
  total_jobs: 10,
  jobs: { CREATED: 0, QUEUED: 2, SCHEDULED: 0, RUNNING: 1, SUCCEEDED: 5, FAILED: 1, TIMED_OUT: 1, CANCELLED: 0 },
  workers: { ONLINE: 1, BUSY: 1, UNHEALTHY: 0, DEAD: 1, STOPPED: 0 },
  ...patch,
});
