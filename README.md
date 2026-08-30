# Grid-Aware Multi-Robot Task Allocation (MRTA)

A ROS 2 (Jazzy) workspace for **explainable, grid-aware multi-robot task allocation**
in a warehouse setting. Robots move on a discrete grid via A\* path planning; every
allocation strategy scores robot–task pairs from the **same grid-path features and
physics energy model the simulator actually uses**. A headless simulator runs
thousands of repeatable, seeded trials with no Gazebo or Nav2.

This is the implementation base for the R&D project *"Outcome-Aware Inductive Logic
Programming for Explainable Grid-Aware Task Allocation in Multi-Robot Warehouse
Systems."* The Inductive Logic Programming (ILP) layer is planned; this repo delivers
the simulator, the feature/cost model, and the classical baseline strategies it learns
from and is compared against.

> **Full technical documentation lives in [`docs/`](docs/)** — architecture, per-package
> reference, algorithm analysis, grid/path cost, metrics, experiments, testing,
> troubleshooting, current status and research gaps. Start at
> [`docs/README.md`](docs/README.md). For a verified account of what is and is not
> implemented, see [`docs/AUDIT_2026_08_29.md`](docs/AUDIT_2026_08_29.md).

---

## Table of contents

1. [Architecture](#architecture)
2. [Repository layout](#repository-layout)
3. [Prerequisites](#prerequisites)
4. [Build](#build)
5. [Quick start](#quick-start)
6. [Running manually](#running-manually)
7. [Nodes](#nodes)
8. [Allocation strategies](#allocation-strategies)
9. [The grid and physics model](#the-grid-and-physics-model)
10. [Output files](#output-files)
11. [Testing](#testing)
12. [Roadmap](#roadmap)

---

## Architecture

Five ROS 2 packages. `mrta_core` is a pure-Python library (no ROS dependency) shared
by every node, so the simulator and the allocators compute distances, times and energy
from one source of truth.

```
                         order_generator
                               │ /order_requests (Order)
                               ▼
   /get_shelf_list      ┌──────────────────┐      /task_list (TaskList)
   /get_drop_off_pose   │  task_manager    │────────────────────────────►┌───────────────────┐
 ◄─────────────────────►│      _node       │                             │  fleet_manager    │
 │  shared_memory_node  │  (decision loop) │◄────────────────────────────│ (grid simulator,  │
 │  (shelf/inventory)   └──────────────────┘  /fleet_status              │  mock_fleet_mgr)  │
 │         ▲                 │        │        /fleet_travel_stats        └───────────────────┘
 │ /end_order│               │        │        /simulation/congestion            │
 └───────────┘               │        │                                          │ moves robots
              /ilp/task_allocation_event      /fleet_travel_stats                 │ cell-by-cell
                     │        │                       │                           │ via A*
                     ▼        ▼                       ▼
              ilp_dataset_logger              metrics_logger
              (per-candidate CSV)             (summary CSV)
```

**Data flow of one order:** `order_generator` publishes an `Order` → `task_manager`
turns it into a pickup→drop task, queries idle robots (`/fleet_status`) and shelf/drop
poses, scores candidates with the selected strategy, and sends waypoints to
`fleet_manager` via `/task_list`. The simulator drives the robot along an A\* grid path,
draining battery by the physics model, and publishes completion on
`/fleet_travel_stats`. The task manager then frees the robot and emits `/end_order`
(inventory) and `/ilp/task_allocation_event` (the decision trace).

---

## Repository layout

```
rnd_mrta_ilp_ws/
├── src/
│   ├── robot_interfaces/     # msg/srv/action contracts (ament_cmake)
│   ├── mrta_core/            # pure-Python engine (ament_python)
│   │   └── mrta_core/
│   │       ├── grid.py        # A* on a GridMap, path features
│   │       ├── world.py       # WorldGrid: metre <-> cell transform, ASCII maps
│   │       ├── kinematics.py  # physics time + energy model (RobotModel)
│   │       ├── models.py      # RobotState, TaskState
│   │       └── symbolic.py    # numeric -> symbolic ILP predicate vocabulary
│   ├── shared_memory_node/   # shelf/drop/inventory database + CSV layouts
│   ├── simulation_env/       # headless grid mock_fleet_manager + launch
│   └── task_management/      # task_manager, strategies, loggers, order generator
├── scripts/
│   └── run_experiment.sh     # one-command run + save results
├── Mrta_experiments/         # run outputs (git-ignored)
└── README.md
```

---

## Prerequisites

- **ROS 2 Jazzy** (`/opt/ros/jazzy`)
- **colcon** (`colcon build`)
- **Python 3.12** (stdlib only for `mrta_core`; nodes use `rclpy`)
- *(future ILP layer)* `swi-prolog`, `janus_swi`, `popper-ilp`

---

## Build

```bash
cd ~/rnd_mrta_ilp_ws
source /opt/ros/jazzy/setup.bash
colcon build
source install/setup.bash        # source the overlay after every build
```

Build a single package while iterating:

```bash
colcon build --packages-select task_management && source install/setup.bash
```

> `build/`, `install/` and `log/` each carry a `COLCON_IGNORE` marker. The previous
> workspace is kept only as the git-ignored archive `ilp_ws.zip` (not a directory, so
> colcon never sees it).

---

## Quick start

One command runs the full headless stack, fires a seeded order stream, streams live
progress, and saves everything under `Mrta_experiments/<strategy>_<timestamp>/`:

```bash
scripts/run_experiment.sh [strategy] [num_robots] [num_orders] [seed] [rate_hz] [time_scale]

# examples
scripts/run_experiment.sh                      # auction, 6 robots, 20 orders, seed 42
scripts/run_experiment.sh nearest 10 30 42     # nearest, 10 robots, 30 orders
scripts/run_experiment.sh score 6 20 7 5 20    # score, seed 7, 5 Hz, 20x real-time
```

Example output:

```
== launching headless stack: strategy=nearest robots=6 ==
== sending 15 orders (seed=42, rate=5.0Hz) ==
  progress: completed=8 / 15
  progress: completed=15 / 15
==================== RESULT ====================
strategy  n_assign  n_defer  n_completed  makespan_s  throughput_ops  mean_travel_m  mean_energy_pct  mean_alloc_runtime_ms
nearest   15        0        15           4.108       3.6516          44.067         13.365           0.0034
saved to: /home/.../Mrta_experiments/nearest_2026-07-30_12-34-23
```

---

## Running manually

Two terminals — the launch terminal now prints `[assign] ...` and `status:` lines so
you can watch activity live.

**Terminal A — bring up the stack:**

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ros2 launch task_management headless_warehouse.launch.py \
  strategy:=score num_robots:=6 time_scale:=20.0 \
  out_dir:=/home/you/rnd_mrta_ilp_ws/Mrta_experiments/score_run
```

**Terminal B — drive orders:**

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ros2 run task_management order_generator --ros-args \
  -p num_orders:=20 -p seed:=42 -p rate_hz:=5.0
```

> **Use an absolute `out_dir`.** ROS launch does **not** expand `~`; passing
> `out_dir:=~/Documents` creates a literal `~` folder. (The loggers now call
> `expanduser`, but absolute paths are safest.)

Just the simulator (no task manager), e.g. to test motion:

```bash
ros2 launch simulation_env sim_only.launch.py num_robots:=6 time_scale:=2.0
```

---

## Nodes

### `fleet_manager` — grid simulator
Package `simulation_env`, executable `mock_fleet_manager` (node name `fleet_manager`).
Simulates robots moving on the grid via A\*; live cell occupancy becomes congestion
(and A\* routes around it); battery drains by the physics model.

| Parameter | Default | Meaning |
|---|---|---|
| `num_robots` | 4 | fleet size |
| `world_size_m` | 30.0 | square world side length (metres) |
| `resolution_m` | 1.0 | metres per grid cell |
| `robot_speed_ms` | 2.0 | cruise speed (`v_max` of the physics model) |
| `time_scale` | 1.0 | run faster than real time (e.g. 20 = 20×) |
| `congestion_weight` | 1.0 | A\* penalty for congested cells |
| `grid_map_file` | "" | optional ASCII obstacle map |
| `battery_low_threshold` | 20.0 | enter charging at/below (%) |
| `battery_resume_threshold` | 80.0 | return to service at/above (%) |

**Services:** `task_list` (TaskList), `/get_robot_fleet_status`, `/reset_simulation`
**Publishes:** `/fleet_status` (FleetStatus, latched), `/fleet_travel_stats` (JSON),
`/simulation/congestion` (JSON), `/simulation/battery_updates` (JSON)

### `shared_memory_node` — warehouse database
Package `shared_memory_node`. Loads shelves + drop-off pose from a CSV; serves them and
tracks inventory.

| Parameter | Default | Meaning |
|---|---|---|
| `database_file` | `headless_shelves_100.csv` | shelf/drop CSV (first row = drop-off) |

**Services:** `/get_shelf_list`, `/shelf_query`, `/get_drop_off_pose`,
`/update_inventory`, `/get_robot_state`, `/get_robot_fleet_status`,
`/reset_shared_memory`
**Subscribes:** `/end_order` (decrement inventory), `/fleet_status`

Available layouts (`src/shared_memory_node/databases/`): `headless_shelves_100`,
`headless_shelves_100_large`, `headless_shelves_stress`.

### `task_manager_node` — allocation decision loop
Package `task_management`. Non-blocking loop: dispatches every pending task to an idle
robot each tick (no head-of-line blocking); re-queues (defers) when a strategy returns
no eligible robot. Builds the same `WorldGrid` + `RobotModel` as the simulator.

| Parameter | Default | Meaning |
|---|---|---|
| `allocation_strategy` | `auction` | one of the 12 strategy names |
| `allocator_seed` | 0 | RNG seed (for `random`) |
| `world_size_m` / `resolution_m` / `robot_speed_ms` | 30 / 1 / 2 | must match the simulator |
| `min_battery_after` | 20.0 | battery feasibility floor `b_min` (%) |

**Subscribes:** `/order_requests`, `/fleet_status`, `/simulation/congestion`,
`/fleet_travel_stats`
**Publishes:** `/ilp/task_allocation_event` (per-decision trace), `/end_order`
**Clients:** `/get_shelf_list`, `/get_drop_off_pose`, `/task_list`

### `ilp_dataset_logger` — trace logger
Package `task_management`. One CSV row per candidate robot per decision — the trace the
ILP pipeline will later convert into Prolog facts/examples.

| Parameter | Default |
|---|---|
| `out_dir` | `/tmp/mrta_run/ilp_logs` |

Columns: `timestamp, task_id, order_id, shelf_id, priority, strategy, decision,
chosen_robot, runtime_ms, candidate_robot, is_chosen, reachable, distance,
travel_time, congestion, energy_pct, battery, workload, cumulative_load,
battery_feasible, explanation`.

### `metrics_logger` — summary metrics
Package `task_management`. Rolling summary CSV: `strategy, n_assign, n_defer,
n_completed, makespan_s, throughput_ops, mean_travel_m, mean_energy_pct,
mean_alloc_runtime_ms`.

| Parameter | Default |
|---|---|
| `out_dir` | `/tmp/mrta_run` |

### `order_generator` — experiment driver
Package `task_management`. Publishes a reproducible order stream; same seed ⇒ identical
sequence across strategies (fair paired comparison).

| Parameter | Default | Meaning |
|---|---|---|
| `num_orders` | 20 | total orders |
| `rate_hz` | 1.0 | orders per second |
| `seed` | 42 | RNG seed |

---

## Allocation strategies

Switch with `strategy:=<name>`. All operate on `Candidate` features
(`task_management/features.py`) and return a robot id or defer.

| Name | Family | Rule |
|---|---|---|
| `random` | baseline | seeded uniform among reachable |
| `round_robin` | baseline | cycle robot ids |
| `nearest` | distance | `argmin D_ij` (grid path length) |
| `least_busy` | load | `argmin W_i` |
| `highest_battery` | energy | `argmax b_i` |
| `spt` | time | shortest processing time `argmin T` |
| `lpt` | load | least cumulative load (balancing) |
| `edf` | deadline | queue by deadline, then nearest |
| `heuristic` (`greedy`) | hand-crafted | feasible → least workload → nearest → most battery |
| `score` | cost | `argmin C_ij` |
| `bid` | market | first-price on task cost (load-blind) |
| `auction` | market | sealed bid `1/(1+C_ij)`, infeasible → 0 (load-aware) |

The composite strategies minimise the proposal's cost:

```
C_ij = λ1·T_ij + λ2·D_ij + λ3·G_ij + λ4·E_ij + λ5·W_i − λ6·q_j
```

where `T`=travel time, `D`=distance, `G`=congestion, `E`=energy, `W`=workload,
`q`=priority. Defaults live in `CostWeights` (`features.py`).

> **Before comparing strategies, read
> [`docs/05_MRTA_ALGORITHMS.md`](docs/05_MRTA_ALGORITHMS.md).** Because tasks are
> currently created with a fixed `priority=1` and a fixed deadline, and because a robot
> is only ever a candidate while idle (so `W_i` is always 0), the `W` and `q` terms are
> inert. Several of the names above are therefore *behaviourally identical* in the
> running system — measured: 13 names collapse to 5 distinct behaviours.

**Planned:** `hungarian`, `genetic` (batch); `manual_prolog`, `single/multi/
outcome-aware ILP` (learned).

---

## The grid and physics model

- **`WorldGrid`** (`mrta_core/world.py`) maps world metres ↔ integer grid cells and
  builds the `GridMap` A\* runs on. Loads ASCII obstacle maps (`#` = obstacle).
- **A\*** (`mrta_core/grid.py`) — 4-connected, admissible Manhattan heuristic, congestion
  as edge cost so paths bend around busy cells.
- **`RobotModel`** (`mrta_core/kinematics.py`) — physics time and energy:
  - **Time:** trapezoidal velocity profile `(v_max, a_max)` per straight run + in-place
    90° turns `(π/2)/ω_max`.
  - **Energy:** rolling-resistance work `μ(m+payload)g·L / η` + idle power over run time
    + turn power → joules → Wh → % of `battery_capacity_wh`.
  - Defaults model a warehouse AMR so a task drains a few percent — battery is a
    meaningful, discriminating feature.
- The **simulator drains the same energy** the allocators estimate, so predicted cost
  matches reality.

---

## Output files

Each run writes to `Mrta_experiments/<strategy>_<timestamp>/`:

```
metrics_<ts>.csv           # summary metrics for the run
ilp_logs/ilp_dataset_<ts>.csv   # per-candidate per-decision trace (ILP training data)
launch.log                 # full node stdout (assignments, status heartbeat)
order_gen.log              # order generator stdout
```

`Mrta_experiments/` is git-ignored (generated output).

---

## Testing

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash

# mrta_core (grid, world, kinematics)  -> 27 tests
python3 -m pytest src/mrta_core/tests -q

# allocation strategies                -> 16 tests
PYTHONPATH=src/task_management:$PYTHONPATH \
  python3 -m pytest src/task_management/test/test_allocators.py -q
```

---

## Roadmap

**Done:** interfaces · `mrta_core` (grid + physics) · shelf DB · grid simulator ·
task manager + 12 strategies · loggers · headless launch · run script.

**Next:**
- Batch allocators: Hungarian, Genetic Algorithm.
- ILP core: wire `symbolic.py` → dataset → Popper (`bias.pl`, learned `assign/reject`,
  `janus_swi` runtime), outcome-aware labelling.
- Scenarios (normal / high-load / congested / battery-stress / failure), deadlines and
  priorities on the task stream.
- `experiment_runner` sweeping all strategies × fleet sizes × seeds with statistics.
