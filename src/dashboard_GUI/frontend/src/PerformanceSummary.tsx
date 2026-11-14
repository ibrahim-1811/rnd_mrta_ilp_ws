import { ArrowDown,ArrowUp,ExternalLink } from 'lucide-react'
import { display,human } from './model'

export type PerformanceRecord={strategy:string;baseline:string;robots:number;metric:string;n:number;expected_n:number;complete:boolean;baseline_mean:number;mean_difference:number;ci_low:number|null;ci_high:number|null;percent_of_baseline:number|null}

const details:Record<string,{unit:string;direction:'higher'|'lower'}>={
 throughput_orders_per_s:{unit:'orders/s',direction:'higher'},
 order_makespan_s:{unit:'s',direction:'lower'},makespan_s:{unit:'s',direction:'lower'},
 total_distance_m:{unit:'m',direction:'lower'},total_waiting_s:{unit:'s',direction:'lower'},
 total_energy_percent:{unit:'%',direction:'lower'},total_energy_wh:{unit:'Wh',direction:'lower'},
 charging_time_s:{unit:'s',direction:'lower'},
}

export function PerformanceSummary({records,complete,method,provenanceHref}:{records:PerformanceRecord[];complete:boolean;method:string;provenanceHref:string}){
 if(!records.length)return <p className="notice">No eligible paired metric values for this selection; unavailable values are not replaced with zero.</p>
 return <div className="performance-summary">
  {!complete&&<p className="notice" role="status">INCOMPLETE PAIRED SAMPLE: actual / expected n varies by row. {records.map(record=>`${record.strategy} ${record.metric}: ${record.n} / ${record.expected_n}`).join(' · ')}</p>}
  <div className="performance-cards">{records.map(record=>{const detail=details[record.metric]??{unit:'',direction:'lower' as const};const width=record.percent_of_baseline===null?0:Math.min(100,Math.abs(record.percent_of_baseline));return <article className="performance-card" key={`${record.strategy}:${record.robots}:${record.metric}`}>
   <div className="performance-card-head"><div><span>{human(record.metric)}</span><strong>{human(record.strategy)} · {record.robots} robots</strong></div><span className="direction">{detail.direction==='higher'?<ArrowUp size={12}/>:<ArrowDown size={12}/>} {detail.direction==='higher'?'Higher is better':'Lower is better'}</span></div>
   <div className="performance-delta"><strong>{display(record.mean_difference,3)} <small>{detail.unit}</small></strong><span>strategy − {human(record.baseline)}</span></div>
   <div className="performance-track" aria-label={`${display(record.percent_of_baseline,2)} percent of baseline difference`}><i style={{width:`${width}%`}}/></div>
   <dl><dt>Actual / expected n</dt><dd>{record.n} / {record.expected_n}{!record.complete?' · partial':''}</dd><dt>Baseline mean</dt><dd>{display(record.baseline_mean,3)} {detail.unit}</dd><dt>95% CI</dt><dd>{record.ci_low===null||record.ci_high===null?'Unavailable (n < 2)':`[${display(record.ci_low,3)}, ${display(record.ci_high,3)}] ${detail.unit}`}</dd><dt>% baseline</dt><dd>{display(record.percent_of_baseline,2)}{record.percent_of_baseline===null?'':'%'}</dd></dl>
  </article>})}</div>
  <div className="performance-method"><span>{method} No winner or ranking is inferred.</span><a className="text-button" href={provenanceHref}>Provenance and method <ExternalLink size={12}/></a></div>
 </div>
}
