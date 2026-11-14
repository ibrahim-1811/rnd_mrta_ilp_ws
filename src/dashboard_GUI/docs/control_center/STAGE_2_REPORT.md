# Stage 2 implementation report

2026-10-27. Stage 2 completed and stopped for review. All development is under
src/dashboard_GUI. No Stage 3 telemetry or GUI work started.

## Implemented behavior

- Strict JSON runner models, live registry/capacity checks and source-declared
  option verification. Preview contains exact original argv, effective config,
  fingerprints, warnings and the actual experiment grid; preview itself creates
  no output directory and launches nothing. One-use previews expire in 10 minutes.
- Source, interpreter-content, git-state and output recheck at launch. One active
  top-level job, experiment jobs bounded 1–4; no scientific defaults silently
  changed. Original runners, scientific interpreter and root cwd are preserved.
- Durable jobs.sqlite records, lifecycle events, original merged process.log,
  64-KiB byte-cursor log pages and bounded 500-event WebSocket reconnect history.
  Disk log threshold cancels above 64 MiB (polled, not a hard disk quota).
- New process session/group, boot/start/session ownership verification, TERM then
  KILL of owned group members, restart/interrupted handling and manager lock.
  A fail-closed stdlib exec gate persists readiness and requires a permit issued
  only after SQLite records ownership. It then execs the unchanged original CLI.
- Session token/header protects POST, same-origin loopback Host checks, HttpOnly
  same-site WebSocket cookie. Swagger Authorize exposes the header requirement.
  Read routes remain read-only; no arbitrary command, executable, PID or path API.
- Explicit app-owned new outputs, retry archives before resume/force, input and
  workload drift guards, original runner overwrite/resume/force rules retained.
- Completed requires original process exit 0 plus structural expected-artifact
  validation. Cancellation and unknown exit never become success by filename
  presence. No metric recomputation or decision/outcome joining.
- Existing historical readers unchanged in meaning. New outputs are available
  using scope=app; default scope=history still reads repository runs/.

Exact usage, request examples, routes, paths and recovery are in
STAGE_2_STARTUP.md; backend README has an additive Stage 2 section. The separately
added Stage 1 entrypoint remains available and read-only.

## Files authored or extended in this stage

New production modules:

```text
backend/control_center/adapters/commands.py
backend/control_center/schemas/jobs.py
backend/control_center/services/jobs.py
backend/control_center/services/processes.py
backend/control_center/services/job_gate.py
backend/control_center/storage/jobs.py
backend/control_center/api/jobs.py
```

New focused tests/utilities and documentation:

```text
backend/tests/test_commands.py
backend/tests/test_jobs.py
backend/tests/test_jobs_api.py
scripts/check_stage2_smoke.py
docs/control_center/STAGE_2_PLAN.md
docs/control_center/STAGE_2_STARTUP.md
docs/control_center/STAGE_2_REPORT.md
```

Five existing app files intentionally extended:

```text
backend/control_center/app.py                 manager lifespan, local actions, safe validation errors
backend/control_center/api/routes.py          truthful capability/health, app history scope
backend/pyproject.toml                        pinned WebSocket transport
backend/tests/test_backend.py                 Stage 2 startup assertions; remove unused import
backend/README.md                            additive Stage 2 startup/usage pointer
```

No scientific/root/configuration files edited. The pre-existing typed table
reader was briefly used to test a threading hypothesis, then restored byte for
byte; the fix belongs in application startup, not scientific dependencies.

## Verification

The full focused backend suite: **77 passed in 14.25 seconds**, including the
concurrently added Stage 1 read-only tests. One pre-existing upstream
Starlette/AnyIO BlockingPortal deprecation warning remains. Scoped Ruff check
passed for backend/control_center, backend/tests and dashboard scripts.

Tests include validated CLI flags, invalid/strict/nonfinite inputs, capacity,
reference grid semantics, immutable preview, output symlink refusal, source drift,
reference resume, zero-exit incomplete artifacts, token/Origin, actual app artifact
scope, WebSocket reconnect, one-job and one-manager gates, persisted logs/status,
PID-reuse refusal, recoverable orphan groups, resistant descendants, log threshold,
retention gaps, native reader thread reuse and backend death during spawn.
The default suite starts only tiny stand-in processes, not scientific workloads.

