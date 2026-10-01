import { describe, expect, it } from 'vitest';
import { emptyForm, FormState, isSafePath, LIMITS, PRESETS, validate } from './spec';

const form = (patch: Partial<FormState> = {}): FormState => ({ ...emptyForm(), ...patch });

describe('validate', () => {
  it('turns the default form into the API payload', () => {
    const { errors, spec } = validate(form());
    expect(errors).toEqual({});
    expect(spec).toEqual({
      type: 'script', runtime: 'python', command: 'python main.py',
      files: { 'main.py': "print('hello from the engine')\n" },
      timeout_seconds: 60, resources: { cpu: 1, memory_mb: 512 }, max_retries: 2, network_enabled: false,
    });
  });

  it('trims the command and ignores blank file rows', () => {
    const { spec } = validate(form({ command: '  python main.py  ', files: [...emptyForm().files, { path: '', content: '' }] }));
    expect(spec?.command).toBe('python main.py');
    expect(Object.keys(spec!.files)).toEqual(['main.py']);
  });

  it.each([
    ['timeout', { timeout: '0' }], ['timeout', { timeout: '3601' }], ['timeout', { timeout: '1.5' }],
    ['timeout', { timeout: '' }], ['timeout', { timeout: 'abc' }],
    ['cpu', { cpu: '0.05' }], ['cpu', { cpu: '9' }],
    ['memory', { memory: '32' }], ['memory', { memory: '99999' }],
    ['maxRetries', { maxRetries: '6' }], ['maxRetries', { maxRetries: '-1' }],
    ['command', { command: '   ' }], ['command', { command: 'x'.repeat(LIMITS.command + 1) }],
  ] as const)('rejects %s %j', (field, patch) => {
    const { errors, spec } = validate(form(patch));
    expect(errors[field]).toBeTruthy();
    expect(spec).toBeUndefined();
  });

  it('accepts the boundary values', () => {
    const { spec } = validate(form({ timeout: '3600', cpu: '0.1', memory: '64', maxRetries: '0' }));
    expect(spec).toBeDefined();
  });

  it('rejects unsafe, duplicate and oversized files', () => {
    expect(validate(form({ files: [{ path: '../x', content: '' }] })).errors.files).toMatch(/Unsafe/);
    expect(validate(form({ files: [{ path: 'a.py', content: '1' }, { path: 'a.py', content: '2' }] })).errors.files).toMatch(/Duplicate/);
    expect(validate(form({ files: [{ path: 'big', content: 'x'.repeat(LIMITS.fileBytes + 1) }] })).errors.files).toMatch(/larger/);
    const many = Array.from({ length: LIMITS.files + 1 }, (_, i) => ({ path: `f${i}`, content: '' }));
    expect(validate(form({ files: many })).errors.files).toMatch(/At most/);
    const total = Array.from({ length: 5 }, (_, i) => ({ path: `f${i}`, content: 'x'.repeat(250 * 1024) }));
    expect(validate(form({ files: total })).errors.files).toMatch(/total/);
  });

  it('counts bytes, not characters, against the size limit', () => {
    const emoji = '😀'.repeat(LIMITS.fileBytes / 4 + 1); // 4 bytes each, few characters
    expect(validate(form({ files: [{ path: 'e.txt', content: emoji }] })).errors.files).toMatch(/larger/);
  });
});

describe('isSafePath', () => {
  it.each(['main.py', 'pkg/util.py', 'a/b/c.txt', '.hidden'])('allows %s', (p) => expect(isSafePath(p)).toBe(true));
  it.each(['', '/etc/passwd', '../x', 'a/../b', 'a//b', './x', 'a\\b', 'a/'])('blocks %j', (p) => expect(isSafePath(p)).toBe(false));
});

describe('presets', () => {
  it.each(PRESETS.map((p) => [p.label, p] as const))('"%s" is a valid submission', (_l, preset) => {
    const { errors, spec } = validate(preset.form);
    expect(errors).toEqual({});
    expect(spec).toBeDefined();
  });
  it('the timeout demo really has a short timeout and the memory demo a small limit', () => {
    const by = (l: string) => validate(PRESETS.find((p) => p.label === l)!.form).spec!;
    expect(by('Timeout demo').timeout_seconds).toBeLessThanOrEqual(10);
    expect(by('Memory limit demo').resources.memory_mb).toBeLessThan(600);
  });
});
