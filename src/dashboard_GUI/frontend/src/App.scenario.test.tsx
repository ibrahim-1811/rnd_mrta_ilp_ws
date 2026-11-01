import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import App from './App'

const envelope = (data: unknown) => new Response(JSON.stringify({ api_schema_version: 1, data, availability: 'available', warnings: [] }))
const baseScenario = { label: 'Scientific W100', reference: true, valid: true, width: 40, height: 54, capacity: 100,
  settings: {}, hashes: {}, identity: 'a'.repeat(64), validation: { errors: [], warnings: [] }, provenance: 'test' }
const geometry = (id: string, width: number) => ({ id, width, height: 10, resolution_m: 1, origin: [0, 0], obstacles: [],
  traversable_cells: [], non_transit_cells: [], zones: [], directed_edges: [] })

it('loads scenario catalogue first and replaces geometry through the selected encoded ID', async () => {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (path: string) => {
    calls.push(path)
    if (path.endsWith('/health')) return envelope({ version: 'test', read_only: false })
    if (path.endsWith('/capabilities')) return envelope({ simulation_launch: { implemented: true, readable: true }, telemetry: { implemented: true, readable: false } })
    if (path.endsWith('/strategies')) return envelope([{ id: 'nearest_astar', implementation: 'NearestAstar', review_status: 'registered' }])
    if (path.endsWith('/scenarios')) return envelope([
      { ...baseScenario, id: 'W100' }, { ...baseScenario, id: 'second_map', label: 'Second map', reference: false, width: 12, capacity: 8, identity: 'b'.repeat(64) },
    ])
    if (path.endsWith('/scenarios/W100/map')) return envelope(geometry('W100', 40))
    if (path.endsWith('/scenarios/second_map/map')) return envelope(geometry('second_map', 12))
    if (path.includes('/jobs?')) return envelope([])
    throw new Error(`Unexpected request ${path}`)
  }))

  render(<App />)
  const selector = await screen.findByLabelText('Warehouse scenario')
  await waitFor(() => expect(calls).toContain('/api/v1/scenarios/W100/map'))
  await userEvent.selectOptions(selector, 'second_map')
  await waitFor(() => expect(calls).toContain('/api/v1/scenarios/second_map/map'))
  expect(screen.getByText('12 × 54 grid · 8 homes')).toBeVisible()
  expect(calls.indexOf('/api/v1/scenarios')).toBeLessThan(calls.indexOf('/api/v1/scenarios/W100/map'))
})

it('keeps selected-job map labels and provenance coherent without changing completed-job setup', async () => {
  class Socket { close = vi.fn() }
  vi.stubGlobal('WebSocket', Socket)
  const secondHash = 'b'.repeat(64)
  const second = { ...baseScenario, id: 'second_map', label: 'Second map', reference: false, width: 12, capacity: 8,
    hashes: { map: secondHash }, settings: { config_path: '/scenarios/second.yaml' }, identity: secondHash }
  const job = { id: 'job2', kind: 'run', status: 'completed', sequence: 1, created_at: '2026-10-31T00:00:00Z',
    output_dir: '/tmp/job2', owned_process_alive: false, artifact_status: 'complete', elapsed_wall_s: 1,
    plan: { job_id: 'job2', kind: 'run', argv: [], output_dir: '/tmp/job2', request: { strategy: 'nearest_astar' },
      warnings: [], effective: { scenario_id: 'second_map', robots: 2 } },
    telemetry: { available: false, availability: 'unavailable', reason: 'Original CLI mode' } }
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (path: string) => {
    calls.push(path)
    if (path.endsWith('/health')) return envelope({ version: 'test', read_only: false })
    if (path.endsWith('/capabilities')) return envelope({ simulation_launch: { implemented: true, readable: true }, telemetry: { implemented: true, readable: false } })
    if (path.endsWith('/strategies')) return envelope([{ id: 'nearest_astar', implementation: 'NearestAstar', review_status: 'registered' }])
    if (path.endsWith('/scenarios')) return envelope([{ ...baseScenario, id: 'W100' }, second])
    if (path.endsWith('/scenarios/second_map/map')) return envelope(geometry('second_map', 12))
    if (path.endsWith('/jobs?limit=100')) return envelope([job])
    if (path.endsWith('/jobs/job2/snapshot')) return envelope(job)
    if (path.includes('/jobs/job2/logs?')) return envelope({ text: '', next_cursor: 0, size: 0, complete: true })
    if (path.endsWith('/session')) return envelope({ csrf_token: 'token' })
    if (path.includes('/runs?scope=app')) return envelope([])
    throw new Error(`Unexpected request ${path}`)
  }))

  render(<App />)

  const selector = await screen.findByLabelText('Warehouse scenario')
  await waitFor(() => expect(calls).toContain('/api/v1/scenarios/second_map/map'))
  expect(selector).toHaveValue('W100')
  expect(selector).not.toBeDisabled()
  expect(document.querySelector('.sidebar-map')).toHaveTextContent('second_map')
  expect(document.querySelector('.sidebar-map')).toHaveTextContent('Second map')
  expect(document.querySelector('.warehouse-label')).toHaveTextContent('second_map')
  expect(document.querySelector('.warehouse-label')).toHaveTextContent('Second map')

  await userEvent.click(screen.getByRole('button', { name: 'System' }))
  const provenance = screen.getByText('Scenario provenance').closest('.system-card')
  expect(provenance).toHaveTextContent('second_map')
  expect(screen.getByTitle(secondHash)).toBeVisible()
  expect(provenance?.querySelector('pre')).toHaveTextContent('/scenarios/second.yaml')
})
