# Dashboard source and operator audit — 2026-10-28

Scope: `/home/ibrahim/rnd_mrta_ilp_ws/src/dashboard_GUI`, operational evidence, and research-worktree compatibility. No production/test/source edits, commits, campaigns, launcher start/verify/smoke, or existing-service stops. Fresh tests used temporary fixtures; frontend build output is ignored. Final `git status --short src/dashboard_GUI` was empty. No listeners on 8000/5173 at initial or final inventory; no matching GUI/scientific runner processes at initial inventory.

## Current delivery and workspace selection

The full GUI is in `/home/ibrahim/rnd_mrta_ilp_ws`, branch `traffic/rmf-integration`, HEAD `ae832f5d445b84683050365b2a239d950ca4111d` at audit. Stages 1–6 are implemented:

| Stage | Delivered |
|---|---|
| 1 | Read-only local catalogue, map and typed history readers; separate read-only entrypoint remains |
| 2 | Original CLI preview, supervised launch/cancel, durable jobs/events/logs, restart reconciliation |
| 3 | Explicit observed single-run wrapper, actual copied fleet state, bounded recordings and transport |
| 4 | React Operations, Run history and System screens; actual map and sampled recording playback |
| 5 | Experiment matrix/library, guarded original paired analysis, decisions/outcomes inspector, validated CSV inputs |
| 6 | One-command local startup, tests/build/parity/proxy/shutdown QA, operations handoff |
| 7 | ROS/Gazebo dashboard adapter remains deferred |

The research worktree `/home/ibrahim/rnd_mrta_ilp_research` is on `research/outcome-aware-ilp`, tracked M0 HEAD `163c6a473aa1673176243a8a03a3bde5b0b8540b`, plus current dirty/untracked M1 work. Its dashboard directory contains plans/contracts only, with no backend, frontend or launcher. It is not a second working GUI.

There is **no workspace selector**, supported root environment variable, `--workspace` flag, or stop subcommand. `ROOT`/`REPO` derive from the source paths (`scripts/launch_control_center.py:19`, `adapters/catalogue.py:6`, `adapters/observed_runner.py:16`). Calling the ws launcher after changing directory to research still runs ws. Do not invent `MRTA_ROOT`, `MRTA_WORKSPACE` or `DASHBOARD_DATA_DIR` instructions.

Backend defaults (`app.py:40`): history=`REPO/runs`, app data=`REPO/src/dashboard_GUI/var`. Python callers can supply `create_app(history_root=..., data_dir=...)`, but the operator launcher exposes no such options and the scientific command planner still uses its source-derived workspace.

The only allowlisted scenario is W100, from `src/simulation_env/config/warehouse.yaml`. Fresh loader read: 40×54, capacity 100, resolution 1 m, origin [0,0], 4,244 directed edges including terminal access/retreat. Current defaults: speed 1.5 m/s, shelf/dropoff service 1 s, fleet-size candidate window, q=0, reference `config/orders.csv`, configured seed42.

## Screens and limitations

- **Operations:** registered strategy and run form; observed or original CLI mode; review preview then launch; selected job/cancel; real SVG W100 with lanes/pan/zoom/fit; captured robot cells/status/SOC/orders/counters; exact Fraction clock; recording playback; logs/follow/copy; original metric cards.
- **Experiments:** strategies × fleet × seed setup; seeded/reference workload source; order limit and generated workload size; 1–4 children; exports; explicit new/resume/force; actual expanded-grid/command/destination preview. App/Repository library shows present/missing/invalid/outside-grid evidence. Original paired analysis shows baseline, metric, actual/expected n, means, differences, paired 95% t intervals and percentages.
- **Run history:** App vs Repository; original manifests/metrics/provenance/downloads; associated observation; separate paginated Decisions and Outcomes. Decision filters: decision/order/robot/selected; outcome filters: decision/order. UI pages contain 50 rows in physical order and exact strings.
- **System:** backend status/version, capabilities, registry implementations, input hashes/effective configuration. Registration does not imply scientific approval.

