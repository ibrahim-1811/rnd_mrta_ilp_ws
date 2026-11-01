# MRTA Warehouse Control Center — user guide

The Control Center is a local browser interface for running and inspecting the
existing warehouse simulator. Open **http://127.0.0.1:5173/** on this computer.
The backend API is **http://127.0.0.1:8000/docs**.

Stages 1–6 are implemented, including one-command startup and integration
verification. Stage 7 (separately approved ROS/Gazebo integration) remains planned.
The research simulator and its scientific phase are unchanged.

## What you can do today

| Screen | Available features |
|---|---|
| Operations | Selected registered warehouse map, pan/zoom/fit, optional directed lanes, observed robot cells/status/SOC, robot selection and details, order counts, exact simulated clock |
| Run setup | Validated warehouse scenario, registry strategies, robot/order counts, allocation seed, observed or original CLI mode, quantum/window/speed overrides supported by the original single runner, preview then explicit launch |
| Custom orders | Explicit CSV selection, map-aware validation, hash-addressed immutable copy, effective logical-order count in preview; original reference workload untouched |
| Process controls | Owned job selection, running/completed/failed/interrupted status, cancel request while active, original stdout/stderr, follow/copy logs, original scientific metrics |
| Recording | Real captured samples, play/pause, first sample, seek, display rate, latest state and bounded next segment; dropped/stale/incomplete labels |
| Experiments | Strategy × fleet × seed setup, seeded/reference workload source, orders, generated workload size, 1–4 parallel children, export, explicit new/resume/force intent, original expanded-grid preview and launch |
| Experiment library | App/repository experiment scopes, expected grid and present/missing/failed/invalid evidence, original specification and provenance |
| Paired comparisons | Original paired math, baseline selection, metric selection, actual/expected n, baseline mean, strategy-minus-baseline difference, paired 95% CI and percentage; explicit incomplete-analysis option |
| Run history | App/repository scopes, original metrics/manifest, integrity/provenance notes, downloads, associated genuine observation |
| Scientific records | Separate paginated decisions and outcomes; decision/order filters, robot/selected filters for decisions, exact strings and physical row order |
| System | Connection, capabilities, registered strategy implementations, source hashes/configuration and API documentation link |

Not available: scientific pause/resume of a simulation clock, continuous
trajectories, matrix fleet recordings, remote access, ROS/Gazebo UI, map editing,
learned ILP rules/explanations, or arbitrary matrix quantum/config/window/speed/CSV
overrides. Registration of a strategy is separate from scientific approval.

## Start from a fresh checkout or worktree

The launcher needs two interpreters inside the checkout: the scientific one at
`.pixi/envs/default` (in a git worktree, a symlink to the main workspace's
environment) and the dashboard backend at `src/dashboard_GUI/backend/.venv`.
Create the backend once (needs network for pip; the scientific environment is not
modified):

```bash
cd <checkout>
.pixi/envs/default/bin/python -m venv --system-site-packages src/dashboard_GUI/backend/.venv
src/dashboard_GUI/backend/.venv/bin/python -m pip install -e 'src/dashboard_GUI/backend[test]'
(cd src/dashboard_GUI/frontend && npm ci)
python3 src/dashboard_GUI/scripts/launch_control_center.py check
python3 src/dashboard_GUI/scripts/launch_control_center.py start
```

In Run setup choose **W100 research warehouse** (reference, default) or
**my_map custom warehouse** (200 parking cells), set robots = 100, preview, launch.
Every Excel file in `src/simulation_env/maps/` is listed: drop a file there and
restart. `start` runs `build_scenario_bundle.py --sync`, which builds what the map
needs and adds it to `config/scenarios.yaml`. Matrix experiments remain W100-only.

## Start or restart

