import { describe, expect, it } from 'vitest'
import { buildSpec, screenCell, metricCards, isSnapshot, acceptJob, previewMatches } from './model'
import type { Job, Snapshot } from './types'

describe('scientific display and input boundaries', () => {
  it('keeps unavailable values unknown and never converts legacy fleet time into order makespan', () => {
    expect(metricCards({ makespan_s: 12, orders_completed: 3 }, 'legacy').find(c => c.key === 'order_makespan_s')?.value).toBeNull()
    expect(metricCards({ makespan_s: 12, order_makespan_s: 10 }, 'current').find(c => c.key === 'order_makespan_s')?.value).toBe(10)
    expect(metricCards(null, 'missing').every(c => c.value === null)).toBe(true)
    expect(metricCards({ makespan_s: 0 }, 'legacy').find(c => c.key === 'makespan_s')?.value).toBe(0)
  })
  it('renders grid centers with upward source Y and introduces no half-cell world offset', () => {
    expect(screenCell([0, 0], 54)).toEqual([0, 53])
    expect(screenCell([39, 53], 54)).toEqual([39, 0])
  })
  it('only produces supported single-run fields and rejects invalid finite/count values', () => {
    const form = { strategy: 'random', robots: '2', orders: '3', seed: '7', mode: 'observed' as const,
      window: '', quantum: '', speed: '', sample: '1', export: false }
    expect(buildSpec(form, 100, 'second')).toEqual({ kind: 'run', scenario_id: 'second', strategy: 'random', robots: 2, orders: 3,
      seed: 7, execution_mode: 'observed', observation_sample_s: 1, quiet: true, export: false, progress_interval_s: 10 })
    for (const values of [{ robots: '101' }, { robots: '2.5' }, { orders: '-1' }, { speed: 'NaN' }, { quantum: '-1' }]) {
      expect(() => buildSpec({ ...form, ...values }, 100, 'second')).toThrow()
    }
  })
  it('invalidates a preview whenever its effective request changes', () => {
    expect(previewMatches({ strategy: 'random', robots: 2 }, { strategy: 'random', robots: 2 })).toBe(true)
    expect(previewMatches({ strategy: 'random', robots: 2 }, { strategy: 'random', robots: 3 })).toBe(false)
  })
  it('does not regress the selected job with a slower old response', () => {
    const current = { id: 'a', sequence: 10, status: 'running' } as Job
    expect(acceptJob(current, { ...current, sequence: 9 })).toBe(current)
    expect(acceptJob(current, { ...current, sequence: 11 }).sequence).toBe(11)
    expect(acceptJob(current, { ...current, id: 'other' })).toBe(current)
  })
  it('refuses unknown/invalid telemetry instead of fabricating a robot position', () => {
    const record = { schema_version: 1, type: 'fleet_snapshot', source: 'executor_observer', job_id: 'a',
      stream_generation: 'g', sample_sequence: 1, sim_time_exact: '2/3', sim_time_s: 2 / 3, payload: { orders: {total:1,pending:1,assigned:0,in_progress:0,completed:0,failed:0,deferred_now:0,cumulative_deferrals:0},resources:[],robots: [{ robot_id: 'r1', cell: [1, 2], status: 'IDLE', soc_percent: null }] } }
    expect(isSnapshot(record)).toBe(true)
    expect(isSnapshot({ ...record, schema_version: 2 })).toBe(false)
    expect(isSnapshot({ ...record, sim_time_s: NaN })).toBe(false)
    expect(isSnapshot({ ...record, payload: { robots: [{ cell: ['x', 2] }] } })).toBe(false)
  })
})
