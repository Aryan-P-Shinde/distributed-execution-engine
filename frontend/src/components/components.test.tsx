import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import { stats, summary, worker } from '../test/fixtures';
import { HealthPills } from './HealthPills';
import { JobsTable } from './JobsTable';
import { StatsBar } from './StatsBar';
import { SubmitJobForm } from './SubmitJobForm';
import { WorkersTable } from './WorkersTable';

const h = vi.hoisted(() => ({ submit: (_spec: unknown): Promise<unknown> => Promise.reject(new Error('unset')) }));
vi.mock('../api/client', async (orig) => ({
  ...(await orig<typeof import('../api/client')>()),
  api: { submit: (spec: unknown) => h.submit(spec) },
}));

const NOW = Date.UTC(2026, 0, 1, 12, 0, 30);

describe('StatsBar', () => {
  it('shows the PRD totals with failed = failed + timed out', () => {
    render(<StatsBar stats={stats()} />);
    const value = (label: string) =>
      within(screen.getByText(label).closest('.card') as HTMLElement).getByText(/^\d+$/).textContent;
    expect(value('Total jobs')).toBe('10');
    expect(value('Queued')).toBe('2');
    expect(value('Running')).toBe('1');
    expect(value('Succeeded')).toBe('5');
    expect(value('Failed')).toBe('2');
    expect(value('Active workers')).toBe('2');
    expect(value('Unavailable workers')).toBe('1');
  });
  it('shows a loading state before the first response', () => {
    render(<StatsBar stats={undefined} />);
    expect(screen.getByText('Loading…')).toBeTruthy();
  });
});

describe('JobsTable', () => {
  it('has the PRD columns and links each job to its page', () => {
    render(<JobsTable jobs={[summary({ id: 12, status: 'SUCCEEDED', completed_at: '2026-01-01T12:00:05Z' })]} now={NOW} />);
    expect(screen.getAllByRole('columnheader').map((h) => h.textContent))
      .toEqual(['ID', 'Type', 'Status', 'Worker', 'Created', 'Duration']);
    const link = screen.getByRole('link', { name: '#12' });
    expect(link.getAttribute('href')).toBe('#/jobs/12');
    expect(screen.getByText('SUCCEEDED')).toBeTruthy();
    expect(screen.getByText('5.0s')).toBeTruthy();
  });
  it('counts up the duration of a running job and shows dashes for one that has not started', () => {
    render(<JobsTable jobs={[summary({ id: 1 }), summary({ id: 2, status: 'QUEUED', worker_id: null, started_at: null })]} now={NOW} />);
    const rows = screen.getAllByRole('row').slice(1);
    expect(within(rows[0]!).getByText('30s')).toBeTruthy();
    expect(within(rows[1]!).getAllByText('—').length).toBe(2); // worker and duration
  });
  it('notes retried jobs', () => {
    render(<JobsTable jobs={[summary({ attempt_count: 3 })]} now={NOW} />);
    expect(screen.getByText(/3 attempts/)).toBeTruthy();
  });
  it('has an empty state', () => {
    render(<JobsTable jobs={[]} now={NOW} />);
    expect(screen.getByText(/No jobs yet/)).toBeTruthy();
  });
});

describe('WorkersTable', () => {
  it('shows state, capabilities, current job and heartbeat age', () => {
    render(<WorkersTable workers={[worker({ status: 'BUSY', current_job_id: 9 })]} />);
    expect(screen.getByText('BUSY')).toBeTruthy();
    expect(screen.getByText('python, node')).toBeTruthy();
    expect(screen.getByRole('link', { name: '#9' }).getAttribute('href')).toBe('#/jobs/9');
    expect(screen.getByText('2.1s ago')).toBeTruthy();
  });
  it('does not call a clean shutdown a heartbeat problem', () => {
    render(<WorkersTable workers={[worker({ status: 'STOPPED', seconds_since_heartbeat: 400 })]} />);
    expect(screen.getByText('stopped cleanly')).toBeTruthy();
  });
  it('highlights a dead worker that still holds a job', () => {
    render(<WorkersTable workers={[worker({ status: 'DEAD', seconds_since_heartbeat: 45, current_job_id: 3 })]} />);
    expect(screen.getByText('DEAD')).toBeTruthy();
    expect(screen.getByText('45s ago').className).toContain('warn-text');
  });
});

