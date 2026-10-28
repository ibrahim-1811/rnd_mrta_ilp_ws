import { fireEvent,render,screen,waitFor } from '@testing-library/react'
import { expect,it,vi } from 'vitest'
import { DataInspector } from './DataInspector'
import { ExperimentSetup } from './ExperimentSetup'
const env=(data:unknown,next_cursor:string|null=null)=>new Response(JSON.stringify({api_schema_version:1,data,availability:'available',warnings:[],next_cursor}))
it('inspects separate decision/outcome pages with exact strings and supported filters',async()=>{
 const fetch=vi.fn(async(path:string)=>env(path.includes('/outcomes')?[{decision_id:1,actual_distance:12}]:[{decision_id:1,sim_time_exact:'2/3',static_cost_m:10,selected:true}]))
 vi.stubGlobal('fetch',fetch)
 render(<DataInspector runId="r1" scope="app" />)
 expect(await screen.findByText('2/3')).toBeVisible()
 await fireEvent.click(screen.getByRole('button',{name:'Outcomes'}))
 expect(await screen.findByText('actual_distance')).toBeVisible()
 expect(screen.queryByText('2/3')).not.toBeInTheDocument()
 expect(fetch.mock.calls.some(c=>c[0].includes('/outcomes?scope=app'))).toBe(true)
})
it('matrix preview never launches and uses only real experiment fields',async()=>{
 const calls:{path:string;options?:RequestInit}[]=[]
 vi.stubGlobal('fetch',vi.fn(async(path:string,options?:RequestInit)=>{
  calls.push({path,options})
  if(path.endsWith('/session'))return env({csrf_token:'token'})
  if(path.endsWith('/preview'))return env({preview_id:'a',expires_in_s:600,effective:{expected_runs:2,quantum_s:0},grid:[['nearest_astar',2,1],['random',2,1]],warnings:[],argv:[]})
  return env({id:'job',status:'running'})
 }))
 const launched=vi.fn()
 render(<ExperimentSetup strategies={[{id:'nearest_astar',implementation:'',review_status:''},{id:'random',implementation:'',review_status:''}]} capacity={100} activeJob={false} onLaunched={launched} />)
 expect(calls).toHaveLength(0)
 fireEvent.change(screen.getByLabelText('Experiment name'),{target:{value:'tiny'}})
 fireEvent.click(screen.getByRole('button',{name:'Preview matrix'}))
 expect(await screen.findByRole('dialog')).toBeVisible()
 expect(calls.some(c=>c.path.endsWith('/experiments'))).toBe(false)
 const body=JSON.parse(calls.find(c=>c.path.endsWith('/preview'))!.options!.body as string)
 expect(body.kind).toBe('experiment'); expect(body).not.toHaveProperty('quantum_s');expect(body).not.toHaveProperty('execution_mode')
 fireEvent.click(screen.getByRole('button',{name:'Launch matrix'}))
 await waitFor(()=>expect(launched).toHaveBeenCalledOnce())
})

it('prevents a late decision page from appearing after selecting outcomes',async()=>{
 let resolve!:(response:Response)=>void
 vi.stubGlobal('fetch',vi.fn((path:string)=>path.includes('/decisions')?new Promise<Response>(r=>{resolve=r}):Promise.resolve(env([{actual_waiting_s:7}]))))
 render(<DataInspector runId="r1" scope="app" />)
 fireEvent.click(screen.getByRole('button',{name:'Outcomes'}))
 expect(await screen.findByText('actual_waiting_s')).toBeVisible()
 resolve(env([{future_decision_marker:'wrong'}]))
 await waitFor(()=>expect(screen.queryByText('future_decision_marker')).not.toBeInTheDocument())
})


it('warns when the requested order count exceeds the generated workload',()=>{
 render(<ExperimentSetup strategies={[{id:'nearest_astar',implementation:'',review_status:''}]} capacity={100} activeJob={false} onLaunched={vi.fn()} />)
 fireEvent.change(screen.getByLabelText('Orders per run'),{target:{value:'50'}})
 expect(screen.getByRole('status')).toHaveTextContent('Each run will execute 3 orders')
})