Exact verification commands (repository root, documented source PYTHONPATH):

```bash
PYTHONDONTWRITEBYTECODE=1 src/dashboard_GUI/backend/.venv/bin/python -m pytest src/dashboard_GUI/backend/tests -q -p no:cacheprovider
.pixi/envs/default/bin/ruff check src/dashboard_GUI/backend/control_center src/dashboard_GUI/backend/tests src/dashboard_GUI/scripts --output-format concise
PYTHONFAULTHANDLER=1 PYTHONDONTWRITEBYTECODE=1 src/dashboard_GUI/backend/.venv/bin/python src/dashboard_GUI/scripts/check_stage2_smoke.py
```

Final explicit tiny smoke, after launch-gate fixes:

- Original CLI and app-launched single run: each 2 robots / 3 logical orders,
  nearest_astar, allocation seed 7, progress off. **5 decision rows, 3 outcomes,
  exact parsed decision/outcome/manifest equality**, all deterministic metric keys
  equal. Only wall_clock_s, allocator_cpu_s, mission_build_cpu_s and
  worst_dispatch_s excluded from deterministic equality; actual values retained.
- Original experiment CLI: 2 policies × 1 robot × 1 seed × 1 logical order;
  both runs completed with structurally valid artifacts. Resume reports
  “2 already done, 0 to run”; previous attempt archived.
- Real loopback socket: HTTP health 200, session acquired, authenticated actual
  WebSocket upgrade and first durable event received at sequence 1; test-owned
  server stopped cleanly. Existing user-owned server was left alone.
- No long simulation, full scientific suite, ROS/Gazebo or large campaign ran.

Smoke evidence (ignored runtime files, retained for review):
`var/verification/stage2_16da800d03/result.json`, original CLI/app datasets,
experiment/resume logs and socket.log. The final smoke has the IDs:

```json
{
  "single_job": "2dd5f57d17b54eceb2dcbddf8065a160",
  "experiment": "b1f0f571580e4e26a9d511e683b6e58b",
  "resume": "b5bb8856fadd44909ef916b1dcc55913",
  "output_root": "/home/ibrahim/rnd_mrta_ilp_ws/src/dashboard_GUI/var/verification/stage2_16da800d03",
  "socket": {
    "health_http": 200,
    "websocket_first_sequence": 1,
    "bind": "127.0.0.1"
  }
}
```

Deterministic metric keys compared: 56.

Host timing measurements (seconds; not scientific durations):

```json
{
  "cli": {
    "worst_dispatch_s": 0.0017747660021996126,
    "wall_clock_s": 0.18457190899789566,
    "allocator_cpu_s": 0.0006081280080252327,
    "mission_build_cpu_s": 0.0026226249974570237
  },
  "app": {
    "worst_dispatch_s": 0.0017807939948397689,
    "wall_clock_s": 0.1410874480061466,
    "allocator_cpu_s": 0.0005310999986249954,
    "mission_build_cpu_s": 0.00208234999445267
  }
}
```

## Defects found and resolved

Initial tests were red for absent Stage 2 interfaces. Regression tests then
caught reference-mode resume incorrectly requiring seeded workload metadata,
output symlink ancestors, nonfinite JSON error serialization, coerced string
inputs and interpreter access time being treated as scientific input drift.

The first two explicit smoke attempts exposed a native PyArrow segmentation
fault after first import on a short-lived worker and subsequent thread reuse.
A simulation-free subprocess regression reproduced it. Disabling reader threads
alone did not fix it. Initializing pyarrow.parquet on the lifespan/owner thread
before workers fixed the regression and the final real smoke. No scientific
package, version or engine code was changed. Failed-attempt artifacts remain
under separate var/verification directories and are not represented as success.

Independent review found two actionable lifecycle defects, both reproduced red
and fixed green:

1. Monitor exception cleanup waited only for the leader; a TERM-resistant child
   could survive and a dead worker entry prevented later cancellation. Cleanup
   now escalates based on whole-group liveness and dead-worker recovery checks
   thread liveness.
