import { afterEach, describe, expect, it, vi } from 'vitest';
import { _setClockOffsetForTest, api, ApiError, describeError, serverNow } from './client';

const json = (body: unknown, init: ResponseInit = {}) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' }, ...init });

const mockFetch = (impl: (url: string, init?: RequestInit) => Promise<Response>) =>
  vi.stubGlobal('fetch', vi.fn(impl));

afterEach(() => { vi.unstubAllGlobals(); _setClockOffsetForTest(0); });

describe('describeError', () => {
  it('uses a string detail as is', () => {
    expect(describeError(409, { detail: 'job already SUCCEEDED' })).toBe('job already SUCCEEDED');
  });
  it('flattens FastAPI validation errors to one line per field', () => {
    const body = { detail: [
      { loc: ['body', 'timeout_seconds'], msg: 'Input should be greater than or equal to 1' },
      { loc: ['body', 'files'], msg: 'Value error, unsafe file path' },
      { loc: [], msg: 'something general' },
    ] };
    expect(describeError(422, body)).toBe(
      'timeout_seconds: Input should be greater than or equal to 1\nfiles: Value error, unsafe file path\nsomething general');
  });
  it('falls back to the status code', () => {
    expect(describeError(502, null)).toBe('Request failed (HTTP 502)');
    expect(describeError(500, {})).toBe('Request failed (HTTP 500)');
  });
});

describe('requests', () => {
  it('prefixes /api and builds query strings, skipping undefined', async () => {
    const f = vi.fn(async () => json([]));
    vi.stubGlobal('fetch', f);
    await api.jobs({ limit: 25, status: 'FAILED' });
    await api.jobs({ limit: 10 });
    await api.logs(7);
    await api.logs(7, 2);
    const urls = f.mock.calls.map((c) => (c as unknown as [string])[0]);
    expect(urls).toEqual(['/api/jobs?limit=25&status=FAILED', '/api/jobs?limit=10', '/api/jobs/7/logs', '/api/jobs/7/logs?attempt=2']);
  });

  it('posts the spec as JSON', async () => {
    const f = vi.fn(async (_u: string, _i?: RequestInit) => json({ id: 1 }));
    vi.stubGlobal('fetch', f);
    await api.submit({ type: 'script' } as never);
    const [url, init] = f.mock.calls[0]!;
    expect(url).toBe('/api/jobs');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(init?.body as string)).toEqual({ type: 'script' });
  });

  it('raises ApiError with status and readable message', async () => {
    mockFetch(async () => json({ detail: 'job already SUCCEEDED' }, { status: 409 }));
    await expect(api.cancel(1)).rejects.toMatchObject({ name: 'ApiError', status: 409, message: 'job already SUCCEEDED' });
  });

  it('survives a non-JSON error body (e.g. a proxy error page)', async () => {
    mockFetch(async () => new Response('<html>Bad Gateway</html>', { status: 502 }));
    await expect(api.stats()).rejects.toMatchObject({ status: 502, message: 'Request failed (HTTP 502)' });
  });

  it('reports an unreachable API as status 0', async () => {
    mockFetch(async () => { throw new TypeError('Failed to fetch'); });
    const err = await api.stats().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 0, message: 'Cannot reach the API' });
  });

  it('lets aborts through untouched so pollers can ignore them', async () => {
    mockFetch(async () => { throw new DOMException('aborted', 'AbortError'); });
    await expect(api.stats()).rejects.toMatchObject({ name: 'AbortError' });
  });
});

describe('server clock', () => {
  it('tracks the offset from the Date header', async () => {
    const serverTime = Date.now() + 60_000; // server is a minute ahead
    mockFetch(async () => json({}, { headers: { date: new Date(serverTime).toUTCString() } }));
    await api.stats();
    expect(Math.abs(serverNow() - (Date.now() + 60_000))).toBeLessThan(2000);
  });
  it('keeps the previous offset when the header is missing', async () => {
    _setClockOffsetForTest(5000);
    mockFetch(async () => json({}));
    await api.stats();
    expect(serverNow() - Date.now()).toBeGreaterThan(4000);
  });
});
