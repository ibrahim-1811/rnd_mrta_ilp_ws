# 00 — Runbook: every command, from scratch

Verified end to end on branch `traffic/rmf-integration`, ROS 2 Jazzy,
Gazebo (gz-sim), 2026-09-03. Every command below was executed; the
numbers quoted are real output, not estimates.

This is the operational document. For *why* the aisles are one-way see
[18_TRAFFIC_TOPOLOGY.md](18_TRAFFIC_TOPOLOGY.md).

---

## 0. Shell setup

Two interpreters are in play and mixing them is the most common
mistake:

| Interpreter | Use for | Why |
|---|---|---|
| `.pixi/envs/default/bin/python` | headless science, all `scripts/*.py` | has numpy/pandas/scipy |
| ROS 2 Jazzy python | anything launched by `ros2 launch` | needs rclpy |

`openpyxl` is **not** in the pixi environment. The XLSX map loader
needs it, so the system copy is put on `PYTHONPATH`:

```bash
cd ~/rnd_mrta_ilp_ws

export PY=.pixi/envs/default/bin/python
export PP=src/mrta_core:src/simulation_env:src/task_management:/usr/lib/python3/dist-packages
```

Keep those two exports in every headless shell. Anything in this
document that starts `PYTHONPATH=$PP $PY` is a headless command;
anything that starts `ros2` needs the ROS overlay instead:

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
```

> To remove the `PYTHONPATH` workaround permanently, add `openpyxl` to
> `[dependencies]` in `pixi.toml` and re-solve the environment.

---

## 1. Build

```bash
cd ~/rnd_mrta_ilp_ws
source /opt/ros/jazzy/setup.bash

colcon build --packages-select \
    mrta_core task_management simulation_env rmf_grid_traffic \
    robot_interfaces gazebo_fleet_pose_transport

source install/setup.bash
```

Do **not** mix `--symlink-install` into a tree that was built without
it. It leaves `share/simulation_env/worlds/` empty, and `gz sim` then
fails to find the world. If that happens:

```bash
rm -rf build/simulation_env install/simulation_env
colcon build --packages-select simulation_env
```

---

## 2. Scenarios: one config drives everything

The config file is the single source of truth. Map, fleet size, cell
size, order file and Gazebo world name all live in it, and every other
artefact is derived:

```
warehouse_sim*.yaml            <- the one thing you edit
   │
   ├── warehouse.map ────────► CSV_To_Gazebo.xlsx   (hand-authored)
   │                                │
   │                                ├─► worlds/<gazebo.world_name>.world
   │                                │      geometry + exactly
   │                                │      fleet.robot_count robots
   │                                │
   │                                └─► warehouse_nav_graph.yaml
   │                                       RMF lanes, from the lane policy
   │
   └── orders.file ──────────► orders*.csv
```

### 2.1 Build a scenario

Change the config, then run one command. It regenerates the world and
the nav graph and then runs both topology gates, so a scenario can
never be half-updated:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/build_scenario.py \
    --config src/simulation_env/config/warehouse_sim.yaml

colcon build --packages-select simulation_env rmf_grid_traffic
source install/setup.bash
```

It refuses to generate anything if `fleet.robot_count` exceeds the
staging cells the map provides, rather than writing a world the fleet
cannot be placed in.

### 2.2 Worked example — a 10-robot scenario

```bash
sed -e 's/^  robot_count: 18$/  robot_count: 10/' \
    -e 's/^  world_name: warehouse_grid$/  world_name: warehouse_10/' \
    src/simulation_env/config/warehouse_sim.yaml \
    > src/simulation_env/config/warehouse_sim_10.yaml

PYTHONPATH=$PP $PY src/simulation_env/scripts/build_scenario.py \
    --config src/simulation_env/config/warehouse_sim_10.yaml

colcon build --packages-select simulation_env rmf_grid_traffic
source install/setup.bash

ros2 launch simulation_env warehouse_sim.launch.py \
    config_file:=$PWD/src/simulation_env/config/warehouse_sim_10.yaml \
    orders:=true
```

Verified: the world gets **10** robot models, the launch resolves
`warehouse_10.world` on its own, and the run reports
`10 robot poses pushed to Gazebo world 'warehouse_10'`,
`100/100 orders`, `10/10` home, makespan `512.0 s`.

**Always give each scenario its own `gazebo.world_name`.** The launch
derives the world path from it. Reuse a name and you overwrite the
other scenario's world; the fleet size and the rendered world then
disagree.

### 2.3 What to change, and what it forces

| Change in config | Rebuild needed |
|---|---|
| `fleet.robot_count` | `build_scenario.py` — the world bakes in one model per robot |
| `warehouse.map` | `build_scenario.py` — world **and** nav graph, plus the topology gate |
| `gazebo.world_name` | `build_scenario.py` — writes a new world file |
| `orders.file` | nothing; read at runtime |
| `fleet.speed_mps` | nothing; read at runtime |
| `service.*`, `mission.*`, `visualization.*` | nothing |