Dependencies are already installed in this checkout. Start both services in one terminal:

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws
python3 src/dashboard_GUI/scripts/launch_control_center.py start
```

Wait for `Dashboard ready`, then open http://127.0.0.1:5173/. Keep the terminal
open; Ctrl+C stops both services and requests cancellation of owned active jobs.
No simulation starts automatically. The launcher checks dependencies/ports and
builds the UI, with no automatic package installation.

Read-only preflight:

```bash
python3 src/dashboard_GUI/scripts/launch_control_center.py check
```

Full verification (explicit tests/tiny runs; stop app servers first):

```bash
python3 src/dashboard_GUI/scripts/launch_control_center.py verify
```

See [OPERATIONS.md](docs/control_center/OPERATIONS.md) for development mode,
shutdown, diagnostics and handoff. The original two-terminal startup also works:

Terminal 1:

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws
export PYTHONPATH="$PWD/src/dashboard_GUI/backend:$PWD/src/mrta_core:$PWD/src/simulation_env:$PWD/src/task_management"
PYTHONDONTWRITEBYTECODE=1 src/dashboard_GUI/backend/.venv/bin/python -m control_center.app
```

Terminal 2:

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws/src/dashboard_GUI/frontend
npm run dev
```

Open http://127.0.0.1:5173/. Stop a service with Ctrl+C in its terminal. If the
port is occupied, use the existing instance or stop your own earlier one. Do not
start a second backend against the same job directory. Node 22.12+ is required;
22.23.2 was verified. A fresh frontend dependency installation uses `npm ci`
inside frontend, without changing the scientific environment.

The agent-started local servers have logs/PID metadata in ignored `var/`.
Processes can stop when the desktop session ends; the two terminal commands are
an alternative way to restart them. Prefer the one-command launcher above.

## Your first single run

1. Open **Operations**. Keep the **W100 research warehouse** reference scenario
   selected. Leave robots = **2**, orders = **3**, seed = **7**.
2. Select **Nearest A*** and **Observed**. Keep the reference workload selected.
3. Click **Preview run**. Review the effective counts, seed, quantum, speed,
   original server-generated command, destination and warnings.
4. Click **Launch run**. The backend owns a new process and output directory.
   Opening the page or clicking Preview alone never starts a simulation.
5. Read the status, logs and original metrics. Select a robot to inspect its cell,
   active/assigned order, distance, waiting and exact SOC when available.
6. Click **Play recording** to see recorded movement. Fast headless runs can
   complete before you see a live frame.

A **job** means one supervised execution. A **run artifact ID** identifies an
indexed result directory. The manifest's scientific `run_id` is input-derived,
so equivalent repeated executions can share it. These IDs serve different roles.

### Single-run settings

- **Robots:** positive count up to the map's home capacity.
- **Orders:** number of logical orders, not CSV rows. Zero selects all orders;
  above the file's count selects all available. Preview shows the effective count.
- **Allocation seed:** allocation randomness. It does not generate a new workload.
- **Observed:** explicit wrapper around the same scientific components, with
  sampled read-only observation. **Original CLI:** existing runner, logs/artifacts,
  no fleet recording.
- **Candidate window:** configured default, fleet size or legacy behavior.
- **Quantum:** dispatch interval in simulated seconds. Zero is event-driven.
- **Speed:** the original single-run override, applied uniformly to the fleet.
- **Sample interval:** simulated-time spacing requested from existing ticks;
  it does not insert events or slow the simulator.
- **Export CSV/Prolog:** requests the original runner's optional exports.

Changing settings invalidates the preview. Launch rechecks input/source hashes;
if files changed, preview again instead of launching different inputs silently.

### Warehouse scenarios

The read-only registry lists warehouse configurations known to the Control
Center. W100 is the tracked scientific reference and the default. Each entry is
loaded by the production configuration and XLSX loaders and checked by the
production map validator; the UI shows its dimensions and initial home capacity.
An invalid entry remains visible with its validation errors, but it cannot be
selected for geometry or previewed.

Scenario IDs are registry identifiers, not file paths. A single-run preview
records the selected ID, a content identity, and SHA-256 hashes of the config,
map, and orders. Launch rebuilds that plan and refuses it if those bytes changed.
Selecting another scenario clears any preview and uploaded-order selection.
Selection is locked while an owned job is active, and an older selected job
continues to use the map associated with that job rather than the current setup.

The registry and tracked scenarios are declarations, not mutable runtime state.
Future generated or interactive maps belong in ignored, app-owned, immutable
runtime bundles. A custom map should be registered only after preserving its
bytes, passing the full topology/routing/traffic/terminal and visualization
consistency gates, and receiving project-owner approval; registration must never
rewrite W100 or its config/orders.

### Your own order CSV

Use **Upload and validate CSV**. The header must be exactly:

```csv
order_id,item_id,shelf_id,quantity,dropoff_id
```

Use shelf and drop-off IDs from the currently selected scenario. Quantity must
be a positive integer. Multiple rows with one order ID are one logical order;
they must agree on the drop-off. Limits: 2 MiB and 50,000 line items. Unsupported
priority, release-time, deadline and battery columns are rejected.

The backend validates with the original loader, copies the bytes under
`var/inputs/<sha256>.csv`, and returns a hash ID. It never replaces the reference
CSV. The hash is rechecked during planning, launch and final validation.
Selecting the reference workload again clears the custom selection. A browser
reload clears the selection; upload the same file again to reuse its same hash.
Custom CSV is a single-run feature, including observed runs. Upload validation
and preview validation both use the selected scenario.

## Recording and map use

Click/keyboard-select a robot or choose its fleet row. Drag the map to pan, use
zoom buttons, and Fit to restore the whole warehouse. Lanes shows actual directed
edges. Grid coordinates use upward Y; one cell's physical scale comes from the
map. Overlapping robots can occupy the same terminal bank and their markers may
overlap; fleet rows remain individually selectable.

Playback shows actual captured cells, without interpolation. **Samples/s** is
browser display rate, not simulator speed. Exact clock strings retain the source
Fraction time. Segments load at most 500 samples and roughly 8 MiB of source pages
(with at most one bounded page beyond the byte boundary). **Next recording
segment** replaces the current segment; **Latest** shows the latest captured state.

A successful scientific run can have a partial observation because samples were
dropped. Stale state means the displayed cells are old. A CLI-only run or old
history without a trace has no spatial playback; positions are never invented.

## Run an experiment matrix

Open **Experiments**. A matrix is the original experiment runner, not a second
simulation engine. For a first check use:

```text
strategies: nearest_astar, random
fleet sizes: 2
seeds: 1, 2
orders per run: 3
generated workload orders: 3
parallel children: 1
workload source: seeded
execution intent: new
```

Use a unique experiment name (letters, numbers, underscore or hyphen, at most
64 characters). Fleet sizes and seeds are comma/space-separated integers; no
range syntax. Preview shows the actual expanded grid. This seeded example is
four runs. Reference mode follows the original runner's rule: deterministic
strategies use the first seed while stochastic ones use each seed.

**Orders per run = 0** means all logical orders of that workload. **Generated
workload orders** controls how many orders each seed creates; it is unused in
reference mode. Parallel children is capped at four and defaults to one. The
backend permits one active top-level job (single or experiment).

After **Launch matrix**, Operations shows its job/status/logs and cancellation.
Return to Experiments and **Refresh studies** to inspect generated artifacts.
Matrix children remain explicitly bound to the original W100 reference
configuration and use its quantum, window, speed and no observation wrapper.
Unsupported overrides are intentionally absent. Different
quantum conditions must remain separate experiments; comparisons reject mixed
settings. Supporting a new matrix CLI flag would be separate runner work.

### New, resume and force

- **New:** fresh name/destination.
- **Resume:** reuse a previously app-owned experiment with the same grid and
  verified inputs. Only missing/failed runs are rerun. This does not restore
  simulated time. Source/config/workload drift or unsafe existing artifacts
  cause a refusal; use a new name.
- **Force:** explicit acknowledgement plus preview and launch. The original
  runner archives the old directory using its UTC suffix and reruns the grid.
  Evidence is retained. Restricted to previously app-owned experiments.

Large campaigns remain your deliberate action. The GUI never auto-launches a
100-robot or 30-seed study.

## Read paired comparisons

Select a saved experiment and baseline, then **Compare paired runs**. Seeded
workloads are required. The backend validates actual per-run artifact sets,
identities, settings signatures and workload hashes, then calls
`simulation_env.paired` in memory. No metrics or summary files are rewritten.
The original individual run metrics are authoritative, not a possibly stale
summary.csv or previously generated paired.csv.

Each row shows:

- **Actual / expected n:** metric-specific usable paired seeds vs planned pairs.
- **Baseline mean:** mean original baseline metric over those usable pairs.
- **Mean difference:** strategy minus baseline. Negative makespan is earlier;
  positive throughput is higher. Interpret direction for the selected metric.
- **95% CI:** the original paired t interval; unavailable for n < 2.
- **% baseline:** original percentage difference; unknown for a zero baseline.

Incomplete analysis is refused by default. Its explicit checkbox permits
available pairs while retaining the INCOMPLETE banner and missing/invalid rows.
It does not permit mixed settings or workload mismatch. Outside-grid run
folders are excluded and listed. An absent metric is skipped by original paired
math, so actual n can be below expected n even with a complete grid. No ranking
or significance claim is inferred by the UI. Two seeds verify plumbing, not a
research conclusion.

Readers bound discovery to 10,000 directories/200 experiments and detail to 5,000
grid entries. Original table integrity validation can take time on large studies;
use Refresh after an experiment finishes. Archive unnecessary app output manually
outside the GUI if these reader bounds are reached; the dashboard has no delete API.

## History and scientific records

**App runs** are new output under `var/runs`; **Repository history** reads the
existing repository `runs/` tree. Select a row to inspect manifest, original
metrics, provenance, downloads and associated observation.

The **Decisions** table contains candidate scores/predictions available at
allocation time. Filter by decision ID, order ID, robot ID or selected candidate.
The **Outcomes** table contains realized distance, waiting, energy and completion;
filter by decision ID or order ID. The tables are separate: outcomes never become
allocation inputs. ID filters are for traceability, not scientific predicates.

Tables retain original field names, exact timestamp strings and physical order.
Use the horizontal scrollbar for all columns and Previous/Next for 50-row pages.
Changing filters restarts pagination. File changes can invalidate a cursor;
clear filters or reopen the run. Dash means unknown, never numeric zero.
Legacy `makespan_s` is labelled **Fleet settled time** and is never converted to
order makespan. Process status, artifact integrity and scientific provenance are
separate evidence; complete files alone do not prove an old process exited 0.

## Clear reports and worked 50-robot pilot

Create readable categorical strategy plots, explicit metric sample counts,
per-seed completion curves and an offline HTML preview without changing old figures:

```bash
src/dashboard_GUI/backend/.venv/bin/python src/dashboard_GUI/scripts/render_experiment.py --name demo_dashboard
```

Open the new preview path printed under `var/reports/`. See the
[complete pilot walkthrough](docs/control_center/PILOT_WALKTHROUGH.md) for verified
demo findings, the approved 24-run pilot results and step-0-to-end repeat commands.
The form warns if generated workload size is below the requested order limit.

## Original plotting command

Graphs are produced by the existing scientific plotting script. They are not
currently embedded in the dashboard. From the repository root, use an actual
completed experiment name (the small browser verification study is shown here):

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws
EXPERIMENT=stage5_browser_20261028
.pixi/envs/default/bin/python src/simulation_env/scripts/plot_experiment.py \
  --out src/dashboard_GUI/var/runs/experiments --name "$EXPERIMENT"
```

