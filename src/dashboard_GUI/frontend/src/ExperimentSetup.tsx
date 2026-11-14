import { useEffect,useRef,useState } from 'react'
import { act,message } from './api'
import { display,human } from './model'
import type { Job,Preview,Strategy } from './types'

type MatrixPreview=Preview&{grid:[string,number,number][]}
export function ExperimentSetup({strategies,capacity,activeJob,onLaunched,disabled=false}:{strategies:Strategy[];capacity:number;activeJob:boolean;onLaunched:(job:Job)=>void;disabled?:boolean}) {
 const [name,setName]=useState(()=>`study_${Date.now()}`);const [chosen,setChosen]=useState(['nearest_astar','random'])
 const [robots,setRobots]=useState('2');const [seeds,setSeeds]=useState('1, 2');const [orders,setOrders]=useState('3')
 const [workloadOrders,setWorkloadOrders]=useState('3');const [jobs,setJobs]=useState('1')
 const [workloads,setWorkloads]=useState('seeded');const [mode,setMode]=useState('new');const [force,setForce]=useState(false);const [exportCsv,setExport]=useState(false)
 const [purpose,setPurpose]=useState('');const [quantum,setQuantum]=useState('');const [window,setWindow]=useState('')
 const [features,setFeatures]=useState(false);const [teacherPanel,setTeacherPanel]=useState(false)
 const [preview,setPreview]=useState<MatrixPreview|null>(null);const [busy,setBusy]=useState(false);const [error,setError]=useState('')
 const dialog=useRef<HTMLDialogElement>(null);const action=useRef(false);const expires=useRef(0)
 useEffect(()=>{if(preview)dialog.current?.showModal()},[preview])
 const integer=(text:string,max:number,min=0)=>{const n=Number(text);if(!text.trim()||!Number.isInteger(n)||n<min||n>max)throw Error(`Expected whole number from ${min} to ${max}`);return n}
 const numbers=(text:string,max:number,min:number)=>{const values=text.trim().split(/[\s,]+/).map(n=>integer(n,max,min));if(new Set(values).size!==values.length)throw Error('Remove duplicate fleet sizes or seeds');return values}
 async function plan(){
  if(action.current||disabled)return;action.current=true;setBusy(true);setError('')
  try{
   if(!chosen.length)throw Error('Select at least one strategy')
   if(mode==='force'&&!force)throw Error('Acknowledge the force replacement before previewing')
   const body={kind:'experiment',name,strategies:chosen,robots:numbers(robots,capacity,1),seeds:numbers(seeds,2**32-1,0),orders:integer(orders,1000000),jobs:integer(jobs,4,1),workloads,workload_orders:integer(workloadOrders,1000000,1),export:exportCsv,mode,features,teacher_panel:teacherPanel,...(purpose?{purpose}:{}),...(quantum?{quantum_s:Number(quantum)}:{}),...(window?{window}: {})}
   const result=await act<MatrixPreview>('/jobs/preview',body);expires.current=Date.now()+result.data.expires_in_s*1000;setPreview(result.data)
  }catch(e){setError(message(e))}finally{action.current=false;setBusy(false)}
 }
 async function launch(){
  if(!preview||activeJob||disabled||action.current)return
  if(Date.now()>expires.current){setPreview(null);setError('Preview expired. Preview again.');return}
  action.current=true;setBusy(true)
  try{const result=await act<Job>('/experiments',{preview_id:preview.preview_id});setPreview(null);onLaunched(result.data)}catch(e){setPreview(null);setError(message(e))}finally{action.current=false;setBusy(false)}
 }
 return <section className="panel matrix-setup"><div className="panel-heading"><div><span className="eyebrow">ORIGINAL EXPERIMENT RUNNER</span><h2>Experiment matrix</h2></div></div>
 <form onSubmit={e=>{e.preventDefault();void plan()}} onChange={()=>setPreview(null)}><fieldset disabled={busy||!!preview}><label>Experiment name<input aria-label="Experiment name" required pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,63}" maxLength={64} value={name} onChange={e=>setName(e.target.value)}/></label>
 <div className="field-label">Registered strategies</div><div className="strategy-checks">{strategies.filter(s=>s.id!=='ilp').map(s=><label className="checkbox" key={s.id}><input type="checkbox" checked={chosen.includes(s.id)} onChange={e=>setChosen(v=>e.target.checked?[...v,s.id]:v.filter(x=>x!==s.id))}/>{human(s.id)}</label>)}</div>
 <div className="field-row"><label>Fleet sizes<input aria-label="Fleet sizes" value={robots} onChange={e=>setRobots(e.target.value)} required/></label><label>Seeds<input aria-label="Seeds" value={seeds} onChange={e=>setSeeds(e.target.value)} required/></label></div><p className="field-hint">Comma or space separated. Fleet capacity {capacity}. The preview expands the actual runner grid.</p>
 <div className="field-row"><label>Orders per run<input type="number" min="0" max="1000000" value={orders} onChange={e=>setOrders(e.target.value)} required/></label><label>Parallel children<input type="number" min="1" max="4" value={jobs} onChange={e=>setJobs(e.target.value)} required/></label></div>
 <label>Workload source<select value={workloads} onChange={e=>setWorkloads(e.target.value)}><option value="seeded">Seeded · paired workloads</option><option value="reference">Reference · original seed rules</option></select></label>
 {workloads==='seeded'&&Number(orders)>Number(workloadOrders)&&<p className="notice" role="status">Requested {orders} orders, but each generated workload contains only {workloadOrders}. Each run will execute {workloadOrders} orders. Increase Generated workload orders for a larger workload.</p>}
 <label>Generated workload orders<input type="number" min="1" max="1000000" value={workloadOrders} onChange={e=>setWorkloadOrders(e.target.value)} disabled={workloads!=='seeded'} required/></label>
 <label>Purpose<select aria-label="Experiment purpose" value={purpose} onChange={e=>setPurpose(e.target.value)}><option value="">Development default</option>{['training','induction','exploratory','validation','backup','evaluation','final'].map(value=><option value={value} key={value}>{human(value)}</option>)}</select></label>
 <div className="field-row"><label>Quantum override (s)<input aria-label="Experiment quantum" type="number" min="0" max="3600" step="any" value={quantum} onChange={e=>setQuantum(e.target.value)}/></label><label>Window override<select aria-label="Experiment window" value={window} onChange={e=>setWindow(e.target.value)}><option value="">Configuration default</option><option value="fleet_size">Fleet size</option><option value="legacy">Legacy</option></select></label></div>
 <label className="checkbox"><input type="checkbox" checked={features} onChange={e=>setFeatures(e.target.checked)}/>Record congestion features</label>
 <label className="checkbox"><input type="checkbox" checked={teacherPanel} onChange={e=>setTeacherPanel(e.target.checked)}/>Record teacher panel</label>
 <label>Execution intent<select value={mode} onChange={e=>{setMode(e.target.value);setForce(false)}}><option value="new">New experiment</option><option value="resume">Resume missing / failed runs</option><option value="force">Force · archive old directory and rerun</option></select></label>
 {mode==='force'&&<label className="checkbox force-note"><input type="checkbox" checked={force} onChange={e=>setForce(e.target.checked)}/>I intend to replace this app-owned experiment; the original runner archives its old directory.</label>}
 <label className="checkbox"><input type="checkbox" checked={exportCsv} onChange={e=>setExport(e.target.checked)}/>Export CSV / Prolog</label>
 <div className="notice">Optional quantum and window values are passed to every child through the original experiment runner. Feature and teacher records remain decision-time evidence; future outcomes are not added to decisions.</div>
 </fieldset>{error&&<p className="notice error" role="alert">{error}</p>}<button className="button primary full" type="submit" disabled={busy||disabled}>{busy?'Validating…':'Preview matrix'}</button></form>
 {preview&&<dialog ref={dialog} className="preview-dialog" aria-label="Review experiment matrix" onCancel={()=>setPreview(null)}><span className="eyebrow">EXPLICIT EXECUTION REVIEW</span><h2>Launch experiment matrix?</h2><div className="preview-facts"><strong>{display(preview.effective.expected_runs,0)} runs</strong><strong>{mode} intent</strong><strong>Quantum {display(preview.effective.quantum_s)} s</strong><strong>{display(preview.effective.orders_per_run,0)} effective orders / run</strong></div>
 <div className="table-scroll matrix-grid"><table><thead><tr><th>Strategy</th><th>Robots</th><th>Seed</th></tr></thead><tbody>{preview.grid?.map(([s,r,seed])=><tr key={`${s}:${r}:${seed}`}><td>{s}</td><td>{r}</td><td>{seed}</td></tr>)}</tbody></table></div>
 <p className="inspector-note">This starts the expanded runs using the existing scientific runner. Large studies can take significant time.</p>
 {preview.warnings?.map(w=><p className="notice" key={w}>{w}</p>)}<details className="command-details"><summary>Command and destination</summary><pre>{JSON.stringify(preview.argv,null,2)}</pre><code>{preview.output_dir}</code></details>
 {activeJob&&<p className="notice">An owned job is active. Let it stop before launching.</p>}<div className="dialog-actions"><button className="button" disabled={busy} onClick={()=>setPreview(null)}>Back to setup</button><button className="button primary" disabled={busy||activeJob||disabled} onClick={()=>void launch()}>Launch matrix</button></div></dialog>}
 </section>
}
