# MRTA Warehouse Control Center
## Final architecture, phased delivery, and copy-ready Codex tasks

**Plan date:** 2026-10-27  
**Target repository:** `~/rnd_mrta_ilp_ws`  
**Working branch reported by user:** `traffic/rmf-integration`  
**Decision:** Headless first, ROS 2/Gazebo later, localhost browser only.  
**Status:** Specification only. No repository modifications or local simulations performed to prepare this plan.

## 1. Mission and scope

Build an operational research GUI for the existing *Outcome-Aware Inductive Logic Programming for Explainable Grid-Aware Task Allocation in Multi-Robot Warehouse Systems* project.

The GUI must allow a researcher to select a scenario, configure and start an existing headless MRTA run, see live observations, inspect robots and orders, manage reproducible experiment matrices, investigate run history, view metrics, examine raw decisions and realized outcomes, replay available timestamped observations, and export authentic research artifacts.

Initial scope: local web dashboard and headless execution. ROS 2 `/mrta/*` and Gazebo integration are intentionally deferred. ILP learning, new warehouse physics, new strategy semantics, and scientific simulator fixes are also **out of scope**. Represent unimplemented ILP features with transparent disabled states instead of simulation or fabricated outputs.

### Out of scope for v1

- Modifying or replacing `FleetSimulationEngine`, allocation policies, reservation logic, queue semantics, directed A*, charging logic, clock calculations, metrics, or experimental integrity rules.
- Adding priority/deadline/order fields that are not supported by the current CSV schema.
- Running Gazebo or ROS 2, live external robot control, multi-user access, remote access, shell execution from the browser.
- Live scientific pause/resume, manual robot teleoperation, editing active robot assignments, or changing strategies in the middle of a run.
- Claiming full-fidelity frame-by-frame replay unless a lossless trajectory record is actually captured and validated.

## 2. Source grounding and points Codex must verify

The attached **RND_SYSTEM_EXPLORER(1).html** is an offline repository snapshot dated 2026-10-27, not a live repo access. It contains a source inventory for branch `traffic/rmf-integration` (snapshot HEAD `aaf0ab97e8...`) and historical example data. The user's separately reported working tree has many modified and untracked files, plus one unpushed commit. Treat the working tree on the actual computer as authoritative.

| Existing authoritative source | Use in GUI |
| --- | --- |
| `src/simulation_env/simulation_env/fleet_engine.py` | The single exact `Fraction` simulation clock; observation only. |
| `src/simulation_env/simulation_env/workload_executor.py` | Existing FIFO, dispatch, progress, status snapshot, result production; observation only. |
| `src/simulation_env/scripts/run_workload.py` | Headless single-run entrypoint. |
| `src/simulation_env/scripts/run_experiment.py` | Experiment matrix runner, safe overwrite/resume behavior. |
| `src/simulation_env/simulation_env/dataset.py` | Provenance and separate decisions/outcomes datasets. |
| `src/simulation_env/simulation_env/run_metrics.py` | Final authoritative run metrics, current schema version. |
| `src/simulation_env/simulation_env/paired.py` and `src/simulation_env/scripts/compare_paired.py` | Statistical pairing and integrity checks. |
| `src/mrta_core/mrta_core/warehouse_map_xlsx.py` and `warehouse_routing.py` | Authoritative XLSX geometry and lane direction. |
| `src/simulation_env/maps/W100.xlsx` | Initial selectable warehouse map. |
| `src/task_management/task_management/allocation_strategy.py` | Registry of currently implemented baseline strategies. |
| `docs/EXPERIMENT_PROTOCOL.md` and `docs/DECISIONS.md` | Research definitions and invariants; existing local versions take precedence. |
| `docs/RND_SYSTEM_EXPLORER.html` | Existing static research explorer. Link to it without rewriting it. |

The snapshot shows eight named registered baseline strategies: `random`, `nearest_manhattan`, `nearest_astar`, `earliest_finish`, `multi_criteria`, `auction`, `hungarian`, and `milp`. Codex **must read the live registry** rather than hardcoding this list. It also shows that recent work on Phase 7 strategies and experiment integrity is still unstaged. Do not infer these changes are ready or committed.

The W100 map in the snapshot is 40 by 54 cells. The renderer must use the loaded map dimensions instead of embedding 40 by 54 in logic.

