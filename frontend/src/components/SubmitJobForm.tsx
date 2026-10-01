import { useState } from 'react';
import { api } from '../api/client';
import type { Job, JobType, Runtime } from '../api/types';
import { emptyForm, FormState, LIMITS, PRESETS, validate } from '../lib/spec';

export function SubmitJobForm({ onSubmitted }: { onSubmitted: (job: Job) => void }) {
  const [form, setForm] = useState<FormState>(emptyForm);
  const [errors, setErrors] = useState<ReturnType<typeof validate>['errors']>({});
  const [serverError, setServerError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm((f) => ({ ...f, [k]: v }));
  const setFile = (i: number, patch: Partial<FormState['files'][number]>) =>
    set('files', form.files.map((f, j) => (j === i ? { ...f, ...patch } : f)));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setServerError(undefined);
    const { errors: found, spec } = validate(form);
    setErrors(found);
    if (!spec) return;
    setBusy(true);
    try {
      onSubmitted(await api.submit(spec));
    } catch (err) {
      setServerError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const err = (k: keyof typeof errors) => errors[k] && <div className="field-error" role="alert">{errors[k]}</div>;

  return (
    <form onSubmit={submit} noValidate className="form">
      <fieldset className="presets">
        <legend>Start from an example</legend>
        {PRESETS.map((p) => (
          <button type="button" key={p.label} className="btn btn-ghost" title={p.hint}
                  onClick={() => { setForm(structuredClone(p.form)); setErrors({}); setServerError(undefined); }}>
            {p.label}
          </button>
        ))}
      </fieldset>

      <div className="grid">
        <label>Workload type
          <select value={form.type} onChange={(e) => set('type', e.target.value as JobType)}>
            <option value="script">script</option><option value="test">test</option><option value="build">build</option>
          </select>
        </label>
        <label>Runtime
          <select value={form.runtime} onChange={(e) => set('runtime', e.target.value as Runtime)}>
            <option value="python">python</option><option value="node">node</option>
          </select>
        </label>
        <label>Timeout (seconds)
          <input inputMode="numeric" value={form.timeout} onChange={(e) => set('timeout', e.target.value)} />
          {err('timeout')}
        </label>
        <label>CPU (cores)
          <input inputMode="decimal" value={form.cpu} onChange={(e) => set('cpu', e.target.value)} />
          {err('cpu')}
        </label>
        <label>Memory (MB)
          <input inputMode="numeric" value={form.memory} onChange={(e) => set('memory', e.target.value)} />
          {err('memory')}
        </label>
        <label>Max retries
          <input inputMode="numeric" value={form.maxRetries} onChange={(e) => set('maxRetries', e.target.value)} />
          {err('maxRetries')}
        </label>
      </div>
      <p className="hint">
        Retries re-run the job only if its worker is lost or the executor fails, never for a non-zero exit.
        A retry can run the code twice; use 0 for jobs with side effects.
      </p>

      <label>Command <span className="dim">(runs inside the container from the source directory)</span>
        <input className="mono" value={form.command} onChange={(e) => set('command', e.target.value)} />
        {err('command')}
      </label>

      <label className="check">
        <input type="checkbox" checked={form.network} onChange={(e) => set('network', e.target.checked)} />
        Allow network access <span className="dim">(off by default)</span>
      </label>

      <div className="files">
        <div className="files-head">
          <strong>Source files</strong>
          <span className="dim">up to {LIMITS.files} files, {LIMITS.fileBytes / 1024} KB each</span>
        </div>
        {form.files.map((f, i) => (
          <div className="file" key={i}>
            <div className="file-row">
              <input className="mono" aria-label={`File ${i + 1} path`} placeholder="path/to/file.py"
                     value={f.path} onChange={(e) => setFile(i, { path: e.target.value })} />
              <button type="button" className="btn btn-ghost" aria-label={`Remove file ${i + 1}`}
                      onClick={() => set('files', form.files.filter((_, j) => j !== i))}>Remove</button>
            </div>
            <textarea className="mono" aria-label={`File ${i + 1} content`} rows={6} spellCheck={false}
                      value={f.content} onChange={(e) => setFile(i, { content: e.target.value })} />
          </div>
        ))}
        <button type="button" className="btn btn-ghost" onClick={() => set('files', [...form.files, { path: '', content: '' }])}>
          + Add file
        </button>
        {err('files')}
      </div>

      {serverError && <pre className="server-error" role="alert">{serverError}</pre>}
      <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Submitting…' : 'Submit job'}</button>
    </form>
  );
}
