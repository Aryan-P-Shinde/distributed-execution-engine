import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { usePolling } from './usePolling';

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => {
  vi.useRealTimers();
  Object.defineProperty(document, 'hidden', { configurable: true, value: false });
});

const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe('usePolling', () => {
  it('fetches immediately, then every interval', async () => {
    let n = 0;
    const fetcher = vi.fn(async () => ++n);
    const { result } = renderHook(() => usePolling(fetcher, 1000));
    await advance(0);
    expect(result.current.data).toBe(1);
    expect(result.current.loading).toBe(false);
    await advance(1000);
    expect(result.current.data).toBe(2);
    await advance(2000);
    expect(result.current.data).toBe(4);
  });

  it('never overlaps requests: a slow API gets slower polling, not a pile-up', async () => {
    const pending = deferred<number>();
    const fetcher = vi.fn(() => pending.promise);
    renderHook(() => usePolling(fetcher, 1000));
    await advance(10_000);
    expect(fetcher).toHaveBeenCalledTimes(1);
    pending.resolve(1);
    await advance(0);
    await advance(1000);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('refresh() and tab-visibility events do not start a second request while one is in flight', async () => {
    const pending = deferred<number>();
    const fetcher = vi.fn(() => pending.promise);
    const { result } = renderHook(() => usePolling(fetcher, 1000));
    await advance(0);
    expect(fetcher).toHaveBeenCalledTimes(1);

    act(() => result.current.refresh());
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    await advance(0);
    expect(fetcher).toHaveBeenCalledTimes(1);

    pending.resolve(1);
    await advance(0);
    // and no duplicated timer chain was left behind: one request per interval
    const before = fetcher.mock.calls.length;
    await advance(3000);
    expect(fetcher.mock.calls.length - before).toBe(3);
  });

  it('keeps the last good data on failure, flags the error, and recovers', async () => {
    let call = 0;
    const fetcher = vi.fn(async () => {
      call++;
      if (call === 2) throw new Error('boom');
      return call;
    });
    const { result } = renderHook(() => usePolling(fetcher, 1000));
    await advance(0);
    expect(result.current.data).toBe(1);
    await advance(1000);
    expect(result.current.error?.message).toBe('boom');
    expect(result.current.data).toBe(1); // stale, not blank
    await advance(1000);
    expect(result.current.data).toBe(3);
    expect(result.current.error).toBeUndefined();
  });

  it('with a null interval fetches once, then only on refresh()', async () => {
    let n = 0;
    const fetcher = vi.fn(async () => ++n);
    const { result } = renderHook(() => usePolling(fetcher, null));
    await advance(0);
    await advance(30_000);
    expect(fetcher).toHaveBeenCalledTimes(1);
    act(() => result.current.refresh());
    await advance(0);
    expect(result.current.data).toBe(2);
  });

  it('stopping the poll does not blank the data, and does one last fetch', async () => {
    let n = 0;
    const fetcher = vi.fn(async () => ++n);
    const { result, rerender } = renderHook(({ ms }: { ms: number | null }) => usePolling(fetcher, ms), {
      initialProps: { ms: 1000 as number | null },
    });
    await advance(0);
    expect(result.current.data).toBe(1);
    rerender({ ms: null });
    expect(result.current.data).toBe(1); // still shown immediately
    await advance(0);
    expect(result.current.data).toBe(2);  // the final fetch
    await advance(10_000);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('resets and aborts in-flight requests when deps change', async () => {
    const signals: AbortSignal[] = [];
    const fetcher = vi.fn(async (signal: AbortSignal) => {
      signals.push(signal);
      return new Promise<string>(() => {}); // never resolves
    });
    const { result, rerender } = renderHook(({ id }: { id: number }) => usePolling(fetcher, 1000, [id]), {
      initialProps: { id: 1 },
    });
    await advance(0);
    expect(signals[0]!.aborted).toBe(false);
    rerender({ id: 2 });
    await advance(0);
    expect(signals[0]!.aborted).toBe(true);
    expect(result.current.data).toBeUndefined();
    expect(result.current.loading).toBe(true);
  });

  it('does not show the old id’s data under the new id', async () => {
    const fetcher = vi.fn(async (_s: AbortSignal) => 'job-1');
    const { result, rerender } = renderHook(({ id }: { id: number }) => usePolling(fetcher, 1000, [id]), {
      initialProps: { id: 1 },
    });
    await advance(0);
    expect(result.current.data).toBe('job-1');
    fetcher.mockImplementation(() => new Promise(() => {}));
    rerender({ id: 2 });
    await advance(0);
    expect(result.current.data).toBeUndefined();
  });

  it('pauses while the tab is hidden and catches up when it returns', async () => {
    const fetcher = vi.fn(async () => 1);
    renderHook(() => usePolling(fetcher, 1000));
    await advance(0);
    expect(fetcher).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    await advance(10_000);
    expect(fetcher).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    await advance(0);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('stops everything on unmount', async () => {
    const fetcher = vi.fn(async () => 1);
    const { unmount } = renderHook(() => usePolling(fetcher, 1000));
    await advance(0);
    unmount();
    await advance(10_000);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