The active order file documented in the snapshot has five core fields: `order_id,item_id,shelf_id,quantity,dropoff_id`. Do not present release times, priorities, or deadlines as supported upload columns until the repository itself implements them.

Before selecting dependencies or command-line flags, check the actual source, `pixi.toml`, README, `AGENTS.md`, `CLAUDE.md`, `run_workload.py --help`, and `run_experiment.py --help`. Do not guess argument spellings or assume flags such as `--seed` apply to single-run mode.

## 3. Primary user journeys

**Single headless run:** Open control center, select validated scenario and real registered strategy, set only runner-supported arguments, preview the effective command and config, run, see status/logs/summary telemetry, inspect robots and order progress, and open authoritative metrics/artifacts on completion.

**Scientific experiment:** Select registry strategies, fleet sizes, workload-seed set, and supported workload/quantum conditions. Preview number of runs. Launch the existing experiment runner with an immutable manifest. Watch current run and experiment progress. Resume according to existing integrity semantics. Show missing, failed, stalled, and cancelled conditions distinctly. Compare via existing paired engine, not new web formulas.

**Past run review:** Open persisted history index, view `run.json` provenance, `metrics.json` schema, `decisions.parquet`, `outcomes.parquet`, logs and optional telemetry trace. Filter large tables server-side. Export existing artifacts. Allow real timestamped replay if recorded, otherwise clearly state it is unavailable or sampled.

**Explain allocation:** Display the recorded candidate set and selected robot-order pair with recorded decision-time features, then link to the realized outcome in a **separate panel**. Do not introduce future outcomes into the pre-decision view.

## 4. Product modules and v1 priorities

| Module | v1 treatment |
| --- | --- |
| Overview | Active job, selected scenario, fleet/orders progress, warnings, recent events. Real values only. |
| Live warehouse map | Loaded XLSX cells, zones, directed lanes and latest observed robot states; sampling status displayed. |
| Robot fleet | Robot ID, status, grid cell, current order, recorded battery SOC, last observed sim time; drill down. |
| Orders | FIFO queue and lifecycle counts, searchable orders; run-scoped data. |
| Run control | Validated headless launches, dry-run argv preview, log/status stream, cancel. |
| Experiment lab | Deterministic experiment matrices using existing CLI, progress, resume integrity. |
| Metrics and comparisons | Render authoritative schema v2 metrics and existing paired comparisons with completeness banners. |
| Run history | Search/filter local run artifacts, provenance, exports, optional replay. |
| Decision inspector | Pair features and decision/outcome separated by time. |
| Logs & diagnostics | Live stdout/stderr, stack traces, process exit, artifacts, connection state. |
| Settings | Read-only active environment, tool capabilities, registered strategy names, app-local preferences. |
| ILP and research explorer | Clearly marked not implemented for learned policy; link existing static explorer. |

### Critical UI distinctions

- `Live`, `replaying recording`, `stale/disconnected`, `completed`, `partial/incomplete`, `failed`, and `cancelled` are visibly distinct.
- Display both `sim_time_s` and `elapsed_wall_s` where available. Never call wall time makespan.
- Use actual recorded metrics, units, precision and schema version. `order_makespan_s` must not be silently replaced with legacy `makespan_s` or `fleet_recovery_time_s`.
- Unknown data is `unavailable`, not zero, not estimated, and never an invented value.
- Progress seen live can be provisional, final metrics must come from `metrics.json` and existing artifact readers.
- Charts must link each point to provenance, run ID, strategy, robot size and workload seed. Pairing must pass existing completeness and settings checks.

## 5. Target architecture

```text
Local Browser (127.0.0.1 only)
  React + TypeScript + Vite
     | GET/POST JSON, WebSocket
     v
FastAPI Control Center (127.0.0.1 only)
  - scenario and strategy catalogue, validated from code
  - API input contracts, preview, whitelist and local-only safeguards
  - one-instance headless job manager, subprocess process groups
  - WebSocket relay and reconnectable snapshot
  - read-only experiment and artifact readers
  - SQLite index for operational metadata ONLY
     |                        ^
     v                        | logs / optional telemetry
Existing run_workload.py or run_experiment.py
     |
     v
Existing simulator: engine -> executor -> allocator -> A* -> battery
     |
     +--> Existing artifacts in runs/*, including run.json / metrics.json
     |     / decisions.parquet / outcomes.parquet
     +--> Optional non-invasive read-only state observation
```

Place new code in isolated directories unless the audit justifies a different convention:

