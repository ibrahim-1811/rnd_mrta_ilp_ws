import { useEffect, useState } from 'react'
import { read, session, message } from './api'
import { acceptJob, active, isSnapshot } from './model'
import type { Job, LogPage, Snapshot } from './types'

export function useJob(id: string | null, revision: number) {
  const [job, setJob] = useState<Job | null>(null)
  const [logs, setLogs] = useState('')
  const [error, setError] = useState('')
  const [transport, setTransport] = useState('idle')
  useEffect(() => {
    setJob(null); setLogs(''); setError(''); setTransport(id ? 'connecting' : 'idle')
    if (!id) return
    let closed = false, sequence = 0, cursor = 0, logging = false, refreshing = false, last: Job | null = null
    let poll: ReturnType<typeof setTimeout> | undefined, reconnect: ReturnType<typeof setTimeout> | undefined
    let logTimer: ReturnType<typeof setTimeout> | undefined
    let socket: WebSocket | null = null
    const controller = new AbortController()
    const apply = (next: Job) => {
      if (closed || next.id !== id) return
      last = acceptJob(last, next)
      sequence = Math.max(sequence, last.sequence)
      setJob(last)
    }
    const logPage = async () => {
      if (logging || closed) return
      logging = true
      let remaining = false
      try {
        for (let n = 0; n < 3 && !closed; n++) {
          const result = await read<LogPage>(`/jobs/${encodeURIComponent(id)}/logs?cursor=${cursor}`, controller.signal)
          if (closed) return
          if (result.data.next_cursor <= cursor) break
          cursor = result.data.next_cursor
          remaining = cursor < result.data.size
          setLogs(text => (text + result.data.text).slice(-150000))
          if (cursor >= result.data.size) break
        }
      } catch (e) { if (!closed) setError(message(e)) } finally { logging = false; if (remaining && !closed) logTimer = setTimeout(() => void logPage(), 30) }
    }
    const refresh = async () => {
      if (closed || refreshing) return
      refreshing = true
      clearTimeout(poll)
      try {
        const result = await read<Job>(`/jobs/${encodeURIComponent(id)}/snapshot`, controller.signal)
        if (closed) return
        apply(result.data); setError(''); void logPage()
      } catch (e) { if (!closed) setError(message(e)) }
      refreshing = false
      if (!closed && (!last || active(last))) poll = setTimeout(refresh, 1200)
    }
    const connect = async () => {
      try {
        await session(controller.signal)
        if (closed) return
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
        socket = new WebSocket(`${protocol}//${location.host}/api/v1/ws/jobs/${encodeURIComponent(id)}?after_sequence=${sequence}`)
        socket.onopen = () => { if (!closed) setTransport('streaming') }
        socket.onmessage = event => {
          if (closed) return
          try {
            const data = JSON.parse(event.data)
            if (data.type === 'resync_required') {
              apply(data.snapshot); cursor = 0; setLogs(''); void logPage(); return
            }
            if (data.schema_version !== 1 || data.job_id !== id || !Number.isInteger(data.sequence) || data.sequence <= sequence) return
            sequence = data.sequence
            if (data.type === 'fleet_snapshot' && isSnapshot(data) && last) {
              apply({ ...last, sequence, snapshot: data as Snapshot,
                telemetry: { ...last.telemetry, available: true, availability: 'available', reason: undefined } } as Job)
            } else if (data.type === 'log') void logPage()
            else void refresh()
          } catch { setTransport('polling'); void refresh() }
        }
        socket.onclose = event => {
          if (closed) return
          setTransport(last && !active(last) ? 'recorded' : 'polling')
          if (event.code === 1008) sequence = 0
          if (!last || active(last)) reconnect = setTimeout(connect, 2500)
          void refresh()
        }
        socket.onerror = () => { if (!closed) setTransport('polling') }
      } catch { if (!closed) { setTransport('polling'); reconnect = setTimeout(connect, 4000) } }
    }
    void refresh(); void connect()
    return () => { closed = true; controller.abort(); clearTimeout(poll); clearTimeout(reconnect); clearTimeout(logTimer); socket?.close() }
  }, [id, revision])
  return { job: job?.id === id ? job : null, logs: job?.id === id ? logs : '', error, transport }
}
