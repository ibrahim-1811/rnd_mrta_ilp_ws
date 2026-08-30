# 13 — Metrics and outputs

## Output layout

Via `scripts/run_experiment.sh`:

```
Mrta_experiments/<strategy>_<YYYY-MM-DD_HH-MM-SS>/
├── metrics_<ts>.csv                 # 9-column, ONE-ROW summary
├── ilp_logs/
│   └── ilp_dataset_<ts>.csv         # 21 columns, one row per candidate per decision
├── launch.log                       # combined stdout of all five nodes
└── order_gen.log                    # order generator stdout
```

Via `ros2 launch` directly: only the two CSVs, under `out_dir` (default
`/tmp/mrta_run`). No `launch.log` or `order_gen.log`.

Note the two timestamps differ — the directory is stamped by the shell script, the CSVs
by each logger node at construction, a second or two later.

`Mrta_experiments/` is git-ignored.

---

## `metrics_<ts>.csv`

Written by `metrics_logger.py`. **The entire file is rewritten after every event**
(`metrics_logger.py:83`, mode `'w'`), so it always holds exactly one data row — the
running total. It is a final-state snapshot, not a time series.

| Column | Type | Source | Definition |
|---|---|---|---|
| `strategy` | str | allocation event | strategy name, `unknown` until the first decision |
| `n_assign` | int | allocation events | decisions resulting in an assignment |
| `n_defer` | int | allocation events | decisions returning `None` (task re-queued) |
| `n_completed` | int | `/fleet_travel_stats` | tasks finished |
| `makespan_s` | float | wall clock | `last_completion_ts − first_decision_ts` |
| `throughput_ops` | float | derived | `n_completed / makespan_s` |
| `mean_travel_m` | float | `/fleet_travel_stats` | mean `travel_dist` per completed task |
| `mean_energy_pct` | float | `/fleet_travel_stats` | mean `battery_consumed` per completed task |
| `mean_alloc_runtime_ms` | float | allocation events | mean `select()` wall time, ms |

### Caveats

1. **`makespan_s` is wall-clock**, taken from `get_clock().now()` at the first
   allocation event and the last completion. It therefore measures how fast *your
   machine* ran the simulation, scaled by `time_scale` — not simulated warehouse time.
   Repeated identical runs give 3.751 vs 3.756 s. Under CPU load it would drift further.
   **Do not report this as a scientific result without first converting the simulator
   to a simulation clock.**

2. **`throughput_ops` inherits that problem** — it is `n_completed / makespan_s`.

3. **`n_defer` counts deferral *events*, not deferred tasks.** A task re-queued five
   times contributes five to `n_defer`, so the value can exceed the number of tasks.

4. **`mean_alloc_runtime_ms` measures only `allocator.select()`**
   (`task_manager.py:216-218`). It excludes `build_candidates`, which is where all the
   A\* work happens — so it under-reports the true per-decision cost by a wide margin.
   Measured values are 0.003–0.015 ms, which is the cost of an `argmin` over ≤8 items,
   not the cost of planning.

5. **Nothing is recorded per robot or per task.** Only fleet-wide means.

6. **No run metadata.** The file records the strategy but not `num_robots`,
   `num_orders`, `seed`, `rate_hz`, `time_scale`, the shelf CSV, or the commit — all of
   which live only in the directory name and `launch.log`. Aggregating many runs
   requires parsing directory names.

---

## `ilp_dataset_<ts>.csv`

Written by `ilp_dataset_logger.py`. One row per **candidate robot** per **decision**;
flushed after each event. This is the richest artefact the system produces.

| # | Column | Scope | Meaning |
|---:|---|---|---|
| 1 | `timestamp` | decision | wall-clock seconds |
| 2 | `task_id` | decision | `order_<oid>_task_<n>` |
| 3 | `order_id` | decision | source order |
| 4 | `shelf_id` | decision | pickup shelf |
| 5 | `priority` | decision | **always 1** |
| 6 | `strategy` | decision | strategy name |
| 7 | `decision` | decision | `assign` or `defer` |
| 8 | `chosen_robot` | decision | winning robot id, or empty on defer |
| 9 | `runtime_ms` | decision | `select()` time |
| 10 | `candidate_robot` | candidate | this row's robot |
| 11 | `is_chosen` | candidate | 1 if this robot won — **the only label** |
| 12 | `reachable` | candidate | A\* found a path |
| 13 | `distance` | candidate | metres to pickup |
| 14 | `travel_time` | candidate | seconds to pickup |
| 15 | `congestion` | candidate | congestion summed over both legs |
| 16 | `energy_pct` | candidate | full-task energy, % |
| 17 | `battery` | candidate | charge at decision time, % |
| 18 | `workload` | candidate | **always 0** |
| 19 | `cumulative_load` | candidate | predicted cumulative proc time, s |
| 20 | `battery_feasible` | candidate | `b − E ≥ b_min` |
| 21 | `explanation` | decision | the strategy's text reason |