Registered strategies: auction, earliest_finish, hungarian, milp, multi_criteria, nearest_astar, nearest_manhattan, random.

Unavailable: scientific clock pause/resume; continuous trajectories/interpolation; matrix recordings; recordings for old CLI runs; remote access/public deployment; map editing; embedded plots; arbitrary command/path execution; matrix q/window/config/speed/custom-CSV overrides on this ws runner; learned ILP rules/explanations/training. MILP allocation optimization exists; inductive logic programming does not. Exported bk.pl/exs.pl are training material, not learned rules. Existing scientific Gazebo pose mirroring and RMF graph tooling do not make the dashboard native ROS/RMF integration.

Caveat: `/capabilities` still reports broad `replay.implemented=false` with stale reason “Outside Stage 2” (`api/routes.py:62`), while `useRecording.ts` implements captured-sample playback. Describe sampled playback explicitly; do not infer continuous replay or no playback from that stale flag. Earlier stage reports describe historical states; latest source/README/Stage6 describe current delivery.

## Tomorrow-ready operator walkthrough

### Readiness and start

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws
python3 src/dashboard_GUI/scripts/launch_control_center.py --help
python3 src/dashboard_GUI/scripts/launch_control_center.py check
python3 src/dashboard_GUI/scripts/launch_control_center.py start
```

Open `http://127.0.0.1:5173/` after “Dashboard ready.” API documentation: `http://127.0.0.1:8000/docs`; JSON `/openapi.json`; alternative `/redoc`. Keep the owning terminal open. Startup checks dependencies, builds TypeScript/UI and supervises backend8000 plus Vite production preview5173 on loopback. No package installation or simulation starts automatically. `start --dev` uses Vite development mode without the production build. A running instance intentionally fails `check`'s free-port check; that is a fresh-start check, not a running-service health diagnosis.

Requirements: Linux with /proc process identity, Python3, backend `.venv/bin/python` (requires >=3.11; existing 3.12), repository `.pixi/envs/default/bin/python`, Node>=22.12, npm and scoped frontend node_modules. Fresh Node:22.23.2. Existing science dependencies including pyarrow/scipy/map loaders must import. No ROS/Gazebo needed for this headless interface. Backend declares fastapi0.119.1, uvicorn0.38.0, websockets15.0.1; test extras httpx0.28.1 and pytest8.

Launcher sets absolute project PYTHONPATH, PYTHONDONTWRITEBYTECODE=1, PYTHONUNBUFFERED=1, PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 and removes inherited PYTHON*/NODE_* injection. Child science retains PATH/HOME/LANG/LC_ALL/TMPDIR plus explicit project Python paths. No root-selection environment variable is supported.

If frontend dependencies are missing, explicit recovery is `npm ci` inside `/home/ibrahim/rnd_mrta_ilp_ws/src/dashboard_GUI/frontend`. Restore established Python environments if imports fail; do not upgrade scientific solver locks merely to start the UI.

Supported two-terminal fallback:

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws
export PYTHONPATH="$PWD/src/dashboard_GUI/backend:$PWD/src/mrta_core:$PWD/src/simulation_env:$PWD/src/task_management"
PYTHONDONTWRITEBYTECODE=1 src/dashboard_GUI/backend/.venv/bin/python -m control_center.app
```

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws/src/dashboard_GUI/frontend
npm run dev
```

### Tiny single run and recording

Operations defaults: 2 robots, 3 orders, seed7, Nearest A*, Observed, reference workload. Preview -> review effective counts, seed/q/speed, command/destination/warnings -> Launch. Inspect completion/logs/metrics, select robot via row or map, then Play recording. Fast headless work can finish before the browser displays live movement. Pan/zoom/Fit/Lanes show the original geometry. Playback controls change display only.

