# 06 — Outcome-Aware ILP

This is the central research contribution of the project. This document reports its
implementation status.

## Status: NOT IMPLEMENTED

There is no Inductive Logic Programming code in this repository.

### Evidence

1. **No allocator exists.** The strategy registry contains exactly 13 names, none of
   which is an ILP variant. The node itself enumerates them on error:

   ```
   known: ['auction', 'bid', 'edf', 'greedy', 'heuristic', 'highest_battery',
           'least_busy', 'lpt', 'nearest', 'random', 'round_robin', 'score', 'spt']
   ```

2. **No solver or ILP library anywhere.** A tree-wide search for `pulp`, `mip`,
   `cvxpy`, `ortools`, `scipy.optimize`, `linear_sum_assignment`, `popper`, `janus`,
   `hungarian` and `genetic` across `src/`, `scripts/` and all `package.xml` files
   returns only **documentation strings and one unused conda dependency**
   (`swi-prolog` in `pixi.toml`). No `.pl` file exists in `src/`. `swipl` is not
   installed on `PATH`.

3. **No learner, no rule file, no bias file.** No `bias.pl`, no `learned_rules.pl`, no
   dataset→examples converter, no training script.

4. **Nothing consumes the training data.** `ilp_dataset_logger` faithfully writes
   `ilp_dataset_*.csv`, and **no code anywhere reads it back**. The data pipeline
   terminates at the CSV.

5. **The source says so.** `allocation_strategy.py:15-16`:
   *"Batch/optimization (hungarian, genetic) and symbolic/learned (prolog, ILP
   variants) allocators are added in later passes."* The root `README.md` likewise
   describes the ILP layer as planned.

### Consequently

Every item requested for this section — decision variables, objective function,
constraints, robot eligibility constraints, capacity/battery/deadline/workload
constraints, path-cost incorporation, outcome terms, normalisation, solver
configuration, infeasibility handling, returned allocation — **has no implementation to
document.** Writing equations here would be fabrication.

## "What makes it Outcome-Aware rather than a weighted assignment optimizer?"

**Nothing in the current code justifies the phrase "Outcome-Aware."**

The evidential test for outcome-awareness is whether *realised execution results* feed
back into future allocation decisions. In this system they do not:

- `/fleet_travel_stats` carries the only realised outcome data (`travel_dist`,
  `battery_consumed`). Its two subscribers are `metrics_logger` (aggregates for the
  summary CSV) and `task_manager._on_travel_done` (`task_manager.py:160-173`).
- `_on_travel_done` uses that message solely to **decrement `workload`, increment
  `_completed`, and publish `/end_order`**. It reads neither `travel_dist` nor
  `battery_consumed`. The realised values are discarded.
- No predicted-vs-actual comparison exists. `cumulative_load` accumulates the
  **predicted** `proc_time` at assignment time and is never corrected against what
  actually happened.
- No outcome label — success, lateness, deadline miss, energy overrun, deferral cost —
  is computed, stored, or used. `ilp_dataset_*.csv` has no outcome column at all; its
  only label is `is_chosen`, i.e. what the teacher policy *did*, not how it *turned out*.

What exists today is a **greedy per-task weighted-sum selector**: `argmin` of
`C_ij = λ1T + λ2D + λ3G + λ4E + λ5W − λ6q` over the idle robots. And as
[05_MRTA_ALGORITHMS.md](05_MRTA_ALGORITHMS.md) shows, `λ5W` and `λ6q` are inert because
`W ≡ 0` and `q ≡ 1`, so it is in practice a weighted sum of **time, distance,
congestion and energy** — all of them *predictions made before execution*.

This is not a criticism of the work done; it is an accurate statement of where the
project stands. The simulation substrate that outcome-aware learning would need is
built and working. The learning layer is not started.

## What exists that is *preparatory*

Three assets are genuinely useful groundwork:

### 1. `mrta_core/symbolic.py` — the predicate vocabulary

