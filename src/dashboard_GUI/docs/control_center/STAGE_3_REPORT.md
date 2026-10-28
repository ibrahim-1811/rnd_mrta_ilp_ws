# Stage 3: measured observation and scientific parity

2026-10-27. Stage 3 complete in this conversation. No GUI, ROS/Gazebo or matrix
spatial observation added. No commit/push or scientific phase change.

## Result

Explicit `execution_mode=observed` single runs now provide copied fleet cells,
status, exact/display SOC, counters, separate active/deferred ownership, aggregate
order counts and terminal-resource state. Original CLI mode remains default.
Experiments remain original-runner logs/artifacts only.

A standalone adapter uses existing scientific constructors/functions and their
callbacks, manifest builder, recorder, metrics and exports. It never edits or
monkeypatches the original runner/executor, nor inserts a clock event. A source
fingerprint gate refuses observed mode after original-runner drift.

Post-tick callbacks copy primitives into a 128-sample queue. A separate writer
records bounded NDJSON, with source fingerprint/version, stream generation,
exact timestamps, explicit loss counts and end/partial evidence. A separate final
slot retains final state under queue saturation when writing/quota permits.
The localhost API delivers latest snapshots, paginated traces and durable global
WebSocket events. Historical data lacking a genuine trace stays unavailable.

The robotics-oriented explanation, exact startup commands and first-run steps
are in `STAGE_3_STARTUP.md`.

## Files

New source: `services/observation.py`, `services/telemetry.py`,
`adapters/observed_runner.py` under `backend/control_center/`.

Extended existing source: `schemas/jobs.py`, `adapters/commands.py`,
`services/jobs.py`, `storage/jobs.py`, `api/jobs.py`, `api/routes.py`.

New tests: `test_observation.py`, `test_observed_runner.py`,
`test_telemetry_api.py`. New utility: `scripts/check_stage3_smoke.py`.
New documentation: this report, `STAGE_3_PLAN.md`, `STAGE_3_STARTUP.md`.
No dependency installation or root lockfile change was needed.

## Verification

Baseline: **94 tests passed**. Final complete backend suite: **130 passed in
32.46 seconds**, one pre-existing upstream Starlette/AnyIO deprecation warning.
Scoped Ruff passed for all backend source/tests and dashboard scripts.

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src/dashboard_GUI/backend:src/mrta_core:src/simulation_env:src/task_management \
src/dashboard_GUI/backend/.venv/bin/python -m pytest \
  src/dashboard_GUI/backend/tests -q -p no:cacheprovider

.pixi/envs/default/bin/ruff check src/dashboard_GUI/backend/control_center \
  src/dashboard_GUI/backend/tests src/dashboard_GUI/scripts --output-format concise
```

Observer off/on/overflow/copy-error/charging tests compared processed event
kind/time/sequence order, dispatches, decisions, outcomes, final cells/SOC and
exact final time. Repeated timestamps remain distinct samples. Writer error and
quota checks leave scientific completion unchanged. Queue saturation retains
the final state through the separate final slot.

Tiny original-CLI/adapter subprocess comparisons passed for nearest-A* with
fleet-sized window/quantum 3, and random with legacy window/quantum 0. Parsed
scientific records and deterministic metric keys match, and CSV/Prolog export
bytes match. Only wall_clock_s, allocator_cpu_s, mission_build_cpu_s and
worst_dispatch_s are excluded from deterministic metric equality.

The final real app-launched smoke passed with 2 robots, 3 orders, random, seed 7,
quantum 3 and export enabled. It compared against the unchanged original CLI,
then restarted a real loopback HTTP/WebSocket service over the persisted job.
Actual health was 200; HTTP delivered the persisted latest snapshot; WebSocket
delivered a genuine fleet snapshot at global sequence 4. Final exact time was
`674/3` seconds and matched original fleet-settled metrics. Final copied cells:
robot_001 `[39,2]`, robot_002 `[0,18]`. All **56 deterministic metric keys** match.

The burst produced **24 dropped observation samples**, reported as a partial
recording, while scientific outputs still matched and the final state was
retained. This is measured sampling loss, not missing scientific rows or a failed
simulation. The first smoke correctly failed an overly strong no-loss assertion;
the final check accepts explicit bounded-queue loss and requires exact final state
and scientific parity. Failed-attempt evidence remains separate.

The smoke outputs and result.json live in
`src/dashboard_GUI/var/verification/stage3_0d57e23269/`. The trace retained
158 genuine snapshots, from exact time `0` to `674/3`. The result includes
operational host timings. They are single tiny measurements, not an overhead benchmark.
No long simulation, fleet campaign, ROS or full scientific suite was run.

## Review and fixes

One fresh independent review found three important relay issues, all reproduced
red and fixed green in one fix pass:

1. Error-reporting storage failures could escape into process control, and failed
   event commits could persist an advanced source cursor. Relay restores the
   last committed job state, isolates failures in error reporting itself, and
   the job manager has an outer optional-observation boundary. Tests inject
   transient and persistent event failures and a crashed relay; the owned
   stand-in process remains running until explicitly cleaned up by the test.
2. Extra records/partial bytes after an end marker could retain a complete claim.
   Readers reject post-end data and inconsistent footer evidence.
3. Run-to-trace lookup searched only the first 100 jobs. It now streams persisted
   associations without that page limit; a 101-newer-job regression passes.

Parent checks additionally covered malformed schema/identity/NaN/exact time/UTC
timestamp, truncation/replacement, decoder errors, committed restart/reconnect
state and final-state loss under saturation. No critical or deferred minor
findings remain. There was no second independent review claim after the fix pass.

## Preservation and scope

SHA-256 baseline covered **447 pre-existing files**, including **168 historical
run artifacts**. Six existing backend source files were intentionally extended;
all other baseline files stayed byte-identical, and none disappeared. All
**321 protected scientific/history/contract files** stayed byte-identical.
HEAD, branch and Git index bytes remain unchanged. Initial HEAD:
`badbc59671acd06fdcaf513060175ab74aeccc3d`, `traffic/rmf-integration`.
No staging, commit, push, branch switch, stash/reset/clean/revert or user-process
termination occurred. The prior dirty/untracked work and old plan deletion remain.
Original dashboard files were backed up outside the repo under
`/tmp/mrta-stage3-g_4rwjfj/original/` before edits.

Decisions retained from the approved design:

- Separate observed wrapper, original CLI default: maintenance requires fresh
  parity when runner setup changes; the fingerprint gate prevents silent drift.
- Sampled cells/state, bounded queue/trace: gaps and partial traces are explicit;
  no continuous trajectory, interpolation or inferred future outcomes.
- Trusted local account and ordinary filesystem/process failure boundary:
  optional relay faults do not stop science, but malicious same-user tampering,
  deliberate session escape, power loss and total storage failure are not fully
  supervised. Source checkpoints do not freeze a mutable checkout mid-run.
- Matrix observation and rendered playback remain deferred. Stage 4 can consume
  these real APIs and recordings for the visual dashboard. No dashboard was
  silently scaffolded during this backend-only stage.