### Fitness as ILP training data

The **shape** is right: contrastive rows per decision with a chosen/not-chosen label.
Four problems limit its usefulness.

**1. Constant columns carry no information.** `priority` is 1 in every row and
`workload` is 0 in every row — verified across **1,396 rows** from 25 runs. Two of the
three predicate families the ICAPS work relied on (`low/medium/high_workload`,
`high/medium/low_priority`) would be degenerate.

**2. Half of all decisions have no alternative.** Candidate counts from a 10-order run:

| Candidates in decision | Decisions |
|---:|---:|
| 1 | 5 |
| 2 | 3 |
| 3 | 1 |
| 4 | 1 |

A single-candidate decision yields one positive example and **zero negatives** — no
contrast to learn from. Root cause: only *idle, unassigned-this-tick* robots become
candidates, and at 5 Hz dispatch most robots are already busy.

**3. Busy robots are invisible.** A robot excluded for being busy generates no row, so
the dataset cannot express "not chosen because unavailable" — arguably the most common
real reason.

**4. No outcome column.** `is_chosen` records what the teacher *did*, never how it
*turned out*. There is no realised travel distance, no realised energy, no completion
time, no lateness. **A dataset with no outcome column cannot train an outcome-aware
policy** — see [06_OUTCOME_AWARE_ILP.md](06_OUTCOME_AWARE_ILP.md).

Also missing: the composite cost `C_ij` and its per-term decomposition are never
written, though they are the quantities the composite strategies actually decide on.

### Row-count arithmetic

Rows are not proportional to tasks. A 10-order run produced 18 rows, a 40-order run
about 60. Row count depends on how many robots happen to be idle, so it is a function of
fleet size, order rate and task duration.

---

## `launch.log`

Combined stdout of all five nodes. The most useful lines:

```
[task_manager_node-3] [INFO] TaskManager: strategy=nearest, seed=0, grid=31x31
[task_manager_node-3] [INFO] Loaded 100 shelves
[task_manager_node-3] [INFO] [assign] order_1_task_1 -> robot 2 | nearest: robot 2 closest at 37.0 m
[task_manager_node-3] [INFO] status: orders=10 pending=0 in_flight=3 completed=7
[mock_fleet_manager-1] [INFO] Robot 3 entering charging at 18.4%
```

This is the only place run configuration is recorded, and it is unstructured text.

```bash
grep '\[assign\]'          Mrta_experiments/<run>/launch.log
grep 'status:'             Mrta_experiments/<run>/launch.log | tail -5
grep -i 'charging\|WARN\|ERROR' Mrta_experiments/<run>/launch.log
```

---

## What is never written

| Data | Status |
|---|---|
| Per-task completion time | ❌ not computed |
| Per-task waiting/queue time | ❌ not computed |
| Per-robot distance / energy / utilisation | ❌ only fleet means |
| Workload balance (Gini, variance, makespan spread) | ❌ not computed |
| Deadline violations | ❌ no deadline variance exists |
| Congestion time series | ❌ published at 1 Hz, never recorded |
| Executed paths | ❌ discarded after motion |
| Path planning time | ❌ not measured |
| Cost decomposition (`C_ij` and its terms) | ❌ not computed |
| Realised vs predicted comparison | ❌ realised values discarded |
| Run configuration manifest | ❌ only in the directory name and `launch.log` |
| Aggregated multi-run results | ❌ no aggregation exists |

## Recommended minimum additions

Ordered by value per unit effort, for when this work resumes:

1. **A run manifest** (`run_config.json`: all parameters, seed, git commit, timestamp)
   — small change, makes every result self-describing.
2. **Simulated-time makespan** instead of wall-clock — required before any timing
   result can be published.
3. **Per-task records** (release, assign, complete → completion and waiting time).
4. **An outcome join** from `/fleet_travel_stats` back onto the decision row — the
   prerequisite for outcome-aware learning.
5. **Cost decomposition columns** for the composite strategies — the prerequisite for
   real explainability.
