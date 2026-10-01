import { useState } from 'react';
import { api } from '../api/client';
import type { Attempt, Job, Logs } from '../api/types';
import { StatusBadge } from '../components/StatusBadge';
import { useNow } from '../hooks/useNow';
import { usePolling } from '../hooks/usePolling';
import { formatClock, formatElapsed, isSettled, isTerminalJob, relativeTime } from '../lib/format';
import { POLL_MS } from './Dashboard';

function Timeline({ job, now }: { job: Job; now: number }) {
  const steps: [string, string | null][] = [
    ['Created', job.created_at], ['Queued', job.queued_at],
    ['Started', job.started_at], ['Finished', job.completed_at],
  ];
  return (
    <ol className="timeline">
      {steps.map(([label, ts]) => (
        <li key={label} className={ts ? 'done' : ''}>
          <span className="tl-label">{label}</span>
          <span className="tl-time">{ts ? `${formatClock(ts)} · ${relativeTime(ts, now)}` : '—'}</span>
        </li>
      ))}
    </ol>
  );
}

function AttemptsTable({ attempts, selected, onSelect, now }: {
  attempts: Attempt[]; selected: number; onSelect: (n: number) => void; now: number;
}) {
  if (attempts.length === 0) return <p className="empty">No attempt has started yet.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>#</th><th>Worker</th><th>Status</th><th>Exit</th><th>Duration</th><th>Detail</th></tr></thead>
        <tbody>
          {attempts.map((a) => (
            <tr key={a.id} className={a.attempt_number === selected ? 'selected' : ''}
                onClick={() => onSelect(a.attempt_number)} tabIndex={0}
                onKeyDown={(e) => { if (e.key === 'Enter') onSelect(a.attempt_number); }}>
              <td>{a.attempt_number}</td>
              <td className="mono">{a.worker_id ?? '—'}</td>
              <td><StatusBadge status={a.status} /></td>
              <td className="mono">{a.exit_code ?? '—'}</td>
              <td className="mono">{formatElapsed(a.started_at, a.finished_at, now)}</td>
              <td className="dim">{a.error ?? ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LogsPanel({ logs, running }: { logs: Logs | undefined; running: boolean }) {
  if (!logs) return <p className="empty">Loading logs…</p>;
  if (logs.attempt_number === null) return <p className="empty">Logs appear once an attempt has run.</p>;
  return (
    <div className="logs">
      {running && <p className="hint">Output is stored when an attempt finishes, so it will appear here then.</p>}
      <h3>stdout <span className="dim">attempt {logs.attempt_number}</span></h3>
      <pre data-testid="stdout">{logs.stdout || '(empty)'}</pre>
      <h3>stderr</h3>
      <pre className={logs.stderr ? 'stderr' : ''} data-testid="stderr">{logs.stderr || '(empty)'}</pre>
    </div>
  );
}

export function JobDetail({ id }: { id: number }) {
  const now = useNow();
  const [selected, setSelected] = useState<number>();
  const [cancelError, setCancelError] = useState<string>();
  const [cancelling, setCancelling] = useState(false);

  // Poll until the job is terminal AND every attempt has closed. When that becomes
  // true the interval goes to null, which re-runs each poll once: a final fetch
  // that is guaranteed to postdate the last status change.
  const [settledFlag, setSettledFlag] = useState(false);
  const interval = settledFlag ? null : POLL_MS;

  const job = usePolling((s) => api.job(id, s), interval, [id]);
  const attempts = usePolling((s) => api.attempts(id, s), interval, [id]);
  const logs = usePolling((s) => api.logs(id, selected, s), interval, [id, selected]);

  const settled = job.data && attempts.data ? isSettled(job.data, attempts.data) : false;
  if (settled !== settledFlag) setSettledFlag(settled);

  if (job.error && !job.data) {
    return <p className="error-text">{(job.error as { status?: number }).status === 404
      ? `Job #${id} does not exist.` : `Could not load job: ${job.error.message}`}</p>;
  }
  if (!job.data) return <p className="empty">Loading…</p>;
  const j = job.data;
  const list = attempts.data ?? [];
  const latest = list.at(-1)?.attempt_number ?? 0;
  const shownAttempt = selected ?? latest;
  const runningAttempt = list.find((a) => a.attempt_number === shownAttempt)?.status === 'RUNNING';

  async function cancel() {
    setCancelError(undefined);
    setCancelling(true);
    try {
      await api.cancel(id);
      job.refresh(); attempts.refresh(); logs.refresh();
    } catch (e) {
      setCancelError((e as Error).message);
    } finally {
      setCancelling(false);
    }
  }

  const spec = j.spec;
  return (
    <>
      <p><a href="#/">← Overview</a></p>
      <div className="row-between">
        <h1>Job <span className="mono">#{j.id}</span> <StatusBadge status={j.status} /></h1>
        {!isTerminalJob(j.status) && (
          <button className="btn btn-danger" onClick={cancel} disabled={cancelling}>
            {cancelling ? 'Cancelling…' : 'Cancel job'}
          </button>
        )}
      </div>
      {cancelError && <p className="error-text" role="alert">{cancelError}</p>}
      {job.error && <p className="warn-text">Connection problem, showing the last data received.</p>}
      {j.result_summary && <p className="summary">{j.result_summary}</p>}

      <div className="two-col">
        <section>
          <h2>Lifecycle</h2>
          <Timeline job={j} now={now} />
          <dl className="facts">
            <dt>Worker</dt><dd className="mono">{j.worker_id ?? '—'}</dd>
            <dt>Exit code</dt><dd className="mono">{j.exit_code ?? '—'}</dd>
            <dt>Duration</dt><dd className="mono">{formatElapsed(j.started_at, j.completed_at, now)}</dd>
            <dt>Attempts</dt><dd>{j.attempt_count} of {spec.max_retries + 1} allowed</dd>
          </dl>
        </section>
        <section>
          <h2>Configuration</h2>
          <dl className="facts">
            <dt>Type</dt><dd>{spec.type} · {spec.runtime}</dd>
            <dt>Command</dt><dd className="mono">{spec.command}</dd>
            <dt>Timeout</dt><dd>{spec.timeout_seconds}s</dd>
            <dt>CPU</dt><dd>{spec.resources.cpu} cores</dd>
            <dt>Memory</dt><dd>{spec.resources.memory_mb} MB</dd>
            <dt>Max retries</dt><dd>{spec.max_retries}</dd>
            <dt>Network</dt><dd>{spec.network_enabled ? 'enabled' : 'disabled'}</dd>
          </dl>
          {Object.entries(spec.files).map(([path, content]) => (
            <details key={path}><summary className="mono">{path}</summary><pre>{content}</pre></details>
          ))}
        </section>
      </div>

      <section>
        <h2>Attempts</h2>
        <AttemptsTable attempts={list} selected={shownAttempt} onSelect={setSelected} now={now} />
      </section>
      <section>
        <h2>Logs</h2>
        <LogsPanel logs={logs.data} running={runningAttempt} />
      </section>
    </>
  );
}