```text
apps/control_center/
  backend/
    control_center/
      app.py
      api/
      adapters/
      services/
      schemas/
      storage/
    tests/
    pyproject.toml
  frontend/
    src/
    package.json
  .gitignore
  README.md
docs/control_center/
  BASELINE_INVENTORY.md
  API_CONTRACT.md
  EVENT_SCHEMA.md
  SCIENTIFIC_PARITY.md
  OPERATIONS.md
```

If the repository already has a suitable package/workspace convention, prefer it and document the deviation. Do **not** add new ROS packages for the initial headless dashboard.

## 6. Suggested API contracts (proposed, not existing endpoints)

REST, under `/api/v1`:

```text
GET   /health
GET   /capabilities
GET   /strategies
GET   /scenarios
GET   /scenarios/{id}/map
GET   /jobs
GET   /jobs/{id}
GET   /jobs/{id}/snapshot
GET   /jobs/{id}/logs?cursor=...
POST  /jobs/preview
POST  /runs
POST  /experiments
POST  /jobs/{id}/cancel
GET   /runs
GET   /runs/{id}
GET   /runs/{id}/metrics
GET   /runs/{id}/decisions?cursor=...&limit=...
GET   /runs/{id}/outcomes?cursor=...&limit=...
GET   /runs/{id}/artifacts
GET   /runs/{id}/artifacts/{safe_artifact_name}
GET   /experiments
GET   /experiments/{id}
GET   /experiments/{id}/comparisons
WS    /ws/jobs/{id}
```

Keep read routes read-only. Support pagination for potentially tens of thousands of orders and millions of decisions. Use catalogued IDs, strict schema validation, allowlisted CLI choices, and safe filenames. Never interpolate raw shell commands, accept browser-supplied executable paths, or allow arbitrary filesystem downloads. Use `subprocess.Popen([...], shell=False, cwd=verified_repo_root, ...)` with the vetted Python environment.

**Streaming event envelope (proposed v1):**

```json
{
  "schema_version": 1,
  "job_id": "job_example",
  "sequence": 42,
  "type": "fleet_snapshot",
  "source": "simulator_observer",
  "sim_time_exact": "128/3",
  "sim_time_s": 42.6666666667,
  "observed_at_utc": "2026-10-27T16:00:00Z",
  "payload": {"robots": [], "orders": {}}
}
```

Important: this is an interface proposal, **not a claim that these existing source files already emit such events**. An adapter must verify available callbacks and extract correct values. Use monotonic sequence per job. Reconnect retrieves `GET /jobs/{id}/snapshot`, then streams events newer than that sequence. Display dropped or missing sequence indicators. High-rate animation samples may be reduced, but irreversible science-critical decision and outcome evidence must remain in the existing original artifacts.

**Operational data in SQLite:** `jobs`, `job_events`, `artifact_index`, `ui_preferences`. This is an index of jobs, not a new source of scientific truth. The `runs/` tree and its manifests stay authoritative. Never rewrite completed run artifacts to satisfy dashboard formatting.

**Replay:** Timestamped positions must come from actual captured state. If only sampled snapshots exist, label it `sampled playback` and show sampling cadence. Do not rerun the allocation strategy to recreate a historical path. Visual interpolation must not affect experiment results.

## 7. Repository preservation and security contract

Before any stage, inspect the **current** `git status --porcelain=v1`, `git diff --name-status`, `git diff --cached --name-status`, `git branch --show-current`, and `git rev-parse HEAD` and keep a short baseline file under new dashboard documentation.

The user reported a dirty `traffic/rmf-integration` branch, ahead of origin by one commit, including preexisting modified files under `docs/`, `.scratch/`, `src/simulation_env/`, and `src/task_management/`, and untracked `.codex/`, `AGENTS.md`, `docs/RND_SYSTEM_EXPLORER.html` and new tests. The **current working tree is sacrosanct**.

No `git reset`, `git clean`, `git stash`, `git checkout`, `git switch`, `git rebase`, `git merge`, `git cherry-pick`, or `git restore`. No commits or pushes. No mass formatter. Never overwrite or revert existing changes, and never alter a tracked file unless stage scope explicitly requires it and its existing diffs were inspected. Prefer additive files under `apps/control_center` and `docs/control_center`. If a small additive observation hook is later indispensable, stop and submit a proposed patch for review rather than silently editing the scientific engine.