Changing the lane policy in `warehouse_routing.py` also requires
`build_scenario.py`, because the RMF nav graph is derived from it.

### 2.4 Naming in Gazebo

Every semantic cell is its own named model, so you can read a pose
straight off the entity tree or with `gz model -m <name> -p`:

```
staging_001 .. staging_018      shelf_001 .. shelf_072
dropoff_001 .. dropoff_040      charging_001 .. charging_016
robot_001   .. robot_<N>        blocked_0000 ...
```

Staging tiles always reflect the **map** (18 cells here), while robot
models reflect `fleet.robot_count`. A 10-robot scenario therefore shows
18 staging tiles with 10 occupied — that is correct, not a bug.

### 2.5 Regenerating individual artefacts

`build_scenario.py` calls these; run them directly only if you want one
in isolation.

```bash
# Gazebo world
PYTHONPATH=$PP $PY src/simulation_env/scripts/generate_gazebo_grid_world.py \
    --config src/simulation_env/config/warehouse_sim.yaml

# RMF nav graph (--config, or --map for a bare XLSX)
PYTHONPATH=$PP $PY src/rmf_grid_traffic/scripts/generate_nav_graph.py \
    --config src/simulation_env/config/warehouse_sim.yaml \
    --output src/rmf_grid_traffic/config/warehouse_nav_graph.yaml

PYTHONPATH=$PP $PY src/rmf_grid_traffic/scripts/verify_nav_graph.py \
    --config src/simulation_env/config/warehouse_sim.yaml \
    --graph src/rmf_grid_traffic/config/warehouse_nav_graph.yaml
```

### 2.6 The map

`src/simulation_env/maps/CSV_To_Gazebo.xlsx` is edited by hand; there
is no generator. Cell colours carry meaning:

| Colour | Meaning |
|---|---|
| orange | coordinate border, not part of the warehouse |
| red | blocked / non-traversable |
| blank white | normal floor |
| merged named region | `SHELVES`, `DROP ZONE`, `CHARGING`, `STAGE` |

Current map: **17 × 35**, 118 blocked, 72 shelves, 40 dropoffs, 18
staging, 16 charging. The staging count is the hard ceiling on
`fleet.robot_count` — add `STAGE` cells to run more than 18 robots.

### 2.7 Orders

Seeded and deterministic — same seed in, byte-identical file out.

```bash
# 500 orders, written alongside the committed 100-order baseline
$PY src/simulation_env/scripts/generate_orders.py \
    --orders 500 --seed 42 \
    --output src/simulation_env/config/orders_500.csv
```

> ⚠️ With no `--output` it **overwrites** `config/orders.csv`, the
> committed reference workload. Always pass `--output`, or back the
> file up first.

Because the generator draws from one sequential RNG stream, orders
`O0001`–`O0100` in a 500-order file are byte-identical to the
100-order baseline. A larger file is a strict superset, so earlier
results stay comparable.

---

## 3. Headless runs

This is the scientific path: no ROS, no Gazebo, no display.

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py [options]
```

| Flag | Default | Meaning |
|---|---|---|
| `--orders N` | `0` = whole CSV | how many logical orders |
| `--robots N` | `0` = config value | fleet size |
| `--speed M` | `0` = config value | m/s |
| `--strategy NAME` | `nearest_astar` | allocation policy |
| `--progress S` | `10.0` | simulated seconds between progress lines; `0` = off |
| `--config PATH` | `config/warehouse_sim.yaml` | which config |
| `--quiet` | off | suppress per-dispatch lines |

### Examples

```bash
# the committed baseline
PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py --orders 100 --progress 0

# small, fast
PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py --orders 5 --robots 2

# a sweep
for r in 6 12 18; do for o in 18 50 100; do
  PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
      --robots $r --orders $o --progress 0 | grep -E "completed|makespan"
