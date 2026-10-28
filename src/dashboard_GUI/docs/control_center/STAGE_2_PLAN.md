# Stage 2 implementation plan

Goal: preview and launch original headless runners, track/cancel owned jobs, persist status/logs and reconnect over localhost. Spec: STAGED_PLAN.md Stage 2 and API_CONTRACT.md. User approved Stage 1 and authorized Stage 2; stop after Stage 2.

Architecture: strict typed request -> source-fingerprinted preview -> one active app-owned subprocess group -> SQLite operational status/events and original artifacts. The scientific interpreter, CLI, config, strategies and metrics remain untouched. All development stays under src/dashboard_GUI.

## Global constraints and rulings

- Preserve existing changes; no commits, pushes, branches, stash/reset/clean/revert or scientific edits.
- Extend only the Stage 1 backend where integration requires it; preserve existing history/download behavior and tests.
- No GUI, motion telemetry, ROS or later-stage features. All simulations used by the agent are tiny disposable checks.
- Existing approved Stage 0/1 design is the implementation authority. Execute inline; one final independent review. No additional design approval round is needed for the already-authorized stage.
- Override generic skill worktree/commit/full-suite requirements with the user's preservation and lightweight-verification restrictions. Canonical progress remains in work_state_write.

## Review focus

Input drift between preview/launch; cancellation with descendants; restart/PID reuse; zero-exit partial artifacts; cross-origin or tokenless launches. Tests must pin these behaviors.

## Task 1: Validated CLI previews

Files: schemas/jobs.py, adapters/commands.py, tests/test_jobs.py.
Interface: RunSpec/ExperimentSpec; CommandPlanner.preview(spec, job_id) -> plan with argv, fingerprint, effective inputs, grid and destination. Inputs are catalogued; paths/executables server-owned.

1. Write failing tests for exact supported argv, invalid flags/types, grid/reference semantics, capacity, source/output drift, resume/force refusal and preview no-write behavior.
2. Run tests and confirm missing feature failure.
3. Implement typed validation and read-only planner using source declarations. Single-run seed is allocation-only; experiment has no quantum/config/custom CSV control.
4. Verify focused tests green. No commit.

## Task 2: Durable owned job lifecycle

Files: services/jobs.py, storage/jobs.py, services/processes.py; tests/test_jobs.py.
Interface: JobManager.preview/launch/get/list/cancel/logs/events/close. Job SQLite and lock separate from scientific artifacts; process identity includes boot/start/session, not PID alone.

1. Write failing tests using tiny fake runner processes for launch, failure, cancel/escalation, descendants, exclusive job/manager, persisted logs, restart reconciliation and unowned process refusal.
2. Run red; implement original CLI Popen argv with shell=False, explicit cwd/env, new process session, bounded logs/event retention and durable transitions.
3. Validate completed outputs against original readers; keep process exit, artifact completeness and scientific validity distinct. Resume preserves previous attempt evidence; force delegates original rename safeguard.
4. Verify focused tests green. No commit.

## Task 3: Local API integration

Files: api/jobs.py, app.py, api/routes.py, existing startup tests where stage behavior changes; tests/test_jobs_api.py.
Interface: session token GET, preview POST, run/experiment POST by preview ID, job GET/logs/snapshot/cancel, authenticated same-origin WebSocket status/log relay. Snapshot explicitly reports spatial telemetry unavailable.

1. Write failing tests for token/Origin/Host, stale preview, route-kind mismatch, one-job gate, logs/status reconnect and historical/app artifact scope.
2. Run red; integrate manager in lifespan with shutdown cleanup; update capabilities/health truthfully. Add app-artifact scope without altering history.
3. Verify all backend tests, scoped lint/import, one tiny original CLI/app parity pair, socket health/session smoke, hash preservation. No long simulations.
4. Update backend README and write STAGE_2_REPORT.md with exact usage, results, limitations and final git status; stop for review.
