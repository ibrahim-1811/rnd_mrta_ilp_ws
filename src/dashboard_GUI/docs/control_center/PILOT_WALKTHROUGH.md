# Demo review and controlled 50-robot pilot

## What happened in demo_dashboard

Eight datasets validate: four strategies, 20 robots, seeds 1/2. But the request
orders=50 used workload_orders=3, so every run actually executed 3/3 orders. A
limit cannot generate missing orders. The original plot style stacks all strategy
points at x=20, its completion title/legend collide, and many panels are zero
because no traffic waiting or charging occurred. The old completion plot shows
only the lowest seed. This is a valid plumbing test, not a sustained-load study.
Original graphs and scientific artifacts were preserved.

## Approved pilot and measured results

`pilot_r50_o200_s1-3_20261028`: eight registered strategies x 50 robots x three
seeds = 24 runs; BOTH generated workload size and per-run order limit = 200.
W100, configured event-driven quantum=0, fleet-size window, unchanged common
routing/traffic/battery/speed/service settings. One child ran at a time.

Job ac2424b5da1e4952af487d9a74e99b3a completed, exit 0, artifacts complete,
176.23 seconds wall time. Every run completed 200/200. Every strategy's recorded
workload hash matches the same seed's actual CSV bytes. Shared settings signature:
716179a809aa95a5. Scientific code/runners/configuration remain unchanged.

Mean original order makespan over three seeds (simulated seconds):

| Strategy | Mean | Difference vs Nearest A* | Original paired 95% CI |
|---|---:|---:|---|
| Auction | 412.889 | +19.667 | [-20.526, 59.859] |
| Earliest Finish | 398.222 | +5.000 | [-20.869, 30.869] |
| Hungarian | 402.222 | +9.000 | [-13.280, 31.280] |
| MILP | 396.556 | +3.333 | [-19.985, 26.651] |
| Multi Criteria | 403.556 | +10.333 | [1.113, 19.554] |
| Nearest A* | 393.222 | reference | — |
| Nearest Manhattan | 416.444 | +23.222 | [15.801, 30.644] |
| Random | 427.889 | +34.667 | [10.201, 59.133] |

Paired n=3/3. Exploratory pilot, not a final ranking or a multiple-comparison-
adjusted result. Traffic waiting is nonzero. Charging sessions are zero for every
run: this does not validate charger saturation/preemption. Quantum=3 would be a
separate condition; the original matrix CLI lacks that override. No config edit
was used to create a hidden alternative condition.

## Read the new previews

Fresh `var/reports/<study>/<UTC-id>/index.html` links to PNGs and `data.json`.
Source datasets are validated and original paired math is reused. Old graphs are
not overwritten. Each strategy has its own row. Dots are actual seed values,
diamonds are arithmetic means and lines are min–max, NOT confidence intervals.
Actual/expected sample counts are labelled for every metric. Missing is unknown;
all-zero panels explain that no event occurred. Completion plots show each seed
separately with a non-overlapping legend. Individual order-time boxplots are
purely descriptive, not independent seed-level confidence intervals. Paired
uncertainty remains in the original paired t table.

## Step 0 — readiness

Bash commands below run from the repository root. For terminal-only campaigns,
servers are unnecessary. Stop your own launcher with Ctrl+C before the free-port
check; do not stop unrelated processes. Run only one campaign at a time.

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws
python3 src/dashboard_GUI/scripts/launch_control_center.py check
```

Keep source/configuration fixed throughout the campaign.

## Step 1 — fresh study name and eight strategies

```bash
STUDY="pilot_r50_o200_$(date +%Y%m%d_%H%M%S)"
STRATEGIES=(auction earliest_finish hungarian milp multi_criteria nearest_astar nearest_manhattan random)
```

Use a fresh name to repeat a study; do not use --force casually.

## Step 2 — unchanged original runner

```bash
PYTHONDONTWRITEBYTECODE=1 .pixi/envs/default/bin/python \
  src/simulation_env/scripts/run_experiment.py \
  --out src/dashboard_GUI/var/runs/experiments \
  --name "$STUDY" \
  --strategies "${STRATEGIES[@]}" \
  --robots 50 --seeds 1 2 3 \
  --orders 200 --workloads seeded --workload-orders 200 --jobs 1
```

Read all ok/missing/error messages and the exit status. Expect 24 complete runs;
never silently discard a failure. A terminal-launched campaign is controlled by
that terminal. Its artifacts appear in the GUI experiment library on Refresh,
but its process is not adopted as an app-owned job. For GUI logs/cancel, instead
launch the same settings through Experiments → Preview matrix → Launch matrix.
Choose one route; do not run both simultaneously. Backend resume/force is limited
to app-owned studies; terminal-created studies use the original CLI rules.

## Step 3 — original paired report

```bash
.pixi/envs/default/bin/python src/simulation_env/scripts/compare_paired.py \
  --out src/dashboard_GUI/var/runs/experiments \
  --name "$STUDY" --baseline nearest_astar
```

Writes paired.csv/paired.md from original summary.csv and refuses incompatible or
incomplete data. The new renderer separately validates individual source runs.
Resolve an edited/stale summary discrepancy rather than pool inconsistent data.

## Step 4 — clear plots and offline preview

```bash
src/dashboard_GUI/backend/.venv/bin/python \
  src/dashboard_GUI/scripts/render_experiment.py --name "$STUDY"
```

Open the absolute HTML path printed after `Preview:` with `xdg-open`. The current
completed pilot already has this ready-to-open report:

```bash
xdg-open /home/ibrahim/rnd_mrta_ilp_ws/src/dashboard_GUI/var/reports/pilot_r50_o200_s1-3_20261028/20261028T112516Z-cc4de5/index.html
```

Regenerate the demo's presentation separately:

```bash
src/dashboard_GUI/backend/.venv/bin/python \
  src/dashboard_GUI/scripts/render_experiment.py --name demo_dashboard
```

Each invocation creates a new report folder. Local images/data are linked from
the HTML, with no CDN or web service required.

## Step 5 — review in the GUI

```bash
python3 src/dashboard_GUI/scripts/launch_control_center.py start
```

Open http://127.0.0.1:5173/, Experiments → App → Refresh studies. Inspect the grid
and paired results; Run history → selected dataset → Decisions/Outcomes shows
predicted features versus realized execution. Matrices have no fleet recording;
a separate observed single run provides motion samples.

The form now warns when generated workload size is below the request. Restart
your own launcher after the campaign to load updated backend preview code, and
reload the browser for the rebuilt frontend. No running user server was restarted.

## Step 6 — interpret, then decide next research work

Read makespan/throughput, productive distance/waiting, then total/support/recovery,
energy and CPU tradeoffs. Inspect individual seeds and outcomes, not only means.
This pilot tests the workflow and reveals hypotheses; it does not establish
transfer, charging performance or a final winner. ILP training/rules/explanations
are unimplemented scientific phases; collecting data does not train a learner.
ROS/Gazebo is also separately approved work, not an automatic next action.