Existing observed job `72bf65af9031466890148ba76ca0a97f` completed 3 orders: order makespan202s, fleet settled656/3s, distance528m, waiting0, with14 dropped observation samples. Scientific completion and partial recording are separate. Existing job `2e0a8559f1924b059448539f38255424` has854 samples and0 drops, useful for a saved presentation without generating new science.

Single-run seed controls allocation, not workload generation. Orders0 selects all available; a larger limit clamps to actual available orders. Custom CSV must have exact header `order_id,item_id,shelf_id,quantity,dropoff_id`, valid W100 stations, positive integer quantities and a common dropoff within each logical order; <=2MiB/50,000 line items. Upload stores read-only `var/inputs/<sha256>.csv`; reference remains unchanged. Browser reload clears selection; reuploading the same bytes reuses the hash. Fields invalidate preview; previews expire after600s, are one-use and are rechecked at launch/final validation.

### Small matrix and comparison

Experiments default example: nearest_astar/random, fleet2, seeds1/2, orders3, generated workload3, children1, seeded, new unique name. Preview should show4 rows and3 effective orders. Launch -> Operations for logs/cancel -> Experiments/Refresh studies/App for evidence. Compare paired runs with Nearest A* baseline. Existing `stage5_browser_20261028` had baseline mean163.667s, difference6.5s, CI[-50.678,63.678]s, n2/2: a plumbing check, not a research conclusion.

GUI reads original per-run artifacts and invokes simulation_env.paired in memory. Grid, settings, identities and shared workload hashes are guarded. Incomplete analysis requires explicit checkbox/banner and does not bypass mixed settings/workload mismatch. Missing metrics can reduce n. Negative order-makespan difference means earlier completion; throughput has the opposite direction. Unknown remains unknown. Legacy makespan_s is fleet settled time, never order makespan.

New requires fresh output. Resume is restricted to app-owned same-grid/source/input/generation/hash-safe studies and reruns missing/failed runs; no simulated-time resume. Force requires explicit acknowledgement/preview/launch and retains the original runner's archived directory plus previous_attempt evidence. Use new names for drift; do not use force to conceal it.

### Repeat exploratory pilot and render reports

Terminal studies need no app server. Choose GUI or terminal execution, never both simultaneously. Terminal processes are not adopted as app-owned jobs, although their artifacts appear after Refresh.

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws
STUDY="pilot_r50_o200_$(date +%Y%m%d_%H%M%S)"
STRATEGIES=(auction earliest_finish hungarian milp multi_criteria nearest_astar nearest_manhattan random)
PYTHONDONTWRITEBYTECODE=1 .pixi/envs/default/bin/python \
  src/simulation_env/scripts/run_experiment.py \
  --out src/dashboard_GUI/var/runs/experiments --name "$STUDY" \
  --strategies "${STRATEGIES[@]}" --robots 50 --seeds 1 2 3 \
  --orders 200 --workloads seeded --workload-orders 200 --jobs 1
.pixi/envs/default/bin/python src/simulation_env/scripts/compare_paired.py \
  --out src/dashboard_GUI/var/runs/experiments --name "$STUDY" \
  --baseline nearest_astar
src/dashboard_GUI/backend/.venv/bin/python \
  src/dashboard_GUI/scripts/render_experiment.py --name "$STUDY"
