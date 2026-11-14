# Demo report review and 50-robot pilot

User reviewed Stages 5/6, requested review of demo_dashboard graphs, clearer
reports, explanation and a controlled 50-robot/all-eight-strategy experiment.
User chose 200 orders x seeds 1/2/3 (24 runs) and requested reproducible commands.

Findings: demo spec orders=50 but workload_orders=3. Eight complete runs each
actually execute 3 orders, 20 robots, four strategies, two seeds. Single-fleet
plots overlap at x=20; completion title/legend collide; mostly zero battery and
traffic panels are uninformative. Original graph files must remain preserved.

- Add tested display-only preview warning/effective matrix order count; no CLI,
  simulator, configuration, RNG, policy or statistical changes.
- Add isolated report renderer under dashboard_GUI using validated original
  run metrics and original paired math. Fresh report folder only. Categorical
  strategy rows, actual seed points, labelled mean/min-max (not CI), explicit
  zero/missing metrics, independent single-metric PNGs and offline HTML index.
  Never overwrite original figures, metrics, manifests or summaries.
- Audit all demo identities/artifacts, preview fields and pairs; explain exact
  order count, weak sample size, lack of congestion/charging and q=0 semantics.
- Run approved pilot through unchanged original CLI, 50 robots, all 8 registry
  strategies, orders=workload_orders=200, seeds 1/2/3, jobs=1. Preserve default
  q/config/window/speed. Do not silently set q=3 (matrix CLI lacks override).
  Record job/log/preflight provenance, validate completeness/settings/pairing,
  render reports and explain outcomes. No ROS/ILP auto-expansion or commits.
- Provide step-0-to-end commands including equivalent preview/UI configuration,
  original CLI launch, status, integrity/comparison and report generation.

Completed: demo validated (8/8 actual 3 orders), preview warning tests RED→GREEN,
new plots inspected, metric-n review regression RED→GREEN, approved pilot complete
24/24 with verified input hashes/settings. Backend152/frontend22/build pass.
Original paired.csv/md generated for the new pilot; old demo artifacts unchanged.
User commands and interpretation: PILOT_WALKTHROUGH.md. Results/report paths:
REPORT_AND_PILOT_RESULTS.md. No original simulator/runner/config edits or commits.
