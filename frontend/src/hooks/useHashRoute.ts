import { useEffect, useState } from 'react';

export type Route =
  | { name: 'home' }
  | { name: 'new' }
  | { name: 'job'; id: number }
  | { name: 'notfound' };

export function parseRoute(hash: string): Route {
  const path = hash.replace(/^#/, '').replace(/\/+$/, '') || '/';
  if (path === '/') return { name: 'home' };
  if (path === '/new') return { name: 'new' };
  const m = /^\/jobs\/(\d+)$/.exec(path);
  if (m) return { name: 'job', id: Number(m[1]) };
  return { name: 'notfound' };
}

export const jobPath = (id: number): string => `#/jobs/${id}`;

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  return route;
}
