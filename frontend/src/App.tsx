import { useEffect, useState } from 'react';
import { api } from './api/client';
import { HealthPills } from './components/HealthPills';
import { useHashRoute } from './hooks/useHashRoute';
import { Dashboard } from './pages/Dashboard';
import { JobDetail } from './pages/JobDetail';
import { NewJob } from './pages/NewJob';

export function App() {
  const route = useHashRoute();
  // a small, separate check so the banner works on every page
  const [down, setDown] = useState(false);
  useEffect(() => {
    let stop = false;
    const check = async () => {
      try { await api.ready(); if (!stop) setDown(false); } catch { if (!stop) setDown(true); }
    };
    void check();
    const t = setInterval(check, 5000);
    return () => { stop = true; clearInterval(t); };
  }, []);

  return (
    <>
      <header className="topbar">
        <a className="brand" href="#/">▸ execution engine</a>
        <nav>
          <a href="#/" aria-current={route.name === 'home' ? 'page' : undefined}>Overview</a>
          <a href="#/new" aria-current={route.name === 'new' ? 'page' : undefined}>Submit job</a>
        </nav>
        {route.name !== 'home' && <HealthPills ready={undefined} unreachable={down} />}
      </header>
      {down && (
        <div className="banner" role="status">
          Cannot reach the API. The dashboard will keep retrying and show the last data it received.
        </div>
      )}
      <main>
        {route.name === 'home' && <Dashboard />}
        {route.name === 'new' && <NewJob />}
        {route.name === 'job' && <JobDetail key={route.id} id={route.id} />}
        {route.name === 'notfound' && <p className="empty">Page not found. <a href="#/">Back to overview</a></p>}
      </main>
    </>
  );
}
