import { describe, expect, it } from 'vitest';
import { parseRoute } from './useHashRoute';

describe('parseRoute', () => {
  it.each([
    ['', { name: 'home' }], ['#', { name: 'home' }], ['#/', { name: 'home' }],
    ['#/new', { name: 'new' }], ['#/new/', { name: 'new' }],
    ['#/jobs/12', { name: 'job', id: 12 }], ['#/jobs/12/', { name: 'job', id: 12 }],
    ['#/jobs/abc', { name: 'notfound' }], ['#/jobs/', { name: 'notfound' }],
    ['#/jobs/1/extra', { name: 'notfound' }], ['#/nope', { name: 'notfound' }],
  ])('%j', (hash, route) => expect(parseRoute(hash)).toEqual(route));
});