This explicitly writes derived figures and `summary.md` into that experiment.
Open the figures directory in your file manager:

```bash
xdg-open "src/dashboard_GUI/var/runs/experiments/$EXPERIMENT/figures"
```

Generated files: `mrta.png`, `traffic.png`, `battery.png`,
`completion_curves.png`, `order_times.png`. MRTA plots include order makespan,
throughput, completion time, fleet settled time, distance, waiting and allocator
CPU. Traffic/battery plots use their original recorded counters. Completion
curves show orders finished over simulated time; order-time plots show per-order
duration and waiting. Plot bars/ranges are descriptive; use the paired comparison
for its separately reported paired t confidence interval.

For a saved paired CSV/Markdown report using the original CLI:

```bash
.pixi/envs/default/bin/python src/simulation_env/scripts/compare_paired.py \
  --out src/dashboard_GUI/var/runs/experiments --name "$EXPERIMENT" \
  --baseline nearest_astar
```

That command writes `paired.csv` and `paired.md`. It reads the original runner's
`summary.csv`, whereas the GUI validates individual source run artifacts. If
someone edits summary.csv separately, those analyses can differ; retain original
artifacts and resolve the discrepancy. Do not add `--allow-incomplete` casually:
the CLI report must disclose missing runs, just as the GUI does.