done; done
```

### Two silent caps to know about

Both `--orders` and `--robots` are applied as plain **list slices**.
Asking for more than exists is not an error and prints no warning:

* `--orders 300` against the 100-order CSV silently runs **100**.
  To really run 300, generate a bigger CSV (§2.4) and point the config
  at it (§4.1).
* `--robots 20` silently runs **18**, because the map has 18 staging
  cells. To go higher, add staging cells to the XLSX — `build_fleet`
  raises if `fleet.robot_count` exceeds the staging count.

Always read the header line the run echoes back:

```
robots     : 18
orders     : 100 (206 line items)
```

---

## 4. Configuration

`src/simulation_env/config/warehouse_sim.yaml` is authoritative. The
CLI flags above override it for one run only.

| Key | Current | Notes |
|---|---|---|
| `warehouse.map` | `CSV_To_Gazebo.xlsx` | resolved relative to the config |
| `fleet.robot_count` | `18` | hard-capped by staging cells |
| `fleet.speed_mps` | `1.5` | one cell = 1 m, so 2/3 s per move |
| `service.shelf_pick_s` | `2.0` | scientific, never altered by playback |
| `service.dropoff_s` | `2.0` | " |
| `orders.file` | `orders.csv` | **the only way to select a different CSV** |
| `mission.park_when_idle` | `true` | idle robots return home |
| `mission.park_zone_mode` | `home` | each robot to its own staging cell |
| `visualization.time_scale` | `4.0` | playback only; never affects results |
| `gazebo.enabled` | `true` | |

`visualization.time_scale` divides wall time only:
`wall = sim_seconds / time_scale`. Lower it to watch more slowly; it
cannot change makespan, waiting, or any recorded outcome.

### 4.1 Running a different order file

`run_workload.py` has no `--orders-file`. Copy the config and edit one
line:

```bash
sed 's/^  file: orders\.csv$/  file: orders_500.csv/' \
    src/simulation_env/config/warehouse_sim.yaml \
    > src/simulation_env/config/warehouse_sim_500.yaml

PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
    --config src/simulation_env/config/warehouse_sim_500.yaml \
    --orders 500 --progress 25
```

---

## 5. Gazebo

Gazebo is a **mirror**, never a planner. The engine owns all state;
`fleet_node` pushes poses out over gz-transport and never reads back.
A Gazebo run reproduces the headless numbers exactly.

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash

# watch the 100-order workload
ros2 launch simulation_env warehouse_sim.launch.py orders:=true

# slower, so one-way lane direction is visible to the eye
ros2 launch simulation_env warehouse_sim.launch.py orders:=true speed:=0.4

# deploy the fleet only, no orders
ros2 launch simulation_env warehouse_sim.launch.py

# no GUI (CI, or a headless box)
ros2 launch simulation_env warehouse_sim.launch.py headless:=true orders:=true
```

### 5.1 Two terminals: Gazebo once, orders many times

This is the workflow to use day to day. Gazebo stays open; you re-run
the workload as often as you like and the robots start moving each
time.

**Terminal 1 — Gazebo only.** Leave this running.

```bash
cd ~/rnd_mrta_ilp_ws
source /opt/ros/jazzy/setup.bash && source install/setup.bash

gz sim -r $(ros2 pkg prefix simulation_env)/share/simulation_env/worlds/warehouse_grid.world
```

The warehouse appears with no robots in it. That is correct — robots
are spawned by the world file and only *placed* once the fleet node
connects.

**Terminal 2 — fleet and orders.** Run this whenever you want a run;
`start_gazebo:=false` attaches to the server already running in
terminal 1 instead of starting a second one.

```bash
cd ~/rnd_mrta_ilp_ws
source /opt/ros/jazzy/setup.bash && source install/setup.bash

ros2 launch simulation_env warehouse_sim.launch.py \
    start_gazebo:=false orders:=true
```

Robots snap to their staging cells, then start moving. Wait for this
line before expecting motion:

```
[OK] 18 robot poses pushed to Gazebo world 'warehouse_grid' as one persistent fleet stream
```

When the run finishes, terminal 2 exits and Gazebo stays up. Re-run
terminal 2 to replay — no need to restart Gazebo.

Variations for terminal 2:

```bash
# slow enough to see lane direction
ros2 launch simulation_env warehouse_sim.launch.py \
    start_gazebo:=false orders:=true speed:=0.4

# place the fleet, no orders
ros2 launch simulation_env warehouse_sim.launch.py start_gazebo:=false
```

> If terminal 2 prints `set_pose_vector not found; continuing without
> Gazebo`, terminal 1 is not up yet. It waits 60 s
> (`wait_for_gazebo`), so start Gazebo first and let the world finish
> loading.

### 5.2 Launch arguments

| Launch arg | Default | Meaning |
|---|---|---|
| `orders` | `false` | run the workload; naming a `strategy` implies true |
| `strategy` | `nearest_astar` | allocation policy |
| `speed` | `0.0` = config | m/s override |
| `headless` | `false` | `gz sim -s`, no GUI |
| `start_gazebo` | `true` | set false to attach to a server you started |
| `config_file` | installed `warehouse_sim.yaml` | drives map, fleet size, order file, world name |
| `world_file` | *derived from the config* | override only to force a specific `.world` |

### ⚠️ Exactly one server

Two `gz sim` servers both advertise `set_pose_vector` for the same
world. gz-transport hands each request to one of them, poses get
split, and robots appear to stall mid-aisle. Before relaunching after
a crash:

```bash
pkill -f "gz sim"
```

### Checking robots really move

