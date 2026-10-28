import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { RunSetup } from './RunSetup'
import type { Job, Scenario, Strategy } from './types'

const scenario = { id: 'W100', capacity: 100, settings: {} } as Scenario
const strategies = [{ id: 'nearest_astar', implementation: 'NearestAstar', review_status: 'Registered' }] as Strategy[]
const envelope = (data: unknown) => new Response(JSON.stringify({ api_schema_version: 1, data, availability: 'available', warnings: [] }))

it('never launches on mount or preview; requires deliberate launch of the returned preview', async () => {
  const calls: { path: string; options?: RequestInit }[] = []
  vi.stubGlobal('fetch', vi.fn(async (path: string, options?: RequestInit) => {
    calls.push({ path, options })
    if (path.endsWith('/session')) return envelope({ csrf_token: 'token' })
    if (path.endsWith('/jobs/preview')) return envelope({ preview_id: 'p1', expires_in_s: 600, argv: ['python', 'runner'], warnings: [], effective: { robots: 2, orders: 3, seed: 7 } })
    if (path.endsWith('/runs')) return envelope({ id: 'job1', status: 'running' })
    throw new Error('Unexpected request')
  }))
  const launched = vi.fn()
  render(<RunSetup scenario={scenario} strategies={strategies} canObserve={true} activeJob={false} onLaunched={launched} onRefresh={() => {}} />)
  expect(calls).toHaveLength(0)
  await userEvent.click(screen.getByRole('button', { name: 'Preview run' }))
  expect(await screen.findByRole('dialog')).toBeVisible()
  expect(calls.some(c => c.path.endsWith('/runs'))).toBe(false)
  expect(screen.getByText('3 orders')).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Launch run' }))
  await waitFor(() => expect(launched).toHaveBeenCalledWith(expect.objectContaining({ id: 'job1' })))
  expect(calls.filter(c => c.path.endsWith('/runs'))).toHaveLength(1)
  expect(JSON.parse(calls.find(c => c.path.endsWith('/runs'))!.options!.body as string)).toEqual({ preview_id: 'p1' })
})

it('invalidates a completed preview after editing inputs and blocks launch with an active job', async () => {
  vi.stubGlobal('fetch', vi.fn(async (path: string) => path.endsWith('/session') ? envelope({ csrf_token: 'token' }) : envelope({ preview_id: 'p', expires_in_s: 600, argv: [], warnings: [], effective: { robots: 2, orders: 3 } })))
  render(<RunSetup scenario={scenario} strategies={strategies} canObserve={true} activeJob={true} onLaunched={(_j: Job) => {}} onRefresh={() => {}} />)
  await userEvent.click(screen.getByRole('button', { name: 'Preview run' }))
  expect(await screen.findByRole('button', { name: 'Launch run' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: 'Back to setup' }))
  fireEvent.change(screen.getByLabelText('Robots'), { target: { value: '3' } })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Launch run' })).not.toBeInTheDocument()
})
