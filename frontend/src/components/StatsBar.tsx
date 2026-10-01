import type { Stats } from '../api/types';
import { summarize } from '../lib/format';

function Card({ label, value, tone, hint }: { label: string; value: number | string; tone?: string; hint?: string }) {
  return (
    <div className={`card${tone ? ` card-${tone}` : ''}`} title={hint}>
      <div className="card-value">{value}</div>
      <div className="card-label">{label}</div>
    </div>
  );
}

export function StatsBar({ stats }: { stats: Stats | undefined }) {
  if (!stats) return <div className="cards" aria-busy="true"><div className="card card-skeleton">Loading…</div></div>;
  const s = summarize(stats);
  return (
    <div className="cards" aria-label="System totals">
      <Card label="Total jobs" value={s.total} />
      <Card label="Queued" value={s.queued} tone="muted" />
      <Card label="Running" value={s.running} tone="info" hint="Scheduled or running" />
      <Card label="Succeeded" value={s.succeeded} tone="ok" />
      <Card label="Failed" value={s.failed} tone="bad" hint="Failed or timed out" />
      <Card label="Active workers" value={s.activeWorkers} tone="ok" hint="Online or busy" />
      <Card
        label="Unavailable workers" value={s.unavailableWorkers}
        tone={s.unavailableWorkers > 0 ? 'bad' : 'muted'} hint="Unhealthy or dead"
      />
    </div>
  );
}
