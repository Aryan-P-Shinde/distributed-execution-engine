/**
 * Contract + smoke test against a RUNNING stack. Skipped unless CONTRACT_BASE_URL is set.
 *
 *   (API, Postgres, Redis, at least one worker and the controller running)
 *   CONTRACT_BASE_URL=http://localhost:8000 npm run contract
 *
 * Checks that every field the dashboard relies on exists with the expected
 * shape (and that the API sends nothing the types do not know about), then
 * renders the real pages against real responses.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { JobDetail } from '../pages/JobDetail';
import { Dashboard } from '../pages/Dashboard';
import { parseTs } from '../lib/format';
import { ATTEMPT_STATUSES, JOB_STATUSES, SHAPES, WORKER_STATUSES } from './contract';
import { describeError } from './client';

const BASE = process.env.CONTRACT_BASE_URL;
const sorted = (o: object) => Object.keys(o).sort();

async function call(method: string, path: string, body?: unknown) {
  const res = await fetch(BASE + path, {
    method, headers: { 'content-type': 'application/json' }, body: body ? JSON.stringify(body) : undefined,
  });
  return { status: res.status, body: await res.json() };
}

describe.skipIf(!BASE)('live API contract', () => {
  let jobId = 0;

  beforeAll(async () => {
    // route the client's /api/* calls to the real server
    const real = globalThis.fetch;
    vi.stubGlobal('fetch', (input: string, init?: RequestInit) =>
      real(String(input).startsWith('/api') ? BASE + String(input).slice(4) : input, init));

    const spec = { type: 'script', runtime: 'python', command: 'python main.py', files: { 'main.py': 'print(1)' } };
    const { status, body } = await call('POST', '/jobs', spec);
    expect(status).toBe(201);
    jobId = body.id;
    for (let i = 0; i < 100; i++) { // wait for a worker to finish it
      const j = (await call('GET', `/jobs/${jobId}`)).body;
      if (['SUCCEEDED', 'FAILED', 'TIMED_OUT', 'CANCELLED'].includes(j.status)) break;
      await new Promise((r) => setTimeout(r, 300));
    }
  }, 60_000);
  afterAll(() => vi.unstubAllGlobals());

  it('GET /ready', async () => {
    const { status, body } = await call('GET', '/ready');
    expect(status).toBe(200);
    expect(sorted(body)).toEqual(SHAPES.ready);
    expect(['ready', 'degraded']).toContain(body.status);
  });

  it('POST /jobs and GET /jobs/{id} return the Job shape', async () => {
    const { body } = await call('GET', `/jobs/${jobId}`);
    expect(sorted(body)).toEqual(SHAPES.job);
    expect(sorted(body.spec)).toEqual(SHAPES.spec);
    expect(sorted(body.spec.resources)).toEqual(['cpu', 'memory_mb']);
    expect(JOB_STATUSES).toContain(body.status);
    expect(parseTs(body.created_at)).not.toBeNull();
    expect(body.status).toBe('SUCCEEDED');
  });

  it('GET /jobs returns summaries without the heavy spec', async () => {
    const { body } = await call('GET', '/jobs?limit=5');
    expect(body.length).toBeGreaterThan(0);
    for (const j of body) expect(sorted(j)).toEqual(SHAPES.summary);
  });

  it('GET /jobs/{id}/attempts and /logs', async () => {
    const attempts = (await call('GET', `/jobs/${jobId}/attempts`)).body;
    expect(attempts.length).toBeGreaterThan(0);
    for (const a of attempts) {
      expect(sorted(a)).toEqual(SHAPES.attempt);
      expect(ATTEMPT_STATUSES).toContain(a.status);
    }
    const logs = (await call('GET', `/jobs/${jobId}/logs`)).body;
    expect(sorted(logs)).toEqual(SHAPES.logs);
    expect(logs.attempt_number).toBe(attempts.at(-1).attempt_number);
  });

  it('GET /workers', async () => {
    const { body } = await call('GET', '/workers');
    expect(body.length).toBeGreaterThan(0);
    for (const w of body) {
      expect(sorted(w)).toEqual(SHAPES.worker);
      expect(WORKER_STATUSES).toContain(w.status);
      expect(parseTs(w.last_heartbeat_at)).not.toBeNull();
    }
  });

  it('GET /stats has every status key and a consistent total', async () => {
    const { body } = await call('GET', '/stats');
    expect(sorted(body)).toEqual(SHAPES.stats);
    expect(sorted(body.jobs)).toEqual(JOB_STATUSES);
    expect(sorted(body.workers)).toEqual(WORKER_STATUSES);
    const sum = (Object.values(body.jobs) as number[]).reduce((a, b) => a + b, 0);
    expect(body.total_jobs).toBe(sum);
  });

  it('error bodies are what describeError expects', async () => {
    const invalid = await call('POST', '/jobs', { type: 'script', runtime: 'python', command: 'x', timeout_seconds: 0, files: { '../x': 'y' } });
    expect(invalid.status).toBe(422);
    const msg = describeError(422, invalid.body);
    expect(msg).toContain('timeout_seconds');
    expect(msg).not.toContain('[object');

    const finished = await call('POST', `/jobs/${jobId}/cancel`);
    expect(finished.status).toBe(409);
    expect(describeError(409, finished.body)).toBe('job already SUCCEEDED');

    const missing = await call('GET', '/jobs/99999999');
    expect(missing.status).toBe(404);
    expect(describeError(404, missing.body)).toBe('job not found');
  });

  it('renders the real job page: status, attempt and output', async () => {
    render(<JobDetail id={jobId} />);
    await waitFor(() => expect(screen.getAllByText('SUCCEEDED').length).toBeGreaterThan(0), { timeout: 8000 });
    await waitFor(() => expect(screen.getByTestId('stdout').textContent).toContain('hello'), { timeout: 8000 });
    expect(screen.getByText(/1 of 3 allowed/)).toBeTruthy();
  }, 20_000);

  it('renders the real dashboard: totals, this job, and live workers', async () => {
    render(<Dashboard />);
    await waitFor(() => expect(screen.getByRole('link', { name: `#${jobId}` })).toBeTruthy(), { timeout: 8000 });
    expect(screen.getByText('Total jobs')).toBeTruthy();
    await waitFor(() => expect(screen.getAllByText(/ONLINE|BUSY/).length).toBeGreaterThan(0), { timeout: 8000 });
  }, 20_000);
});
