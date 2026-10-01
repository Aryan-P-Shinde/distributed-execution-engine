import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import { stats, summary, worker } from '../test/fixtures';
import { Dashboard } from './Dashboard';

vi.mock('../api/client', async (orig) => ({
  ...(await orig<typeof import('../api/client')>()),
  api: { stats: vi.fn(), ready: vi.fn(), jobs: vi.fn(), workers: vi.fn() },
}));
import { api } from '../api/client';

const m = vi.mocked(api);
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const READY = { status: 'ready', database: 'ok', redis: 'ok', controller: 'ok' } as const;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-01-01T12:00:30Z'));
  Object.values(m).forEach((f) => f.mockReset());
  m.stats.mockResolvedValue(stats());
  m.ready.mockResolvedValue(READY);
  m.jobs.mockResolvedValue([summary({ id: 3 }), summary({ id: 2, status: 'SUCCEEDED' })]);
  m.workers.mockResolvedValue([worker({ id: 'w1' }), worker({ id: 'w2', status: 'DEAD', seconds_since_heartbeat: 60 })]);
});
afterEach(() => vi.useRealTimers());

describe('Dashboard', () => {
  it('shows totals, jobs, workers and health together', async () => {
    render(<Dashboard />);
    await advance(0);
    expect(screen.getByText('Total jobs')).toBeTruthy();
    expect(screen.getByRole('link', { name: '#3' })).toBeTruthy();
    expect(screen.getByText('w2')).toBeTruthy();
    expect(screen.getByText('DEAD')).toBeTruthy();
    expect(screen.getByText('redis ok')).toBeTruthy();
  });

  it('refreshes every two seconds', async () => {
    render(<Dashboard />);
    await advance(0);
    m.jobs.mockResolvedValue([summary({ id: 9 })]);
    await advance(2000);
    expect(screen.getByRole('link', { name: '#9' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: '#3' })).toBeNull();
  });

  it('filters by status server-side and resets paging', async () => {
    render(<Dashboard />);
    await advance(0);
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'FAILED' } });
    await advance(0);
    expect(m.jobs).toHaveBeenLastCalledWith({ limit: 25, status: 'FAILED' }, expect.any(AbortSignal));
  });

  it('offers "Show more" only when the page is full, and asks for more rows', async () => {
    render(<Dashboard />);
    await advance(0);
    expect(screen.queryByRole('button', { name: 'Show more' })).toBeNull();

    m.jobs.mockResolvedValue(Array.from({ length: 25 }, (_, i) => summary({ id: i + 1 })));
    await advance(2000);
    fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
    await advance(0);
    expect(m.jobs).toHaveBeenLastCalledWith({ limit: 50, status: undefined }, expect.any(AbortSignal));
  });

  it('says the API is unreachable instead of showing empty zeros', async () => {
    m.stats.mockRejectedValue(new ApiError(0, 'Cannot reach the API'));
    m.jobs.mockRejectedValue(new ApiError(0, 'Cannot reach the API'));
    m.workers.mockRejectedValue(new ApiError(0, 'Cannot reach the API'));
    render(<Dashboard />);
    await advance(0);
    expect(screen.getByText('API unreachable')).toBeTruthy();
    expect(screen.getByText(/Could not load jobs/)).toBeTruthy();
    expect(screen.getByText(/Could not load workers/)).toBeTruthy();
  });

  it('keeps the last good data through a temporary outage', async () => {
    render(<Dashboard />);
    await advance(0);
    m.jobs.mockRejectedValue(new ApiError(502, 'Bad gateway'));
    m.workers.mockRejectedValue(new ApiError(502, 'Bad gateway'));
    await advance(4000);
    expect(screen.getByRole('link', { name: '#3' })).toBeTruthy();
    expect(screen.getByText('w2')).toBeTruthy();
  });
});
