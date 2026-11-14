import { useEffect,useRef,useState } from 'react'
import { act,message } from './api'
import { display,human } from './model'
import type { Job,Preview,Strategy } from './types'

export function GazeboSetup({strategies,capacity,activeJob,onLaunched,disabled=false}:{strategies:Strategy[];capacity:number;activeJob:boolean;onLaunched:(job:Job)=>void;disabled?:boolean}) {
 const available=strategies.filter(strategy=>strategy.id!=='ilp')
 const [strategy,setStrategy]=useState(available.some(item=>item.id==='nearest_astar')?'nearest_astar':available[0]?.id??'')
 const [robots,setRobots]=useState('10');const [orders,setOrders]=useState('50');const [window,setWindow]=useState('')
 const [quantum,setQuantum]=useState('');const [speed,setSpeed]=useState('');const [progress,setProgress]=useState('10')
 const [headless,setHeadless]=useState(false);const [verbose,setVerbose]=useState(false)
 const [preview,setPreview]=useState<Preview|null>(null);const [busy,setBusy]=useState(false);const [error,setError]=useState('')
 const dialog=useRef<HTMLDialogElement>(null);const action=useRef(false);const expires=useRef(0)
 useEffect(()=>{if(preview)dialog.current?.showModal()},[preview])
 const whole=(value:string,min:number,max:number)=>{const number=Number(value);if(!value.trim()||!Number.isInteger(number)||number<min||number>max)throw Error(`Expected a whole number from ${min} to ${max}`);return number}
 const optional=(value:string)=>value.trim()?Number(value):undefined
 async function plan(){
  if(action.current||disabled)return;action.current=true;setBusy(true);setError('')
  try{
   const body={kind:'gazebo',scenario_id:'W100',strategy,robots:whole(robots,1,capacity),orders:whole(orders,0,1000000),headless,verbose,progress_interval_s:Number(progress),...(window?{window}:{}),...(quantum?{quantum_s:optional(quantum)}:{}),...(speed?{speed_mps:optional(speed)}:{})}
   const result=await act<Preview>('/jobs/preview',body);expires.current=Date.now()+result.data.expires_in_s*1000;setPreview(result.data)
  }catch(cause){setError(message(cause))}finally{action.current=false;setBusy(false)}
 }
 async function launch(){
  if(!preview||activeJob||disabled||action.current)return
  if(Date.now()>expires.current){setPreview(null);setError('Preview expired. Preview again.');return}
  action.current=true;setBusy(true)
  try{const result=await act<Job>('/gazebo',{preview_id:preview.preview_id});setPreview(null);onLaunched(result.data)}catch(cause){setPreview(null);setError(message(cause))}finally{action.current=false;setBusy(false)}
 }
 return <section className="panel gazebo-setup"><div className="panel-heading"><div><span className="eyebrow">ROS / GAZEBO EXECUTION MIRROR</span><h2>Gazebo session</h2></div></div>
 <form onSubmit={event=>{event.preventDefault();void plan()}} onChange={()=>setPreview(null)}><fieldset disabled={busy||!!preview}>
 <div className="scenario-tile"><div><strong>W100</strong><span>Fixed scientific simulator is the source of truth</span></div><span className="mini-tag">FIXED</span></div>
 <label>Allocation strategy<select aria-label="Gazebo strategy" value={strategy} onChange={event=>setStrategy(event.target.value)}>{available.map(item=><option key={item.id} value={item.id}>{human(item.id)}</option>)}</select></label>
 <div className="field-row"><label>Robots<input aria-label="Gazebo robots" type="number" min="1" max={capacity} value={robots} onChange={event=>setRobots(event.target.value)} required/></label><label>Order limit<input aria-label="Gazebo orders" type="number" min="0" max="1000000" value={orders} onChange={event=>setOrders(event.target.value)} required/></label></div>
 <div className="field-row"><label>Quantum (s)<input type="number" min="0" max="3600" step="any" value={quantum} onChange={event=>setQuantum(event.target.value)} placeholder="Configuration"/></label><label>Window<select value={window} onChange={event=>setWindow(event.target.value)}><option value="">Configuration</option><option value="fleet_size">Fleet size</option><option value="legacy">Legacy</option></select></label></div>
 <div className="field-row"><label>Speed (m/s)<input type="number" min="0.0001" max="100" step="any" value={speed} onChange={event=>setSpeed(event.target.value)} placeholder="Configuration"/></label><label>Progress interval (s)<input type="number" min="0" max="3600" step="any" value={progress} onChange={event=>setProgress(event.target.value)} required/></label></div>
 <label className="checkbox"><input aria-label="Headless Gazebo" type="checkbox" checked={headless} onChange={event=>setHeadless(event.target.checked)}/>Headless Gazebo</label>
 <label className="checkbox"><input type="checkbox" checked={verbose} onChange={event=>setVerbose(event.target.checked)}/>Verbose ROS logs</label>
 <p className="notice">This launches the existing ROS package as a visualization and execution mirror. It does not add a second allocator, planner, or traffic policy.</p>
 </fieldset>{error&&<p className="notice error" role="alert">{error}</p>}<button className="button primary full" type="submit" disabled={busy||disabled}>{busy?'Validating…':'Preview Gazebo'}</button></form>
 {preview&&<dialog ref={dialog} className="preview-dialog" aria-label="Review Gazebo session" onCancel={()=>setPreview(null)}><span className="eyebrow">EXPLICIT EXECUTION REVIEW</span><h2>Launch Gazebo?</h2><div className="preview-facts"><strong>{display(preview.effective.robots,0)} robots</strong><strong>{display(preview.effective.orders,0)} orders</strong><strong>{headless?'Headless':'Gazebo window'}</strong><strong>{human(strategy)}</strong></div>
 <p>The scientific engine remains authoritative. Gazebo mirrors its state through the fixed launch path.</p>{preview.warnings?.map(warning=><p className="notice" key={warning}>{warning}</p>)}<details className="command-details"><summary>ROS command and destination</summary><pre>{JSON.stringify(preview.ros_argv??preview.argv,null,2)}</pre><code>{preview.output_dir}</code></details>
 {activeJob&&<p className="notice">An owned job is active. Let it stop before launching Gazebo.</p>}<div className="dialog-actions"><button className="button" disabled={busy} onClick={()=>setPreview(null)}>Back to setup</button><button className="button primary" disabled={busy||activeJob||disabled} onClick={()=>void launch()}>Launch Gazebo</button></div></dialog>}
 </section>
}
