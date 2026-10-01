import { useEffect, useState } from 'react';
import { serverNow } from '../api/client';

/** Server-clock "now", re-rendering every `ms` so running durations tick between polls. */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(serverNow);
  useEffect(() => {
    const id = setInterval(() => setNow(serverNow()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}
