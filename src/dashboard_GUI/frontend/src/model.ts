import type { Cell, Form, Job, Snapshot } from './types'

export const active = (job: Job | null) => !!job && (['queued', 'starting', 'running', 'cancel_requested'].includes(job.status) || job.owned_process_alive)
export const human = (value: string) => value.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase()).replace('Astar', 'A*').replace('Milp', 'MILP')
export const numeric = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null
export const display = (value: unknown, decimals = 1) => numeric(value) === null ? '—' : new Intl.NumberFormat('en', { maximumFractionDigits: decimals }).format(value as number)
export const screenCell = ([x, y]: Cell, height: number): Cell => [x, height - 1 - y]
export const previewMatches = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)
export function acceptJob(current: Job | null, next: Job): Job {
  if (current && (current.id !== next.id || next.sequence < current.sequence)) return current
  return next
}
export function buildSpec(form: Form, capacity: number, scenarioId = 'W100') {
  const number = (text: string, name: string, minimum: number, maximum: number, integer = false) => {
    const value = Number(text)
    if (!text.trim() || !Number.isFinite(value) || value < minimum || value > maximum || (integer && !Number.isInteger(value))) throw new Error(`${name} must be ${integer ? 'a whole number' : 'a number'} between ${minimum} and ${maximum}.`)
    return value
  }
  const spec: Record<string, unknown> = { kind: 'run', scenario_id: scenarioId, strategy: form.strategy,
    robots: number(form.robots, 'Robots', 1, capacity, true), orders: number(form.orders, 'Orders', 0, 1_000_000, true),
    seed: number(form.seed, 'Seed', 0, 2 ** 32 - 1, true), execution_mode: form.mode,
    quiet: true, export: form.export, progress_interval_s: 10 }
  if (form.input_id) spec.input_id = form.input_id
  if (!form.strategy) throw new Error('Select a registered strategy.')
  if (form.mode === 'observed') spec.observation_sample_s = number(form.sample, 'Observation interval', .001, 3600)
  if (form.window) spec.window = ['fleet_size', 'legacy'].includes(form.window) ? form.window : number(form.window, 'Window', 1, 100000, true)
  if (form.quantum.trim()) spec.quantum_s = number(form.quantum, 'Quantum', 0, 3600)
  if (form.speed.trim()) spec.speed_mps = number(form.speed, 'Speed', .000001, 100)
  return spec
}
export function metricCards(metrics: Record<string, unknown> | null, availability: string) {
  const supported = availability === 'legacy' || availability === 'current'
  return [
    { key: 'order_makespan_s', label: 'Order makespan', unit: 's', value: availability === 'current' ? numeric(metrics?.order_makespan_s) : null },
    { key: 'makespan_s', label: 'Fleet settled time', unit: 's', value: supported ? numeric(metrics?.makespan_s) : null },
    { key: 'total_distance_m', label: 'Fleet distance', unit: 'm', value: supported ? numeric(metrics?.total_distance_m) : null },
    { key: 'total_waiting_s', label: 'Fleet waiting', unit: 's', value: supported ? numeric(metrics?.total_waiting_s) : null },
  ]
}
export function isSnapshot(value: unknown): value is Snapshot {
  if (!value || typeof value !== 'object') return false
  const r = value as Snapshot
  return r.schema_version === 1 && r.type === 'fleet_snapshot' && r.source === 'executor_observer'
    && typeof r.job_id === 'string' && typeof r.stream_generation === 'string'
    && Number.isInteger(r.sample_sequence) && r.sample_sequence >= 0
    && typeof r.sim_time_exact === 'string' && Number.isFinite(r.sim_time_s)
    && !!r.payload?.orders && ['total', 'pending', 'assigned', 'in_progress', 'completed', 'failed', 'deferred_now', 'cumulative_deferrals'].every(key => { const n = (r.payload.orders as unknown as Record<string, unknown>)[key]; return typeof n === 'number' && Number.isInteger(n) && n >= 0 })
    && Array.isArray(r.payload.resources)
    && Array.isArray(r.payload?.robots) && r.payload.robots.every(robot => !!robot && typeof robot.robot_id === 'string'
      && Array.isArray(robot.cell) && robot.cell.length === 2 && robot.cell.every(Number.isInteger)
      && typeof robot.status === 'string' && (robot.soc_percent === null || Number.isFinite(robot.soc_percent)))
}
