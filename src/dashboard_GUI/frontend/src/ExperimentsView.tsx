import { useEffect,useRef,useState } from 'react'
import { FlaskConical,RefreshCw } from 'lucide-react'
import { ExperimentSetup } from './ExperimentSetup'
import { read,message } from './api'
import { display,human } from './model'
import type { Job,Scope,Strategy } from './types'

type Experiment={id:string;name:string;directory:string;spec:{strategies?:string[];workloads?:string};expected_runs:number}
type Cell={strategy:string;robots:number;seed:number;status:string;reason:string|null;integrity:string;run_id:string;warnings:string[]}
type Detail=Experiment&{manifest:Record<string,unknown>;complete:boolean;runs:Cell[];excluded:Cell[];extra:string[];warnings:string[]}
type Pair={strategy:string;baseline:string;robots:number;metric:string;n:number;expected_n:number;complete:boolean;baseline_mean:number;mean_difference:number;ci_low:number|null;ci_high:number|null;percent_of_baseline:number|null}
type Comparison={records:Pair[];complete:boolean;excluded:Cell[];extra:string[];method:string;expected_runs:number;present_runs:number;warnings:string[]}
export function ExperimentsView({strategies,capacity,activeJob,onLaunched,disabled}:{strategies:Strategy[];capacity:number;activeJob:boolean;onLaunched:(j:Job)=>void;disabled:boolean}){
 const [scope,setScope]=useState<Scope>('app');const [revision,setRevision]=useState(0)
 const [items,setItems]=useState<Experiment[]>([]);const [selected,setSelected]=useState('')
 const [detail,setDetail]=useState<Detail|null>(null);const [error,setError]=useState('');const [loading,setLoading]=useState(false)
 const [baseline,setBaseline]=useState('nearest_astar');const [allow,setAllow]=useState(false);const [comparison,setComparison]=useState<Comparison|null>(null);const [comparing,setComparing]=useState(false)
 const [metric,setMetric]=useState('order_makespan_s');const [analysisError,setAnalysisError]=useState('')
 const analysis=useRef<AbortController|null>(null)
 useEffect(()=>{
  const c=new AbortController();setLoading(true);setError('');setItems([]);setSelected('');setDetail(null)
  void read<Experiment[]>(`/experiments?scope=${scope}`,c.signal).then(r=>{if(!c.signal.aborted){setItems(r.data);setSelected(r.data[0]?.id??'')}}).catch(e=>{if(!c.signal.aborted)setError(message(e))}).finally(()=>{if(!c.signal.aborted)setLoading(false)})
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
 return <div><div className="section-intro"><div><span className="eyebrow">REPRODUCIBLE STUDIES</span><h1>Experiments & comparisons</h1><p>Run a controlled matrix, inspect its evidence, compare paired workloads.</p></div><button className="button" onClick={()=>setRevision(v=>v+1)}><RefreshCw size={14}/>Refresh studies</button></div>
 <div className="research-grid"><ExperimentSetup strategies={strategies} capacity={capacity} activeJob={activeJob} onLaunched={onLaunched} disabled={disabled}/><div className="study-column">
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
 <div className="table-scroll"><table><thead><tr><th>Strategy</th><th>Fleet</th><th>Metric</th><th>Actual / expected n</th><th>Baseline mean</th><th>Mean difference</th><th>95% CI</th><th>% baseline</th></tr></thead><tbody>{records.map(r=><tr key={`${r.strategy}:${r.robots}:${r.metric}`}><td>{r.strategy}</td><td>{r.robots}</td><td>{r.metric}</td><td>{r.n} / {r.expected_n}{!r.complete?' · partial':''}</td><td>{display(r.baseline_mean,3)}</td><td>{display(r.mean_difference,3)}</td><td>{r.ci_low===null||r.ci_high===null?'Unavailable (n < 2)':`[${display(r.ci_low,3)}, ${display(r.ci_high,3)}]`}</td><td>{display(r.percent_of_baseline,2)}</td></tr>)}</tbody></table></div>{!records.length&&<p className="notice">No eligible paired metric values for this selection; unavailable values are not replaced with zero.</p>}<p className="inspector-note">{comparison.method}</p></>}
 </section></>}
 </div></div></div>
}