2. Popen preceded durable process identity, so a backend crash in that window
   could leave untracked scientific execution. The exec gate cannot authorize
   original CLI execution until ownership is persisted. Restart can recover its
   identity; uncertain launch briefly blocks the job gate and fails closed.

The final 77-test suite includes both regressions. No important review finding
is deferred. No second independent review was claimed after the fix pass.

## Scope, limitations and rulings

- User's approved Stage 2 design executed inline with test-first checks; no
  generic skill worktree/commit/full-scientific-suite actions, because they
  conflict with the explicit preservation and lightweight-stage contract.
- App JSON request limits and grid bounds are operational validation, not a new
  scientific policy. Defaults: one top-level job, jobs=1 (maximum 4), preview
  cache 128 entries / 10-minute TTL, grid at most 5,000 planned runs. The agent
  never launches the main-study preset automatically.
- Matrix CLI has no quantum/config/window/speed/custom CSV controls. These remain
  unavailable. Upload, comparative statistics UI, live positions/SOC and replay
  remain later stages. Snapshot provides process state only and exact sim time
  is null where no source signal exists.
- Resume/force is restricted to app-owned experiments; unchanged settings/source
  required for resume; prior directory archived with a 256-MiB retry bound.
  Larger existing outputs require a new name. Artifacts are original evidence,
  not a second research dataset. Keep inputs/source fixed during execution;
  preview guards cannot make an ordinary checkout immutable mid-run.
- The localhost account/processes are trusted; no remote/multi-user security
  claim. Group ownership protects against PID reuse but does not supervise a
  deliberately escaped session, power failure or an unwritable/full disk.
- Parent process.log threshold is not a disk quota and does not cap original
  matrix child logs. Events are bounded; original logs retained separately.
- Native reader initialization is tied to the lifespan/manager owner thread;
  supported startup uses the documented main-thread entrypoint.
- Historical records still lack verified exit codes. App job ledger supplies
  exit evidence only for processes it actually owns and observes.
- Only application virtualenv dependency added: websockets==15.0.1. Root
  pixi environment and lockfiles unchanged. Standard Swagger assets remain CDN
  hosted as in Stage 1; API/OpenAPI JSON are local.
- Separate Stage 1 entrypoint/test/startup files appeared concurrently during
  this task. They were preserved, not authored or removed in Stage 2. Their
  18 tests are included in the final 77-test backend result.

## Preservation and git state

Baseline: 426 pre-existing nonignored checkout/historical files hashed. Exactly
five intentional Stage 1 application files changed as listed above; the other
**421 files retained identical content**, including **168 historical run files**.
No original path disappeared. Scientific modules, inputs, configs, root lockfiles,
existing uncommitted research work and the unexpected pre-existing root file
26.2.2 were preserved. Existing plan deletion and SCIENTIFIC_PARITY modification
remain untouched. HEAD/branch/staged diff unchanged:
`badbc59671acd06fdcaf513060175ab74aeccc3d`, `traffic/rmf-integration`, no staged diff.
No commit, push, branch change, stash/reset/clean/revert or user-process termination.

Final git status:

```text
 D "src/dashboard_GUI/MRTA_CONTROL_CENTER_CODEX_PLAN (1).md"
 M src/dashboard_GUI/docs/control_center/SCIENTIFIC_PARITY.md
?? .codex/
?? 26.2.2
?? src/dashboard_GUI/.gitignore
?? src/dashboard_GUI/backend/
?? src/dashboard_GUI/docs/control_center/STAGE_1_REPORT.md
?? src/dashboard_GUI/docs/control_center/STAGE_1_STARTUP.md
?? src/dashboard_GUI/docs/control_center/STAGE_2_PLAN.md
?? src/dashboard_GUI/docs/control_center/STAGE_2_REPORT.md
?? src/dashboard_GUI/docs/control_center/STAGE_2_STARTUP.md
?? src/dashboard_GUI/scripts/
```

Stop after Stage 2 for user review; Stage 3 is not authorized by this completion.