Bind to `127.0.0.1`, do not open firewall ports. Validate loopback Host/Origin for state-changing requests and WebSockets. Avoid permissive CORS. Do not persist machine secrets, personal paths in distributed exports, or arbitrary process controls. Default to a **single active job** and explicit run output directory. No Docker requirement for v1.

## 8. Scientific parity and integrity gates

1. Same effective CLI arguments, authoritative scenario/config, registered policy, workload input and seed if supported.
2. Headless CLI baseline versus app-launched run: compare deterministic simulation metrics, decisions, outcomes, selected assignments, sim time, charging/traffic counters. Use exact values or a documented serialization-aware canonical comparison; exclude inherently environment-dependent CPU/wall timing measurements from exact equality, but report their overhead separately. Ignore job IDs, process timestamps, app logs and absolute artifact paths.
3. Prove that activating the observer does not change any decision, event ordering, simulated timestamps, final state, or deterministic outcome metric. A unit-level focused fixture first. Treat allocator CPU and other host-computation timings as measurable overhead, not deterministic equality targets. Escalate to long scenarios only when the user runs them.
4. Preserve decision-time versus outcome-data separation. Web/API may *join* for presentation but must not add future outcome fields to decision records.
5. Preserve exact rational time where recorded. Frontend float values are only for display.
6. No changes to `run_metrics.py`, `paired.py` statistical formulas, seed pairing, or experiment integrity behavior. UI calls these original routines.
7. Experiment status must reflect missing/failed/cancelled/stalled runs. `--resume` and `--force` keep their existing rules; never silently overwrite an experiment.
8. A sampled display can skip intermediate positions. It cannot be called an exact replay.
9. The observer must be nonblocking and never affect the simulation event queue. If the required hook cannot be implemented safely, show live logs/progress first and defer the spatial overlay.
10. For all stages, Codex runs only build, lint, import, and small focused tests. The user runs full 100-robot and multi-seed tests.

## 9. Stage breakdown and Codex prompts

Run these prompts **in order**. Give Codex the output of the previous stage before authorizing the next. Each stage must stop on completion and report files, checks, remaining risks, and git status. No autonomous progression to later stages.

### Stage 0. Source audit and frozen design, docs only

**Expected time:** 0.5 to 1 focused day.  
**Deliverable:** Source-grounded executable plan and interfaces. No GUI code.

**COPY TO CODEX:**

> You are in `~/rnd_mrta_ilp_ws`, branch expected `traffic/rmf-integration`. This is Stage 0 of the MRTA Warehouse Control Center. **Read and comply with `AGENTS.md`, `CLAUDE.md`, and repository instructions first.**
>
> The repository contains important uncommitted work. Do not stash, clean, reset, restore, switch branches, commit, push, or overwrite it. Record HEAD, branch and exact git changes without changing them. Inspect the real source and any existing local diffs in files you read. Treat existing scientific engine/allocator/traffic behavior as frozen.
>
> Audit `run_workload.py`, `run_experiment.py`, `FleetSimulationEngine`, `WorkloadExecutor.snapshot`, existing progress/event callbacks, `dataset.py`, `run_metrics.py`, `paired.py`, warehouse XLSX loader and active strategies registry. Read `docs/EXPERIMENT_PROTOCOL.md`, `docs/DECISIONS.md`, `pixi.toml` and both runner CLI parsers. Determine real supported flags, current metric schema, available snapshots, run artifacts and safe instrumentation points. Pay attention to experimental integrity and exact `Fraction` sim time.
>
> Add only new files under `docs/control_center/`: `BASELINE_INVENTORY.md`, `API_CONTRACT.md`, `EVENT_SCHEMA.md`, `SCIENTIFIC_PARITY.md`, `STAGED_PLAN.md`. Propose headless-first, localhost-only FastAPI+React architecture. Mark fields and routes as proposed. Design a non-invasive observation path without inventing signals, new sim events or calls that advance the clock. Document unresolved constraints explicitly. Check if running headless too quickly requires recorded sampled replay to be useful. Document how the dashboard indexes existing `runs/*` without editing them.
>
> Do **not** implement UI/backend, run long simulations, or modify core source in Stage 0. At end report source paths/symbols and exact usage, changed/new files, lightweight checks performed, risks, and `git status`. Stop and wait for my review.

**Acceptance:** all run flags and telemetry contracts are verified against current code; new docs only; working tree remains intact.

### Stage 1. Read-only backend and history index

