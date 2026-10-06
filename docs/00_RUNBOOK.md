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

`openpyxl` is declared in `pixi.toml`, so the XLSX map loader works
from the pixi environment directly. `PYTHONPATH` only has to carry the
three source packages:

```bash
cd ~/rnd_mrta_ilp_ws

export PY=.pixi/envs/default/bin/python
export PP=src/mrta_core:src/simulation_env:src/task_management
```

Keep those two exports in every headless shell. Anything in this
document that starts `PYTHONPATH=$PP $PY` is a headless command;
anything that starts `ros2` needs the ROS overlay instead:

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
```

> Earlier revisions appended `/usr/lib/python3/dist-packages` to `$PP`
> to borrow the system `openpyxl`. That workaround is gone; if you see
> it in an old shell or script, drop it.

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
   ├── warehouse.map ────────► CSV_To_Gazebo_v2.xlsx (hand-authored)
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
home cells the map provides, rather than writing a world the fleet
cannot be placed in.

### 2.2 Worked example — a 10-robot scenario

```bash
sed -e 's/^  robot_count: 22$/  robot_count: 10/' \
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
| `battery.*` | nothing; read at runtime |
| `service.*`, `mission.*`, `visualization.*` | nothing |

Changing the lane policy in `warehouse_routing.py` also requires
`build_scenario.py`, because the RMF nav graph is derived from it.

### 2.4 Naming in Gazebo

Every semantic cell is its own named model, so you can read a pose
straight off the entity tree or with `gz model -m <name> -p`:

```
staging_001 .. staging_022      shelf_001 .. shelf_072
dropoff_001 .. dropoff_036      charging_001 .. charging_008
charge_queue_001 .. _014        blocked_0000 ...
robot_001   .. robot_<N>
```

Home tiles always reflect the **map** (22 cells on V2), while robot
models reflect `fleet.robot_count`. A 10-robot scenario therefore shows
22 home tiles with 10 occupied — that is correct, not a bug.

The home bank is labelled `STAGE` on V1 and V2 maps and may be
relabelled `COLLECTION ZONE`; the code accepts either, so the tiles
appear as `staging_001..` or `collection_001..` accordingly.

### 2.5 Coordinates: the grid and Gazebo are the same numbers

A grid cell maps onto a world point as a direct identity on the cell
index:

```
world_x = origin_x + grid_x * resolution
world_y = origin_y + grid_y * resolution
```

So grid cell `(5, 15)` **is** Gazebo `(5.0, 15.0)`. A pose read out of
Gazebo is the cell, with nothing to subtract:

```bash
gz model -m robot_002 -p
#  [5.000000 15.000000 0.155000]     -> cell (5, 15)
#  [10.250000 10.000000 0.155000]    -> mid-move, a quarter of the way
#                                       from (10,10) toward (11,10)
```

Whole numbers mean the robot is on a cell centre; fractions mean it is
interpolating along the edge it reserved. Cell `(0, 0)` sits on the
Gazebo origin.

The trade this makes: a cell's tile spans `x-0.5 .. x+0.5`, so tile
edges land on half-integers and straddle Gazebo's 1 m ground grid
rather than sitting inside it. That is cosmetic; having the two
coordinate systems be the same numbers is worth more day to day.

The transform lives in **one place**, `mrta_core/world_view.py`, and is
imported by the Gazebo world generator, the live fleet node and the
RMF nav-graph generator. It has to be shared for the same reason
`warehouse_routing` is: three layers place the same cell and may never
disagree. It also honours `origin_x` / `origin_y` from the map, so a
map that does not start at zero stays consistent everywhere.

### 2.6 Regenerating individual artefacts

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

### 2.7 The map

`src/simulation_env/maps/CSV_To_Gazebo_v2.xlsx` is edited by hand; there
is no generator. Cell colours carry meaning:

| Colour | Meaning |
|---|---|
| orange | coordinate border, not part of the warehouse |
| red | blocked / non-traversable |
| blank white | normal floor |
| merged named region | `SHELVES`, `DROP ZONE`, `CHARGING`, `STAGE` |
| purple `charge queue` | charging holding cell (V2) |

