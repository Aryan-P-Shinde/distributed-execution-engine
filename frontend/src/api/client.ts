import type { Attempt, Job, JobSpec, JobStatus, JobSummary, Logs, Ready, Stats, Worker } from './types';

const BASE = '/api';

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

// The browser's clock is not the server's. Durations of running jobs are
// "server now - server started_at", so track the offset from each response's
// Date header (1s resolution, plenty for a dashboard).
let clockOffsetMs = 0;
export const serverNow = (): number => Date.now() + clockOffsetMs;
export const _setClockOffsetForTest = (ms: number): void => { clockOffsetMs = ms; };

/** Turn a FastAPI error body into one readable line per problem. */
export function describeError(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => {
        const field = (d.loc ?? []).filter((x) => x !== 'body').join('.');
        return field ? `${field}: ${d.msg}` : String(d.msg);
      })
      .join('\n');
  }
  return `Request failed (HTTP ${status})`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      ...init,
      headers: { 'content-type': 'application/json', ...init.headers },
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    throw new ApiError(0, 'Cannot reach the API');
  }
  const date = Date.parse(res.headers.get('date') ?? '');
  if (!Number.isNaN(date)) clockOffsetMs = date - Date.now();

  if (!res.ok) {
    let body: unknown = null;
    try { body = await res.json(); } catch { /* non-JSON error page */ }
    throw new ApiError(res.status, describeError(res.status, body));
  }
  return (await res.json()) as T;
}

const qs = (params: Record<string, string | number | undefined>): string => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined) p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : '';
};

export const api = {
  stats: (signal?: AbortSignal) => request<Stats>('/stats', { signal }),
  ready: (signal?: AbortSignal) => request<Ready>('/ready', { signal }),
  jobs: (p: { limit: number; status?: JobStatus }, signal?: AbortSignal) =>
    request<JobSummary[]>(`/jobs${qs({ limit: p.limit, status: p.status })}`, { signal }),
  job: (id: number, signal?: AbortSignal) => request<Job>(`/jobs/${id}`, { signal }),
  attempts: (id: number, signal?: AbortSignal) => request<Attempt[]>(`/jobs/${id}/attempts`, { signal }),
  logs: (id: number, attempt?: number, signal?: AbortSignal) =>
    request<Logs>(`/jobs/${id}/logs${qs({ attempt })}`, { signal }),
  workers: (signal?: AbortSignal) => request<Worker[]>('/workers', { signal }),
  submit: (spec: JobSpec) => request<Job>('/jobs', { method: 'POST', body: JSON.stringify(spec) }),
  cancel: (id: number) => request<Job>(`/jobs/${id}/cancel`, { method: 'POST' }),
};
