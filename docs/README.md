# Documentation

## New here? Read these, in order

| | |
|---|---|
| **[START_HERE.md](START_HERE.md)** | from an empty computer to robots in Gazebo: one map, one config, one world |
| **[MAKE_A_MAP.md](MAKE_A_MAP.md)** | how to draw a warehouse in Excel that the simulator can run |
| **[HOW_IT_WORKS.md](HOW_IT_WORKS.md)** | the repo map and the data flow, the project status, and a 10-minute demo |
| **[REPO_ATLAS.html](REPO_ATLAS.html)** | the whole repository on one page, with diagrams; open it in a browser. Kept up to date with the code |

## Using the system

| Document | What it covers |
|---|---|
| [00_RUNBOOK.md](00_RUNBOOK.md) | every command, in depth, with expected output |
| [ARCHITECTURE.md](ARCHITECTURE.md) | how the code fits together: packages, execution path, invariants, file index |
| [18_TRAFFIC_TOPOLOGY.md](18_TRAFFIC_TOPOLOGY.md) | why roads are one-way, and what that costs |
| [19_MAP_V2_MIGRATION.md](19_MAP_V2_MIGRATION.md) | how the original V2 map was built |

## The project contract

These are kept up to date with every change, and they are what the
code is held to.

| Document | What it covers |
|---|---|
| [CURRENT_PHASE.md](CURRENT_PHASE.md) | exactly what is done, with measured results |
| [DECISIONS.md](DECISIONS.md) | every frozen design choice, and the reason for it |
| [ROADMAP.md](ROADMAP.md) | the phases, what each delivers, and their status |
| [EXPERIMENT_PROTOCOL.md](EXPERIMENT_PROTOCOL.md) | how experiments must be run to be fair |

## Folders

```text
figures/    figures used by the main README; generated, never drawn
            (scripts/plot_dataset.py)
images/     other images
agents/     instructions for AI coding assistants working on this repo
papers/     the ICAPS'26 PlanRob paper and presentation
archive/    old documents, kept for the record -- see below
```

## The archive

`archive/` holds documents 01–17 and the audit of 2026-08-29. They
describe the repository **as it was then**, and several of their claims
are now wrong. They are kept, unedited, so the history stays honest.
**Do not use them to learn how the system works today.**

Known-wrong claims, so nobody is misled if they do read them:

- **"12 allocation strategies."** At the time only one existed. Today
  three exist: `nearest_astar`, `nearest_manhattan`, `random`.
- **MAPF / space-time planning.** Removed at the 2026-09-03 simulator
  freeze; every run reported 0 conflicts and 0 replans.
- **Two-way roads, or Manhattan distance as path cost.** The warehouse
  is fully one-way; for 90.9% of shelf pairs the distance from A to B
  differs from B to A.
- **"MAPF conflicts" metric.** It was never MAPF; it is reported as
  `reservation violations`.
- **"No decision/outcome logging."** Built in Phase 8.
- **No battery model.** Battery V1 (charging, holding, preemption)
  exists.
- **The allocation window `min(available robots, pending)`.** Replaced
  by `W = fleet_size` after it was measured to reduce 87% of decisions
  to a single robot and a single order.
- **One fixed map.** Any map can now be drawn in Excel with its own
  lanes ([MAKE_A_MAP.md](MAKE_A_MAP.md)).
- **Test counts.** 317 tests as of 2026-10-23.

The ICAPS'26 results (makespan tables for fleets of 5/10/15/20) came
from an **earlier, different workspace** and are not reproducible from
this repository, which does not yet contain the ILP learner.

## Regenerating the figures

```bash
MPLBACKEND=Agg PYTHONPATH=$PP $PY \
    src/simulation_env/scripts/plot_dataset.py \
    --run legacy=runs/legacy --run continuous=runs/astar --run batched=runs/batched \
    --totals legacy=282.0,6430.0,264.6667 \
    --totals continuous=268.6667,5798.0,305.3333 \
    --totals batched=264.6667,5716.0,376.3333 \
    --out docs/figures
```

The three runs have to be recorded first; see the main README.
