# Demo review, report correction and approved pilot — results

User requested demo_dashboard integrity/plot/preview review and then explicitly
selected a controlled 50-robot/all-eight-strategy pilot with 200 orders and three
seeds, plus commands to repeat the workflow. Work stayed under dashboard_GUI;
no automatic ROS/Gazebo or ILP phase began.

## Findings and corrections

Demo: 8/8 valid datasets, 20 robots, four strategies, two seeds. Actual orders=3
in all runs because generated workload size was 3 while requested limit was 50.
Old single-fleet plots overlap at one robot-count x coordinate, completion legend
collides with title, and zero traffic/charging panels add little information.
Old plots/metrics/manifests/summaries were preserved byte-for-byte.

Added a display-only preview effective orders_per_run and warning if requested
orders exceed available generated/reference workload. Frontend also warns before
preview and displays the effective count. Original CLI args/loader semantics did
not change. The current user's running backend was not restarted; restart the
launcher/reload UI to use the updated preview code.

New `scripts/render_experiment.py` / `backend/control_center/reports.py` create
fresh local HTML/PNG/data previews using validated source run files and original
paired math. Categorical strategy rows show real seed values, mean and min-max
(not CI), explicit actual/expected n, unavailable fields and zero-event panels.
Stable colors cover eight registered strategies. Per-seed completion graphs have
separate legend space; order distributions are labelled descriptive pooled orders.

Independent review caught missing per-metric sample-count disclosure. Regression
failed before the fix and passes afterward; rows now show n=actual/expected and
unavailable annotations, with machine-readable counts in data.json.

## Pilot evidence

Name: pilot_r50_o200_s1-3_20261028. App-owned job:
ac2424b5da1e4952af487d9a74e99b3a. Existing unchanged runner, W100, 50 robots,
all eight registry strategies, seeds 1/2/3, seeded workload_orders=orders=200,
jobs=1, original configured quantum=0 (event-driven).

Completed: 24/24, 200/200 per run, exit 0, artifact_status complete, 176.23 s wall.
Source datasets and original paired analysis validate. Each seed's actual CSV
SHA256 matches all eight manifests; one shared settings signature 716179a809aa95a5.
Pilot-analysis.json retains source/detail/comparison/hash audit. No scientific
changes or hidden quantum/config overrides. Waiting is nonzero; all charge
session counts are zero. No charging-performance or general winner claim follows.
The three-seed comparisons are exploratory, without multiple-comparison correction.

Means/CIs and full reproduction commands are in PILOT_WALKTHROUGH.md.

## Saved previews

- Demo: var/reports/demo_dashboard/20261028T112514Z-c0b8bf/index.html
- Pilot: var/reports/pilot_r50_o200_s1-3_20261028/20261028T112516Z-cc4de5/index.html

Visual inspection checked the original broken overview/battery/completion charts,
then the corrected categorical overview and seed-1 completion graph. The pilot
completion plot now displays eight labels below the axes and all 200 completions.
HTML uses local files and escaped source strings, without external assets.

## Checks and preservation

- Backend full suite: 152 passed, one existing Starlette/AnyIO warning.
- Frontend: 22 passed; TypeScript/production build passed.
- Focused report tests verify source preservation, fresh-output requirement,
  refusal of incomplete data and metric-specific missing sample disclosure.
- Preview regression verifies requested 50/generated 3 produces effective 3 and
  a warning; frontend reproduces the same mistaken form settings.
- Baseline: 555 files. Original demo scientific artifacts/figures and original
  repository history unchanged; no missing source or changes outside dashboard.
  Detailed hashes: var/report-audit/preservation.json. HEAD unchanged; no commits,
  pushes, resets or changes to existing dirty scientific work.
- Source changes are dashboard presentation/planning/tests/docs only. The new
  authorized 24-run pilot and fresh reports are ignored app-owned output.
