import { useEffect, useRef, useState } from 'react'
import { ArrowUpRight, Download, FileClock, FileJson, RefreshCw } from 'lucide-react'
import { DataInspector } from './DataInspector'
import { read, message } from './api'
import { display, human, metricCards } from './model'
import type { Artifact, Envelope, Run, Scope, TracePage } from './types'

export function HistoryView({ onOpenJob }: { onOpenJob: (id: string) => void }) {
  const [scope, setScope] = useState<Scope>('app')
  const [revision, setRevision] = useState(0)
  const [runs, setRuns] = useState<Run[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [selected, setSelected] = useState<Run | null>(null)
  const [manifest, setManifest] = useState<Record<string, unknown> | null>(null)
  const [metrics, setMetrics] = useState<Envelope<Record<string, unknown> | null> | null>(null)
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const context = useRef(''); context.current = `${scope}:${revision}`
  const selection = useRef(''); selection.current = `${scope}:${selected?.id ?? ''}`
  useEffect(() => {
    const controller = new AbortController()
    setRuns([]); setSelected(null); setCursor(null); setError(''); setLoading(true)
    read<Run[]>(`/runs?scope=${scope}&limit=100`, controller.signal).then(result => {
      if (!controller.signal.aborted) { setRuns(result.data); setCursor(result.next_cursor) }
    }).catch(e => { if (!controller.signal.aborted) setError(message(e)) }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [scope, revision])
  useEffect(() => {
    setManifest(null); setMetrics(null); setArtifacts([]); setNote('')
    if (!selected) return
    const controller = new AbortController()
    const prefix = `/runs/${encodeURIComponent(selected.id)}`
    void Promise.allSettled([
      read<{ manifest: Record<string, unknown> | null }>(`${prefix}?scope=${scope}`, controller.signal),
      read<Record<string, unknown> | null>(`${prefix}/metrics?scope=${scope}`, controller.signal),
      read<Artifact[]>(`${prefix}/artifacts?scope=${scope}`, controller.signal),
    ]).then(results => {
      if (controller.signal.aborted) return
      if (results[0].status === 'fulfilled') setManifest(results[0].value.data.manifest)
      if (results[1].status === 'fulfilled') setMetrics(results[1].value)
      if (results[2].status === 'fulfilled') setArtifacts(results[2].value.data)
      const failures = results.filter(r => r.status === 'rejected') as PromiseRejectedResult[]
      if (failures.length) setNote(failures.map(r => message(r.reason)).join('; '))
    })
    return () => controller.abort()
  }, [selected?.id, scope])
  async function more() {
    if (!cursor || loading) return
    setLoading(true)
    const key = context.current
    try { const result = await read<Run[]>(`/runs?scope=${scope}&limit=100&cursor=${encodeURIComponent(cursor)}`); if (key !== context.current) return; setRuns(items => [...items, ...result.data]); setCursor(result.next_cursor) }
    catch (e) { if (key === context.current) setError(message(e)) } finally { if (key === context.current) setLoading(false) }
  }
  async function observation() {
    if (!selected) return
    const key = selection.current
    setNote('Finding an associated recording…')
    try {
      const result = await read<TracePage | null>(`/runs/${encodeURIComponent(selected.id)}/telemetry?scope=${scope}&limit=1`)
      if (key !== selection.current) return
      const record = result.data?.records[0] as { job_id?: string } | undefined
      if (result.availability === 'available' && typeof record?.job_id === 'string') onOpenJob(record.job_id)
      else setNote('This run has no genuine recorded fleet observation. Its scientific artifacts remain available.')
    } catch (e) { if (key === selection.current) setNote(message(e)) }
  }
  return <div className="history-view">
    <div className="section-intro"><div><span className="eyebrow">RESEARCH ARTIFACTS</span><h1>Run history</h1><p>Original results, with provenance and integrity kept separate from process status.</p></div><button className="button" onClick={() => setRevision(r => r + 1)}><RefreshCw size={14} /> Refresh</button></div>
    <div className="history-toolbar"><div className="segmented"><button aria-pressed={scope === 'app'} className={scope === 'app' ? 'active' : ''} onClick={() => setScope('app')}>App runs</button><button aria-pressed={scope === 'history'} className={scope === 'history' ? 'active' : ''} onClick={() => setScope('history')}>Repository history</button></div><span className="muted">{runs.length} loaded{cursor ? ' · more available' : ''}</span></div>
    {error && <div className="notice error" role="alert">{error} Refresh to restart pagination if source artifacts changed.</div>}
    <section className="panel history-table"><div className="table-scroll"><table><thead><tr><th>Run directory</th><th>Artifact integrity</th><th>Metrics</th><th>Process evidence</th><th></th></tr></thead><tbody>
      {runs.map(run => <tr key={run.id} className={selected?.id === run.id ? 'row-selected' : ''}><td><button className="run-link" onClick={() => setSelected(run)}><FileClock size={16} /><span>{run.directory}<small>{run.id.slice(0, 12)}</small></span></button></td><td><span className={`badge ${run.integrity === 'complete' ? 'good' : 'warn'}`}>{human(run.integrity)}</span></td><td>{human(run.metrics_schema)}</td><td>{human(run.process_status)}</td><td><button className="icon-button" aria-label={`Inspect ${run.directory}`} onClick={() => setSelected(run)}><ArrowUpRight size={16} /></button></td></tr>)}
    </tbody></table></div>{!runs.length && <div className="empty"><FileClock size={28} /><strong>{loading ? 'Indexing original artifacts…' : 'No runs in this scope yet'}</strong><span>{loading ? 'The first history scan may take a moment.' : 'Launch a run, or browse repository history.'}</span></div>}
      {cursor && <div className="table-footer"><button className="button" onClick={() => void more()} disabled={loading}>{loading ? 'Loading…' : 'Load more runs'}</button></div>}
    </section>
    {selected && <section className="panel history-detail"><div className="panel-heading"><div><span className="eyebrow">SELECTED RUN</span><h2>{selected.directory}</h2></div><button className="button" onClick={() => void observation()}><ArrowUpRight size={14} /> Open observation</button></div>
      <div className="history-facts"><span>Strategy <strong>{typeof manifest?.strategy === 'string' ? human(manifest.strategy) : '—'}</strong></span><span>Robots <strong>{display(manifest?.robot_count, 0)}</strong></span><span>Orders <strong>{display(manifest?.orders, 0)}</strong></span><span>Seed <strong>{display(manifest?.seed, 0)}</strong></span></div>
      <div className="metric-grid">{metricCards(metrics?.data ?? null, metrics?.availability ?? 'missing').map(card => <div className="metric-card" key={card.key}><span>{card.label}</span><strong>{display(card.value)} <small>{card.value === null ? '' : card.unit}</small></strong></div>)}</div>
      {metrics?.availability === 'legacy' && <div className="notice">Legacy metrics: fleet settled time is preserved. Order makespan was not recorded.</div>}
      {metrics && !['legacy', 'current'].includes(metrics.availability) && <div className="notice">Metrics {metrics.availability}; missing values remain unknown.</div>}
      {!!selected.warnings?.length && <details className="provenance"><summary>Integrity and provenance notes</summary><ul>{selected.warnings.map(w => <li key={w}>{w}</li>)}</ul></details>}
      {note && <div className="notice" role="status">{note}</div>}
      <div className="artifact-list">{artifacts.filter(a => a.size !== null && a.availability !== 'unavailable').map(a => <a className="artifact" key={a.id} href={`/api/v1/runs/${encodeURIComponent(selected.id)}/artifacts/${encodeURIComponent(a.id)}?scope=${scope}`} download><Download size={14} /><span>{a.id}</span><small>{display((a.size ?? 0) / 1024)} KB</small></a>)}</div>
      <DataInspector key={`${scope}:${selected.id}`} runId={selected.id} scope={scope} />
      <details className="provenance"><summary><FileJson size={14} /> Original manifest</summary><pre>{manifest ? JSON.stringify(manifest, null, 2) : 'No readable manifest available.'}</pre></details>
    </section>}
  </div>
}
