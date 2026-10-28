# Stage 3: observed single-run implementation plan

> Execute inline using the test-first and executing-plans skills, with one final
> independent review. The user authorized Stage 3 in this conversation. Existing
> approved EVENT_SCHEMA.md and SCIENTIFIC_PARITY.md govern the design; no further
> design approval round, worktree, commit, or scientific source edit is needed.

**Goal:** Genuine sampled fleet snapshots, recorded separately and delivered by
the existing localhost job API, with measured scientific parity.

**Architecture:** An explicitly selected standalone observer runner constructs
the unchanged executor using the original CLI's input/manifest/recorder/result
semantics. Its post-tick callback copies primitive state into a bounded queue.
A separate writer records bounded NDJSON; the existing job monitor relays those
records into durable sequenced events and latest-snapshot state. Original CLI
mode remains default; experiment mode remains logs-only.

**Spec:** EVENT_SCHEMA.md, SCIENTIFIC_PARITY.md and STAGED_PLAN.md Stage 3.
**Stack:** Existing Python/scientific interpreter, stdlib queue/thread/JSON,
existing FastAPI/SQLite/WebSocket stack. No dependency installation.

## Constraints and decisions

- All changes stay under src/dashboard_GUI. Preserve dirty/untracked and external
  changes; keep HEAD/index and scientific files/history unchanged. No commits.
- RunSpec adds explicit `execution_mode=cli|observed`; default cli. Observed mode
  supports existing single-run fields only. Experiments reject observation fields.
- Default observation interval 1 simulated second, selected from existing ticks;
  no timers, sleeps, clock advancement, RNG use, routing or allocator calls in
  observation callbacks. Repeated exact timestamps are legal.
- Snapshots contain real robot cells/status/SOC/counters, separate active/deferred
  order ownership, resource state and aggregate order counts. No trajectories,
  interpolated motion, future outcomes or fabricated congestion/ILP values.
- Queue capacity 128; trace cap 64 MiB; per-record bound 256 KiB; trace read page
  at most 1 MiB and 100 records. Drops, gaps, failure and partial traces explicit.
- Observer/copy/write/relay failures cannot fail a scientific run. Cancellation
  may leave partial trace and lose original in-memory scientific tables as before.
- New code uses a reviewed original-runner source fingerprint to reject drift in
  duplicated CLI setup. Original CLI launches remain available if it drifts.
- No GUI, ROS/Gazebo, matrix spatial telemetry or later-stage work.

## Tasks and acceptance

### 1. Snapshot producer and bounded recording

Files: services/observation.py; tests/test_observation.py.
Interfaces: `SnapshotObserver(executor, queue, interval_s).capture(force=False)`
and callback; `TraceRecorder(path, job_id, source_fingerprint, ...).close(...)`.

- [x] Write and observe failing tests for primitive-copy isolation, exact time,
  battery disabled/charge states, ownership, repeated times, queue overflow,
  callback failure, writer error, bounded trace and partial trailing lines.
- [x] Implement pure copying and nonblocking enqueue; writer outside callbacks.
- [x] Prove observer off/on/overflow/error preserve event sequence, dispatches,
  recorder decisions/outcomes, final cells/SOC and deterministic metrics on tiny
  fixtures, including a charging/deferred or same-time case where available.

### 2. Explicit observed runner and original CLI parity

Files: adapters/observed_runner.py, schemas/jobs.py, adapters/commands.py;
tests/test_observed_runner.py; scripts/check_stage3_smoke.py.

- [x] Add failing mode/argv/default/capability and small parity tests.
- [x] Reproduce original setup and original manifest/recorder/metrics/export
  functions without modifying/import-patching the original runner or simulator.
- [x] Keep original argv in preview alongside explicit observed argv; protect
  adapter/observer source drift. Scientific environment needs no FastAPI import.
- [x] Compare tiny original CLI and observed adapter records exactly. Exclude
  only the four documented host timing fields from deterministic metric equality.

### 3. Durable relay, API and operational verification

Files: services/telemetry.py, services/jobs.py, storage/jobs.py, api/jobs.py,
api/routes.py; tests/test_telemetry_api.py; STAGE_3_STARTUP.md/STAGE_3_REPORT.md.

- [x] Add failing tests for source/schema validation, bounded byte pages,
  trailing partial records, latest snapshot persistence, source/generation gaps,
  reconnect/restart, missing/legacy traces and observer failure isolation.
- [x] Relay into the existing global job event sequence, keeping sample sequence
  and stream generation separate. Slow WebSockets cannot block observation.
- [x] Expose latest snapshot and paginated recorded telemetry; history without
  actual traces stays explicitly unavailable. No rendered playback claim.
- [x] Full backend tests, scoped lint, tiny CLI/observer/app/socket checks and
  source/history/Git preservation audit. Independent review, test-first fixes,
  measured report; stop after Stage 3.

## Review focus

1. Copied fields must be primitives and reflect the post-tick source, including
   active-versus-deferred ownership and null SOC with battery disabled.
2. Full/failed writer or relay must never change engine scheduling or results.
3. Growing/truncated/replaced/corrupt traces must not terminate a scientific job
   or silently masquerade as complete current snapshots.
4. Restart and reconnect must preserve global sequence and expose sample loss.
5. Observed CLI setup/export/provenance must match current original behavior;
   future runner drift must refuse observed mode with a clear CLI fallback.
