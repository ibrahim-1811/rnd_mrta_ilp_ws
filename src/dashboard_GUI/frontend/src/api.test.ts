import { expect, it, vi } from 'vitest'
import { act, read } from './api'

it('acquires a same-origin session and sends exactly one explicit action with the token', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ api_schema_version: 1, data: { csrf_token: 'session' } })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ api_schema_version: 1, data: { id: 'job' } })))
  vi.stubGlobal('fetch', fetch)
  await act('/runs', { preview_id: 'preview' })
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(fetch.mock.calls[0][0]).toBe('/api/v1/session')
  const options = fetch.mock.calls[1][1]
  expect(options.method).toBe('POST')
  expect(options.credentials).toBe('same-origin')
  expect(options.headers['X-Control-Center-Token']).toBe('session')
  expect(JSON.parse(options.body)).toEqual({ preview_id: 'preview' })
})

it('shows backend validation errors and rejects unsupported API versions', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: [{ msg: 'Fleet exceeds map capacity' }] }), { status: 422 })))
  await expect(read('/jobs')).rejects.toThrow('Fleet exceeds map capacity')
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ api_schema_version: 999, data: [] }))))
  await expect(read('/jobs')).rejects.toThrow('Unsupported API')
})
