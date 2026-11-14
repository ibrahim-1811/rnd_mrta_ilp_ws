import { useEffect,useRef,useState } from 'react'
import { FlaskConical,RefreshCw,Square,Terminal } from 'lucide-react'
import { ExperimentSetup } from './ExperimentSetup'
import { PerformanceSummary,type PerformanceRecord } from './PerformanceSummary'
import { read,message } from './api'
import { active,display,human } from './model'
import type { Job,Scope,Strategy } from './types'

type Experiment={id:string;name:string;directory:string;spec:{strategies?:string[];workloads?:string};expected_runs:number}
type Cell={strategy:string;robots:number;seed:number;status:string;reason:string|null;integrity:string;run_id:string;warnings:string[]}
type Detail=Experiment&{manifest:Record<string,unknown>;complete:boolean;runs:Cell[];excluded:Cell[];extra:string[];warnings:string[]}
type Comparison={records:PerformanceRecord[];complete:boolean;excluded:Cell[];extra:string[];method:string;expected_runs:number;present_runs:number;warnings:string[]}
export function ExperimentsView({strategies,capacity,activeJob,onLaunched,disabled,selectedJob=null,logs='',transport='idle',onCancel,cancelling=false}:{strategies:Strategy[];capacity:number;activeJob:boolean;onLaunched:(j:Job)=>void;disabled:boolean;selectedJob?:Job|null;logs?:string;transport?:string;onCancel?:()=>void;cancelling?:boolean}){
 const [scope,setScope]=useState<Scope>('app');const [revision,setRevision]=useState(0)
 const [items,setItems]=useState<Experiment[]>([]);const [selected,setSelected]=useState('')
 const [detail,setDetail]=useState<Detail|null>(null);const [error,setError]=useState('');const [loading,setLoading]=useState(false)
 const [baseline,setBaseline]=useState('nearest_astar');const [allow,setAllow]=useState(false);const [comparison,setComparison]=useState<Comparison|null>(null);const [comparing,setComparing]=useState(false)
 const [metric,setMetric]=useState('order_makespan_s');const [analysisError,setAnalysisError]=useState('')
 const analysis=useRef<AbortController|null>(null)
 const preferredRef=useRef('')
 const lastStatusRef=useRef('')
 useEffect(()=>{
  const key=selectedJob?.kind==='experiment'?`${selectedJob.id}:${selectedJob.status}`:''
  if(key&&key!==lastStatusRef.current){
   lastStatusRef.current=key
   const requestedName=String(selectedJob?.plan?.request?.name??'')
   if(requestedName)preferredRef.current=requestedName
   if(selectedJob?.status==='completed'){setScope('app');setRevision(v=>v+1)}
  }
 },[selectedJob?.id,selectedJob?.kind,selectedJob?.status,selectedJob?.plan?.request?.name])
 useEffect(()=>{
  const c=new AbortController();setLoading(true);setError('');setItems([]);setSelected('');setDetail(null)
  void read<Experiment[]>(`/experiments?scope=${scope}`,c.signal).then(r=>{if(!c.signal.aborted){setItems(r.data);const preferred=preferredRef.current&&r.data.find(item=>item.id===preferredRef.current||item.name===preferredRef.current)?.id;setSelected(preferred??r.data[0]?.id??'')}}).catch(e=>{if(!c.signal.aborted)setError(message(e))}).finally(()=>{if(!c.signal.aborted)setLoading(false)})
  return()=>c.abort()
 },[scope,revision])
 useEffect(()=>{
  const c=new AbortController();setDetail(null);setError('')
  if(selected)void read<Detail>(`/experiments/${encodeURIComponent(selected)}?scope=${scope}`,c.signal).then(r=>{if(!c.signal.aborted){setDetail(r.data);const names=r.data.spec.strategies??[...new Set(r.data.runs.map(v=>v.strategy))];setBaseline(names.includes('nearest_astar')?'nearest_astar':names[0]??'')}}).catch(e=>{if(!c.signal.aborted)setError(message(e))})
  return()=>c.abort()
 },[selected,scope,revision])
 useEffect(()=>{analysis.current?.abort();setComparison(null);setAnalysisError('');setComparing(false);return()=>analysis.current?.abort()},[selected,scope,revision,baseline,allow])
 async function compare(){
  if(!detail||comparing)return
  const c=new AbortController();analysis.current?.abort();analysis.current=c;setComparing(true);setComparison(null);setAnalysisError('')
  try{const r=await read<Comparison>(`/experiments/${encodeURIComponent(detail.id)}/comparisons?scope=${scope}&baseline=${encodeURIComponent(baseline)}&allow_incomplete=${allow}`,c.signal);if(!c.signal.aborted)setComparison(r.data)}catch(e){if(!c.signal.aborted)setAnalysisError(message(e))}finally{if(!c.signal.aborted)setComparing(false)}
 }
 const names=detail?[...new Set(detail.runs.map(v=>v.strategy))]:[]
 const records=comparison?.records.filter(r=>!metric||r.metric===metric)??[]
 const experimentJob=selectedJob?.kind==='experiment'?selectedJob:null
 return <div><div className="section-intro"><div><span className="eyebrow">REPRODUCIBLE STUDIES</span><h1>Experiments & comparisons</h1><p>Run a controlled matrix, monitor live progress here, and compare paired workloads.</p></div><button className="button" onClick={()=>setRevision(v=>v+1)}><RefreshCw size={14}/>Refresh studies</button></div>
 <div className="research-grid"><ExperimentSetup strategies={strategies} capacity={capacity} activeJob={activeJob} onLaunched={onLaunched} disabled={disabled}/><div className="study-column">
 {experimentJob&&<section className="panel log-panel"><div className="panel-heading"><div><span className="eyebrow">MATRIX EXECUTION</span><h2><Terminal size={17}/>{String(experimentJob.plan?.request?.name??`Job ${experimentJob.id.slice(0,8)}`)}</h2></div><div className="log-tools"><span className={`badge ${experimentJob.status==='completed'?'good':active(experimentJob)?'recorded':'warn'}`}>{human(experimentJob.status)} · {display(experimentJob.elapsed_wall_s)} s</span><span className="transport"><span className={`dot ${transport==='streaming'?'':'neutral'}`}/>{transport}</span>{active(experimentJob)&&onCancel&&<button className="button small" onClick={onCancel} disabled={cancelling||experimentJob.status==='cancel_requested'}><Square size={12}/>{cancelling||experimentJob.status==='cancel_requested'?'Stopping…':'Cancel matrix'}</button>}</div></div>{experimentJob.error&&<p className="notice error" role="alert" style={{margin:'12px 18px'}}>{experimentJob.error}</p>}<pre className="log-output" tabIndex={0}>{logs||(active(experimentJob)?'Waiting for matrix runner output…':'No runner output recorded.')}</pre><div className="log-footer"><span>Artifacts: {human(experimentJob.artifact_status??'unknown')}</span><span className="mono">job {experimentJob.id.slice(0,12)}</span></div></section>}
 <section className="panel"><div className="panel-heading"><h2><FlaskConical size={17}/>Experiment library</h2><div className="segmented"><button aria-pressed={scope==='app'} onClick={()=>setScope('app')}>App</button><button aria-pressed={scope==='history'} onClick={()=>setScope('history')}>Repository</button></div></div><div className="study-picker"><label>Saved experiment<select aria-label="Saved experiment" value={selected} onChange={e=>setSelected(e.target.value)}><option value="">Select an experiment</option>{items.map(e=><option key={e.id} value={e.id}>{e.name} · {e.expected_runs??'unknown'} runs</option>)}</select></label><p className="field-hint">Each experiment is a separate condition. Different quantum/configuration settings cannot be pooled.</p></div>
 {loading&&<p className="inspector-note">Reading experiment manifests…</p>}{error&&<p role="alert" className="notice error">{error}</p>}{!loading&&!items.length&&<div className="empty"><FlaskConical size={27}/><strong>No experiment manifests in this scope</strong><span>Preview and launch a small matrix, then refresh after it writes its original files.</span></div>}
 </section>
 {detail&&<><section className="panel experiment-detail"><div className="panel-heading"><div><span className="eyebrow">ORIGINAL ARTIFACT EVIDENCE</span><h2>{detail.name}</h2></div><span className={`badge ${detail.complete?'good':'warn'}`}>{detail.complete?'Complete artifacts':'Incomplete / invalid'}</span></div>
 <div className="history-facts"><span>Grid <strong>{detail.runs.length}</strong></span><span>Present <strong>{detail.runs.filter(r=>r.status==='present').length}</strong></span><span>Missing / invalid <strong>{detail.excluded.length}</strong></span><span>Workload <strong>{detail.spec.workloads??'Unknown'}</strong></span></div>
 <div className="table-scroll matrix-grid"><table><thead><tr><th>Strategy</th><th>Fleet</th><th>Seed</th><th>Evidence</th><th>Reason</th></tr></thead><tbody>{detail.runs.map(r=><tr key={`${r.strategy}:${r.robots}:${r.seed}`}><td>{r.strategy}</td><td>{r.robots}</td><td>{r.seed}</td><td><span className={`badge ${r.status==='present'?'good':'warn'}`}>{human(r.status)}</span></td><td>{r.reason??'Validated artifact set; process exit tracked separately'}</td></tr>)}</tbody></table></div>
 {!!detail.extra.length&&<p className="notice">Outside-grid run directories are excluded: {detail.extra.join(', ')}</p>}
 <details className="provenance"><summary>Original specification & provenance</summary><pre>{JSON.stringify(detail.manifest,null,2)}</pre>{detail.warnings.map(w=><p key={w}>{w}</p>)}</details>
 </section>
 <section className="panel comparison-panel"><div className="panel-heading"><div><span className="eyebrow">ORIGINAL PAIRED ANALYSIS</span><h2>Strategy − baseline</h2></div></div><div className="comparison-controls"><label>Baseline<select aria-label="Comparison baseline" value={baseline} onChange={e=>setBaseline(e.target.value)}>{names.map(s=><option key={s}>{s}</option>)}</select></label><label className="checkbox"><input type="checkbox" checked={allow} onChange={e=>setAllow(e.target.checked)}/>Explicitly allow incomplete paired samples</label><button className="button primary" disabled={comparing||detail.spec.workloads!=='seeded'||(!detail.complete&&!allow)} onClick={()=>void compare()}>{comparing?'Checking…':'Compare paired runs'}</button></div>
 {detail.spec.workloads!=='seeded'&&<p className="notice">Reference workloads do not support this seeded paired analysis.</p>}
 <p className="inspector-note">Negative order makespan differences mean earlier completion. Throughput has the opposite direction. CI is a paired 95% t interval; n &lt; 2 cannot provide an interval. No rankings are inferred.</p>
 {analysisError&&<p role="alert" className="notice error">Comparison refused: {analysisError}</p>}
 {comparison&&<>{!comparison.complete&&<p className="notice">INCOMPLETE EXPERIMENT: {comparison.present_runs}/{comparison.expected_runs} runs present. Read every actual n against expected n. Missing/invalid runs remain listed above.</p>}<label className="metric-select">Displayed metric<select value={metric} onChange={e=>setMetric(e.target.value)}><option value="">All original paired metrics</option>{[...new Set(comparison.records.map(r=>r.metric))].map(m=><option key={m}>{m}</option>)}</select></label>
 <PerformanceSummary records={records} complete={comparison.complete&&records.every(record=>record.complete)} method={comparison.method} provenanceHref="#comparison-provenance"/><details id="comparison-provenance" className="provenance"><summary>Comparison provenance</summary><pre>{JSON.stringify({experiment:detail.name,baseline,method:comparison.method,present_runs:comparison.present_runs,expected_runs:comparison.expected_runs,warnings:comparison.warnings},null,2)}</pre></details></>}
 </section></>}
 </div></div></div>
}