Reading the terminal is not proof — it shows engine state, not what
Gazebo received. Ask Gazebo directly:

```bash
gz model -m robot_003 -p
```

Fractional coordinates such as `[3.750000 18.500000 0.155000]` are
correct and expected: a robot mid-move is interpolated along the edge
it reserved. Whole `.5` values mean it is sitting on a cell centre.

---

## 6. Verification gates

Run all of these before trusting a change. All pass at the freeze.

```bash
# unit tests
PYTHONPATH=$PP $PY -m pytest src/mrta_core/tests src/simulation_env/test -q
# -> 69 passed

# traffic topology: the core must stay fully one-way
PYTHONPATH=$PP $PY src/simulation_env/scripts/validate_directional_highways.py
# -> uncontrolled bidi edges : 0 ... status : OK

# RMF graph must mirror the lane policy exactly
PYTHONPATH=$PP $PY src/rmf_grid_traffic/scripts/verify_nav_graph.py \
    --map src/simulation_env/maps/CSV_To_Gazebo.xlsx \
    --graph src/rmf_grid_traffic/config/warehouse_nav_graph.yaml
# -> 0 illegal, 0 missing, 0 two-way core, status : OK

# full workload
PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py --orders 100 --progress 0
# -> 100/100, 18/18 home, makespan 315.3333 s, reservation viol. 0

# traffic behaviour suite
for s in check_local_traffic_controller check_local_yield_selector \
         check_local_headon_resolution check_batch_reservations \
         check_engine_batch_traffic check_engine_headon_yield \
         check_raw_mission_cycle check_18_robot_traffic_stress; do
  PYTHONPATH=$PP $PY src/simulation_env/scripts/$s.py > /dev/null \
      && echo "$s PASS" || echo "$s FAIL"
done

# lint
.pixi/envs/default/bin/ruff check src
```

RMF-side checks (need the ROS overlay):

```bash
ros2 run rmf_grid_traffic graph_check \
    src/rmf_grid_traffic/config/warehouse_nav_graph.yaml
# -> 477 waypoints, 1235 directed lanes, OK

ros2 run rmf_grid_traffic route_check \
    src/rmf_grid_traffic/config/warehouse_nav_graph.yaml shelf_045 staging_001
```

Route lengths are deliberately **asymmetric** — `staging_001 →
shelf_045` is 22 waypoints, the reverse is 20. That is the one-way
network working, not a bug.

---

## 7. Frozen baseline

| Measure | Value |
|---|---|
| orders completed | 100/100 |
| robots returned home | 18/18 |
| makespan | 315.3333 s |
| total distance | 6282.0 m |
| total waiting | 236.0000 s |
| total service | 612.0000 s |
| reservation violations | 0 |
| uncontrolled bidirectional core edges | 0 |
| unit tests | 69 passed |

Gazebo reproduces these exactly. Any deviation means something
changed in the planner or the map.

---

## 8. Next steps

### 8.1 Adding an allocation strategy

Only `nearest_astar` is registered today. The docs elsewhere still
describe twelve; those were removed in commit `7a07208` and the claim
is stale.

Register a new one in
`src/task_management/task_management/allocation_strategy.py`
(`BATCH_STRATEGIES`), then:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
    --strategy my_new_strategy --orders 100 --progress 0
```

Because the map, the router, and the traffic layer are frozen, any
difference in makespan or waiting between two strategies is
attributable to the allocation decision alone. That is the point of
freezing.

### 8.2 Charging — not yet simulated

Be precise about the current state:

* the map defines **16 charging cells**, and they are parsed and
  rendered
* charging cells are ordinary **transit** cells — they are not
  service cells, so robots may drive across them
* `fleet.initial_battery_percent` is read, and `mrta_core/symbolic.py`
  has `battery_high` / `battery_medium` / `battery_low` predicates
* **but battery is never decremented anywhere, and no robot ever
  routes to a charger**

To bring charging in you need: battery drain per move and per service
in the engine, a charge action with a service duration, a policy that
sends a low robot to a charger, and a decision about whether charging
cells become service cells. Making them service cells would change the
traffic topology — the core is currently 347 transit cells including
the 16 chargers, so re-run the topology gate afterwards.

### 8.3 Where MAPF went

Removed at the freeze. Full-horizon space-time MAPF was superseded by
the one-way topology plus the reactive local traffic layer, and every
run reported `0` conflicts, `0` replans and `0` space-time expansions.

Deleted: `mapf.py`, `mapf_planner.py`, `execution_buffer.py`,
`fleet_playback.py`, their two test files, and the trajectory
execution path in `fleet_engine.py`.

The metric formerly printed as `MAPF conflicts` was never MAPF — it is
`reservations.audit()`, a reservation-violation count, and is now
labelled `reservation violations`. It must stay `0`.
