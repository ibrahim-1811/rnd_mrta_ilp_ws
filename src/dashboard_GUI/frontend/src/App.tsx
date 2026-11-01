import { useEffect, useRef, useState } from 'react'
import { Activity, ArrowUpRight, Bot, ChevronRight, CircleHelp, Clock3, Copy, Database, FileClock, Layers3, LayoutDashboard, LoaderCircle, Pause, Play, Radio, RefreshCw, Settings2, ShieldCheck, SkipBack, Square, Terminal, Warehouse, Wifi } from 'lucide-react'
import { act, read, message } from './api'
import { active, display, human, metricCards } from './model'
import type { Capabilities, Capability, Envelope, Geometry, Job, Run, Scenario, Snapshot, Strategy } from './types'
import { RunSetup } from './RunSetup'
import { WarehouseMap } from './WarehouseMap'
import { ExperimentsView } from './ExperimentsView'
import { HistoryView } from './HistoryView'
import { useJob } from './useJob'
import { useRecording } from './useRecording'

type View = 'operations' | 'history' | 'system' | 'experiments'
const sortJobs = (jobs: Job[]) => [...new Map(jobs.map(j => [j.id, j])).values()].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))

export default function App() {
  const [view, setView] = useState<View>('operations')
  const [revision, setRevision] = useState(0)
  const [health, setHealth] = useState<{ version: string; read_only: boolean } | null>(null)
  const [capabilities, setCapabilities] = useState<Capabilities>({})
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [scenarioId, setScenarioId] = useState('')
  const [geometry, setGeometry] = useState<Geometry | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [jobId, setJobId] = useState<string | null>(() => new URLSearchParams(location.search).get('job'))
  const [robotId, setRobotId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [mapLoading, setMapLoading] = useState(false)
  const [catalogueError, setCatalogueError] = useState('')
  const [mapError, setMapError] = useState('')
  const [actionError, setActionError] = useState('')
  const [cancelling, setCancelling] = useState(false)
  const [storedMetrics, setMetrics] = useState<(Envelope<Record<string, unknown> | null> & { job_id: string }) | null>(null)
  const metrics = storedMetrics?.job_id === jobId ? storedMetrics : null
  const [follow, setFollow] = useState(true)
  const initialized = useRef(false)
  const selectedRef = useRef(jobId); selectedRef.current = jobId
  const logBox = useRef<HTMLPreElement>(null)
  const { job, logs, error: jobError, transport } = useJob(jobId, revision)
  const scenario = scenarios.find(item => item.id === scenarioId) ?? null
  const referenceScenario = scenarios.find(item => item.reference && item.valid) ?? scenarios.find(item => item.valid) ?? null
  const jobScenarioId = typeof job?.plan?.effective?.scenario_id === 'string' ? job.plan.effective.scenario_id : null
  const displayedScenario = scenarios.find(item => item.id === (jobScenarioId ?? scenarioId)) ?? null
  const geometryScenarioId = jobScenarioId ?? scenarioId
  const recording = useRecording(job)
  const frame = recording.frame?.job_id === jobId ? recording.frame : null
  const canObserve = (capabilities.telemetry as Capability | undefined)?.readable === true
  const canLaunch = !!health && !health.read_only && (capabilities.simulation_launch as Capability | undefined)?.readable === true

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setCatalogueError('')
    void Promise.allSettled([
      read<{ version: string; read_only: boolean }>('/health', controller.signal),
      read<Capabilities>('/capabilities', controller.signal), read<Strategy[]>('/strategies', controller.signal),
      read<Scenario[]>('/scenarios', controller.signal), read<Job[]>('/jobs?limit=100', controller.signal),
    ] as const).then(results => {
      if (controller.signal.aborted) return
      if (results[0].status === 'fulfilled') setHealth(results[0].value.data); else setHealth(null)
      if (results[1].status === 'fulfilled') setCapabilities(results[1].value.data)
      if (results[2].status === 'fulfilled') setStrategies(results[2].value.data)
      if (results[3].status === 'fulfilled') {
        const available = results[3].value.data
        setScenarios(available)
        setScenarioId(current => available.some(item => item.id === current && item.valid)
          ? current : (available.find(item => item.reference && item.valid) ?? available.find(item => item.valid))?.id ?? '')
      }
      if (results[4].status === 'fulfilled') {
        const available = sortJobs(results[4].value.data)
        setJobs(available)
        if (!initialized.current) { initialized.current = true; if (!selectedRef.current && available.length) setJobId(available[0].id) }
      }
      const failures = results.filter(r => r.status === 'rejected') as PromiseRejectedResult[]
      setCatalogueError(failures.length ? failures.map(r => message(r.reason)).join(' · ') : '')
      setLoading(false)
    })
    return () => controller.abort()
  }, [revision])
  useEffect(() => {
    setGeometry(null); setMapError('')
    if (!geometryScenarioId) return
    const controller = new AbortController()
    setMapLoading(true)
    void read<Geometry>(`/scenarios/${encodeURIComponent(geometryScenarioId)}/map`, controller.signal)
      .then(result => { if (!controller.signal.aborted) setGeometry(result.data) })
      .catch(error => { if (!controller.signal.aborted) setMapError(message(error)) })
      .finally(() => { if (!controller.signal.aborted) setMapLoading(false) })
    return () => controller.abort()
  }, [geometryScenarioId, revision])
  useEffect(() => {
    if (job) setJobs(current => sortJobs([job, ...current.filter(j => j.id !== job.id)]))
  }, [job?.id, job?.status, job?.owned_process_alive])
  useEffect(() => { setRobotId(null); setActionError('') }, [jobId])
  useEffect(() => {
    setMetrics(null)
    if (!job || job.kind !== 'run' || active(job)) return
    const controller = new AbortController()
    void read<Run[]>('/runs?scope=app&limit=1000', controller.signal).then(async result => {
      const record = result.data.find(r => r.directory === `singles/${job.id}`)
      if (record) {
        const values = await read<Record<string, unknown> | null>(`/runs/${record.id}/metrics?scope=app`, controller.signal)
        if (!controller.signal.aborted) setMetrics({ ...values, job_id: job.id })
      }
    }).catch(() => { /* Original artifacts may not exist for partial/cancelled jobs. */ })
    return () => controller.abort()
  }, [job?.id, job?.status, revision])
  useEffect(() => { if (follow && logBox.current) logBox.current.scrollTop = logBox.current.scrollHeight }, [logs, follow])
  const refresh = () => setRevision(v => v + 1)
  const openJob = (id: string) => { setJobId(id); setView('operations'); history.replaceState(null, '', `${location.pathname}?job=${encodeURIComponent(id)}`) }
  async function cancelJob() {
    if (!job || cancelling) return
    const id = job.id; setCancelling(true); setActionError('')
    try { await act(`/jobs/${encodeURIComponent(id)}/cancel`, {}); if (selectedRef.current === id) refresh() }
    catch (e) { setActionError(message(e)) } finally { setCancelling(false) }
  }
  const drops = (job?.telemetry?.footer?.dropped_samples_total ?? job?.telemetry?.dropped_samples_total ?? 0) + (job?.telemetry?.footer?.writer_dropped_samples ?? 0)
  const partial = !!job && (job.telemetry.availability === 'partial' || !!job.telemetry.footer?.truncated || !!job.telemetry.footer?.error || drops > 0)
  const live = !!frame && recording.mode !== 'recording' && !!job?.telemetry.live && !job.telemetry.stale && active(job)
  const source = recording.mode === 'recording' ? 'Recorded playback' : !frame ? 'Observation unavailable' : job?.telemetry.stale && active(job) ? 'Stale sample' : live ? 'Live sample' : 'Last recorded sample'

  const chosenRobot = frame?.payload.robots.find(r => r.robot_id === robotId)
  const cards = metricCards(metrics?.data ?? null, metrics?.availability ?? 'missing')
  return <div className="app-shell">
    <a className="skip-link" href="#workspace">Skip to workspace</a>
    <aside className="sidebar"><a className="brand" href="#workspace" onClick={() => setView('operations')}><span className="brand-mark"><Warehouse size={23} /></span><span>MRTA<small>CONTROL CENTER</small></span></a>
      <div className="sidebar-section">WORKSPACE</div><nav aria-label="Main navigation">
        {([['operations', LayoutDashboard, 'Operations'], ['experiments', Layers3, 'Experiments'], ['history', FileClock, 'Run history'], ['system', Settings2, 'System']] as const).map(([key, Icon, title]) => <button key={key} aria-label={title} className={view === key ? 'nav-item active' : 'nav-item'} aria-current={view === key ? 'page' : undefined} onClick={() => setView(key)}><Icon size={18} /><span>{title}</span>{view === key && <ChevronRight size={15} />}</button>)}
      </nav><div className="sidebar-map"><span className="dot" /><div><strong>{displayedScenario?.id ?? 'W100'}</strong><small>{displayedScenario?.label ?? 'Research warehouse'}</small></div><Layers3 size={15} /></div>
      <div className="sidebar-bottom"><div className="local-icon"><ShieldCheck size={17} /></div><div><strong>Local workspace</strong><small>Research operations</small></div></div>
    </aside>
    <div className="main-shell"><header className="topbar"><div className="breadcrumbs">Workspace <ChevronRight size={12} /><span>{view === 'operations' ? 'Warehouse operations' : view === 'history' ? 'Run history' : view === 'experiments' ? 'Experiments' : 'System'}</span></div><div className="topbar-right"><span className="local-chip"><ShieldCheck size={12} /> Local only</span><span className={`connection ${health ? 'connected' : ''}`}><span className={`dot ${health ? '' : 'offline'}`} />{health ? 'Backend connected' : loading ? 'Connecting…' : 'Backend offline'}</span></div></header>
      <main id="workspace" tabIndex={-1}>
        {(catalogueError || mapError) && <div className="notice error" role="alert"><strong>{health ? 'Some data could not be loaded.' : 'Local backend unavailable.'}</strong> {[catalogueError, mapError].filter(Boolean).join(' · ')}<button className="text-button" onClick={refresh}>Try again</button></div>}
        <div hidden={view !== 'operations'}>
          <div className="section-intro"><div><span className="eyebrow">WAREHOUSE CONTROL</span><h1>Warehouse operations<span className="title-dot">.</span></h1><p>Configure a run. Observe the fleet. Inspect the evidence.</p></div><button className="button" onClick={refresh}><RefreshCw size={14} className={loading ? 'spin' : ''} /> Refresh workspace</button></div>
          <div className="job-toolbar"><div className="warehouse-label"><span className="live-square"><Warehouse size={18} /></span><strong>{displayedScenario?.id ?? 'Warehouse'}</strong><span>{displayedScenario?.label ?? 'Headless simulation'}</span></div><label className="job-picker">Selected job<select aria-label="Selected job" value={jobId ?? ''} onChange={e => { if (e.target.value) openJob(e.target.value); else setJobId(null) }}><option value="">Choose a job</option>{jobId && !jobs.some(j => j.id === jobId) && <option value={jobId}>{jobId.slice(0, 8)}</option>}{jobs.map(j => <option value={j.id} key={j.id}>{j.id.slice(0, 8)} · {human(j.status)} · {String(j.plan?.request?.strategy ?? j.kind)}</option>)}</select></label>{job && active(job) && <button className="button" onClick={() => void cancelJob()} disabled={cancelling || job.status === 'cancel_requested'}><Square size={12} />{cancelling || job.status === 'cancel_requested' ? 'Cancellation requested…' : 'Cancel run'}</button>}</div>
          <div className="overview-stats">
            <Stat icon={<Bot size={18} />} label={frame ? 'Robots observed' : 'Requested robots'} value={display(frame?.payload.robots.length ?? job?.plan.effective.robots, 0)} note={frame ? 'From copied fleet state' : job ? 'Effective run configuration' : 'Choose or launch a run'} />
            <Stat icon={<Layers3 size={18} />} label="Completed orders" value={frame ? `${display(frame.payload.orders?.completed, 0)} / ${display(frame.payload.orders?.total, 0)}` : metrics?.data ? `${display(metrics.data.orders_completed, 0)} / ${display(metrics.data.orders, 0)}` : '—'} note={frame ? `${display(frame.payload.orders?.pending, 0)} pending · ${display(frame.payload.orders?.in_progress, 0)} in progress` : 'No inferred completion counts'} />
            <Stat icon={<Clock3 size={18} />} label="Simulation clock" value={frame ? `${display(frame.sim_time_s)} s` : '—'} note={frame ? `Exact ${frame.sim_time_exact} s` : 'Scientific time, not wall time'} />
            <Stat icon={<Activity size={18} />} label="Run status" value={job ? human(job.status) : 'No run selected'} note={job ? `${human(job.artifact_status ?? 'unknown')} artifacts · ${display(job.elapsed_wall_s)} s wall` : 'The simulator is ready when you are'} />
          </div>
          {jobError && <div className="notice error" role="alert">{jobError}</div>}{actionError && <div className="notice error" role="alert">{actionError}</div>}
          <div className="operations-grid">
            <RunSetup scenario={scenario} scenarios={scenarios} scenarioId={scenarioId} onScenarioChange={id => { if (!jobs.some(active)) { setScenarioId(id); if (!jobScenarioId) setGeometry(null) } }} strategies={strategies} canObserve={canObserve} activeJob={jobs.some(active)} disabled={!canLaunch} onRefresh={refresh} onLaunched={j => { setJobs(items => sortJobs([j, ...items])); openJob(j.id) }} />
            <div className="map-column"><div className="observation-strip"><span className={`badge ${frame ? live ? 'good' : 'recorded' : 'neutral'}`}><Radio size={12} />{source}</span><span className="muted">{frame ? `t = ${frame.sim_time_exact} s` : job?.telemetry.reason ?? 'Select an observed run for actual robot state'}</span></div>
              <WarehouseMap geometry={geometry} frame={frame} selected={robotId} onSelect={setRobotId} loading={loading || mapLoading} />
              <div className="playback-bar"><div className="playback-top"><span><FileClock size={15} /><strong>Sampled recording</strong></span><span className="muted">{drops ? `${display(drops, 0)} dropped samples` : job?.telemetry.recording_complete ? 'Recording complete' : 'Actual captured cells · no interpolation'}</span></div><div className="playback-actions">
                <button className="icon-button" aria-label="First recording sample" disabled={!recording.frames.length} onClick={() => recording.seek(0)}><SkipBack size={17} /></button>
                <button className="button small primary" disabled={!job?.telemetry.available || recording.loading} onClick={() => recording.frames.length ? recording.toggle() : void recording.load()}>{recording.loading ? <LoaderCircle size={14} className="spin" /> : recording.playing ? <Pause size={14} /> : <Play size={14} />}{recording.loading ? 'Loading…' : recording.playing ? 'Pause' : recording.frames.length ? 'Play' : 'Play recording'}</button>
                <input aria-label="Recording sample" type="range" min="0" max={Math.max(0, recording.frames.length - 1)} value={recording.index} disabled={!recording.frames.length} onChange={e => recording.seek(Number(e.target.value))} />
                <span className="sample-count">{recording.frames.length ? `${recording.index + 1} / ${recording.frames.length}` : '—'}</span>
                <select aria-label="Playback display rate" value={recording.fps} onChange={e => recording.setFps(Number(e.target.value))}>{[2, 4, 8, 12].map(n => <option key={n} value={n}>{n} samples/s</option>)}</select>
                <button className={`button small ${recording.mode === 'latest' ? 'selected' : 'ghost'}`} onClick={recording.latest}>Latest</button>
              </div>{recording.next !== null && <button className="text-button" onClick={() => void recording.load(recording.next!)}>Load next recording segment</button>}{recording.error && <p className="inline-error" role="alert">{recording.error}</p>}{partial && <p className="notice">Partial observation: samples may be missing or the recording ended early. Scientific results are reported separately.</p>}{job?.telemetry.stale && active(job) && <p className="notice">Observation is stale; the displayed cells are the last captured sample.</p>}</div>
            </div>
            <section className="panel fleet-panel"><div className="panel-heading"><div><span className="eyebrow">OBSERVED STATE</span><h2>Fleet <span className="count-pill">{frame?.payload.robots.length ?? '—'}</span></h2></div><Bot size={18} className="muted" /></div>
              {frame ? <><div className="fleet-source">{source}<span className={`dot ${live ? '' : 'neutral'}`} /></div><div className="fleet-list">{frame.payload.robots.map(robot => <button className={`fleet-row ${robotId === robot.robot_id ? 'selected' : ''}`} key={robot.robot_id} onClick={() => setRobotId(robot.robot_id)}><span className="robot-avatar"><Bot size={18} /></span><span className="fleet-row-body"><strong>{robot.robot_id}</strong><small>{human(robot.status)} · [{robot.cell.join(', ')}]</small><span className="battery-track"><i style={{ width: robot.soc_percent === null ? '0' : `${Math.min(100, Math.max(0, robot.soc_percent))}%` }} /></span></span><span className="soc">{display(robot.soc_percent)}{robot.soc_percent === null ? '' : '%'}</span></button>)}</div>
                {chosenRobot && <div className="robot-detail"><strong>{chosenRobot.robot_id}</strong><dl className="detail-list"><dt>Active order</dt><dd>{chosenRobot.active_order_id ?? '—'}</dd><dt>Assigned order</dt><dd>{chosenRobot.assigned_order_id ?? '—'}</dd><dt>Charging state</dt><dd>{chosenRobot.charge_state ? human(chosenRobot.charge_state) : '—'}</dd><dt>Distance</dt><dd>{display(chosenRobot.distance_m)} m</dd><dt>Waiting</dt><dd>{display(chosenRobot.waiting_time_s)} s</dd><dt>Exact SOC</dt><dd>{chosenRobot.soc_exact ?? 'Unavailable'}</dd></dl></div>}
                <div className="fleet-note"><ShieldCheck size={15} /><span>Read-only observation.<br />Robot ownership is unchanged.</span></div></> : <div className="empty fleet-empty"><Bot size={30} /><strong>No fleet observation</strong><span>{job?.telemetry.reason ?? 'Choose an observed run to inspect actual robot state.'}</span></div>}
            </section>
          </div>
          <div className="bottom-grid"><section className="panel log-panel"><div className="panel-heading"><div><span className="eyebrow">PROCESS OUTPUT</span><h2><Terminal size={17} /> Runner logs</h2></div><div className="log-tools"><span className="transport"><span className={`dot ${transport === 'streaming' ? '' : 'neutral'}`} />{transport}</span><label className="checkbox"><input type="checkbox" checked={follow} onChange={e => setFollow(e.target.checked)} /> Follow</label><button className="icon-button" aria-label="Copy displayed logs" title="Copy displayed logs" disabled={!logs} onClick={() => void navigator.clipboard?.writeText(logs).catch(e => setActionError(message(e)))}><Copy size={14} /></button></div></div><pre ref={logBox} className="log-output" tabIndex={0}>{logs || (job ? 'Waiting for runner output…' : 'Select a job to read its original process output.\nNo simulation is started automatically.')}</pre><div className="log-footer"><span>Showing up to the latest 150 KB of fetched output.</span>{job && <span className="mono">job {job.id.slice(0, 12)}</span>}</div></section>
            <section className="panel results-panel"><div className="panel-heading"><div><span className="eyebrow">ORIGINAL SCIENTIFIC OUTPUT</span><h2>Run metrics</h2></div><Database size={17} className="muted" /></div><div className="result-metrics">{cards.map(c => <div key={c.key}><span>{c.label}</span><strong>{display(c.value)} <small>{c.value === null ? '' : c.unit}</small></strong></div>)}</div><div className="results-note">{metrics?.availability === 'legacy' ? 'Legacy schema: order makespan was not recorded.' : metrics ? `Original ${metrics.availability} metrics · no values recomputed.` : 'Metrics appear when the original runner writes valid artifacts.'}</div><button className="text-button" onClick={() => setView('history')}>Inspect run artifacts <ArrowUpRight size={14} /></button></section></div>
          <div className="workspace-footer"><span><ShieldCheck size={12} /> Fixed scientific infrastructure · policy is the research variable</span><span>Local browser access · ROS/Gazebo deferred</span></div>
        </div>
        {view === 'experiments' && <ExperimentsView strategies={strategies} capacity={referenceScenario?.capacity??100} activeJob={jobs.some(active)} disabled={!canLaunch} onLaunched={j=>{setJobs(items=>sortJobs([j,...items]));openJob(j.id)}} />}
        {view === 'history' && <HistoryView onOpenJob={openJob} />}
        {view === 'system' && <div className="system-view"><div className="section-intro"><div><span className="eyebrow">LOCAL ENVIRONMENT</span><h1>System</h1><p>Capabilities and catalogue values read directly from the backend.</p></div><button className="button" onClick={refresh}><RefreshCw size={14} /> Refresh</button></div><div className="system-grid"><section className="panel system-card"><h2><Wifi size={18} /> Backend connection</h2><dl className="detail-list"><dt>Status</dt><dd>{health ? 'Connected' : 'Offline'}</dd><dt>Version</dt><dd>{health?.version ?? '—'}</dd><dt>Access</dt><dd>Loopback only</dd><dt>Service mode</dt><dd>{health ? health.read_only ? 'Read-only' : 'Operational' : '—'}</dd></dl><a className="text-button" href="http://127.0.0.1:8000/docs" target="_blank" rel="noreferrer">Open backend API documentation <ArrowUpRight size={14} /></a></section><section className="panel system-card"><h2><ShieldCheck size={18} /> Available capabilities</h2>{[['simulation_launch', 'Single-run controls'], ['telemetry', 'Observed single-run state'], ['experiment_control', 'Experiment runner API'], ['paired_comparison', 'Paired comparison'], ['ilp', 'Learned rules']].map(([key, label]) => { const c = capabilities[key] as Capability | undefined; return <div className="capability-row" key={key}><span>{label}</span><span className={`badge ${c?.readable ? 'good' : 'neutral'}`}>{c?.readable ? 'Available' : 'Unavailable'}</span></div> })}</section><section className="panel system-card"><h2><Activity size={18} /> Registered strategies</h2><p className="muted">Registration is separate from scientific review/approval.</p>{strategies.map(s => <div className="strategy-row" key={s.id} title={s.review_status}><strong>{human(s.id)}</strong><code>{s.implementation}</code></div>)}</section><section className="panel system-card"><h2><Layers3 size={18} /> Scenario provenance</h2><dl className="detail-list"><dt>Map</dt><dd>{displayedScenario?.id ?? '—'}</dd><dt>Capacity</dt><dd>{display(displayedScenario?.capacity, 0)} homes</dd><dt>Directed edges</dt><dd>{display(geometry?.directed_edges.length, 0)}</dd>{Object.entries(displayedScenario?.hashes ?? {}).map(([key, value]) => <div className="hash-row" key={key}><dt>{human(key)} SHA-256</dt><dd title={value}>{value.slice(0, 16)}…</dd></div>)}</dl><details className="provenance"><summary>Read-only effective configuration</summary><pre>{JSON.stringify(displayedScenario?.settings ?? {}, null, 2)}</pre></details></section></div></div>}
      </main>
    </div>
  </div>
}

function Stat({ icon, label, value, note }: { icon: React.ReactNode; label: string; value: string; note: string }) {
  return <div className="stat-card"><div className="stat-top">{icon}<span>{label}</span></div><strong>{value}</strong><small>{note}</small></div>
}
