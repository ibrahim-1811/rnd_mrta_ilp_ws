import { fireEvent,render,screen,waitFor } from '@testing-library/react'
import { expect,it,vi } from 'vitest'
import { InputPicker } from './InputPicker'
it('only uploads on explicit file choice and returns the validated immutable identity',async()=>{
 const fetch=vi.fn(async(path:string,_options?:RequestInit)=>new Response(JSON.stringify({api_schema_version:1,availability:'available',warnings:[],data:path.endsWith('/session')?{csrf_token:'token'}:{id:'hash',sha256:'hash',orders:1,line_items:1,bytes:50}})))
 vi.stubGlobal('fetch',fetch);const change=vi.fn();const busy=vi.fn()
 render(<InputPicker value={null} onChange={change} onBusy={busy} disabled={false} scenarioId="second"/>)
 expect(fetch).not.toHaveBeenCalled()
 const file=new File(['content'],'orders.csv',{type:'text/csv'});Object.defineProperty(file,'text',{value:async()=> 'content'})
 fireEvent.change(screen.getByLabelText('Upload order CSV'),{target:{files:[file]}})
 await waitFor(()=>expect(change).toHaveBeenCalledWith(expect.objectContaining({id:'hash',orders:1})))
 expect(fetch.mock.calls.filter(c=>c[0].endsWith('/inputs'))).toHaveLength(1)
 const request=fetch.mock.calls.find(c=>c[0].endsWith('/inputs'))![1] as RequestInit
 expect(JSON.parse(request.body as string)).toEqual({csv_text:'content',scenario_id:'second'})
 expect(busy).toHaveBeenLastCalledWith(false)
})
it('rejects oversized input before reading or transmitting it',async()=>{
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch)
 render(<InputPicker value={null} onChange={vi.fn()} onBusy={vi.fn()} disabled={false} scenarioId="W100"/>)
 const file=new File(['x'],'large.csv');Object.defineProperty(file,'size',{value:2*1024*1024+1})
 fireEvent.change(screen.getByLabelText('Upload order CSV'),{target:{files:[file]}})
 expect(await screen.findByRole('alert')).toHaveTextContent('2 MiB');expect(fetch).not.toHaveBeenCalled()
})