## Where are ILP and rules?

MILP is an implemented allocation optimizer (mixed-integer linear programming).
ILP here means inductive logic programming, the planned symbolic rule learner.
These are separate methods despite their similar names.

The repo currently has decision/outcome collection and Prolog dataset export.
Selecting Export writes `bk.pl` (background facts) and `exs.pl` (selected and
unselected candidate examples) beside each run. These are training material,
not learned rules. The exporter does not define the learner's bias/mode files,
train a policy or produce an outcome-aware rule set. There is no implemented
ILP training command or learned-rules screen to invoke yet.

The future research pipeline must first define/verify a teacher task and learner
bias, then train/evaluate symbolic rules, then develop multi-teacher and
outcome-aware supervision. Learned policies must be evaluated in the same fixed
simulator on suitable held-out workloads/maps. Realized waiting/completion/energy
may inform later supervision; they must never leak into decision-time features.
Dashboard Stage 6 provides integration/startup; it does not implement these future
scientific learning phases. See repository CURRENT_PHASE.md for their status.

## How the backend works — robotics analogy

The browser is your control-station display. FastAPI is its service interface.
The job manager supervises a process. Your existing simulator still owns the
robots, task allocation, routing, traffic, battery and simulated clock.

```text
React screen --HTTP requests--> FastAPI --validated plan--> job manager
     ↑                            ↑                           ↓
     └---- logs/state/files ------┴---- existing scientific simulator
```

