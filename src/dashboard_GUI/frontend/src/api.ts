import type { Envelope } from './types'

async function request<T>(route: string, options: RequestInit = {}): Promise<Envelope<T>> {
  const response = await fetch('/api/v1' + route, { credentials: 'same-origin', ...options })
  let body: Record<string, unknown>
  try { body = await response.json() } catch { throw new Error(`Backend returned ${response.status}. Check the local service on port 8000.`) }
  if (!response.ok) {
    const detail = body.detail
    const message = Array.isArray(detail) ? detail.map(e => e.msg ?? JSON.stringify(e)).join('; ') : String(detail ?? response.statusText)
    throw new Error(message)
  }
  if (body.api_schema_version !== 1) throw new Error('Unsupported API version. Restart matching frontend and backend versions.')
  return body as unknown as Envelope<T>
}
export function read<T>(route: string, signal?: AbortSignal) { return request<T>(route, { signal }) }
export async function session(signal?: AbortSignal) { return read<{ csrf_token: string }>('/session', signal) }
export async function act<T>(route: string, body: unknown): Promise<Envelope<T>> {
  const token = (await session()).data.csrf_token
  return request<T>(route, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Control-Center-Token': token }, body: JSON.stringify(body) })
}
export const message = (error: unknown) => error instanceof Error ? error.message : String(error)
