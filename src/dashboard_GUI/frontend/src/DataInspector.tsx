import { useEffect,useState } from 'react'
import { read,message } from './api'
import type { Envelope,Scope } from './types'

type Row=Record<string,unknown>
export function DataInspector({runId,scope}:{runId:string;scope:Scope}) {
 const [kind,setKind]=useState<'decisions'|'outcomes'>('decisions')
 const [filters,setFilters]=useState({decision_id:'',order_id:'',robot_id:'',selected:''})
 const [query,setQuery]=useState('');const [cursor,setCursor]=useState('')
 const [pages,setPages]=useState<string[]>([])
 const [result,setResult]=useState<{key:string;value:Envelope<Row[]>}|null>(null)
 const [loading,setLoading]=useState(false);const [error,setError]=useState('')
 const key=`${scope}:${runId}:${kind}:${query}:${cursor}`
 useEffect(()=>{
  const controller=new AbortController();setLoading(true);setError('')
  void read<Row[]>(`/runs/${encodeURIComponent(runId)}/${kind}?scope=${scope}&limit=50${query}${cursor?'&cursor='+encodeURIComponent(cursor):''}`,controller.signal).then(value=>{
   if(!controller.signal.aborted)setResult({key,value})
  }).catch(e=>{if(!controller.signal.aborted)setError(message(e))}).finally(()=>{if(!controller.signal.aborted)setLoading(false)})
  return()=>controller.abort()
 },[key])
 const data=result?.key===key?result.value:null
 const columns=[...new Set((data?.data??[]).flatMap(r=>Object.keys(r)))]
 const value=(v:unknown)=>v===null||v===undefined?'—':typeof v==='object'?JSON.stringify(v):String(v)
 return <section className="data-inspector"><div className="panel-heading"><div><span className="eyebrow">SCIENTIFIC RECORDS</span><h2>Decisions & outcomes</h2></div><div className="segmented">{(['decisions','outcomes'] as const).map(k=><button key={k} aria-pressed={kind===k} onClick={()=>{setKind(k);setQuery('');setFilters({decision_id:'',order_id:'',robot_id:'',selected:''});setCursor('');setPages([])}}>{k==='decisions'?'Decisions':'Outcomes'}</button>)}</div></div>
 <p className="inspector-note">{kind==='decisions'?'Decision-time candidates and predicted features.':'Realized execution outcomes after allocation.'} Values are original records, kept separate to prevent future-outcome leakage.</p>
 <form className="inspector-filters" onSubmit={e=>{e.preventDefault();const params=new URLSearchParams();Object.entries(filters).forEach(([k,v])=>{if(v&&(kind==='decisions'||!['robot_id','selected'].includes(k)))params.set(k,v)});setQuery(params.size?'&'+params.toString():'');setCursor('');setPages([])}}>
 <label>Decision ID<input type="number" min="0" step="1" value={filters.decision_id} onChange={e=>setFilters(f=>({...f,decision_id:e.target.value}))}/></label><label>Order ID<input maxLength={256} value={filters.order_id} onChange={e=>setFilters(f=>({...f,order_id:e.target.value}))}/></label>
 {kind==='decisions'&&<><label>Robot ID<input maxLength={256} value={filters.robot_id} onChange={e=>setFilters(f=>({...f,robot_id:e.target.value}))}/></label><label>Selection<select value={filters.selected} onChange={e=>setFilters(f=>({...f,selected:e.target.value}))}><option value="">All candidates</option><option value="true">Selected</option><option value="false">Not selected</option></select></label></>}
 <button className="button" type="submit">Apply filters</button><button className="button" type="button" onClick={()=>{setFilters({decision_id:'',order_id:'',robot_id:'',selected:''});setQuery('');setCursor('');setPages([])}}>Clear</button></form>
 {error&&<div role="alert" className="notice error">{error} Clear filters or reopen the run if files changed.</div>}
 {data?.availability!=='available'&&data&&<p className="notice">Records {data.availability}. {data.warnings.join(' · ')}</p>}
 <div className="table-scroll data-table" tabIndex={0} aria-label={`${kind} records`}><table><thead><tr>{columns.map(k=><th key={k}>{k}</th>)}</tr></thead><tbody>{data?.data?.map((row,i)=><tr key={i}>{columns.map(k=><td key={k}>{value(row[k])}</td>)}</tr>)}</tbody></table></div>
 {!loading&&!data?.data?.length&&<p className="inspector-note">No matching readable records.</p>}
 <div className="table-footer page-controls"><span>{loading?'Reading records…':`${data?.data?.length??0} rows · page ${pages.length+1} · physical file order`}</span><button className="button" disabled={loading||!pages.length} onClick={()=>{setCursor(pages[pages.length-1]);setPages(p=>p.slice(0,-1))}}>Previous page</button><button className="button" disabled={loading||!data?.next_cursor} onClick={()=>{setPages(p=>[...p,cursor]);setCursor(data!.next_cursor!)}}>Next page</button></div>
 </section>
}
