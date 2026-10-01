const TONE: Record<string, string> = {
  CREATED: 'muted', QUEUED: 'muted', SCHEDULED: 'warn', RUNNING: 'info',
  SUCCEEDED: 'ok', FAILED: 'bad', TIMED_OUT: 'warn', CANCELLED: 'muted',
  ASSIGNED: 'muted', WORKER_LOST: 'bad', EXECUTOR_ERROR: 'bad',
  ONLINE: 'ok', BUSY: 'info', UNHEALTHY: 'warn', DEAD: 'bad', STOPPED: 'muted',
};

/** Always shows the status as text; colour is reinforcement, not the signal. */
export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge badge-${TONE[status] ?? 'muted'}`}>{status.replace('_', ' ')}</span>;
}
