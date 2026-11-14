import { useEffect, useRef, useState } from 'react'
import { read, message } from './api'
import { isSnapshot } from './model'
import type { Job, Snapshot, TracePage } from './types'

export function useRecording(job: Job | null) {
  const [frames, setFrames] = useState<Snapshot[]>([])
  const [index, setIndex] = useState(0)
  const [mode, setMode] = useState<'latest' | 'recording'>('latest')
  const [playing, setPlaying] = useState(false)
  const [fps, setFps] = useState(8)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [next, setNext] = useState<number | null>(null)
  const request = useRef<AbortController | null>(null)
  const current = useRef<string | null>(null)
  useEffect(() => {
    current.current = job?.id ?? null
    request.current?.abort()
    setFrames([]); setIndex(0); setMode('latest'); setPlaying(false); setError(''); setLoading(false); setNext(null)
    return () => { request.current?.abort() }
  }, [job?.id])
  useEffect(() => {
    if (!playing || mode !== 'recording') return
    if (index >= frames.length - 1) { setPlaying(false); return }
    const timer = setTimeout(() => setIndex(i => i + 1), 1000 / fps)
    return () => clearTimeout(timer)
  }, [playing, index, frames.length, fps, mode])
  async function load(start = 0) {
    if (!job || loading) return
    const id = job.id
    const controller = new AbortController()
    request.current?.abort(); request.current = controller
    setLoading(true); setPlaying(false); setError('')
    try {
      let cursor = start, bytes = 0, more = true
      const collected: Snapshot[] = []
      let generation: string | null = null
      while (more && collected.length < 500 && bytes < 8 * 1024 * 1024) {
        const page = (await read<TracePage | null>(`/jobs/${encodeURIComponent(id)}/telemetry?cursor=${cursor}&limit=${Math.min(100, 500 - collected.length)}`, controller.signal)).data
        if (current.current !== id || controller.signal.aborted) return
        if (!page) throw new Error('This job has no recorded fleet observations.')
        for (const record of page.records) {
          if (record && typeof record === 'object' && (record as { type?: string }).type === 'fleet_snapshot') {
            if (!isSnapshot(record) || record.job_id !== id) throw new Error('Unsupported or mismatched observation sample.')
            if (generation && record.stream_generation !== generation) throw new Error('Observation generation changed. Reload the recording.')
            if (collected.length && record.sample_sequence <= collected[collected.length - 1].sample_sequence) throw new Error('Recording sample order is invalid.')
            generation = record.stream_generation; collected.push(record)
          }
        }
        bytes += page.next_cursor - cursor
        more = page.has_more
        if (page.next_cursor <= cursor) { more = false; break }
        cursor = page.next_cursor
        if (page.trailing_partial) { more = false; break }
      }
      if (!collected.length) { setNext(null); throw new Error('No further fleet samples are available in this recording.') }
      setFrames(collected); setIndex(0); setNext(more ? cursor : null); setMode('recording'); setPlaying(true)
    } catch (e) { if (!controller.signal.aborted) setError(message(e)) }
    finally { if (current.current === id) setLoading(false) }
  }
  const matches = !!job && (frames.length === 0 || frames[0].job_id === job.id)
  return { frames: matches ? frames : [], index: matches ? index : 0, mode: matches ? mode : 'latest', playing: matches && playing, fps, loading: matches && loading, error: matches ? error : '', next: matches ? next : null, load,
    frame: mode === 'recording' && matches ? frames[index] ?? null : job?.telemetry?.available ? job.snapshot ?? null : null,
    latest: () => { setMode('latest'); setPlaying(false) },
    toggle: () => { setMode('recording'); if (!playing && index >= frames.length - 1) setIndex(0); setPlaying(p => !p) },
    seek: (i: number) => { setIndex(i); setPlaying(false); setMode('recording') }, setFps }
}
