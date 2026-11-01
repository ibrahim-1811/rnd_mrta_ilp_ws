import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { RunSetup } from './RunSetup'
import type { Job, Scenario, Strategy } from './types'

const scenario = { id: 'W100', label: 'Scientific W100', reference: true, valid: true, width: 40, height: 54,
  capacity: 100, provenance: 'test fixture', settings: {}, hashes: {}, identity: 'a'.repeat(64),
  validation: { errors: [], warnings: [] } } as Scenario
const second = { ...scenario, id: 'second', label: 'Compact test hall', reference: false, width: 12, height: 20,
  capacity: 8, identity: 'b'.repeat(64) } as Scenario
const invalid = { ...scenario, id: 'invalid', label: 'Invalid map', reference: false, valid: false, width: null,
  height: null, capacity: null, identity: null, validation: { errors: ['missing station: no parking'], warnings: [] } } as Scenario
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

it('selects a valid scenario, sends its ID, shows map facts, and clears stale preview and CSV input', async () => {
  const calls: { path: string; options?: RequestInit }[] = []
  vi.stubGlobal('fetch', vi.fn(async (path: string, options?: RequestInit) => {
    calls.push({ path, options })
    if (path.endsWith('/session')) return envelope({ csrf_token: 'token' })
    if (path.endsWith('/inputs')) return envelope({ id: 'hash', sha256: 'hash', orders: 1, line_items: 1, bytes: 50, scenario_id: 'W100' })
    if (path.endsWith('/jobs/preview')) return envelope({ preview_id: 'p', expires_in_s: 600, argv: [], warnings: [], effective: { robots: 2, orders: 3 } })
    throw new Error('Unexpected request')
  }))
  function Harness() {
    const [selected, setSelected] = useState('W100')
    const current = [scenario, second, invalid].find(item => item.id === selected) ?? null
    return <RunSetup scenario={current} scenarios={[scenario, second, invalid]} scenarioId={selected}
      onScenarioChange={setSelected} strategies={strategies} canObserve={true} activeJob={false}
      onLaunched={() => {}} onRefresh={() => {}} />
  }
  render(<Harness />)
  expect(screen.getByText('40 × 54 grid · 100 homes')).toBeVisible()
  expect(screen.getByText(/Invalid map: missing station: no parking/)).toBeVisible()
  expect((screen.getByRole('option', { name: /Invalid map/ }) as HTMLOptionElement).disabled).toBe(true)
  const file = new File(['content'], 'orders.csv', { type: 'text/csv' })
  Object.defineProperty(file, 'text', { value: async () => 'content' })
  fireEvent.change(screen.getByLabelText('Upload order CSV'), { target: { files: [file] } })
  expect(await screen.findByText(/1 logical orders/)).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Preview run' }))
  expect(await screen.findByRole('dialog')).toBeVisible()
  fireEvent.change(screen.getByLabelText('Warehouse scenario'), { target: { value: 'second' } })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByText('12 × 20 grid · 8 homes')).toBeVisible()
  expect(screen.getByLabelText('Single-run order source')).toHaveValue('reference')
  await userEvent.click(screen.getByRole('button', { name: 'Preview run' }))
  await screen.findByRole('dialog')
  const previewRequests = calls.filter(call => call.path.endsWith('/jobs/preview'))
  const body = JSON.parse(previewRequests.at(-1)!.options!.body as string)
  expect(body.scenario_id).toBe('second')
  expect(body.input_id).toBeUndefined()
})

it('disables scenario changes while an owned job is active', () => {
  render(<RunSetup scenario={scenario} scenarios={[scenario, second]} scenarioId="W100" onScenarioChange={() => {}}
    strategies={strategies} canObserve={true} activeJob={true} onLaunched={() => {}} onRefresh={() => {}} />)
  expect(screen.getByLabelText('Warehouse scenario')).toBeDisabled()
})
