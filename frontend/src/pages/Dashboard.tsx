import { useState } from 'react';
import { api } from '../api/client';
import type { JobStatus } from '../api/types';
import { HealthPills } from '../components/HealthPills';
import { JobsTable } from '../components/JobsTable';
import { StatsBar } from '../components/StatsBar';
import { WorkersTable } from '../components/WorkersTable';
import { useNow } from '../hooks/useNow';
import { usePolling } from '../hooks/usePolling';

export const POLL_MS = 2000;
const PAGE = 25;
const FILTERS: (JobStatus | '')[] = ['', 'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'TIMED_OUT', 'CANCELLED'];

export function Dashboard() {
  const [limit, setLimit] = useState(PAGE);
  const [filter, setFilter] = useState<JobStatus | ''>('');
  const now = useNow();

  const overview = usePolling((s) => Promise.all([api.stats(s), api.ready(s)]), POLL_MS);
  const jobs = usePolling((s) => api.jobs({ limit, status: filter || undefined }, s), POLL_MS, [limit, filter]);
  const workers = usePolling((s) => api.workers(s), POLL_MS);

  const [stats, ready] = overview.data ?? [undefined, undefined];
  const shown = jobs.data?.length ?? 0;

  return (
    <>
      <div className="row-between">
        <h1>Overview</h1>
        <HealthPills ready={ready} unreachable={overview.error !== undefined && overview.data === undefined} />
      </div>
      <StatsBar stats={stats} />

      <section>
        <div className="row-between">
          <h2>Jobs</h2>
          <label className="inline">Status
            <select value={filter} onChange={(e) => { setFilter(e.target.value as JobStatus | ''); setLimit(PAGE); }}>
              {FILTERS.map((f) => <option key={f} value={f}>{f || 'all'}</option>)}
            </select>
          </label>
        </div>
        {jobs.error && jobs.data === undefined
          ? <p className="error-text">Could not load jobs: {jobs.error.message}</p>
          : <JobsTable jobs={jobs.data ?? []} now={now} />}
        {shown >= limit && (
          <button className="btn btn-ghost" onClick={() => setLimit(limit + PAGE)}>Show more</button>
        )}
      </section>

      <section>
        <h2>Workers</h2>
        {workers.error && workers.data === undefined
          ? <p className="error-text">Could not load workers: {workers.error.message}</p>
          : <WorkersTable workers={workers.data ?? []} />}
        <p className="hint">
          Health comes from heartbeat age: unhealthy after 15s, dead after 30s. CPU and memory usage per worker
          are not collected yet.
        </p>
      </section>
    </>
  );
}
