import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import { attempt, job, logs } from '../test/fixtures';
import { JobDetail } from './JobDetail';

vi.mock('../api/client', async (orig) => ({
  ...(await orig<typeof import('../api/client')>()),
  api: { job: vi.fn(), attempts: vi.fn(), logs: vi.fn(), cancel: vi.fn() },
}));
import { api } from '../api/client';

const m = vi.mocked(api);
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const calls = () => ({ job: m.job.mock.calls.length, attempts: m.attempts.mock.calls.length, logs: m.logs.mock.calls.length });

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-01-01T12:00:30Z'));
  Object.values(m).forEach((f) => f.mockReset());
});
afterEach(() => vi.useRealTimers());

function running() {
  m.job.mockResolvedValue(job());
  m.attempts.mockResolvedValue([attempt()]);
  m.logs.mockResolvedValue(logs());
}

describe('JobDetail', () => {
  it('shows status, lifecycle, configuration, attempts and logs', async () => {
    m.job.mockResolvedValue(job({ status: 'SUCCEEDED', exit_code: 0, completed_at: '2026-01-01T12:00:05Z', result_summary: 'attempt 1: SUCCEEDED' }));
    m.attempts.mockResolvedValue([attempt({ status: 'SUCCEEDED', exit_code: 0, finished_at: '2026-01-01T12:00:05Z' })]);
    m.logs.mockResolvedValue(logs({ attempt_status: 'SUCCEEDED', stdout: 'hello world\n', stderr: '' }));
    render(<JobDetail id={7} />);
    await advance(0);

    expect(screen.getAllByText('SUCCEEDED').length).toBeGreaterThan(0);
    expect(screen.getByText('attempt 1: SUCCEEDED')).toBeTruthy();
    expect(screen.getByText('Created')).toBeTruthy();
    expect(screen.getByText('Finished')).toBeTruthy();
    expect(screen.getByText('512 MB')).toBeTruthy();
    expect(screen.getByText('60s')).toBeTruthy();
    expect(screen.getByText('1 of 3 allowed')).toBeTruthy();
    expect(screen.getByTestId('stdout').textContent).toBe('hello world\n');
    expect(screen.getByTestId('stderr').textContent).toBe('(empty)');
    expect(screen.queryByRole('button', { name: 'Cancel job' })).toBeNull();
  });

  it('keeps polling while running', async () => {
    running();
    render(<JobDetail id={7} />);
    await advance(0);
    const before = calls().job;
    await advance(6000);
    expect(calls().job).toBeGreaterThanOrEqual(before + 3);
  });

  it('stops polling once settled, after one final fetch that postdates the change', async () => {
    running();
    render(<JobDetail id={7} />);
    await advance(0);

    m.job.mockResolvedValue(job({ status: 'SUCCEEDED', exit_code: 0, completed_at: '2026-01-01T12:00:05Z' }));
    m.attempts.mockResolvedValue([attempt({ status: 'SUCCEEDED', exit_code: 0, finished_at: '2026-01-01T12:00:05Z' })]);
    m.logs.mockResolvedValue(logs({ attempt_status: 'SUCCEEDED', stdout: 'final output' }));
    await advance(2000); // tick sees the terminal job
    await advance(0);    // settled -> interval null -> final fetch of all three
    expect(screen.getByTestId('stdout').textContent).toBe('final output');

    const settledCalls = calls();
    await advance(60_000);
    expect(calls()).toEqual(settledCalls);
  });

  it('keeps polling a cancelled job until its attempt has actually closed', async () => {
    m.job.mockResolvedValue(job({ status: 'CANCELLED', completed_at: '2026-01-01T12:00:10Z' }));
    m.attempts.mockResolvedValue([attempt({ status: 'RUNNING' })]); // worker still stopping the container
    m.logs.mockResolvedValue(logs());
    render(<JobDetail id={7} />);
    await advance(0);
    const before = calls().attempts;
    await advance(6000);
    expect(calls().attempts).toBeGreaterThan(before + 1);

    m.attempts.mockResolvedValue([attempt({ status: 'CANCELLED', finished_at: '2026-01-01T12:00:11Z' })]);
    await advance(2000);
    await advance(0);
    const settled = calls();
    await advance(30_000);
    expect(calls()).toEqual(settled);
  });

  it('cancels a running job and refreshes immediately', async () => {
    running();
    m.cancel.mockResolvedValue(job({ status: 'CANCELLED' }));
    render(<JobDetail id={7} />);
    await advance(0);
    const before = calls().job;
    fireEvent.click(screen.getByRole('button', { name: 'Cancel job' }));
    await advance(0);
    expect(m.cancel).toHaveBeenCalledWith(7);
    expect(calls().job).toBeGreaterThan(before);
  });

  it('shows why a cancel was refused', async () => {
    running();
    m.cancel.mockRejectedValue(new ApiError(409, 'job already SUCCEEDED'));
    render(<JobDetail id={7} />);
    await advance(0);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel job' }));
    await advance(0);
    expect(screen.getByRole('alert').textContent).toBe('job already SUCCEEDED');
  });

  it('lists every attempt of a retried job, including the one lost with its worker', async () => {
    m.job.mockResolvedValue(job({ status: 'SUCCEEDED', attempt_count: 2, worker_id: 'w3', completed_at: '2026-01-01T12:00:20Z' }));
    m.attempts.mockResolvedValue([
      attempt({ id: 1, attempt_number: 1, worker_id: 'w2', status: 'WORKER_LOST', error: 'worker w2 dead (no heartbeat for 31s)', finished_at: '2026-01-01T12:00:10Z' }),
      attempt({ id: 2, attempt_number: 2, worker_id: 'w3', status: 'SUCCEEDED', exit_code: 0, finished_at: '2026-01-01T12:00:20Z' }),
    ]);
    m.logs.mockResolvedValue(logs({ attempt_number: 2, attempt_status: 'SUCCEEDED', stdout: 'second try' }));
    render(<JobDetail id={7} />);
    await advance(0);
    expect(screen.getByText('WORKER LOST')).toBeTruthy();
    expect(screen.getByText(/worker w2 dead/)).toBeTruthy();
    expect(screen.getByText('2 of 3 allowed')).toBeTruthy();
  });

  it('loads another attempt’s logs when its row is selected', async () => {
    m.job.mockResolvedValue(job({ status: 'FAILED', attempt_count: 2, completed_at: '2026-01-01T12:00:20Z' }));
    m.attempts.mockResolvedValue([
      attempt({ id: 1, attempt_number: 1, status: 'WORKER_LOST', finished_at: T('10') }),
      attempt({ id: 2, attempt_number: 2, status: 'FAILED', exit_code: 1, finished_at: T('20') }),
    ]);
    m.logs.mockImplementation(async (_id, n) => logs({ attempt_number: n ?? 2, stdout: `output of attempt ${n ?? 2}` }));
    render(<JobDetail id={7} />);
    await advance(0);
    expect(screen.getByTestId('stdout').textContent).toBe('output of attempt 2');

    fireEvent.click(screen.getByText('WORKER LOST').closest('tr')!);
    await advance(0);
    expect(m.logs).toHaveBeenLastCalledWith(7, 1, expect.any(AbortSignal));
    expect(screen.getByTestId('stdout').textContent).toBe('output of attempt 1');
  });

  it('explains that output appears when an attempt finishes', async () => {
    running();
    render(<JobDetail id={7} />);
    await advance(0);
    expect(screen.getByText(/stored when an attempt finishes/)).toBeTruthy();
  });

  it('says so when the job does not exist', async () => {
    m.job.mockRejectedValue(new ApiError(404, 'job not found'));
    m.attempts.mockRejectedValue(new ApiError(404, 'job not found'));
    m.logs.mockRejectedValue(new ApiError(404, 'job not found'));
    render(<JobDetail id={999} />);
    await advance(0);
    expect(screen.getByText('Job #999 does not exist.')).toBeTruthy();
  });

  it('keeps showing the last data and says the connection is bad when the API drops', async () => {
    running();
    render(<JobDetail id={7} />);
    await advance(0);
    m.job.mockRejectedValue(new ApiError(0, 'Cannot reach the API'));
    await advance(2000);
    expect(screen.getByText(/Connection problem/)).toBeTruthy();
    expect(screen.getByText('python main.py')).toBeTruthy(); // still rendered
  });
});

const T = (sec: string) => `2026-01-01T12:00:${sec}Z`;
