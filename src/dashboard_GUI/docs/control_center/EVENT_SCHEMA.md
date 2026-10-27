# Proposed observation and event schema

> Stage 0 design only — audited 2026-10-27. No application or telemetry implementation exists from this stage. The user's subsequent layout instruction overrides the supplied plan: all Control Center development belongs under `src/dashboard_GUI/`; these documents belong in its `docs/control_center/` subdirectory. Scientific sources and historical artifacts remain unchanged. Later stages require separate authorization.

## What can actually be observed

| Signal | Existing source | Boundary / limitation |
|---|---|---|
| Aggregate progress | WorkloadExecutor.snapshot() -> Progress; on_progress(Progress) | Pure counts, float time; no robot list. deferred_now is a subset of assigned |
| Robot cell/status/counters | executor.engine.robots[id]: GridRobot | Mutable internal state; copy on simulation thread only; no robot SOC field |
| SOC/energy | engine.battery.snapshot(id, engine.time), soc(id, engine.time) | Exact Fraction ledger; disabled -> unavailable, no fallback estimate |
| Order state | executor.queue.orders / Order.status, assigned_robot, start_time, completion_time | Copy state, never reset or mutate; times stored as floats |
| Current executing order | executor.active | Can remain active through dropoff retreat even after order COMPLETED |
| Deferred assignment | executor.deferred plus assigned_robot | Separate from active; retain original robot ownership |
| Charging | executor.charge, requesting, chargers/holding slots | ChargeState distinct from GridRobotStatus; copy relevant fields |
| Traffic observations | robot waiting_time_s, wait_event_count, vertex_conflict_count, edge_conflict_count; result.yield_events | Cumulative observed counters; no live congestion score or blocker-cause API promised |
| Dispatch | on_dispatch(Dispatch) | Float time, cost/route summary; not the whole candidate table |
| Order transition | on_order_status(order_id,status,sim_time,robot_id) | Can occur during event handling; do not claim a coherent whole-fleet snapshot at this callback |
| Battery transition | on_battery_event(BatteryEvent) | Exact Fraction time/SOC; existing emitted kinds/reasons, not guessed state machine |
| End-of-tick state | WorkloadExecutor on_tick(sim_time,batch) | External callback invoked after _tick retires, settles charging, dispatches and reports progress |
| Engine events | Event(time,seq,robot_id,kind,detail) in batch | Scheduled completions/timer events, NOT all state transitions; no coordinates in Event |
| Trajectory | on_trajectory constructor parameter | Stored but never invoked in current executor; unavailable as stream |
| ROS topics | FleetNode /mrta/robot_states, /mrta/fleet_status, /mrta/events | ROS process only; deferred Stage 7, not a headless transport |

Engine.step_once advances to the next existing Fraction timestamp, calls on_event BEFORE _handle, then continues pending moves, retries waiting, stamps robots and invokes on_tick. Executor owns engine.on_event/_tick; never replace these handlers. Executor's external on_tick is the preferred coherent observation point. engine.time is the exact source: do not reconstruct a Fraction from callback float. An engine batch may schedule additional same-time events; sequence is needed even when sim time repeats. A tick is a processed batch, not a guarantee all possible events at that time are drained.

## Integration decision and alternatives

1. **Stage 2 default:** unchanged CLI subprocess + stdout/stderr + original artifacts. Progress parser, if added, is explicitly best-effort/rounded; raw logs retained. Experiments disable child progress, so show per-run log/status and artifacts. No motion or live candidate table is implied.
2. **Stage 3 candidate:** standalone app adapter constructing unchanged WorkloadExecutor with existing callbacks and a closure over it. It must reproduce exact CLI input resolution, manifest/recorder and result-writing semantics, with CLI-versus-adapter parity tests. This is orchestration duplication risk, not an already reusable runner API: main() currently constructs the executor internally. It is a separately identified execution mode, never silently substituted into original run_experiment.py children.
3. **Deferred alternative:** if preserving original CLI execution while injecting callbacks requires a runner observation seam, submit the minimal proposed change for user review. No monkeypatching, tracing locals, private event-queue access, ROS launch, or core-source edits as a workaround. If parity/integration is not proven, retain option 1 and label spatial telemetry unavailable. Matrix spatial telemetry remains unavailable through the unmodified CLI.

