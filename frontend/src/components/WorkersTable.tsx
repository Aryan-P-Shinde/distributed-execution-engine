import type { Worker } from '../api/types';
import { jobPath } from '../hooks/useHashRoute';
import { StatusBadge } from './StatusBadge';

export function WorkersTable({ workers }: { workers: Worker[] }) {
  if (workers.length === 0) return <p className="empty">No workers have registered.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Worker</th><th>State</th><th>Runtimes</th><th>Current job</th><th>Last heartbeat</th></tr>
        </thead>
        <tbody>
          {workers.map((w) => (
            <tr key={w.id}>
              <td className="mono" title={`host ${w.hostname}`}>{w.id}</td>
              <td><StatusBadge status={w.status} /></td>
              <td>{w.runtimes.join(', ')}</td>
              <td>
                {w.current_job_id === null
                  ? <span className="dim">—</span>
                  : <a className="mono" href={jobPath(w.current_job_id)}>#{w.current_job_id}</a>}
              </td>
              <td className={w.status === 'UNHEALTHY' || w.status === 'DEAD' ? 'warn-text' : ''}>
                {w.status === 'STOPPED' ? 'stopped cleanly' : `${w.seconds_since_heartbeat}s ago`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