A complete, 27-test-covered mapping from numeric features to Prolog-style predicates:

| Group | Predicates |
|---|---|
| Robot state | `idle/1`, `busy/1`, `failed/1` |
| Battery | `battery_high/1`, `battery_medium/1`, `battery_low/1` |
| Workload | `low_workload/1`, `medium_workload/1`, `high_workload/1` |
| Task | `high_priority/1`, `medium_priority/1`, `low_priority/1` |
| Path | `reachable/2`, `blocked_path/2`, `short_path/2`, `medium_path/2`, `long_path/2` |
| Congestion | `low_congestion/2`, `medium_congestion/2`, `high_congestion/2` |
| Feasibility | `battery_feasible/2`, `battery_insufficient/2` |

Thresholds are configurable via `PredicateThresholds` (`symbolic.py:8-22`), and
`as_prolog_facts()` serialises a predicate list to Prolog syntax.

**This module has no caller.** It is a well-built, unused component.

> ⚠ **Unit mismatch.** `PredicateThresholds` uses `battery_high = 0.70`,
> `battery_low = 0.30`, `min_battery_after_task = 0.20` — i.e. battery as a **0–1
> fraction**. The rest of the system uses **percent 0–100** (`RobotStatus.battery_level`,
> `min_battery_after = 20.0`, `energy_pct`). Wiring `symbolic.py` to live data without
> converting units would classify every robot as `battery_high` and every task as
> `battery_feasible`. The tests pass because they use 0–1 fixtures throughout.

### 2. The trace dataset

`ilp_dataset_*.csv` gives one row per candidate robot per decision with 21 columns —
the right *shape* for ILP examples. Its limitations as training data are analysed in
[13_METRICS_AND_OUTPUTS.md](13_METRICS_AND_OUTPUTS.md); the two most serious are that
**half of all decisions log only one candidate** (no contrastive negative example) and
that **`workload` and `priority` are constant**, so two of the intended predicate
families carry zero information.

### 3. The prior workspace

`ilp_ws.zip` (152 MB, git-ignored, at the repository root) contains the earlier system,
including an `ilp_learning` ROS package and a `learned_rules.pl`. That is where the
ICAPS 2026 results came from. It is the natural reference for porting the ILP layer
into this cleaner grid-aware substrate.

## Reproducibility of the ICAPS 2026 claims

The presentation in `docs/` reports:

- a learned policy `assigned(T,R) :- closest_least_busy_suitable(T,R).` and
  `defer(T) :- no_suitable_robot(T).`, induced by Popper;
- makespan tables for 6 strategies × 4 fleet sizes × 3 trials;
- an explanation study over 600 decisions;
- allocator runtimes of 5.75 ms (n=5) to 33.27 ms (n=15).

**None of this is reproducible from this repository.** There is no ILP, no multi-trial
batch runner, no fleet-size sweep, and no per-decision explanation export in the form
described. For scale, the allocator runtimes measured here are **0.003–0.015 ms** —
three to four orders of magnitude below the reported figures, because a greedy `argmin`
over ≤8 candidates is not the same computation as a Prolog query.

Anyone reading the presentation alongside this repository should understand the two as
describing **different systems**.

## Minimum work to make the title accurate

Documented here as scope, not as a plan to execute (see
[17_RESEARCH_GAPS.md](17_RESEARCH_GAPS.md) for prioritisation):

1. Make `priority` and `deadline` real, varying task attributes.
2. Break the `W_i ≡ 0` invariant so workload is a meaningful feature (e.g. allow task
   queueing per robot).
3. Record realised outcomes per task and join them back onto the decision rows.
4. Define the outcome label the learner targets.
5. Build the CSV→Prolog examples/bias converter on top of `symbolic.py` (fixing the
   unit mismatch).
6. Integrate a learner and a runtime rule evaluator, and register it as a strategy.
7. Only then is there an "Outcome-Aware ILP" to compare against the baselines.