HTTP **GET** reads, like a query service. HTTP **POST** performs an explicit
preview/start/cancel/upload action. A local session token protects actions and
the browser handles it automatically. A **WebSocket** streams sequenced updates,
conceptually like subscribing to a topic. Snapshot/log pages recover missed data.
**SQLite** is the operational ledger/index; original artifacts remain scientific
authority. The sampled NDJSON trace is conceptually a small bag recording.
These are analogies; no ROS node/topic integration was introduced.

The frontend renders values. The backend validates and supervises. The simulator
produces behavior/results. Comparison math is reused from the research code.

## Files and troubleshooting

```text
src/dashboard_GUI/
  frontend/                    React UI, scoped npm dependencies
  backend/control_center/      local API, adapters and job supervisor
  docs/control_center/         contracts, stage reports and startup notes
  var/                         ignored runtime data
    jobs.sqlite                operations/events
    jobs/<job_id>/             logs and optional telemetry.ndjson
    inputs/<sha256>.csv         validated order copies
    runs/singles/<job_id>/      single-run scientific artifacts
    runs/experiments/<name>/    matrix specification/workloads/run artifacts
    app-index/                 disposable artifact index
```

| Symptom | What to do |
|---|---|
| Browser cannot connect | Start frontend on 5173, then reload |
| Backend offline | Start backend on 8000; Refresh workspace |
| Port occupied / jobs locked | Use or stop your existing instance, not unrelated processes |
| Run finishes before motion appears | Play its sampled recording |
| No observation | Check Observed mode and backend capability; old/CLI-only runs have none |
| Preview expired / hash changed | Review source/input changes and preview again |
| Comparison refused | Inspect missing/invalid grid rows, workload mode and settings; do not pool incompatible conditions |
| Missing legacy values | Inspect original artifacts; unavailable values cannot be reconstructed by UI |
| CSV rejected | Exact header, valid map IDs, positive quantity, matching drop-off, size/row limits |
| Experiment not listed yet | Refresh studies after experiment.json is written; choose correct scope |

Verification commands and stage-specific details are in
[Stage 4 startup](docs/control_center/STAGE_4_STARTUP.md),
[Stage 5 plan](docs/control_center/STAGE_5_PLAN.md) and
[Stage 5 report](docs/control_center/STAGE_5_REPORT.md).

## Research workspace integration

The GUI and research folders are separate Git worktrees. See the
[workspace audit and integration story](docs/control_center/WORKSPACE_INTEGRATION.md)
for branch state, M0 compatibility gaps, seed roles, local data/environment
separation and the safe integration sequence. Publication is not a scientific
merge or a claim that the current GUI is already compatible with research M0.