describe('HealthPills', () => {
  it('names what is degraded', () => {
    render(<HealthPills ready={{ status: 'degraded', database: 'ok', redis: 'unavailable', controller: 'stale' }} unreachable={false} />);
    expect(screen.getByText('degraded')).toBeTruthy();
    expect(screen.getByText('redis unavailable')).toBeTruthy();
    expect(screen.getByText('controller stale')).toBeTruthy();
  });
  it('reports an unreachable API instead of stale health', () => {
    render(<HealthPills ready={undefined} unreachable />);
    expect(screen.getByText('API unreachable')).toBeTruthy();
  });
});

describe('SubmitJobForm', () => {
  let calls: unknown[];
  beforeEach(() => {
    calls = [];
    h.submit = async (spec) => { calls.push(spec); return { id: 1 }; };
  });

  it('submits the validated spec and reports the new job', async () => {
    h.submit = async (spec) => { calls.push(spec); return { id: 42 }; };
    const onSubmitted = vi.fn();
    render(<SubmitJobForm onSubmitted={onSubmitted} />);
    fireEvent.change(screen.getByLabelText(/Timeout/), { target: { value: '90' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit job' }));
    await waitFor(() => expect(onSubmitted).toHaveBeenCalledWith({ id: 42 }));
    const spec = calls[0] as import('../api/types').JobSpec;
    expect(spec.timeout_seconds).toBe(90);
    expect(spec.command).toBe('python main.py');
    expect(Object.keys(spec.files)).toEqual(['main.py']);
  });

  it('blocks an invalid form locally and explains why, without calling the API', async () => {
    render(<SubmitJobForm onSubmitted={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/Timeout/), { target: { value: '0' } });
    fireEvent.change(screen.getByLabelText(/Memory/), { target: { value: '8' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit job' }));
    expect((await screen.findAllByRole('alert')).map((a) => a.textContent)).toEqual([
      'Timeout must be between 1 and 3600', 'Memory must be between 64 and 4096',
    ]);
    expect(calls).toEqual([]);
  });

  it('shows the API’s own validation message when it disagrees', async () => {
    h.submit = async () => { throw new ApiError(422, 'files: unsafe file path'); };
    const onSubmitted = vi.fn();
    render(<SubmitJobForm onSubmitted={onSubmitted} />);
    fireEvent.click(screen.getByRole('button', { name: 'Submit job' }));
    expect((await screen.findByRole('alert')).textContent).toBe('files: unsafe file path');
    expect(onSubmitted).not.toHaveBeenCalled();
    expect((screen.getByRole('button', { name: 'Submit job' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('fills the form from an example', () => {
    render(<SubmitJobForm onSubmitted={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Node hello' }));
    expect((screen.getByLabelText(/Runtime/) as HTMLSelectElement).value).toBe('node');
    expect((screen.getByDisplayValue('node index.js') as HTMLInputElement).value).toBe('node index.js');
  });

  it('adds and removes source files', () => {
    render(<SubmitJobForm onSubmitted={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '+ Add file' }));
    expect(screen.getAllByLabelText(/path$/).length).toBe(2);
    fireEvent.click(screen.getByRole('button', { name: 'Remove file 2' }));
    expect(screen.getAllByLabelText(/path$/).length).toBe(1);
  });

  it('warns that retries can run code twice', () => {
    render(<SubmitJobForm onSubmitted={vi.fn()} />);
    expect(screen.getByText(/can run the code twice/)).toBeTruthy();
  });
});