Current map (V2): **17 × 35**, 134 blocked, 72 shelves, 36 dropoffs,
22 home, 8 charging, 14 charge-queue holding cells. The home count is
the hard ceiling on `fleet.robot_count` — add `STAGE` (or `COLLECTION
ZONE`) cells to run more than 22 robots.

Widening the home bank costs dropoff cells: the x=2 and x=15 cells on
a bank row are frontage lane, not drop chute. Going from 18 to 22 home
cells took the dropoff pool from 40 to 36 and **renumbered every
dropoff id**, so `orders.csv` had to be regenerated. Shelf ids were
unaffected. See [`19_MAP_V2_MIGRATION.md`](19_MAP_V2_MIGRATION.md).

`charge queue` cells are the charging holding bank: where a robot waits
for a slot, and where a preempted robot goes when it is not
energy-eligible to rejoin the fleet. Chargers and holding cells are
non-transit terminals — see §2.7a.

### 2.7a Topology gate, and the charging bank

Any map change has to clear the topology gate before it becomes the
current map:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/check_map_topology.py \
    --config src/simulation_env/config/warehouse_sim.yaml
# -> 309 transit cells, 485 directed edges, 0 two-way core,
#    309/309 strongly connected,
#    terminal access: shelf 72/72, dropoff 36/36, staging 22/22,
#                     charging 8/8, charge_queue 14/14,
#    22952 station legs: 0 unreachable, 0 through a terminal,
#    0 terminal->terminal moves, 0 moves not in nav graph,
#    status : OK
```

It imports `non_transit_cells()`, `directional_blocked_edges()`,
`navigation_grid()` and `traversable_lane_edges()` from
`mrta_core.warehouse_routing`, so it scores the graph the planner
actually executes and the graph RMF is built from, rather than a
paraphrase of the lane rules. Terminal access is checked globally —
enter from the core and leave back to it by A* — because a local
"has a lane in and a lane out" test passes a terminal whose only aisle
neighbour is a one-way dead end.

**The charging bank (Battery V1, B0, 2026-09-13).** Chargers and
charge-queue holding cells are terminals: reachable as leg endpoints,
never transit (`CHARGING_APRON_NON_TRANSIT = True`). The apron was
authored two cells deep against the wall, and under that rule 16 of its
32 cells could not be both entered and left — the four corners had no
aisle neighbour at all, and the outer row hung off one-cell one-way
stubs on the northbound trunks. The bank is now **one cell deep**:

```
 y=33   # # # # # # # # # # # # #      wall (was the outer apron row)
 y=32   # C Q Q C Q Q C Q Q C Q #      bank, faces the aisle below
 y=31   . . . . . . . . . . . . D      aisle, eastbound
 ...
 y=3    . . . . . . . . . . . . D      aisle, westbound
 y=2    # C Q Q C Q Q C Q Q C Q #      bank, faces the aisle above
 y=1    # # # # # # # # # # # # #      wall (was the outer apron row)
        x=3                     15