**Expected time:** 2 to 3 focused days.  
**Deliverable:** API can display genuine scenarios, registered strategies, previous runs and safe artifacts; **cannot launch anything yet**.

**COPY TO CODEX:**

> Implement only Stage 1 of `docs/control_center/STAGED_PLAN.md`. First recheck `git status`, `AGENTS.md`, Stage 0 contracts and existing repo diffs. Work in new isolated `apps/control_center/backend` files and scoped tests. Do not edit scientific modules or existing experiment scripts.
>
> Create a local FastAPI backend with versioned REST contracts and Pydantic validation. Bind startup by default to `127.0.0.1`. Implement `/health`, `/capabilities`, `/strategies` from the live registry, `/scenarios` and map from the authoritative XLSX loader/routing rules, and read-only `/runs` plus individual manifests, schema-versioned metrics, decisions/outcomes queries and safe artifact listings/downloads. Index runs with an app-owned SQLite database, but treat `run.json`, `metrics.json`, `decisions.parquet`, `outcomes.parquet` and experiment manifests as authoritative. No write into historical runs.
>
> Support pagination and missing/partial/legacy artifacts. Reject path traversal, symlinks escaping permitted data roots, arbitrary downloads, malformed schema and invalid IDs. Distinguish incomplete experiment status. Do not normalize or silently calculate research metrics in the API.
>
> Write tests with small synthetic fixtures only: valid/invalid manifests, missing artifacts, metric schema, pagination, path traversal, map read, registry. Document startup and package setup without editing the root environment lockfile without permission. No job-launch endpoints yet. Run import/lint and small unit tests only, report changes, stop.

**Acceptance:** genuine read-only history, map and strategies renderable from localhost API; no modified scientific files.

### Stage 2. Headless process management and run launch

**Expected time:** 2 to 4 focused days.  
**Deliverable:** validated single headless runs, experiment dispatch integration, cancellation and log/status stream.

**COPY TO CODEX:**

> Implement only Stage 2. Preserve dirty git tree, inspect Stage 0 verified CLI arguments, and never change scientific simulation behavior. Create a bounded local JobManager for a **single active headless job**. Build exact `argv` arrays for the existing `run_workload.py` and `run_experiment.py` and show a dry-run preview before launching. Use `shell=False`, explicit cwd/env and validated strategy/map/config/robot count/order count/seed/quantum only when current CLI supports each field.
>
> Add `POST /jobs/preview`, `POST /runs`, `POST /experiments`, `/jobs` status, logs with cursor, cancellation, and status reconnection. Use robust process-group lifecycle, output directories and atomic state transitions: `queued`, `starting`, `running`, `completed`, `failed`, `cancel_requested`, `cancelled`, `interrupted`/`unknown`. Reconcile app restarts. Cancellation first sends a graceful signal then a bounded kill of the **owned process group only**; never mark partial artifacts successful. Existing experiment `--resume` and `--force` safeguards remain in charge.
>
> Add local-only Host/Origin safeguards and WebSocket support for job status/logs. Do not open unrestricted shell, arbitrary executable, arbitrary write directory, or network bind. Do not add live motion telemetry yet. Test a fake subprocess and a very small real CLI invocation only if the test can finish quickly and has a disposable output dir; otherwise leave substantive end-to-end test for me. Stop, report parity limitations and git status.

**Acceptance:** GUI-equivalent command is visible, runnable, cancellable, and always leaves existing experiment data safe; no simulator math changes.

### Stage 3. Non-invasive telemetry and digital-twin data

**Expected time:** 3 to 5 focused days.  
**Deliverable:** trustworthy live state or explicit fallback; timestamped sampled replay where supported.

**COPY TO CODEX:**