Observation copies primitives on the simulation thread. A bounded queue sends immutable records to a separate writer/relay; no HTTP, file I/O, waits or sleeping inside callbacks. Observer errors disable telemetry and produce an operational warning without changing simulation execution. Never call step_once/run/schedule_timer/dispatch or an allocator to obtain a frame; never consume scientific RNG. Rendering cadence is wall-clock only in relay/browser. Sampling can select existing ticks by observed simulated-time thresholds, without scheduling new engine events. Avoid copying every order/path every tick; order transitions plus periodic counts and selected details keep cost bounded.

## Proposed v1 envelope

Illustrative shape, not measured data:

```json
{
  "schema_version": 1,
  "job_id": "job_example",
  "run_ref": "optional_app_run_id",
  "sequence": 42,
  "type": "fleet_snapshot",
  "source": "executor_observer",
  "sim_time_exact": "128/3",
  "sim_time_s": 42.666666666666664,
  "observed_at_utc": "2026-10-27T17:00:00Z",
  "payload": {
    "sample_mode": "sampled",
    "dropped_samples_total": 0,
    "robots": [],
    "orders": null
  }
}
```

sequence is a monotonic integer assigned by the job relay across logs, status and snapshots, distinct from engine Event.seq. For matrix jobs each event carries child run_ref; event time is nullable when no single sim clock applies. sim_time_exact is str(Fraction), accepting integers such as "0" and rational strings; display float is derived from that value only when exact time is available. For logs/progress parsed from text, exact=null and precision/source=rounded_cli_text; never fabricate exact time. observed_at_utc and elapsed_wall_s are operational values, never scientific durations.

Proposed types: job_status, log, progress, fleet_snapshot, order_transition, battery_event, dispatch_observation, telemetry_gap, artifact_available, error. Only emit types whose source actually exists in the chosen mode. Dispatch observations are provisional operational records; original decisions/outcomes remain the evidence. Unknown schema/type must be reported or safely ignored, not decoded as a current frame.

Snapshot robot fields: robot_id; cell=[x,y]; status enum from GridRobotStatus; active_order_id; assigned_order_id (separate); charge_state; soc_percent and soc_exact (nullable); cumulative distance_m/travel_time_s/waiting_time_s/service_time_s/charging_time_s; observed counter fields; optional path_cells with explicit observed/planned label. Do not fabricate a timed route from GridRobot.path: it is not a lossless future trajectory. Aggregate orders fields exactly follow Progress: total,pending,assigned,in_progress,completed,failed,deferred_now,cumulative_deferrals. Aggregate robot categories are source counts and may not be a mutually exclusive partition. Resource records copy slot zone_id/cell/state/robot holder/promised_to where present; field presence validated against actual dataclass definitions during Stage 3.

## Backpressure, reconnect and replay

Maintain bounded latest-snapshot storage, bounded transport/log queues, and explicit dropped/coalesced sample counts. Relay sequence gaps indicate missed transport events; sample counters also expose losses before relay assignment. Slow clients cannot block callbacks. Original RunRecorder decision/outcome evidence must remain unchanged; it buffers until end, so cancellation can lose it. The telemetry system must not claim to make incomplete scientific output lossless.

GET snapshot returns sequence plus latest state; WS accepts after_sequence, sends retained newer events or an explicit resync_required gap. Register/replay against a common relay high-water mark to avoid a snapshot/subscribe race. Client deduplicates sequences. Restart starts a new stream generation if durable sequencing cannot be recovered; never silently reuse a cursor. Completed jobs remain readable; connection loss makes state stale, not FAILED.

Proposed telemetry.ndjson belongs to dashboard_GUI/var/jobs/<id>/, separate from scientific tables. Header records source hashes, adapter version, sampling rule, stream generation and exact-time availability. End marker records complete/partial and drops; a missing end marker is partial. Read line-by-line with bounded pages; tolerate a trailing incomplete line. Capture initial/final state only if the adapter can safely access it. High-speed runs may finish between UI refreshes: record then replay is honest sampled playback. Do not rerun policies to synthesize history; interpolation affects visuals only and never claimed exact cell occupancy, battery, traffic or metrics. Historical runs with no trace show replay unavailable.
