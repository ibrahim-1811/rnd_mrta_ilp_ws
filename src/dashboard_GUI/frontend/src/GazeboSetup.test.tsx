import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { GazeboSetup } from './GazeboSetup'
import type { Job, Strategy } from './types'

const strategies = [{ id: 'nearest_astar', implementation: 'NearestAStar', review_status: 'registered' }] as Strategy[]
const envelope = (data: unknown) => new Response(JSON.stringify({ api_schema_version: 1, data, availability: 'available', warnings: [] }))

it('previews a fixed Gazebo job and launches only after explicit review', async () => {
  const calls: { path: string; options?: RequestInit }[] = []
  vi.stubGlobal('fetch', vi.fn(async (path: string, options?: RequestInit) => {
    calls.push({ path, options })
    if (path.endsWith('/session')) return envelope({ csrf_token: 'token' })
    if (path.endsWith('/jobs/preview')) return envelope({ preview_id: 'g1', expires_in_s: 600, argv: ['python', 'run_gazebo.py'], ros_argv: ['ros2', 'launch'], warnings: [], effective: { robots: 10, orders: 50, headless: true } })
    if (path.endsWith('/gazebo')) return envelope({ id: 'gazebo-job', kind: 'gazebo', status: 'running' })
    throw new Error('Unexpected request')
  }))
  const launched = vi.fn()
  render(<GazeboSetup strategies={strategies} capacity={100} activeJob={false} disabled={false} onLaunched={launched} />)
  fireEvent.click(screen.getByLabelText('Headless Gazebo'))
  await userEvent.click(screen.getByRole('button', { name: 'Preview Gazebo' }))
  expect(await screen.findByRole('dialog')).toBeVisible()
  const body = JSON.parse(calls.find(c => c.path.endsWith('/jobs/preview'))!.options!.body as string)
  expect(body).toMatchObject({ kind: 'gazebo', scenario_id: 'W100', headless: true, robots: 10, orders: 50 })
  expect(calls.some(c => c.path.endsWith('/gazebo'))).toBe(false)
  await userEvent.click(screen.getByRole('button', { name: 'Launch Gazebo' }))
  await waitFor(() => expect(launched).toHaveBeenCalledWith(expect.objectContaining({ id: 'gazebo-job' })))
})

it('shows an active-job blocker and disables Gazebo launch', async () => {
  vi.stubGlobal('fetch', vi.fn(async (path: string) => path.endsWith('/session')
    ? envelope({ csrf_token: 'token' })
    : envelope({ preview_id: 'g1', expires_in_s: 600, argv: [], ros_argv: [], warnings: [], effective: {} })))
  render(<GazeboSetup strategies={strategies} capacity={100} activeJob={true} disabled={false} onLaunched={(_job: Job) => {}} />)
  await userEvent.click(screen.getByRole('button', { name: 'Preview Gazebo' }))
  expect(await screen.findByText(/owned job is active/i)).toBeVisible()
  expect(screen.getByRole('button', { name: 'Launch Gazebo' })).toBeDisabled()
})
