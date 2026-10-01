import { describe, expect, it } from 'vitest';
import type { Attempt, Job, Stats } from '../api/types';
import {
  durationMs, formatDuration, formatElapsed, isSettled, parseTs, relativeTime, summarize,
} from './format';

describe('parseTs', () => {
  it('runs in a non-UTC zone, otherwise the naive-timestamp test below proves nothing', () => {
    expect(new Date(2026, 0, 1).getTimezoneOffset()).not.toBe(0);
  });
  it('reads offsets and Z', () => {
    expect(parseTs('2026-01-01T00:00:00Z')).toBe(Date.UTC(2026, 0, 1));
    expect(parseTs('2026-01-01T02:00:00+02:00')).toBe(Date.UTC(2026, 0, 1));
  });
  it('treats a naive timestamp as UTC (the API never sends local time)', () => {
    expect(parseTs('2026-01-01T00:00:00')).toBe(Date.UTC(2026, 0, 1));
    expect(parseTs('2026-01-01T00:00:00.123456')).toBe(Date.UTC(2026, 0, 1) + 123);
  });
  it('returns null for missing or garbage input', () => {
    expect(parseTs(null)).toBeNull();
    expect(parseTs('')).toBeNull();
    expect(parseTs('not a date')).toBeNull();
  });
});

describe('formatDuration', () => {
  it.each([
    [0, '0ms'], [450, '450ms'], [1500, '1.5s'], [9999, '10.0s'], [12_000, '12s'],
    [59_900, '59s'], [60_000, '1m 00s'], [185_000, '3m 05s'], [3_720_000, '1h 02m'], [-5, '0ms'],
  ])('%d ms -> %s', (ms, out) => expect(formatDuration(ms)).toBe(out));
});

describe('durations', () => {
  const start = '2026-01-01T00:00:00Z';
  const now = Date.UTC(2026, 0, 1, 0, 0, 30);
  it('is fixed once finished', () => {
    expect(durationMs(start, '2026-01-01T00:00:05Z', now)).toBe(5000);
  });
  it('counts up against now while running', () => {
    expect(durationMs(start, null, now)).toBe(30_000);
    expect(formatElapsed(start, null, now)).toBe('30s');
  });
  it('is unknown for a job that never started', () => {
    expect(durationMs(null, null, now)).toBeNull();
    expect(formatElapsed(null, null, now)).toBe('—');
  });
});

describe('relativeTime', () => {
  const now = Date.UTC(2026, 0, 2, 12, 0, 0);
  const ago = (s: number) => new Date(now - s * 1000).toISOString();
  it.each([
    [1, 'just now'], [30, '30s ago'], [300, '5m ago'], [7200, '2h ago'], [200_000, '2d ago'],
  ])('%ds -> %s', (s, out) => expect(relativeTime(ago(s), now)).toBe(out));
  it('never goes negative when the clocks disagree', () => {
    expect(relativeTime(ago(-60), now)).toBe('just now');
  });
});

const job = (status: Job['status']) => ({ status } as Job);
const attempt = (status: Attempt['status']) => ({ status } as Attempt);

describe('isSettled', () => {
  it('needs a terminal job', () => {
    expect(isSettled(job('RUNNING'), [attempt('RUNNING')])).toBe(false);
    expect(isSettled(job('QUEUED'), [])).toBe(false);
  });
  it('waits for every attempt to close, e.g. a cancelled job whose worker is still stopping', () => {
    expect(isSettled(job('CANCELLED'), [attempt('RUNNING')])).toBe(false);
    expect(isSettled(job('CANCELLED'), [attempt('CANCELLED')])).toBe(true);
  });
  it('accepts retried jobs whose earlier attempts were lost', () => {
    expect(isSettled(job('SUCCEEDED'), [attempt('WORKER_LOST'), attempt('SUCCEEDED')])).toBe(true);
  });
  it('is settled for a terminal job with no attempts (cancelled while queued)', () => {
    expect(isSettled(job('CANCELLED'), [])).toBe(true);
  });
});

describe('summarize', () => {
  const stats: Stats = {
    total_jobs: 20,
    jobs: { CREATED: 1, QUEUED: 2, SCHEDULED: 1, RUNNING: 3, SUCCEEDED: 8, FAILED: 2, TIMED_OUT: 1, CANCELLED: 2 },
    workers: { ONLINE: 2, BUSY: 1, UNHEALTHY: 1, DEAD: 2, STOPPED: 4 },
  };
  it('groups statuses the way the dashboard reports them', () => {
    expect(summarize(stats)).toEqual({
      total: 20, queued: 3, running: 4, succeeded: 8, failed: 3, cancelled: 2,
      activeWorkers: 3, unavailableWorkers: 3, stoppedWorkers: 4,
    });
  });
});
