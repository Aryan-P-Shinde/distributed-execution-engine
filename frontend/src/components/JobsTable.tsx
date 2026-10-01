import type { JobSummary } from '../api/types';
import { jobPath } from '../hooks/useHashRoute';
import { formatElapsed, relativeTime } from '../lib/format';
import { StatusBadge } from './StatusBadge';

export function JobsTable({ jobs, now }: { jobs: JobSummary[]; now: number }) {
  if (jobs.length === 0) return <p className="empty">No jobs yet. Submit one to see it here.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>ID</th><th>Type</th><th>Status</th><th>Worker</th><th>Created</th><th>Duration</th></tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.id}>
              <td><a className="mono" href={jobPath(j.id)}>#{j.id}</a></td>
              <td>{j.job_type} <span className="dim">· {j.runtime}</span></td>
              <td>
                <StatusBadge status={j.status} />
                {j.attempt_count > 1 && <span className="dim"> · {j.attempt_count} attempts</span>}
              </td>
              <td className="mono">{j.worker_id ?? <span className="dim">—</span>}</td>
              <td title={j.created_at}>{relativeTime(j.created_at, now)}</td>
              <td className="mono">{formatElapsed(j.started_at, j.completed_at, now)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
