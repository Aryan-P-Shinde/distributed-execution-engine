import type { Ready } from '../api/types';

const tone = (ok: boolean) => (ok ? 'ok' : 'warn');

/** Dependency health from /ready. Degraded still serves traffic; say what is degraded. */
export function HealthPills({ ready, unreachable }: { ready: Ready | undefined; unreachable: boolean }) {
  if (unreachable) return <span className="pill pill-bad">API unreachable</span>;
  if (!ready) return null;
  return (
    <span className="pills">
      <span className={`pill pill-${tone(ready.status === 'ready')}`}>{ready.status}</span>
      <span className={`pill pill-${tone(ready.redis === 'ok')}`} title="Only shortens worker wake-up; workers also poll Postgres">
        redis {ready.redis}
      </span>
      <span className={`pill pill-${tone(ready.controller === 'ok')}`} title="Recovers jobs from dead workers">
        controller {ready.controller}
      </span>
    </span>
  );
}