> Implement only Stage 3. Audit available callbacks and `WorkloadExecutor.snapshot()` from the current repo. Build a read-only bridge or standalone observer wrapper that calls the **unchanged** engine/executor code and exposes real robot cell, state, SOC, current order, counts and simulated timestamp. Prefer existing public observation callbacks and avoid editing `FleetSimulationEngine`/allocator. If safe integration would require touching core algorithms, STOP and propose a minimal patch first.
>
> Publish a versioned envelope with per-job sequence, event type, exact sim time text plus display float, UTC observation time and payload. Ensure observation does not schedule events, consume randomness, create extra worker decisions, alter order state, block the event loop or introduce future data into decision features. Provide backpressure: bounded sampled snapshots for visualization, persisted true artifacts for decisions/outcomes, and explicit dropped-frame visibility. Store optional `telemetry.ndjson` separately, with clear sample provenance and partial-run handling. Map data comes from existing XLSX/routing functions, never a second geometry parser.
>
> Provide `/jobs/{id}/snapshot`, `/ws/jobs/{id}`, and read-only telemetry/replay retrieval. A fast headless run may finish before live frames are visually meaningful: support `record then replay`, with client-side playback speed independent of simulated time. Do not call a sampled replay lossless. Write small deterministic parity tests: same chosen assignments, exact sim timestamps and deterministic outcome metrics with versus without observer. Report rather than demand equality for host CPU/wall timings. If reliable motion telemetry cannot be extracted without core modification, ship live logs/progress instead and report the gap. No long fleet tests. Stop.

**Acceptance:** source-true robot observations and parity, or an explicit documented narrower fallback. No fake motion.

### Stage 4. React GUI and daily operational workflows

**Expected time:** 3 to 5 focused days.  
**Deliverable:** functional responsive local browser app and clear run dashboard.

**COPY TO CODEX:**

> Implement only Stage 4 in `apps/control_center/frontend`. React + TypeScript + Vite, clean dark UI, responsive, accessible, with no externally hosted fonts and no outside analytics. Integrate the real Stage 1-3 API without hardcoded operational KPI values. Build sidebar: Overview, Live Warehouse, Fleet, Orders, Run Control, Experiment Lab, Metrics, History, Decision Inspector, Logs, Settings, Research Explorer.
>
> Overview shows genuine active job / status / time / progress and last errors. Map renders authoritative XLSX cells, directed lanes, semantic stations and most recent robot positions with pan/zoom, selected robot and optional observed path. Fleet table supports search and filtering, SOC/status/position/order. Orders table supports pagination and lifecycle. Run Control shows registered policies and only CLI-validated fields, preview argv, run/cancel and live logs. Every panel clearly distinguishes live, replay, disconnected, stale, incomplete and unavailable data.
>
> For unavailable ILP learning, show `Not implemented in repository`, not a simulated classifier. Link the existing static System Explorer without editing it. Preserve keyboard controls and sensible loading/empty/error states. Keep visual interpolation read-only. Unit-test high-risk UI state transitions and API validation; do not run full simulation. Stop and report.

**Acceptance:** browser can actually launch and monitor a headless run; no static example numbers masquerade as data.

### Stage 5. Experiment matrix, research analytics and reproducible history

**Expected time:** 3 to 5 focused days.  
**Deliverable:** headless research workbench with rigorous comparisons.

**COPY TO CODEX:**

> Implement only Stage 5. Complete experiment-matrix form using exactly the current CLI capabilities. Select registered strategies, supported fleet sizes, workload seeds, optional dispatch quantum and existing reference/seeded workload modes. Preview expanded grid, expected run count, command, manifest and storage. Launch through Stage 2 job manager with original `run_experiment.py`. Preserve workloads and experiment manifests and safe resume/force behavior. Show queued/running/completed/failed/missing/cancelled runs and expected versus actual pairs. Never silently drop a failed seed or use incomplete samples without a clear warning.
>
> Add run history searches, provenance view, data inspection for `decisions.parquet` and `outcomes.parquet` with separate temporal meaning, decision inspector and links to outcomes. Render only metrics obtained from existing `metrics.json`; label metric units and schema, keep order makespan vs fleet recovery separate, and show computation time separately. Use `paired.py`/`compare_paired.py` for paired differences and 95 percent confidence intervals; do not reimplement math in JavaScript. Reuse existing `plot_experiment.py` outputs when appropriate. Exports must come from trusted run artifacts. Show historical sampled replay when available, otherwise state unavailable.
>
> Support selecting a validated uploaded orders CSV **only if** the current runner already supports the required immutable path and loader contract. Use `order_id,item_id,shelf_id,quantity,dropoff_id`, validate against current warehouse, hash and copy to a run-scoped safe directory. Otherwise defer upload visibly, do not mutate committed `config/orders.csv`.
>
> Tests: paired comparison validity, incomplete warning, mismatched workload hash, wrong schema, artifact listing, pagination, provenance and a small matrix planning fixture. No 100-robot or 30-seed execution by Codex. Stop.

**Acceptance:** a researcher can identify the provenance and validity of each plotted data point and each comparison.

