# 12 — Experiments

What the repository supports today for research experimentation, verified by running it.

## What you can vary

| Dimension | Supported? | How | Notes |
|---|---|---|---|
| Allocator | ✅ | `strategy:=<name>` | 13 names, but see the collapse warning below |
| Robot count | ✅ | `num_robots:=N` | verified 3–8 |
| Task/order count | ✅ | `-p num_orders:=N` | |
| Order arrival rate | ✅ | `-p rate_hz:=X` | **fixed-rate timer, not stochastic** |
| Order seed | ✅ | `-p seed:=N` | controls shelf choice only |
| Map / warehouse layout | ⚠ | `shelves_csv:=<path>` | 4 CSVs ship; see size trap below |
| World size / resolution | ✅ | `world_size_m:=`, `resolution_m:=` | must match across nodes |
| Robot speed | ✅ | `robot_speed_ms:=` | |
| Time scaling | ✅ | `time_scale:=` | wall-clock only |
| Allocator seed | ⚠ | `-p allocator_seed:=N` | **not forwarded by `run_experiment.sh`** |
| Battery floor `b_min` | ⚠ | `-p min_battery_after:=X` | node param, **not in any launch file** |
| Initial battery | ⚠ | `-p initial_battery:=X` | node param, not in any launch file |
| Congestion weight | ⚠ | `-p congestion_weight:=X` | simulator only; allocator always uses 1.0 |
| **Obstacle density** | ❌ | — | no maps ship; not exposed in launch; allocator never loads obstacles |
| **Task priority** | ❌ | — | hard-coded to `1` (`task_manager.py:155`) |
| **Deadlines** | ❌ | — | hard-coded to `now + 300.0` (`task_manager.py:156`) |
| **ILP / cost weights** | ❌ | — | `CostWeights` hard-coded in `features.py`; requires source edit + rebuild |
| **Energy model parameters** | ❌ | — | `RobotModel` defaults hard-coded; only `v_max` is overridable |
| **Robot start positions** | ❌ | — | always the free cells nearest the drop-off |
| **Robot failures** | ❌ | — | `RobotStatus.FAILED` never set |

> **Layout size trap:** `headless_shelves_100_large.csv` spans X ±45 m and Y to 95 m.
> At the default `world_size_m:=30.0`, `WorldGrid.to_cell` **silently clamps** every
> out-of-range shelf onto the grid boundary. Use `world_size_m:=95.0` (or larger) with
> that layout, and remember to pass the same value to the task manager.

## Headless execution — ✅ fully supported

The entire stack is headless. No Gazebo, no Nav2, no RViz, no display. `time_scale`
compresses wall-clock time; at `time_scale:=20.0` a 40-order, 8-robot run completes in
about 8 seconds of simulated makespan and ~20 s of wall clock including bring-up.

This is a real strength — thousands of trials are computationally feasible.

## Batch execution — ❌ NOT IMPLEMENTED

There is **no experiment runner**. `scripts/run_experiment.sh` executes exactly **one**
configuration per invocation:

- one strategy, one fleet size, one seed, one repetition;
- it restarts the entire five-node stack each time (≈8 s bring-up overhead);
- results land in a fresh timestamped directory with no run index, no manifest, and no
  aggregation.

There is no sweep over strategies × fleet sizes × seeds, no repetition loop, no results
aggregation, and no statistical analysis of any kind. The `/reset_simulation` and
`/reset_shared_memory` services that would make fast in-process repetition possible are
implemented but **called by nothing**.

Sweeping today means a shell loop of your own, as used for this audit:

```bash
for s in nearest score auction heuristic lpt random round_robin; do
  scripts/run_experiment.sh "$s" 8 40 42 10.0 20.0
done
```

…followed by manual collection of the per-run CSVs. There is no tooling for the
collection step.

## Reproducibility — ⚠ partial, and this is the important finding

Two runs with identical parameters and seed were compared at the trace level.

**What *is* reproducible:**

- The order stream. `order_generator` seeds `random.Random(seed)` and each order's shelf
  is drawn from it, so the same seed gives the same order sequence.
- The **assignment sequence**. Across repeated runs of `nearest` and `score` with seed
  42, every task→robot mapping was byte-identical.
- Aggregate distance and energy: `mean_travel_m` and `mean_energy_pct` matched exactly
  between repeated runs (38.7 / 11.661 for `nearest`).

**What is *not* reproducible — the logged features themselves:**

Diffing the two `nearest` runs' trace CSVs, ignoring timestamps, showed the assignments
identical but the **feature values differing**:

```
run A:  order_3_task_3 ... distance=8.0  travel_time=13.766  congestion=1.0  energy_pct=6.256
run B:  order_3_task_3 ... distance=8.0  travel_time=9.426   congestion=3.0  energy_pct=5.522
```

