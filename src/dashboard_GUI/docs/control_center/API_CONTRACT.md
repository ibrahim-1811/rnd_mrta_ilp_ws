# Proposed Control Center API and architecture

> Stage 0 design only — audited 2026-10-27. No application or telemetry implementation exists from this stage. The user's subsequent layout instruction overrides the supplied plan: all Control Center development belongs under `src/dashboard_GUI/`; these documents belong in its `docs/control_center/` subdirectory. Scientific sources and historical artifacts remain unchanged. Later stages require separate authorization.

## Architecture and ownership

```text
Browser: React + TypeScript + Vite, loopback only
  -> FastAPI /api/v1 (typed validation, read-only catalogue/history first)
       -> existing loaders, registry and comparison functions (read only)
       -> app SQLite: operational index, jobs, events, UI preferences
       -> owned subprocess group: original headless CLI and environment
            -> unchanged scientific engine/executor/strategies
            -> original scientific artifacts
       <- stdout/stderr; optional reviewed callback observer later
```

All new development lives in src/dashboard_GUI/: backend/, frontend/, tests/ (cross-layer fixtures), scripts/, docs/control_center/. Proposed runtime state lives in src/dashboard_GUI/var/ (ignored when implemented): index.sqlite, jobs/<job_id>/ logs and telemetry, inputs/<digest>/ validated CSV copies, runs/ new scientific outputs. Existing repository runs/ is a read-only history root. No directory besides documentation is created in Stage 0. Resolve repo root explicitly; never rely on the backend's cwd. This is a local app, not a new ROS package.

FastAPI/Pydantic and React/Vite are proposed dependencies, not installed capabilities. Isolate their manifests/locks under dashboard_GUI. Retain the existing scientific interpreter and its solver versions for subprocesses; no root pixi lockfile edits. If backend dependencies need another environment, use a bounded read-only helper in the scientific environment for authoritative imports, not a reimplementation of loaders/statistics.

## Proposed REST routes

All routes below are proposals, not existing endpoints. Prefix /api/v1. Stage 1 implements GET readers only; launch/cancel routes wait for Stage 2.

| Method and route | Response / behavior |
|---|---|
| GET /health | backend readiness, version; no workload launch |
| GET /capabilities | actual CLI flags, schema versions, environment/import errors, telemetry mode and unsupported reasons |
| GET /strategies | current registry names and review status sourced separately from phase documentation |
| GET /scenarios | allowlisted scenario IDs, config/map/input hashes, capacity and effective settings |
| GET /scenarios/{id}/map | dimensions, grid origin/resolution, obstacles, zones, terminals, directed edges from loaders |
| GET /runs | cursor-paginated directory index; schema, provenance and availability states |
| GET /runs/{id} | original manifest + separate operational integrity metadata |
| GET /runs/{id}/metrics | original metrics values/version, units and availability; no new metric formulas |
| GET /runs/{id}/decisions | cursor, limit, decision_id/order_id/robot_id/selected filters; decision fields only |
| GET /runs/{id}/outcomes | cursor, limit, order_id/decision_id filters; outcome fields only |
| GET /runs/{id}/artifacts | safe server-issued artifact IDs, size/type/availability |
| GET /runs/{id}/artifacts/{artifact_id} | original bytes after resolved-root and symlink checks |
| GET /experiments and /experiments/{id} | spec, expected grid, present/missing/failed/unknown rows and provenance |
| GET /experiments/{id}/comparisons | original existing comparison or in-memory call to paired.py with integrity guards; no files written |
| GET /jobs and /jobs/{id} | app-owned job status, argv, wall time, exit evidence and artifact state |
| POST /jobs/preview | validated argv, effective settings/hashes, output destination and expanded grid; no launch or writes |
| POST /runs | single CLI launch with fresh server-owned destination |
| POST /experiments | original matrix CLI launch; new/resume/force intent explicit |
| POST /jobs/{id}/cancel | request termination of owned process group; not scientific pause |
| GET /jobs/{id}/logs | byte/event cursor, bounded page, truncation indicator |
| GET /jobs/{id}/snapshot | latest copied observation, sequence and age, or explicit unavailable reason |
| WS /ws/jobs/{id} | versioned bounded events; see EVENT_SCHEMA.md |
| GET /runs/{id}/telemetry | proposed Stage 3 paginated sampled trace; unavailable without actual recording |