```

Expected24 runs, q=0 on ws. Its matrix CLI has no q override; do not edit warehouse.yaml secretly to create q=3. Keep source/config fixed. Paired CLI writes paired.csv/md from summary.csv; GUI reads individual metrics. Resolve stale/edited-summary differences rather than mixing analyses.

Renderer refuses incomplete studies, validates source artifacts and reuses original paired math. Each invocation creates fresh `var/reports/<study>/<UTC-id>/index.html`, data.json and PNGs. It preserves old scientific artifacts/figures. Dots are seeds; diamonds means; lines min–max, not CI. Paired95% t intervals remain separate. Completion graphs show every seed; individual-order distributions are descriptive.

Latest already-existing report, no rerun needed:

```bash
xdg-open /home/ibrahim/rnd_mrta_ilp_ws/src/dashboard_GUI/var/reports/pilot_r50_o200_20261028_123625/20261028T115307Z-a889c3/index.html
```

Earlier report: `/home/ibrahim/rnd_mrta_ilp_ws/src/dashboard_GUI/var/reports/pilot_r50_o200_s1-3_20261028/20261028T112516Z-cc4de5/index.html`.

For demo: `src/dashboard_GUI/backend/.venv/bin/python src/dashboard_GUI/scripts/render_experiment.py --name demo_dashboard`. Renderer `--scope history` selects repository history; default app. Names only, no arbitrary paths.

Original plotting command writes derived figures and summary.md into the study:

```bash
.pixi/envs/default/bin/python src/simulation_env/scripts/plot_experiment.py \
  --out src/dashboard_GUI/var/runs/experiments --name "$STUDY"
xdg-open "src/dashboard_GUI/var/runs/experiments/$STUDY/figures"
```

Figures: mrta.png, traffic.png, battery.png, completion_curves.png, order_times.png. Prefer the fresh categorical report for several strategies at one fleet size; older plots overlap.

### Cancel, shutdown, logs and optional verification

Select active owned job -> Cancel run -> cancel_requested -> cancelled/partial. This terminates the owned process group; it is not a pause. States: queued, starting, running, cancel_requested, completed, failed, cancelled, interrupted. Completed requires original exit0 plus expected valid artifacts/input correspondence. Restart marks old active jobs interrupted/exit unknown and prevents overlapping work until a provably owned orphan settles or is cancelled.

Ctrl+C in the launcher terminal stops both services and requests cancellation of owned workloads. Let a job finish first if complete evidence is needed. Closing the browser alone leaves processes. One service failing causes the launcher to stop its other owned service. Repeated Ctrl+C cannot interrupt bounded cleanup. There is no stop subcommand or port-based kill/adoption. Two-terminal services each stop via Ctrl+C.

Logs: `var/launcher/<UTC-id>/{frontend-build.log,backend.log,frontend.log}` (dev may lack build log); job stdout/stderr `var/jobs/<job_id>/process.log`; observed `telemetry.ndjson`; experiment child `run.log`. Operational state `jobs.sqlite`; indexes `index.sqlite`/app-index; inputs hash copies; scientific outputs `var/runs/singles/<job_id>` or `var/runs/experiments/<name>`. Repository `runs/` remains separate read-only history.

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws
ls -lt src/dashboard_GUI/var/launcher
less src/dashboard_GUI/var/jobs/ac2424b5da1e4952af487d9a74e99b3a/process.log
# Optional explicit full gate, only with app servers stopped:
python3 src/dashboard_GUI/scripts/launch_control_center.py verify
```

Verify runs backend/frontend tests, TypeScript/build, tiny observed-vs-CLI parity and real temporary server/proxy/security/shutdown checks. It retains ignored evidence, never a large campaign. `verify --skip-parity` is reduced; backend still has tiny fixtures. This audit did not run launcher verify or app services.

## Safeguards and remaining limitations

Observed wrapper source pin: `f16454ab7eec7ce3b6454da6b303608b3567d3f33b562fddea243726d1f82014` for run_workload.py. Runner drift fails closed, while original CLI remains available; updating the pin requires fresh parity. Observer copies authoritative state after existing ticks, with no new events/planner. Queue128, record256KiB, trace64MiB, explicit drops/gaps/footer/stale/invalid state and bounded final sample slot. Existing ticks determine sampling; requested interval does not slow science. UI loads<=500 samples/roughly8MiB plus one bounded page; playback2/4/8/12 samples/s is display rate. Active observations older than5s label stale. Optional observation failure remains separate from scientific execution. CLI/matrix/history without a genuine trace has no invented positions.