### Stage 6. Integration QA and local release

**Expected time:** 2 to 3 focused days.  
**Deliverable:** one-command localhost start, documentation, safety/parity evidence, handoff.

**COPY TO CODEX:**

> Implement only Stage 6. Audit all dashboard changes. Run targeted imports, linters, frontend type check/build, backend API tests, mocked job lifecycle tests, tiny source-true parity fixtures and a local startup smoke check. Verify that no scientific engine code, metric formulas or paired statistics have been modified. Verify no preexisting working-tree files were overwritten, and no experiment results silently changed.
>
> Add a one-command local launch script and concise `apps/control_center/README.md` plus `docs/control_center/OPERATIONS.md` explaining installation, `127.0.0.1` access, API/GUI startup, paths, allowed operations, failure recovery, data integrity and where source run metrics originate. Include known unsupported ILP/ROS/Gazebo capabilities and how to diagnose missing live telemetry. Make default application read-only until a deliberate run action. Stop, show final git status, file list, small test results and exact manual larger-scale test plan for me. No commits, pushes, network exposure, long simulations or staged additional phase.

**Acceptance:** functional local dashboard, documented limitations, and no changes to science; user runs the complete larger-scale validation.

### Deferred Stage 7. ROS 2 / Gazebo connector (not authorized now)

**Potential later scope:** listen to `/mrta/robot_states`, `/mrta/fleet_status`, `/mrta/events` through a separate ROS adapter; optionally embed Gazebo visuals or mirror controlled ROS launch processes. Explicitly separate simulation authority from Gazebo mirror. Do not implement during Stages 0-6.

## 10. High-risk failure modes and defenses

| Failure | Required defense |
| --- | --- |
| GUI run changes MRTA decisions | Canonical parity fixture and no extra engine events. |
| UI reports wall-clock makespan | Separate elapsed_wall and exact simulated duration. |
| Experiment quietly omits stalled seeds | Completeness and pairing gate with explicit missing rows. |
| UI estimates battery rather than ledger | Read only authoritative ledger/snapshot field; absent means unknown. |
| Frontend assumes priority/deadline in CSV | Use current five-column schema; no invented supported inputs. |
| Fast sim completes between display frames | Persist/stream observations and offer honest sampled playback. |
| CSV upload overwrites reference dataset | Validate, hash, copy to isolated run directory; never edit committed file. |
| Cancel leaves an experiment marked success | Explicit cancelled/incomplete state, original artifacts untouched. |
| File download reads outside runs | Resolve canonical allowed roots, reject traversal and escaping symlinks. |
| App restarts and loses active process | Reconcile state, do not kill or mark success without evidence. |
| Different quantum/seed/config merged into one plot | Group by settings signature and fail scientific comparison when incompatible. |
| Source dirty work accidentally replaced | Create isolated files only, no clean/reset/stash/commit/push. |

## 11. User-run validation after Codex stages

Codex performs small tests only. User manually validates, in order:

1. One tiny headless run started from CLI and the equivalent dashboard runner, compare scientific files after canonicalization.
2. One W100 sample run with about 10 robots and a small order prefix, monitor and replay.
3. One 30-robot workload, check live map performance, battery/traffic states and safe cancellation on a separate disposable run.
4. One 100-robot headless workload, inspect memory, websocket responsiveness and telemetry sampling, without slowing simulation semantics.
5. A small paired matrix using `nearest_astar`, `hungarian`, `milp` and 3 workload seeds; validate run grid, workload hashes, incomplete conditions and paired 95% CI.
6. Later, the approved scientific 30-seed experiment; do not mix incomplete and complete comparisons.

Choose concrete counts and scenario variants from the **current** repository constraints, not this document's illustrative examples. The final R&D submission remains focused on MRTA and outcome-aware ILP, not on the GUI itself.

## 12. Definition of done

- Fully local app on `http://127.0.0.1:<configured_port>` with no LAN exposure.
- Accurate startup, command preview, run tracking, cancellation, original artifact preservation.
- Map and robots driven by authoritative state or honestly flagged availability.
- Discoverable run history, metrics, decision/outcome inspection, integrity-aware paired comparisons.
- Explicit provenance and scientific parity evidence.
- Read-only observer never drives allocation, routing, traffic, battery or clock.
- No edits to preexisting uncommitted work. No commits or pushes.
- Tests and runbook completed. ROS/Gazebo and ILP integration left for separate approved phases.