## Inputs and command mapping

Single request fields: scenario_id -> --config; strategy -> --strategy; orders -> --orders; robots -> --robots; optional seed -> --seed; window -> --window; quantum_s -> --quantum; speed_mps -> --speed; progress_interval_s -> --progress; export -> --export. Dataset directory is server-owned and always supplied for an app run. Quiet mode is an explicit log preference, not assumed silence. CSV selection uses a validated input_id -> absolute --orders-file; upload is deferred to Stage 5, with the exact five-column application contract and existing map-aware loader. No arbitrary browser paths.

Experiment request fields: name, strategies, robots list, seeds list, orders, jobs, workload_orders, workloads, export, mode=new|resume|force. Map to the identically named verified CLI flags; mode adds at most one of --resume/--force. Server controls --out. No experiment quantum/window/speed/config/orders-file fields accepted. Show effective default config read-only. Default jobs=1 is a proposed app resource bound, distinct from the runner default. Main-study presets are 10/30/50/70/100, seeds 1–30, 2,000 orders; never auto-launch that preset.

Validation: reject unknown fields and strategies, nonfinite numbers, negative counts, invalid windows, duplicate/empty lists, fleet sizes above loader capacity, path separators in names, arbitrary executable/output paths. Omitted/0 CLI sentinel values resolve explicitly in preview. Use argv arrays, shell=False, fixed cwd/env, approved interpreter, one active top-level job. Recheck inputs and capabilities at launch; preview includes a hash/fingerprint and changed inputs produce 409 instead of silently launching different science. Bound parallel children and output volume.

Suggested common result envelope: api_schema_version=1, data, availability, warnings, source provenance. Lists add next_cursor; default limit 100, maximum 1000 (proposed). Stable cursor includes artifact fingerprint and row offset; changed files invalidate it. Unknown/missing values remain null with reason, never numeric zero. Dataset IDs are scoped to a directory: the same scientific run_id can occur at multiple paths, so an opaque app run ID maps to canonical directory plus fingerprint, not only run_id.

Errors: 404 unknown catalogue/object; 409 input drift, output collision, busy job or incomplete/incompatible comparison; 422 invalid input/schema; 503 missing environment/import. Legacy data can still be listed with warnings. Unsupported metrics are unavailable; no conversion of old fleet makespan into order makespan. No live order list route is promised from CLI text: Stage 3 may add paginated copied order state; until then only observed counts and historical tables are available.

## History, comparison and security

Index recursively within approved roots, tolerate partial files and retry stable reads. Store canonical directory, artifact size/mtime/hash, schema versions, source git state and validation warnings in app SQLite. Reading never creates experiment.json, repairs summary.csv or changes artifacts. Exclude symlinks escaping roots; never trust run.json paths or summary run_dir as download authorization. Reject malformed files with diagnostic metadata and bounded reads.

Use original paired.py math; compare_paired.load semantics preserve identity hashes as strings and convert numeric metrics for the existing float-only selection. Invoke check_complete, check_settings and paired_differences; report metric-specific actual n as well as grid n. Do not invoke compare_paired.main or plot_experiment.main from GET: both write outputs. Existing plots can be served unchanged. Later requested derived exports belong in app-owned output, separate from historical data.

Bind 127.0.0.1; same-origin UI, strict loopback Host/Origin checks for mutations and WebSockets, no wildcard CORS, session/CSRF protection for state changes. Only manage app-owned processes; PID alone is insufficient after restart, record process start identity and reconcile to interrupted/unknown if ownership cannot be proved. No arbitrary process-kill API. Retain cancellation evidence and partial artifacts. GET never launches simulations or rewrites datasets. Link the existing static docs/RND_SYSTEM_EXPLORER.html read-only.

Jobs: queued -> starting -> running -> completed|failed; running -> cancel_requested -> cancelled only after process exit, with interrupted/unknown for uncertain restart cases. Keep process status, artifact completeness and scientific validity as separate fields. Exit 0 plus validated expected outputs is required for app success, not filename presence alone. Cancelling an experiment must cover its children via its owned process group. Resume reruns missing/failed runs, never resumes a simulation clock.
