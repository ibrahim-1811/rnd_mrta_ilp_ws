import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useRecording } from './useRecording'
import { useJob } from './useJob'
import { read, session } from './api'
import type { Job, Snapshot } from './types'
vi.mock('./api', () => ({ read: vi.fn(), session: vi.fn(), message: (e: unknown) => String(e) }))
const envelope = (data: unknown) => ({data, availability:'available', api_schema_version:1, warnings:[], next_cursor:null})
const sample = (id = 'a', seq = 1, generation = 'g'): Snapshot => ({ schema_version:1, type:'fleet_snapshot', job_id:id, source:'executor_observer', stream_generation:generation, sample_sequence:seq, observed_at_utc:'2026-10-28T00:00:00Z', sim_time_exact:String(seq),sim_time_s:seq,payload:{ sample_mode:'sampled',dropped_samples_total:0,robots:[],orders:{total:3,pending:3,assigned:0,in_progress:0,completed:0,failed:0,deferred_now:0,cumulative_deferrals:0},resources:[] } })
const job = (id = 'a') => ({id,sequence:1,status:'completed', owned_process_alive:false, snapshot:sample(id), telemetry:{ available:true, availability:'available' }} as Job)
const page = (records: unknown[], more=false,cursor=100) => envelope({records,has_more:more,next_cursor:cursor,trailing_partial:false,size:cursor})
describe('recorded observations', () => {
 it('plays only captured samples and clears a previous job when selection changes', async () => {
  vi.mocked(read).mockResolvedValue(page([sample(),sample('a',4)] ) as never)
  const {result,rerender} = renderHook(({j}) => useRecording(j),{initialProps:{j:job()}})
  await act(async () => {await result.current.load()})
  expect(result.current.frames.map(s => s.sample_sequence)).toEqual([1,4])
  act(() => result.current.seek(1))
  expect(result.current.frame?.sim_time_exact).toBe('4')
  act(() => result.current.toggle())
  expect(result.current.index).toBe(0)
  expect(result.current.playing).toBe(true)
  rerender({j:job('b')})
  expect(result.current.frames).toEqual([])
  expect(result.current.frame?.job_id).toBe('b')
 })
 it('rejects out-of-order or mixed-generation samples', async () => {
  vi.mocked(read).mockResolvedValue(page([sample(),sample('a',2,'other')]) as never)
  const {result}=renderHook(()=>useRecording(job()))
  await act(async()=>{await result.current.load()})
  expect(result.current.error).toContain('generation changed')
  expect(result.current.frames).toEqual([])
 })
 it('ignores a late page from a previously selected job', async () => {
  let resolve!: (v: never)=>void
  vi.mocked(read).mockReturnValue(new Promise(r=>{resolve=r}))
  const {result,rerender}=renderHook(({j})=>useRecording(j),{initialProps:{j:job()}})
  let pending!: Promise<void>
  act(()=>{pending=result.current.load()})
  rerender({j:job('b')})
  await act(async()=>{resolve(page([sample()]) as never);await pending})
  expect(result.current.frames).toEqual([])
  expect(result.current.frame?.job_id).toBe('b')
 })
 it('limits each loaded segment to 500 samples', async () => {
  vi.mocked(read).mockImplementation(async route => {
    const cursor=Number(new URL(`http://localhost${route}`).searchParams.get('cursor'))
    return page(Array.from({length:100},(_,i)=>sample('a',cursor+i+1)),true,cursor+100) as never
  })
  const before=vi.mocked(read).mock.calls.length
  const {result}=renderHook(()=>useRecording(job()))
  await act(async()=>{await result.current.load()})
  expect(result.current.frames).toHaveLength(500)
  expect(vi.mocked(read).mock.calls.length-before).toBe(5)
  expect(result.current.next).toBe(500)
 })
})
describe('selected job requests',()=>{
 it('continues bounded log pages until a completed log reaches its end',async()=>{
  class Socket { close=vi.fn() }
  vi.stubGlobal('WebSocket',Socket)
  vi.mocked(session).mockResolvedValue('' as never)
  let pages=0
  vi.mocked(read).mockImplementation(async route=>{
   if(route.includes('/snapshot')) return envelope(job()) as never
   const cursor=Number(new URL(`http://localhost${route}`).searchParams.get('cursor'))
   pages++
   return envelope({text: cursor < 400000 ? 'line\n' : 'FINAL RESULT',next_cursor:Math.min(cursor+65536,460000),size:460000,complete:true}) as never
  })
  const {result}=renderHook(()=>useJob('a',0))
  await waitFor(()=>expect(result.current.logs).toContain('FINAL RESULT'))
  expect(pages).toBeGreaterThan(6)
 })

 it('ignores a stale snapshot response and closes the old socket',async()=>{
  const sockets: { close: ReturnType<typeof vi.fn> }[]=[]
  class Socket { close=vi.fn(); constructor(){sockets.push(this)} }
  vi.stubGlobal('WebSocket',Socket)
  vi.mocked(session).mockResolvedValue('' as never)
  let old!: (v: never)=>void
  vi.mocked(read).mockImplementation(async route=>{
   if(route.includes('/a/snapshot')) return new Promise(r=>{old=r})
   if(route.includes('/b/snapshot')) return envelope(job('b')) as never
   return envelope({text:'b logs',next_cursor:0,size:0,complete:true}) as never
  })
  const {result,rerender}=renderHook(({id})=>useJob(id,0),{initialProps:{id:'a'}})
  await waitFor(()=>expect(sockets).toHaveLength(1))
  rerender({id:'b'})
  await waitFor(()=>expect(result.current.job?.id).toBe('b'))
  await act(async()=>{old(envelope(job('a')) as never)})
  expect(result.current.job?.id).toBe('b')
  expect(sockets[0].close).toHaveBeenCalledOnce()
 })
})
