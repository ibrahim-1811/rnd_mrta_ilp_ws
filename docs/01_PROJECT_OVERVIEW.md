# 01 — Project overview

## Research topic

> *Outcome-Aware Inductive Logic Programming for Explainable Grid-Aware Task
> Allocation in Multi-Robot Warehouse Systems.*

## What this repository is today

A **headless, grid-aware MRTA simulation and baseline-strategy testbed**.

It provides:

- a discrete warehouse grid with A\* path planning (`mrta_core`),
- a physics-based travel-time and energy model shared by simulator and allocators,
- a headless multi-robot simulator that needs neither Gazebo nor Nav2,
- **12 classical online allocation strategies** behind one switchable interface,
- per-decision trace logging intended as future ILP training data,
- summary metrics per run, and a one-command run script.

## What this repository is *not* yet

This is the part most likely to be misread, so it is stated plainly:

- **There is no ILP implementation.** No Inductive Logic Programming learner, no
  Popper integration, no Prolog runtime binding, no learned rule file, and no
  `outcome_aware_ilp` allocator. Verified by exhaustive search for solver and ILP
  libraries across the tree — see [06_OUTCOME_AWARE_ILP.md](06_OUTCOME_AWARE_ILP.md).
- **Nothing is "outcome-aware."** No execution outcome (success, lateness, deadline
  miss, energy overrun) is ever fed back into any allocation decision.
- **No optimisation solver is used.** No ILP/MILP solver, no Hungarian assignment, no
  genetic algorithm. Every implemented strategy is a greedy per-task `argmin`/`argmax`
  over the currently idle robots.

The repository root `README.md` is accurate on this point: it describes the ILP layer
as *planned*.

## Relationship to the ICAPS 2026 material

`docs/` contains a PlanRob @ ICAPS 2026 presentation reporting an ILP policy learned
with Popper, executed through a Janus/SWI-Prolog bridge, and benchmarked against five
baselines across fleet sizes 5/10/15/20.

Those results came from a **previous workspace**, archived (git-ignored) at the
repository root as `ilp_ws.zip`. That archive contains an `ilp_learning` package and a
`learned_rules.pl` file; neither exists in `src/` here.

**The current repository is a clean rewrite that has not yet ported the ILP layer.**
It improves on the old system in the simulation substrate — grid + A\* instead of
Euclidean distance, a physics energy model instead of a flat `0.7 %/m` drain — but it
has regressed on the research contribution, which is currently absent.

## Intended full pipeline

```
warehouse CSV (shelves + drop-off)
        |
        v
shared_memory_node  ──── serves shelf/drop poses, tracks inventory
        |
        v
order_generator  ──────── seeded stream of orders
        |
        v
task_manager_node  ─────── builds candidates, runs the selected strategy
        |
        v
mrta_core (WorldGrid + A* + RobotModel)
        |
        v
mock_fleet_manager  ────── drives robots cell-by-cell, drains battery
        |
        v
metrics_logger + ilp_dataset_logger  ──> CSV under Mrta_experiments/
```

This pipeline is **implemented and verified end to end**; see
[AUDIT_2026_08_29.md](AUDIT_2026_08_29.md) for the executed runs.

The missing stage is everything after the CSV: there is no consumer of
`ilp_dataset_*.csv`, so the arrow from trace data back into a learned policy does not
exist yet.

## Reading order

- To run it: [09_BUILD_AND_INSTALL.md](09_BUILD_AND_INSTALL.md) →
  [10_LAUNCH_GUIDE.md](10_LAUNCH_GUIDE.md)
- To understand the algorithms: [05_MRTA_ALGORITHMS.md](05_MRTA_ALGORITHMS.md) →
  [07_GRID_AND_PATH_COST.md](07_GRID_AND_PATH_COST.md)
- To plan the research work: [16_CURRENT_STATUS.md](16_CURRENT_STATUS.md) →
  [17_RESEARCH_GAPS.md](17_RESEARCH_GAPS.md)
