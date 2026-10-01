import type { Attempt, AttemptStatus, Job, JobStatus, Stats } from '../api/types';

/** API timestamps are UTC. Postgres sends an offset; be tolerant of naive ones. */
export function parseTs(ts: string | null | undefined): number | null {
  if (!ts) return null;
  const withZone = /(Z|[+-]\d{2}:?\d{2})$/.test(ts) ? ts : `${ts}Z`;
  const ms = Date.parse(withZone);
  return Number.isNaN(ms) ? null : ms;
}

export function formatDuration(ms: number): string {
  if (ms < 0) ms = 0;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 10) return `${s.toFixed(1)}s`;
  const total = Math.floor(s);
  if (total < 60) return `${total}s`;
  const m = Math.floor(total / 60);
  if (m < 60) return `${m}m ${String(total % 60).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

/** Elapsed run time; counts up against `now` while unfinished. */
export function durationMs(
  startedAt: string | null, endedAt: string | null, now: number,
): number | null {
  const start = parseTs(startedAt);
  if (start === null) return null;
  return (parseTs(endedAt) ?? now) - start;
}

export function formatElapsed(startedAt: string | null, endedAt: string | null, now: number): string {
  const ms = durationMs(startedAt, endedAt, now);
  return ms === null ? '—' : formatDuration(ms);
}

export function relativeTime(ts: string | null, now: number): string {
  const t = parseTs(ts);
  if (t === null) return '—';
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 5) return 'just now';
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function formatClock(ts: string | null): string {
  const t = parseTs(ts);
  return t === null ? '—' : new Date(t).toLocaleString();
}

const TERMINAL_JOB: ReadonlySet<JobStatus> = new Set(['SUCCEEDED', 'FAILED', 'TIMED_OUT', 'CANCELLED']);
const FINAL_ATTEMPT: ReadonlySet<AttemptStatus> = new Set([
  'SUCCEEDED', 'FAILED', 'TIMED_OUT', 'WORKER_LOST', 'EXECUTOR_ERROR', 'CANCELLED',
]);

export const isTerminalJob = (s: JobStatus): boolean => TERMINAL_JOB.has(s);

/** Nothing further will change: job is terminal and every attempt has closed.
 *  (A cancelled job's attempt closes a moment later, when its worker stops the container.) */
export function isSettled(job: Job, attempts: readonly Attempt[]): boolean {
  return isTerminalJob(job.status) && attempts.every((a) => FINAL_ATTEMPT.has(a.status));
}

export interface Summary {
  total: number; queued: number; running: number; succeeded: number; failed: number;
  cancelled: number; activeWorkers: number; unavailableWorkers: number; stoppedWorkers: number;
}

/** The groupings the PRD asks the dashboard to show. */
export function summarize(s: Stats): Summary {
  const j = s.jobs, w = s.workers;
  return {
    total: s.total_jobs,
    queued: j.CREATED + j.QUEUED,
    running: j.SCHEDULED + j.RUNNING,
    succeeded: j.SUCCEEDED,
    failed: j.FAILED + j.TIMED_OUT,
    cancelled: j.CANCELLED,
    activeWorkers: w.ONLINE + w.BUSY,
    unavailableWorkers: w.UNHEALTHY + w.DEAD,
    stoppedWorkers: w.STOPPED,
  };
}
