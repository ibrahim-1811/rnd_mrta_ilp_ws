# 03 — Repository structure

Repository root: `/home/ibrahim/rnd_mrta_ilp_ws`
Branch: `main` · Commit at audit: `ec6ecd6` · Tracked files: 84

## Tracked tree

```
rnd_mrta_ilp_ws/
├── src/
│   ├── robot_interfaces/          # ament_cmake — msg/srv/action contracts
│   │   ├── msg/    (9 files)      # Order, Product, Task, RobotStatus, FleetStatus,
│   │   │                          #   ShelfStatus, Logs, GoalStartMsg*, GoalReachMsg*
│   │   ├── srv/    (7 files)      # GetShelfList, GetPose, TaskList, ShelfQuery,
│   │   │                          #   InventoryUpdate, GetRobotStatus, GetRobotFleetStatus
│   │   └── action/ (2 files)      # ProcessOrder*, TaskList*        (* = unused)
│   │
│   ├── mrta_core/                 # ament_python — pure Python, NO ROS dependency
│   │   ├── mrta_core/
│   │   │   ├── grid.py            # GridMap, astar_path, extract_path_features
│   │   │   ├── world.py           # WorldGrid: metre <-> cell, ASCII map loader
│   │   │   ├── kinematics.py      # RobotModel: trapezoidal time + energy model
│   │   │   ├── models.py          # RobotState, TaskState, RobotStatus
│   │   │   └── symbolic.py        # Prolog predicate vocabulary  (UNUSED by any node)
│   │   └── tests/  (5 files)      # 27 tests
│   │
│   ├── shared_memory_node/        # ament_python — warehouse database node
│   │   ├── shared_memory_node/shared_memory_node.py
│   │   └── databases/  (4 CSVs)   # shelf layouts
│   │
│   ├── simulation_env/            # ament_python — headless grid simulator
│   │   ├── simulation_env/mock_fleet_manager.py
│   │   └── launch/sim_only.launch.py
│   │
│   └── task_management/           # ament_python — the MRTA brain
│       ├── task_management/
│       │   ├── task_manager.py        # decision loop node
│       │   ├── allocation_strategy.py # the 12 strategies + registry
│       │   ├── features.py            # Candidate, CostWeights, build_candidates
│       │   ├── order_generator.py     # seeded order stream
│       │   ├── metrics_logger.py      # summary CSV
│       │   └── ilp_dataset_logger.py  # per-candidate trace CSV
│       ├── launch/headless_warehouse.launch.py
│       └── test/test_allocators.py    # 16 tests
│
├── scripts/run_experiment.sh      # one-command single run
├── docs/                          # this documentation + ICAPS presentation material
├── pixi.toml, pixi.lock           # conda/pixi env (NOT used by the ROS build)
└── .gitignore
```

## Code size (tracked source only)

| Category | Lines |
|---|---:|
| Python, production | 2,477 |
| Python, tests | 509 |
| Interface definitions (msg/srv/action) | 101 |
| Shell | 62 |
| **Total tracked source** | **~3,150** |

This is a small, readable codebase — every production file is under 400 lines.

## Untracked and generated content

| Path | Tracked? | Notes |
|---|---|---|
| `README.md` | **NO — untracked** | 367 lines, accurate, and the primary user-facing doc. **It has never been committed.** See warning below. |
| `build/`, `install/`, `log/` | ignored | colcon artifacts; each carries a `COLCON_IGNORE` marker |
| `Mrta_experiments/` | ignored | all run outputs land here |
| `data/raw`, `data/processed`, `data/results` | ignored | **all three are empty** — created but never written to by any code |
| `configs/` | tracked dir, **empty** | no configuration files exist; no code reads from it |
| `ilp_ws.zip` (152 MB) | ignored | archived previous workspace containing the ILP implementation |
| `ICAPS 26 PlanRob Presentation.zip` | ignored | duplicate of the tracked `docs/` presentation directory |
| `.pixi/`, `.pytest_cache/`, `.ruff_cache/` | ignored | tool caches |

> ### ⚠ `README.md` is untracked
>
> The repository's main README — the most accurate documentation in the project — is
> **not under version control**. `git status` reports it as untracked. A fresh
> `git clone` of this repository would **not** include it, and a stray `git clean -fd`
> would delete it.
>
> This is the single highest-value, lowest-effort fix available: `git add README.md`.
> Per the audit's terms of reference nothing was committed, so this is left for you.

## Empty scaffolding

`configs/`, `data/raw/`, `data/processed/`, and `data/results/` are empty directories
with no reader or writer anywhere in the source. All runtime output goes to
`Mrta_experiments/` (via `run_experiment.sh`) or `/tmp/mrta_run` (the launch default).

If you intend `data/` to be the canonical output location, nothing currently points
there — see [13_METRICS_AND_OUTPUTS.md](13_METRICS_AND_OUTPUTS.md).

## `pixi.toml` is not part of the build

`pixi.toml` declares a conda environment (Python 3.12, numpy, pandas, scipy, networkx,
matplotlib, pytest, ruff, mypy, **swi-prolog**) plus `test`/`lint`/`format` tasks.

**No part of the ROS 2 build or runtime uses it.** The nodes run under the system
Python 3.12 that ROS 2 Jazzy provides, and none of them import numpy, pandas, scipy or
networkx — `mrta_core` is deliberately stdlib-only. The `swi-prolog` dependency is
provisioning for the unbuilt ILP layer; `swipl` is not on `PATH` in the current
environment and nothing calls it.

Treat `pixi.toml` as forward-looking scaffolding, not as the build environment. See
[09_BUILD_AND_INSTALL.md](09_BUILD_AND_INSTALL.md).
