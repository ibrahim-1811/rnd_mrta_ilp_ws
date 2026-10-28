import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Check, ChevronDown, FlaskConical, LoaderCircle, Play, ScanEye, SlidersHorizontal } from 'lucide-react'
import { InputPicker, type CsvInput } from './InputPicker'
import { act, message } from './api'
import { buildSpec, display, human, previewMatches } from './model'
import type { Form, Job, Preview, Scenario, Strategy } from './types'

const initial: Form = { strategy: 'nearest_astar', robots: '2', orders: '3', seed: '7', mode: 'observed', window: '', quantum: '', speed: '', sample: '1', export: false }

export function RunSetup({ scenario, strategies, canObserve, activeJob, onLaunched, onRefresh, disabled = false }: {
  scenario: Scenario | null; strategies: Strategy[]; canObserve: boolean; activeJob: boolean;
  onLaunched: (job: Job) => void; onRefresh: () => void; disabled?: boolean
}) {
  const [form, setForm] = useState<Form>(initial)
  const current = useRef(form); current.current = form
  const [preview, setPreview] = useState<{ plan: Preview; spec: Record<string, unknown>; expires: number } | null>(null)
  const [busy, setBusy] = useState('')
  const [input, setInput] = useState<CsvInput|null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const action = useRef(false)
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    setForm(f => ({ ...f, mode: canObserve ? 'observed' : 'cli' })); setPreview(null)
  }, [canObserve])
  useEffect(() => {
    if (strategies.length && !strategies.some(s => s.id === current.current.strategy)) { setForm(f => ({ ...f, strategy: strategies[0].id })); setPreview(null) }
  }, [strategies])
  useEffect(() => { if (preview) dialog.current?.showModal() }, [preview])
  const change = (key: keyof Form, value: string | boolean) => { setForm(f => ({ ...f, [key]: value })); setPreview(null); setError('') }
  async function previewRun() {
    if (action.current || !scenario || disabled || uploading) return
    action.current = true; setBusy('preview'); setError('')
    try {
      const spec = buildSpec(form, scenario.capacity)
      const result = await act<Preview>('/jobs/preview', spec)
      if (previewMatches(spec, buildSpec(current.current, scenario.capacity))) setPreview({ plan: result.data, spec, expires: Date.now() + result.data.expires_in_s * 1000 })
    } catch (e) { setError(message(e)) }
    finally { action.current = false; setBusy('') }
  }
  async function launch() {
    if (!preview || action.current || activeJob || !scenario || disabled) return
    if (Date.now() > preview.expires || !previewMatches(preview.spec, buildSpec(current.current, scenario.capacity))) { setPreview(null); setError('Preview expired or settings changed. Preview the run again.'); return }
    action.current = true; setBusy('launch'); setError('')
    try { const result = await act<Job>('/runs', { preview_id: preview.plan.preview_id }); setPreview(null); onLaunched(result.data) }
    catch (e) { setPreview(null); setError(message(e)); onRefresh() }
    finally { action.current = false; setBusy('') }
  }
  return <section className="panel setup-panel" aria-label="Run configuration">
    <div className="panel-heading"><div><span className="eyebrow">RUN CONFIGURATION</span><h2>Set up a run</h2></div><SlidersHorizontal size={17} className="muted" /></div>
    <form onSubmit={e => { e.preventDefault(); void previewRun() }}>
      <div className="scenario-tile"><span className="scenario-icon"><FlaskConical size={20} /></span><div><strong>{scenario?.id ?? 'Loading scenario…'}</strong><span>{scenario ? `${scenario.width} × ${scenario.height} grid · ${scenario.capacity} homes` : 'Reading the local catalogue'}</span></div><span className="mini-tag">MAP</span></div>
      <InputPicker value={input} onChange={v => { setInput(v); setForm(f=>({...f,input_id:v?.id})); setPreview(null) }} onBusy={setUploading} disabled={!!busy} />
      <label>Allocation strategy<select aria-label="Allocation strategy" value={form.strategy} disabled={!!busy || !strategies.length} onChange={e => change('strategy', e.target.value)}>{strategies.map(s => <option key={s.id} value={s.id}>{human(s.id)}</option>)}</select></label>
      <div className="field-row"><label>Robots<input aria-label="Robots" type="number" min="1" max={scenario?.capacity ?? 100} step="1" required value={form.robots} disabled={!!busy} onChange={e => change('robots', e.target.value)} /></label><label>Orders<input aria-label="Orders" type="number" min="0" max="1000000" step="1" required value={form.orders} disabled={!!busy} onChange={e => change('orders', e.target.value)} /></label></div>
      <label>Allocation seed<input aria-label="Allocation seed" type="number" min="0" max="4294967295" step="1" required value={form.seed} disabled={!!busy} onChange={e => change('seed', e.target.value)} /></label>
      <span className="field-hint">The seed controls allocation; it does not generate a new workload.</span>
      <div className="field-label">Execution mode</div>
      <div className="mode-options">
        <button type="button" className={form.mode === 'observed' ? 'mode active' : 'mode'} disabled={!canObserve || !!busy} aria-pressed={form.mode === 'observed'} onClick={() => change('mode', 'observed')}><ScanEye size={17} /><span><strong>Observed</strong><small>Record sampled fleet state</small></span>{form.mode === 'observed' && <Check size={15} />}</button>
        <button type="button" className={form.mode === 'cli' ? 'mode active' : 'mode'} disabled={!!busy} aria-pressed={form.mode === 'cli'} onClick={() => change('mode', 'cli')}><FlaskConical size={17} /><span><strong>Original CLI</strong><small>Logs and scientific artifacts</small></span>{form.mode === 'cli' && <Check size={15} />}</button>
      </div>
      <details className="advanced"><summary><SlidersHorizontal size={13} /> Advanced settings <ChevronDown size={13} /></summary>
        <label>Candidate window<select aria-label="Candidate window" value={form.window} disabled={!!busy} onChange={e => change('window', e.target.value)}><option value="">Configured default</option><option value="fleet_size">Fleet size</option><option value="legacy">Legacy</option>{form.window && !['fleet_size', 'legacy'].includes(form.window) && <option value={form.window}>{form.window}</option>}</select></label>
        <div className="field-row"><label>Quantum (s)<input type="number" min="0" max="3600" step="any" placeholder={String(scenario?.settings.dispatch_quantum_s ?? 0)} value={form.quantum} disabled={!!busy} onChange={e => change('quantum', e.target.value)} /></label><label>Speed (m/s)<input type="number" min="0.000001" max="100" step="any" placeholder={String(scenario?.settings.speed_mps ?? 'Default')} value={form.speed} disabled={!!busy} onChange={e => change('speed', e.target.value)} /></label></div>
        {form.mode === 'observed' && <label>Sample interval (simulated s)<input type="number" min="0.001" max="3600" step="any" value={form.sample} disabled={!!busy} onChange={e => change('sample', e.target.value)} /></label>}
        <label className="checkbox"><input type="checkbox" checked={form.export} disabled={!!busy} onChange={e => change('export', e.target.checked)} /> Export CSV / Prolog</label>
        <p className="field-hint">Blank settings use the existing configuration. Orders = 0 selects all available orders.</p>
      </details>
      {error && <div className="inline-error" role="alert">{error}</div>}
      <button className="button primary full" type="submit" disabled={disabled || uploading || !!busy || !scenario || !strategies.length}>{busy ? <LoaderCircle size={16} className="spin" /> : <Play size={15} />} Preview run <ArrowRight size={15} /></button>
      <p className="setup-note">Review the effective settings before launch.<br />Scientific execution remains in the existing simulator.</p>
    </form>
    {preview && <dialog ref={dialog} className="preview-dialog" aria-label="Review run before launch" onCancel={() => setPreview(null)}>
      <span className="eyebrow">READY FOR YOUR REVIEW</span><h2>Launch this simulation?</h2><p className="muted">A new headless run will write to its own output directory.</p>
      <div className="preview-facts"><strong>{display(preview.plan.effective.robots, 0)} robots</strong><strong>{display(preview.plan.effective.orders, 0)} orders</strong><strong>{human(String(preview.spec.strategy))}</strong></div>
      <dl className="detail-list"><dt>Execution</dt><dd>{form.mode === 'observed' ? 'Observed · sampled state' : 'Original CLI'}</dd><dt>Seed</dt><dd>{display(preview.plan.effective.seed, 0)}</dd><dt>Dispatch quantum</dt><dd>{display(preview.plan.effective.quantum_s)} s</dd><dt>Speed</dt><dd>{display(preview.plan.effective.speed_mps)} m/s</dd></dl>
      {preview.plan.warnings?.length > 0 && <ul className="preview-warnings">{preview.plan.warnings.map(w => <li key={w}>{w}</li>)}</ul>}
      <details className="command-details"><summary>Server-generated command and destination</summary><pre>{JSON.stringify(preview.plan.argv, null, 2)}</pre><code>{preview.plan.output_dir}</code></details>
      {activeJob && <p className="inline-error">An owned job is still active. Let it finish or cancel it before launching.</p>}
      <div className="dialog-actions"><button className="button" type="button" onClick={() => setPreview(null)} disabled={busy === 'launch'}>Back to setup</button><button className="button primary" type="button" onClick={() => void launch()} disabled={disabled || !!busy || activeJob}>{busy === 'launch' ? <LoaderCircle size={15} className="spin" /> : <Play size={15} />}Launch run</button></div>
    </dialog>}
  </section>
}
