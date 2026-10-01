import { useCallback, useEffect, useRef, useState } from 'react';

export interface PollState<T> {
  data: T | undefined;
  /** Last failure, cleared by the next success. `data` is kept so the UI can show stale values. */
  error: Error | undefined;
  /** Time of the last success, ms since epoch. */
  updatedAt: number | undefined;
  loading: boolean;
  /** Fetch now, without waiting for the next tick. */
  refresh: () => void;
}

/**
 * Poll `fetcher` every `intervalMs` (null = fetch once, then only on refresh()).
 *
 * - Requests never overlap: the next one is scheduled after the last finishes,
 *   so a slow API gets slower polling, not a pile-up.
 * - Keeps the last good data when a poll fails.
 * - Paused while the tab is hidden; polls immediately when it becomes visible.
 * - Re-runs from scratch when `deps` change (aborting anything in flight).
 *   State is reset then, so a new id never shows the previous id's data.
 */
export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs: number | null,
  deps: readonly unknown[] = [],
): PollState<T> {
  const [state, setState] = useState<Omit<PollState<T>, 'refresh'>>({
    data: undefined, error: undefined, updatedAt: undefined, loading: true,
  });
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const tickRef = useRef<() => void>(() => {});

  // Reset only when the thing being polled changes, not when the interval does:
  // stopping the poll must not blank the screen. Declared first so it runs
  // before the polling effect on a deps change.
  useEffect(() => {
    setState({ data: undefined, error: undefined, updatedAt: undefined, loading: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps]);

  useEffect(() => {
    let cancelled = false;
    let inFlight = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let abort: AbortController | undefined;

    const schedule = () => {
      if (!cancelled && intervalMs !== null) timer = setTimeout(tick, intervalMs);
    };

    async function tick() {
      if (cancelled || inFlight) return;
      if (typeof document !== 'undefined' && document.hidden) { schedule(); return; }
      inFlight = true;
      abort = new AbortController();
      try {
        const data = await fetcherRef.current(abort.signal);
        if (!cancelled) setState({ data, error: undefined, updatedAt: Date.now(), loading: false });
      } catch (e) {
        const aborted = e instanceof DOMException && e.name === 'AbortError';
        if (!cancelled && !aborted) {
          setState((s) => ({ ...s, error: e as Error, loading: false }));
        }
      } finally {
        inFlight = false;
        schedule();
      }
    }

    tickRef.current = () => { clearTimeout(timer); void tick(); };

    const onVisible = () => {
      if (!document.hidden && !inFlight) { clearTimeout(timer); void tick(); }
    };
    document.addEventListener('visibilitychange', onVisible);
    void tick();

    return () => {
      cancelled = true;
      clearTimeout(timer);
      abort?.abort();
      document.removeEventListener('visibilitychange', onVisible);
    };
    // deps is intentionally caller-controlled
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, ...deps]);

  const refresh = useCallback(() => tickRef.current(), []);
  return { ...state, refresh };
}