```

A robot enters a bank cell from the aisle cell directly outside it and
leaves by reversing into that same cell. The lane policy also forbids
every terminal -> terminal edge, so a leg from a holding cell to the
charger beside it goes out through the aisle — `charge_queue (5,2) ->
(5,3) -> (4,3) -> charging (4,2)` — exactly as the RMF graph routes it.

The correction was a 28-cell XLSX edit (eight `Charging` merges
unmerged; F3:P3 and F35:P35 made red; H4 K4 N4 H34 K34 N34 labelled
`charge queue`). The 8 / 14 split is the V2 capacity only; capacities
for the W20/W30/W50 family are to be set experimentally, not by rule.
Every workload number is bit-identical to before, because no route
ever used the apron.

The superseded V1 map has the same two-deep apron, so it **fails**
this gate under the battery-era policy (331 transit cells, 14/16
chargers usable, 289 unreachable legs). V1 is not maintained.

The full V1 -> V2 migration record is in
[`19_MAP_V2_MIGRATION.md`](19_MAP_V2_MIGRATION.md).

### 2.8 Orders

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

### One silent cap to know about

`--orders` is applied as a plain **list slice**, so asking for more
than exists is not an error and prints no warning:

* `--orders 300` against the 100-order CSV silently runs **100**.
  To really run 300, generate a bigger CSV (§2.4) and point the config
  at it (§4.1).
* `--robots N` overrides `fleet.robot_count` for one run, up **or**
  down. It used to only ever shrink the configured fleet, so asking
  for more than `robot_count` silently did nothing; it now rebuilds
  the fleet at N. The hard ceiling is the map: `build_fleet` raises if
  N exceeds the home cells the XLSX provides.

Always read the header line the run echoes back:

```
robots     : 22
orders     : 100 (200 line items)
```

Every run ends with a `BATTERY` section: per robot initial, final and
minimum SOC with travel / service / waiting / idle energy, then the
fleet summary and two self-checks — the SOC identity (exact, rational
arithmetic) and an independent rebuild of SOC from the engine's own
float counters, which must agree to float precision. Battery is
observation only since increment B1: nothing reads SOC to decide
anything, so every other number in the run is unchanged by it.

---

## 4. Configuration

`src/simulation_env/config/warehouse_sim.yaml` is authoritative. The
CLI flags above override it for one run only.

| Key | Current | Notes |
|---|---|---|
| `warehouse.map` | `CSV_To_Gazebo_v2.xlsx` | resolved relative to the config |
| `fleet.robot_count` | `22` | the fleet size; hard-capped by the map's home cells (22 on V2) |
| `fleet.speed_mps` | `1.5` | one cell = 1 m, so 2/3 s per move |
| `service.shelf_pick_s` | `2.0` | scientific, never altered by playback |
| `service.dropoff_s` | `2.0` | " |
| `orders.file` | `orders.csv` | **the only way to select a different CSV** |
| `battery.enabled` | `true` | SOC accounting on; `false` removes the ledger entirely |
| `battery.initial_soc_percent` | `100.0` | every robot's starting SOC |
| `battery.travel_percent_per_20m` | `1.0` | 1% per 20 m driven |
| `battery.service_percent_per_60s` | `1.0` | 1% per 60 s of pick or drop |
| `battery.waiting_idle_percent_per_300s` | `1.0` | 1% per 300 s standing still; waiting and idle are reported apart |
| `battery.charging_percent_per_second` | `1.0` | declared; no effect until charging exists (B4) |
| `battery.reserve_percent` | `10.0` | declared; no effect until eligibility exists (B2) |
| `battery.charge_due_percent` | `10.0` | declared; no effect until B2 |
| `battery.initial_soc_overrides` | *absent* | `robot_id: percent`, **controlled tests only** — never in a scientific config |
| `visualization.time_scale` | `4.0` | playback only; never affects results |
| `gazebo.enabled` | `true` | |

There is deliberately no parking key. See §4.1.

Unknown `battery` keys, out-of-range values and overrides naming a
robot outside the fleet are refused at load. The old
`fleet.initial_battery_percent` key is refused too: initial SOC lives
only in `battery.initial_soc_percent`.

### 4.1 Parking: the Stage Zone bank is shared

A Stage Zone cell is where a robot is *deployed*, and nothing more.
The bank is a shared fleet resource, not a set of robot-owned homes.

```text
while orders remain   a robot that finishes an order stays available
                      from the aisle cell it retreated to, and takes
                      more work from there. It never drives home
                      between orders.

no work left          it claims the nearest FREE Stage Zone by
                      directed A* cost over the one-way road graph.
```

The claim is made when the return mission is assigned, not on arrival,
so two robots converging on the same bank cannot pick the same cell.
If the cheapest cell is already claimed, the next cheapest reachable
free one is taken. On arrival the claim simply becomes the occupancy.

Nearest is a property of the legal route, not the straight line. From
cell `(2,9)`, `staging_001` is five cells away as the crow flies but 27
by legal route, while `staging_010` is fourteen away and costs 18. The
one-way aisles are the whole reason Manhattan distance may not stand in
here.

Pairing within a batch is greedy on `(route cost, zone_id, robot_id)`.
Route cost is the only scientific quantity in that key; `zone_id`
separates an exact cost tie between two bank cells, and `robot_id` only
ever breaks a tie the first two left equal, so two runs of the same
workload park identically. Robot identity carries no parking priority.

Because cells are claimed as robots arrive rather than solved for the
whole fleet at once, the last robot back takes whatever is left. That
is the policy, not a defect: on the 100-order baseline it is what sets
the 282.0 s makespan.

The implementation is `simulation_env/stage_parking.py`, and it is
fixed infrastructure — every MRTA strategy parks through it, and
parking never changes robot-order ownership.

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

Robots snap to their home cells, then start moving. Wait for this
line before expecting motion:

```
[OK] 22 robot poses pushed to Gazebo world 'warehouse_grid' as one persistent fleet stream
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