`congestion`, `travel_time`, `energy_pct` and `cumulative_load` all vary run to run.

**Root cause:** the simulator advances on `time.sleep` against **wall clock**, with one
thread per in-flight task, while congestion is sampled at the instant of each decision.
Where robots happen to be when a decision fires is therefore non-deterministic. A\* then
routes slightly differently, changing path shape, time and energy.

**Consequences for the research:**

1. `makespan_s` and `throughput_ops` are **wall-clock measurements**, not simulated
   time. They vary run to run (3.751 vs 3.756 s for identical inputs) and they depend on
   machine load. They are not valid scientific quantities as currently computed.
2. The ILP training data is **not reproducible**, so a learner trained on it cannot be
   re-trained to the same result from the same seed.
3. Cost-based strategies could in principle flip a decision between identical runs when
   two candidates are close. It did not happen in the audit's runs, but nothing prevents
   it.

Fixing this needs a **simulation clock** (deterministic stepping rather than
`time.sleep`), which is a substantial but well-understood change. Flagged P0 in
[17_RESEARCH_GAPS.md](17_RESEARCH_GAPS.md).

## Can algorithms be compared fairly today?

**Not yet.** Three independent problems:

### 1. Several strategies are behaviourally identical

At 4 robots / 10 orders, the 13 names collapse to **5 distinct behaviours**; even under
contention `nearest ≡ least_busy ≡ spt`. Comparing them produces a table of duplicated
rows presented as independent results. See
[05_MRTA_ALGORITHMS.md](05_MRTA_ALGORITHMS.md).

### 2. The outcome spread is smaller than the noise

Across all strategies at 8 robots / 40 orders:

| Metric | Range across all strategies | Run-to-run variation, same strategy |
|---|---|---|
| `mean_travel_m` | 45.375 – 45.675 (**0.7 %**) | 0 (deterministic) |
| `mean_energy_pct` | 13.719 – 13.976 (**1.9 %**) | 0 (deterministic) |
| `makespan_s` | 7.606 – 8.005 (**5.2 %**) | ±0.005 s wall-clock jitter |

The scenario is too easy to separate the policies. With 8 robots, 40 orders, an empty
grid and no deadlines, almost any sane policy performs almost identically. Harder
regimes — obstacles, tighter fleets, deadline pressure, battery stress — are needed
before a comparison means anything.

### 3. No statistics

Single runs, no repetitions, no confidence intervals, no significance testing, no
seed sweep. Nothing in the repository computes a mean over trials, let alone a variance.

## Metrics currently logged

From `metrics_logger.py` — **nine fields, one row per run**:

| Metric | Logged? | Definition |
|---|---|---|
| Makespan | ⚠ | wall-clock seconds between first decision and last completion |
| Throughput | ⚠ | `n_completed / makespan` — inherits the wall-clock problem |
| Travel distance | ✅ | mean metres per completed task |
| Energy | ✅ | mean battery percent per completed task |
| Allocation time | ✅ | mean allocator runtime, ms |
| Assignments / deferrals | ✅ | `n_assign`, `n_defer` |
| Completions | ✅ | `n_completed` |
| **Task completion time** | ❌ | never computed per task |
| **Waiting time** | ❌ | queue delay never measured |
| **Deadline violations** | ❌ | no deadline variance exists to violate |
| **Robot utilisation** | ❌ | not computed |
| **Workload balance** | ❌ | not computed (and `W ≡ 0`) |
| **Congestion metric** | ❌ | published live, never aggregated |
| **Path planning time** | ❌ | not measured separately from allocation |
| **Distance/energy per robot** | ❌ | only fleet means |

Seven of the thirteen metrics a comparison study would normally report are missing, and
two of the six present are wall-clock-contaminated. See
[13_METRICS_AND_OUTPUTS.md](13_METRICS_AND_OUTPUTS.md).

## Summary

| Capability | Status |
|---|---|
| Headless execution | ✅ COMPLETE |
| Parameter variation (fleet, orders, rate, seed, map, world) | ✅ FUNCTIONAL |
| Parameter variation (priority, deadline, obstacles, weights) | ❌ NOT IMPLEMENTED |
| Single-run automation | ✅ COMPLETE |
| Batch / sweep automation | ❌ NOT IMPLEMENTED |
| Multi-trial repetition | ❌ NOT IMPLEMENTED |
| Results aggregation | ❌ NOT IMPLEMENTED |
| Statistical analysis | ❌ NOT IMPLEMENTED |
| Deterministic reproducibility | ⚠ PARTIAL — decisions yes, features no |
| Fair algorithm comparison | ❌ NOT YET — see the three problems above |