One active top-level job; children1–4; strict JSON/known registry/capacity and original CLI flags; no shell or arbitrary command/path API. Durable SQLite plus500 retained events,64KiB byte-cursor log pages and WS/poll resync. Browser shows latest150KB fetched logs. A polled64MiB disk-log threshold cancels owned work, not a hard disk quota. Process ownership verifies Linux boot/start/pgrp/session; durable exec gate prevents untracked science in the spawn crash window. Ordinary trusted-local-account boundary does not comprehensively supervise malicious session escape, same-user tampering, power loss or total storage failure.

Loopback binding, single local Host/peer/same-origin checks; POST requires JSON/session CSRF header; WS requires HttpOnly SameSiteStrict cookie/origin. No public authentication/remote service. Mutable source checkpoints do not archive frozen dirty bytes.

Read-only artifact allowlist, descriptor-relative nofollow/stable reads; JSON<=4MiB; typed streamed CSV/Parquet512-row batches, physical ordering and file/filter-bound cursors. Initial full validation and deep filtered pages may be slow. Experiment bounds10000 directories/200 studies/5000 grid entries; API pages<=1000. Multi-file reads are not an atomic snapshot. No delete API. Historical unknown process exit remains unknown despite complete files. Process status, artifact integrity, provenance and recording completeness are separate evidence.

## Existing evidence and measured pilots

Read-only jobs.sqlite:9 jobs, all completed/exit0/complete;5 singles+4 experiments. App outputs:81 run.json and81 metrics.json =5 singles+76 matrix runs;5 experiment manifests. Repository runs:20 manifests,17 metrics,0 experiments. Four offline HTML reports and10 launcher directories.

Fresh read-only ExperimentReader.detail validated all five app studies, no missing/invalid/extra entries:

| Study | Runs | Actual orders/run | Notes |
|---|---:|---:|---|
| demo_dashboard | 8/8 | 3 | requested50/generated3,20 robots,4 strategies,seeds1/2 |
| stage5_browser_20261028 | 4/4 | 3 | 2 robots,2 strategies,seeds1/2 |
| study_1793182117160 | 16/16 | 3 | 2 robots,8 strategies,seeds1/2 |
| pilot_r50_o200_s1-3_20261028 | 24/24 | 200 | 50 robots,8 strategies,seeds1/2/3; app-owned |
| pilot_r50_o200_20261028_123625 | 24/24 | 200 | same condition; terminal-created, no persisted app job |

All metrics schema2 and dataset schema1. Both pilots share settings signature716179a809aa95a5, q0/window_width50, parquet, manifest git badbc59671acd06fdcaf513060175ab74aeccc3d/traffic/rmf-integration/dirtytrue. They are pre-M0 GUI-source exploratory evidence, not research M0/q3 primary results. Two repetitions of the same deterministic workloads are still3 independent seed conditions, not6.

Actual CSV SHA256 matched all8 manifests per seed in each pilot:

- s1 `b5242bef0070f3b1716280e72d9cc2f49b4fc1eea157f819c6145fe077a4b668`
- s2 `3bd23bf6acfb272f930065d1510804f71c69df6c47dbf0923a58b1dd3fc0dcd5`
- s3 `8adafec20a1e1a6b1ffa0c600c4c3e66583c6446a904a00b27f3e71a07140e44`

Original app pilot job ac2424b5da1e4952af487d9a74e99b3a: created11:19:46.063641UTC, ended11:22:42.323142UTC, completed/exit0/artifactcomplete, reported176.23s wall. Every run completed200/200. All24 charge_sessions=0; waiting is nonzero. Fresh original paired math matches both saved reports (n3/3):