Whole numbers mean the robot is sitting on a cell centre, and the pose
*is* the grid cell — `[5.000000 15.000000 0.155000]` is cell `(5, 15)`.
Fractions mean it is mid-move, interpolated along the edge it reserved.
See §2.5.

### Watching SOC live

`/mrta/robot_states` carries each robot's live SOC in the existing
`battery_percent` field, read from the engine's battery ledger at the
current simulated time. In a third terminal:

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash

# one robot per line, refreshed every second
watch -n 1 "ros2 topic echo --once /mrta/robot_states \
    | grep -E 'robot_id|battery_percent' | paste - -"
```

SOC falls continuously and never rises (there is no charging yet). A
value read mid-move or mid-service lags the final booking by at most
one move's or one service's drain. After the run the node keeps
publishing the final SOC, which matches the headless `BATTERY` table.

---

## 6. Verification gates

Run all of these before trusting a change. All pass at the freeze.

```bash
# unit tests
PYTHONPATH=$PP $PY -m pytest \
    src/mrta_core/tests src/simulation_env/test src/task_management/test -q
# -> 155 passed
#
# or, equivalently, straight from the project environment:
#   pixi run test

# map topology: strongly connected, no orphans, every terminal
# enterable and leavable, every station reachable, no shortcut
PYTHONPATH=$PP $PY src/simulation_env/scripts/check_map_topology.py \
    --config src/simulation_env/config/warehouse_sim.yaml
# -> 309 transit, 485 edges, 309/309, 0 unreachable, status : OK

# traffic topology: the core must stay fully one-way
PYTHONPATH=$PP $PY src/simulation_env/scripts/validate_directional_highways.py
# -> uncontrolled bidi edges : 0 ... status : OK

# RMF graph must mirror the lane policy exactly
PYTHONPATH=$PP $PY src/rmf_grid_traffic/scripts/verify_nav_graph.py \
    --config src/simulation_env/config/warehouse_sim.yaml \
    --graph src/rmf_grid_traffic/config/warehouse_nav_graph.yaml
# -> 973 lanes, 0 illegal, 0 missing, 0 two-way core,
#    0 terminal chain, 0 stranded terminals, status : OK

# full workload
PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py --orders 100 --progress 0
# -> 100/100, 22/22 parked, makespan 282.0000 s, reservation viol. 0

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
# -> 461 waypoints, 973 directed lanes, OK

ros2 run rmf_grid_traffic route_check \
    src/rmf_grid_traffic/config/warehouse_nav_graph.yaml shelf_045 staging_001
