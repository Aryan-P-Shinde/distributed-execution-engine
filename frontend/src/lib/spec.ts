import type { JobSpec, JobType, Runtime } from '../api/types';

// Client-side hints only. The API validates again and is the authority; its
// 422 messages are shown when they disagree. Keep in sync with shared/spec.py.
export const LIMITS = {
  timeout: { min: 1, max: 3600 },
  cpu: { min: 0.1, max: 4 },
  memoryMb: { min: 64, max: 4096 },
  retries: { min: 0, max: 5 },
  command: 2000,
  files: 50,
  fileBytes: 256 * 1024,
  totalBytes: 1024 * 1024,
} as const;

export interface FileRow { path: string; content: string }

export interface FormState {
  type: JobType;
  runtime: Runtime;
  command: string;
  timeout: string;
  cpu: string;
  memory: string;
  maxRetries: string;
  network: boolean;
  files: FileRow[];
}

export type FormErrors = Partial<Record<
  'command' | 'timeout' | 'cpu' | 'memory' | 'maxRetries' | 'files', string
>>;

export const emptyForm = (): FormState => ({
  type: 'script', runtime: 'python', command: 'python main.py',
  timeout: '60', cpu: '1', memory: '512', maxRetries: '2', network: false,
  files: [{ path: 'main.py', content: "print('hello from the engine')\n" }],
});

export function isSafePath(path: string): boolean {
  if (!path || path.startsWith('/') || path.includes('\\') || path.includes('\0')) return false;
  return path.split('/').every((seg) => seg !== '' && seg !== '.' && seg !== '..');
}

const byteLength = (s: string): number => new TextEncoder().encode(s).length;

function num(raw: string, min: number, max: number, integer: boolean, label: string): [number?, string?] {
  const v = Number(raw);
  if (raw.trim() === '' || !Number.isFinite(v)) return [undefined, `${label} must be a number`];
  if (integer && !Number.isInteger(v)) return [undefined, `${label} must be a whole number`];
  if (v < min || v > max) return [undefined, `${label} must be between ${min} and ${max}`];
  return [v];
}

export function validate(form: FormState): { errors: FormErrors; spec?: JobSpec } {
  const errors: FormErrors = {};

  const command = form.command.trim();
  if (!command) errors.command = 'Command is required';
  else if (command.length > LIMITS.command) errors.command = `Command is limited to ${LIMITS.command} characters`;

  const [timeout, e1] = num(form.timeout, LIMITS.timeout.min, LIMITS.timeout.max, true, 'Timeout');
  const [cpu, e2] = num(form.cpu, LIMITS.cpu.min, LIMITS.cpu.max, false, 'CPU');
  const [memory, e3] = num(form.memory, LIMITS.memoryMb.min, LIMITS.memoryMb.max, true, 'Memory');
  const [retries, e4] = num(form.maxRetries, LIMITS.retries.min, LIMITS.retries.max, true, 'Retries');
  if (e1) errors.timeout = e1;
  if (e2) errors.cpu = e2;
  if (e3) errors.memory = e3;
  if (e4) errors.maxRetries = e4;

  const files: Record<string, string> = {};
  let total = 0;
  for (const row of form.files) {
    const path = row.path.trim();
    if (!path && !row.content) continue; // ignore a blank row
    if (!isSafePath(path)) { errors.files = `Unsafe or empty file path: "${row.path}"`; break; }
    if (path in files) { errors.files = `Duplicate file path: "${path}"`; break; }
    const size = byteLength(row.content);
    if (size > LIMITS.fileBytes) { errors.files = `"${path}" is larger than ${LIMITS.fileBytes / 1024} KB`; break; }
    total += size;
    files[path] = row.content;
  }
  if (!errors.files) {
    if (Object.keys(files).length > LIMITS.files) errors.files = `At most ${LIMITS.files} files`;
    else if (total > LIMITS.totalBytes) errors.files = `Source is larger than ${LIMITS.totalBytes / 1024} KB in total`;
  }

  if (Object.keys(errors).length > 0) return { errors };
  return {
    errors,
    spec: {
      type: form.type, runtime: form.runtime, command, files,
      timeout_seconds: timeout!, resources: { cpu: cpu!, memory_mb: memory! },
      max_retries: retries!, network_enabled: form.network,
    },
  };
}

export interface Preset { label: string; hint: string; form: FormState }

export const PRESETS: Preset[] = [
  { label: 'Hello world', hint: 'python script, finishes instantly', form: emptyForm() },
  {
    label: 'Failing script', hint: 'exits with code 3',
    form: { ...emptyForm(), files: [{ path: 'main.py', content: "import sys\nprint('about to fail')\nsys.stderr.write('boom\\n')\nsys.exit(3)\n" }] },
  },
  {
    label: 'Timeout demo', hint: 'infinite loop, killed after 5s',
    form: { ...emptyForm(), timeout: '5', files: [{ path: 'main.py', content: 'while True:\n    pass\n' }] },
  },
  {
    label: 'Memory limit demo', hint: 'allocates 600 MB under a 128 MB limit',
    form: { ...emptyForm(), memory: '128', files: [{ path: 'main.py', content: "x = b'x' * (600 * 1024 * 1024)\nprint(len(x))\n" }] },
  },
  {
    label: 'pytest', hint: 'test job',
    form: {
      ...emptyForm(), type: 'test', command: 'pytest -q',
      files: [{ path: 'test_math.py', content: 'def test_addition():\n    assert 1 + 1 == 2\n' }],
    },
  },
  {
    label: 'Node hello', hint: 'node runtime',
    form: {
      ...emptyForm(), runtime: 'node', command: 'node index.js',
      files: [{ path: 'index.js', content: "console.log('hello from node');\n" }],
    },
  },
];