| Strategy | Mean order makespan(s) | Difference vs A*(s) | Paired95% CI(s) | Mean total waiting(s) |
|---|---:|---:|---|---:|
| auction | 412.888889 | 19.666667 | [-20.525559,59.858892] | 334.444444 |
| earliest_finish | 398.222222 | 5 | [-20.868981,30.868981] | 273.888889 |
| hungarian | 402.222222 | 9 | [-13.280437,31.280437] | 302.444444 |
| milp | 396.555556 | 3.333333 | [-19.984651,26.651318] | 281.666667 |
| multi_criteria | 403.555556 | 10.333333 | [1.112604,19.554062] | 311.111111 |
| nearest_astar | 393.222222 | reference | — | 259.444444 |
| nearest_manhattan | 416.444444 | 23.222222 | [15.800541,30.643904] | 360 |
| random | 427.888889 | 34.666667 | [10.200748,59.132586] | 353.666667 |

Three-seed exploratory findings are not a final ranking or multiple-comparison-adjusted conclusion. Zero charging does not validate saturation/preemption/sustained-load behavior. No ILP or transfer result follows.

Retained full Stage6 gate: `var/launcher/20261028T105651Z-8fd7683b/result.json`: passedtrue, parity_skippedfalse,5 checks. Its `http-integration/result.json`: frontend200, builtasset1, proxyhealth200, badOrigin403, badHost403, noCSRF403, sameoriginsession200, WS passed, launcherexit0, portsreleasedtrue. Existing `var/verification/stage3_4337f0979e/result.json`: scientific records equal,56 deterministic metric keys,182 samples,0 drops, exact times0 to674/3, health200, WS snapshotsequence4, finalcells robot001[39,2],robot002[0,18]. Other retained parity runs explicitly had drops24/17 without scientific differences.

## Fresh verification this audit

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=src/dashboard_GUI/backend:src/mrta_core:src/simulation_env:src/task_management \
src/dashboard_GUI/backend/.venv/bin/python -m pytest -q -p no:cacheprovider \
  src/dashboard_GUI/backend/tests
```

152 passed,1 existing Starlette/AnyIO deprecation warning,35.45s. Focused report/research/launcher21 passed7.32s. Frontend `npm test`:22 passed/6 files. TypeScript `--noEmit` exit0. `npm run build`: TypeScript+Vite passed,1949 modules,301.03kB JS,22.86kB CSS,236ms Vite. Launcher check exit0, Node22.23.2, dependencies/W100 loaded, ports free. No fresh browser/HTTP/parity smoke claim; use retained Stage6 evidence for earlier end-to-end acceptance.

## Troubleshooting and research integration

Port occupied/job directory locked -> use existing instance or stop its owning terminal; never kill by port or delete state. Backend offline -> read backend log, start matching backend and refresh. Build failure -> retained build log, no app services left. Preview expiry/drift -> review files and preview again. No observation -> observed mode/pin/capability; CLI/matrix/old history may have none. Fast finish -> Play recording. Partial/stale -> inspect real gaps/footer; scientific metrics are separate. Comparison refusal -> scope/workload/grid/settings/hashes/metric availability, without silently dropping failures. Changed cursor -> clear filters/reopen. CSV rejection -> exact schema/map IDs/quantity/dropoff/size. Absent study -> correct App/Repository scope and Refresh after manifest creation. Legacy missing order makespan -> leave unknown.

Research integration is not accomplished by changing cwd or copying ignored runtime. WORKSPACE_INTEGRATION.md records M0's build_manifest(strategy_identity), per-strategy identity gates, new matrix quantum/window/purpose, seed roles and observer-pin incompatibilities. Keep scientific M0 improvements and adapt the GUI boundary on an explicit integration branch, preserve M1 work, provision the environment deliberately, then rerun tiny parity/integrity/cancellation/restart/proxy and a small q3 matrix. Research previously had no .pixi environment; do not assume current relative interpreter paths work. Git publication does not transfer ignored var datasets/reports/environments/node_modules/dist. Collecting/exporting data does not train an ILP learner.