```

Route lengths are deliberately **asymmetric** — `shelf_045 →
staging_001` is 23 waypoints, the reverse is 27. That is the one-way
network working, not a bug. Both match the scientific planner cell for
cell, which is the point of the mirror.

---

## 7. Frozen baseline

Reference workload on **V2** (`CSV_To_Gazebo_v2.xlsx`), 22 robots,
`orders.csv` seed 42.

| Measure | Value |
|---|---|
| orders completed | 100/100 |
| robots parked at a Stage Zone | 22/22 |
| distinct Stage Zones occupied | 22 |
| parked off their spawn Stage Zone | 22/22 |
| makespan | 282.0000 s |
| total distance | 6430.0 m |
| total waiting | 264.6667 s |
| total service | 600.0000 s |
| reservation violations | 0 |
| uncontrolled bidirectional core edges | 0 |
| transit core cells | 309 |
| directed core edges | 485 |
| RMF nav graph | 461 waypoints, 973 lanes |
| RMF terminal-chain lanes | 0 |
| unit tests | 155 passed |
| lowest final SOC (B1, observation only) | 80.7222% (robot_016); fleet mean 84.7322% |

The B0 charging-bank correction (§2.7a) changed the transit core and
the nav graph (was 353 cells, 569 edges, 483 waypoints, 1013 lanes)
and no workload number.

500-order set, same fleet: 500/500, 22/22 parked on 22 distinct Stage
Zones, makespan 1123.3333 s, distance 29338.0 m, waiting 1242.6667 s,
0 violations. 21 of the 22 parked off their spawn cell.

Gazebo reproduces these exactly. Any deviation means something
changed in the planner or the map.

### 7.1 Superseded V1 baseline

The V1 map (`CSV_To_Gazebo.xlsx`, 18 home cells, 40 dropoffs, 347
transit cells / 559 edges) ran 100/100, 18/18 home, makespan
315.3333 s, distance 6282.0 m. It is **not** comparable to the numbers
above: widening the home bank to 22 cells took one cell off each
dropoff column, so the pool went 40 -> 36 and every dropoff id moved.
`orders.csv` was regenerated against the new map with the same seed.
V1 itself still passes every gate unchanged.

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

### 8.2 Charging — travel to chargers, no charging yet

Be precise about the current state:

* the map defines **8 charging cells and 14 charge-queue holding
  cells**, in two one-cell-deep banks, parsed and rendered
* chargers and holding cells are **terminals** (B0): reachable as leg
  endpoints, never transit — see §2.7a
* SOC is **accounted** (B1): the engine's `BatteryLedger` drains it
  exactly from travel, service, waiting and idle, and every run
  reports it
* SOC **gates allocation** (B2): a robot-order pair is legal only if
  `SOC >= travel(route incl. post-dropoff retreat) + service +
  directed-A* leg to the nearest charger + 10% reserve`, a hard mask
  every strategy must obey. SOC <= 10% is CHARGE_DUE; above that with
  no eligible order among all pending orders is CHARGE_NEEDED. Either
  puts the robot in REQUEST

  ```bash
  PYTHONPATH=$PP $PY src/simulation_env/scripts/check_battery_eligibility.py
  # -> t=0: 12% CHARGE_NEEDED, 10% / 9% CHARGE_DUE, 14% fits 36/100
  #    orders; 40-order run 40/40, 0 ineligible assignments, status : OK
  ```
* a REQUEST robot **leaves the aisle at once** (B3): it reserves the
  FREE charger with the cheapest directed-A* route, else the cheapest
  FREE holding cell, else (saturation) the nearest free Stage Zone, and
  drives there. Slots are FREE / RESERVED / OCCUPIED / VACATING and are
  offered only while FREE. **There is no charging yet**: the robot stays
  on its terminal for the rest of the run and its SOC keeps draining at
  the idle rate. Every run prints a `CHARGER TRAVEL` table

  ```bash
  PYTHONPATH=$PP $PY src/simulation_env/scripts/check_charger_evacuation.py
  # -> five controlled scenarios (4 and 12 robots at 9%, a late
  #    CHARGE_NEEDED robot, saturation, the B2 deadlock fleet),
  #    each "checks: OK"; "B3 charger evacuation: 5/5 scenarios OK"
  ```

  To watch it in Gazebo (§5.1 terminal 1 running), terminal 2:

  ```bash
  # robot_001..004 at 9%: four distinct chargers
  ros2 launch simulation_env warehouse_sim.launch.py start_gazebo:=false \
      orders:=true \
      config_file:=$PWD/src/simulation_env/config/warehouse_sim_b3_four_low.yaml

  # robot_001..012 at 9%: 8 chargers, then 4 holding cells
  ros2 launch simulation_env warehouse_sim.launch.py start_gazebo:=false \
      orders:=true \
      config_file:=$PWD/src/simulation_env/config/warehouse_sim_b3_twelve_low.yaml
  ```

  These two configs are `warehouse_sim.yaml` plus
  `battery.initial_soc_overrides`; they are for visual acceptance only
* `mrta_core/symbolic.py` still has `battery_high` / `battery_medium` /
  `battery_low` predicates on a 0–1 scale; they are not wired to the
  ledger (ROADMAP phase 10)

Battery Scheduler V1 is being built in increments B1–B6 on top of
this; see `docs/CURRENT_PHASE.md` for which are done.

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
