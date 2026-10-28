import { useRef,useState } from 'react'
import { act,message } from './api'
export type CsvInput={id:string;sha256:string;orders:number;line_items:number;bytes:number}
export function InputPicker({value,onChange,onBusy,disabled}:{value:CsvInput|null;onChange:(v:CsvInput|null)=>void;onBusy:(v:boolean)=>void;disabled:boolean}){
 const [error,setError]=useState('');const [busy,setBusy]=useState(false);const request=useRef(0)
 async function choose(file:File|undefined){
  if(!file)return;const n=++request.current;setError('');setBusy(true);onBusy(true);onChange(null)
  try{if(file.size>2*1024*1024)throw Error('CSV must be at most 2 MiB');const csv_text=await file.text();const response=await act<CsvInput>('/inputs',{csv_text});if(request.current===n)onChange(response.data)}catch(e){if(request.current===n)setError(message(e))}finally{if(request.current===n){setBusy(false);onBusy(false)}}
 }
 return <div className="input-picker"><label>Single-run order source<select aria-label="Single-run order source" disabled={disabled||busy} value={value?'custom':'reference'} onChange={()=>{request.current++;onChange(null);setError('')}}><option value="reference">Configured reference workload</option>{value&&<option value="custom">Validated CSV · {value.orders} orders</option>}</select></label>
 <label className="csv-label">Upload and validate CSV<input aria-label="Upload order CSV" type="file" accept=".csv,text/csv" disabled={disabled||busy} onChange={e=>{const file=e.target.files?.[0];e.target.value='';void choose(file)}}/></label>
 <p className="field-hint">Exact columns: order_id, item_id, shelf_id, quantity, dropoff_id. Up to 2 MiB / 50,000 line items. Validation checks W100 stations.</p>
 {busy&&<p role="status" className="field-hint">Validating and preserving CSV…</p>}{error&&<p className="notice error" role="alert">{error}</p>}{value&&<p className="input-proof">{value.orders} logical orders · {value.line_items} line items<br/>SHA256 <code title={value.sha256}>{value.sha256.slice(0,16)}…</code><br/>Content-addressed copy; reference file unchanged.</p>}</div>
}
