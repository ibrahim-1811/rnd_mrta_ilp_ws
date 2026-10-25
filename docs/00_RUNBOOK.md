# 00 — Runbook: every command, from scratch

Verified end to end on branch `traffic/rmf-integration`, ROS 2 Jazzy,
Gazebo (gz-sim), 2026-09-03. Every command below was executed; the
numbers quoted are real output, not estimates.

This is the operational document. For *why* the aisles are one-way see
[18_TRAFFIC_TOPOLOGY.md](18_TRAFFIC_TOPOLOGY.md).

> **Since 2026-10-23: one map, one config, one world.** To run the
> system, follow [START_HERE.md](START_HERE.md). The default config is
> now `src/simulation_env/config/warehouse.yaml` (the current map, W100),
> and the one Gazebo world is `worlds/warehouse.world`.
>
> This runbook documents the **V2 reference scenario**, on which every
> frozen baseline and most tests are measured. Its config, map and order
> lists moved to `src/simulation_env/test/fixtures/`. The numbers below
> are V2 numbers: where a `run_workload.py` command has no `--config`,
> add `--config src/simulation_env/test/fixtures/v2_reference.yaml` to reproduce them.

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
    robot_interfaces gazebo_fleet_pose_transport \
    --cmake-args -DPython3_EXECUTABLE=/usr/bin/python3

source install/setup.bash
```

Keep `--cmake-args`: on a fresh build CMake otherwise finds
`~/.local/bin/python3.10`, which lacks `catkin_pkg`, and
`rmf_grid_traffic` fails.

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
   ├── warehouse.map ────────► W100.xlsx (hand-authored)
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

### 2.1 Build: one command for config, orders and world

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/build_scenario.py \
    --map src/simulation_env/maps/W100.xlsx
```

It checks the map, rewrites `warehouse.map` and `fleet.robot_count` in
the one config, regenerates `config/orders.csv` from the map's stations,
builds the one world `worlds/warehouse.world` (warehouse only) and the
nav graph, and verifies the graph. Tested step by step in
[START_HERE.md](START_HERE.md), step 3.

### 2.2 Fleet size needs no scenario of its own

Until 2026-10-23 the world baked in `robot_count` robots, so every fleet
size needed its own config and world (`warehouse_sim_10.yaml`,
`warehouse_10.world`, ...). Now the world is the warehouse only, and
the fleet node creates exactly the robots a run uses:

```bash
ros2 launch simulation_env warehouse_sim.launch.py orders:=true robots:=10
```

```text
[OK] Gazebo fleet set to 10 robot(s): 10 created, 0 removed
```

Re-running with fewer robots removes the extras from the same world.

### 2.3 What to change, and what it forces

| Change | Do |
|---|---|
| the map | `build_scenario.py --map X.xlsx` |
| an edit to the current map | `build_scenario.py --map` with the same file |
| robot count | nothing: `--robots N` headless, `robots:=N` in Gazebo |
| order count | nothing: `--orders N` takes the first N |
| `battery.*`, `fleet.speed_mps`, `service.*` | nothing; read at run time |
| the lane policy code | `build_scenario.py` again (the nav graph derives from it) |

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
# Always for the current map: the world and the nav graph are single
# files, and building them from any other config overwrites them.
PYTHONPATH=$PP $PY src/simulation_env/scripts/generate_gazebo_grid_world.py \
    --config src/simulation_env/config/warehouse.yaml

# RMF nav graph (--config, or --map for a bare XLSX)
PYTHONPATH=$PP $PY src/rmf_grid_traffic/scripts/generate_nav_graph.py \
    --config src/simulation_env/config/warehouse.yaml \
    --output src/rmf_grid_traffic/config/warehouse_nav_graph.yaml

PYTHONPATH=$PP $PY src/rmf_grid_traffic/scripts/verify_nav_graph.py \
    --config src/simulation_env/config/warehouse.yaml \
    --graph src/rmf_grid_traffic/config/warehouse_nav_graph.yaml
```

### 2.7 The map

`src/simulation_env/test/fixtures/CSV_To_Gazebo_v2.xlsx` (V2) is edited by hand; there
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
    --config src/simulation_env/test/fixtures/v2_reference.yaml
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
    --output src/simulation_env/test/fixtures/orders_500.csv
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
| `--config PATH` | `config/warehouse.yaml` (the one config) | which config |
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

`src/simulation_env/test/fixtures/v2_reference.yaml` is authoritative. The
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
| `battery.charging_percent_per_second` | `1.0` | net SOC gain per simulated second while charging (B4) |
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

Use `--orders-file`. A bare name is looked for next to the config:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
    --config src/simulation_env/test/fixtures/v2_reference.yaml \
    --orders-file orders_500.csv --progress 25
```

The run header names the file used, and a recorded dataset hashes it.
There is no separate config per order file any more.

---

## 4b. S0 allocation candidate window

The window is a scientific parameter. It is printed in the run header
and in an `ALLOCATION DECISIONS` block, so a result can never be read
without knowing which window produced it.

```bash
# the new default, W = fleet_size (22)
pixi run python src/simulation_env/scripts/run_workload.py

# the pre-S0 rule, for the frozen baseline comparison
pixi run python src/simulation_env/scripts/run_workload.py --window legacy

# an explicit width, for a controlled scenario
pixi run python src/simulation_env/scripts/run_workload.py --window 4

# 500 orders
pixi run python src/simulation_env/scripts/run_workload.py \
    --config src/simulation_env/test/fixtures/v2_reference.yaml --orders-file orders_500.csv
```

What to look for:

```text
  window           : fleet_size (W = 22)
  decisions        : 64
  choice-bearing   : 63 (98.4 %)  -- robots x orders > 1
  forced 1x1       : 1 (1.6 %)  -- every strategy agrees on these
  window extensions: 0
```

`--window legacy` must print `forced 1x1 : 62 (87.3 %)` and reproduce
the pre-S0 baseline exactly: makespan 282.0000 s, 6430.0 m, waiting
264.6667 s, 0 violations. If it does not, something other than the
window has changed.

### Dispatch quantum (S0b)

Continuous dispatch is the default and the headline configuration. The
batched scenario exists only so the batch-assignment strategies
(Hungarian, MILP) face decisions with more than one robot in them.

```bash
# the measured batched scenario, T = 3.0 s
pixi run python src/simulation_env/scripts/run_workload.py --quantum 3.0

# sweep T against the default config
pixi run python src/simulation_env/scripts/run_workload.py --quantum 5
```

The batched scenario must report `33 decisions, 33 choice-bearing
(100.0 %), 0 forced` and `makespan 264.6667 s, 5716.0 m`. A larger T
batches harder but costs makespan: 10.0 s is +9% makespan and +75%
waiting, so do not treat T as a value to maximise.

In Gazebo, pass it as a launch argument:

```bash
ros2 launch simulation_env warehouse_sim.launch.py \
    start_gazebo:=false orders:=true window:=legacy
```

An empty `window:=` keeps `allocation.window` from the YAML, which is
authoritative for it.

## 4bb. Allocation strategies

Three are registered. The allocation policy is the research variable;
everything else is identical between them.

```bash
PYTHONPATH=$PP $PY -c \
  "from task_management.allocation_strategy import BATCH_STRATEGIES; \
   print(sorted(BATCH_STRATEGIES))"
```

```text
['nearest_astar', 'nearest_manhattan', 'random']
```

| Strategy | Ranks pairs by | Role |
|---|---|---|
| `nearest_astar` | `C_static`, the legal directed route | the grid-aware baseline |
| `nearest_manhattan` | Manhattan chain, ignoring walls and lanes | the deliberately naive control |
| `random` | a seeded shuffle | the lower bound |

Run all three on the shipped workload:

```bash
for s in nearest_astar nearest_manhattan random; do
    echo "### $s"
    PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
        --quiet --progress 0 --strategy $s \
        | grep -E "completed |makespan|total distance|total waiting"
done
```

Expected (22 robots, 100 orders, seed 42):

```text
nearest_astar       268.6667 s   5798.0 m   305.3333 s
nearest_manhattan   265.3333 s   5974.0 m   325.3333 s
random              266.6667 s   6488.0 m   258.0000 s
```

All three complete 100/100. **Read the distance column, not the
makespan column.** Distance is where the allocation policy shows up
cleanly and it orders as theory predicts: grid-aware < naive < none.
The printed makespan includes end-of-work recovery and is set by
whichever robot happens to finish last after traffic, so on a single
seed it is a noisy signal — `nearest_manhattan` finishes earlier here
while driving 176 m further.

### Stochastic strategies and the seed

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
    --quiet --progress 0 --strategy random --seed 7
```

The seed defaults to `simulation.seed` (42) and is printed in the
header. Same seed, same bytes:

```bash
for i in 1 2; do
    PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
        --quiet --progress 0 --strategy random --dataset runs/rnd$i
done
cmp runs/rnd1/decisions.parquet runs/rnd2/decisions.parquet && echo identical
```

### The distinctness gate

A new strategy is accepted only when it is shown distinct from every
accepted one. Comparing two independent runs would not show that: after
the first decision where they differ the fleets are in different
states, so their decision N is not the same decision. The gate measures
a counterfactual instead — one strategy drives, and every rival is
asked the same question at every decision.

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/check_strategy_distinctness.py
```

```text
driving with nearest_astar      vs nearest_manhattan   76.2 %
                                vs random              90.5 %
driving with nearest_manhattan  vs nearest_astar       60.3 %
                                vs random              97.1 %
driving with random             vs nearest_astar       93.8 %
                                vs nearest_manhattan   95.3 %

threshold 5.0 %          status: OK
```

Exits non-zero if any pair falls below the threshold. Run it on a
smaller scenario with `--orders 40` while developing a new strategy.

---

## 4c. Recording the decision and outcome dataset

Off by default. Recording is observation only -- it changes no
simulated result, which `test_dataset.py` asserts -- but it is not
free, so a run opts in.

```bash
python3 src/simulation_env/scripts/run_workload.py \
    --quiet --progress 0 --dataset runs/astar
```

Writes into `runs/astar/`:

```text
decisions.parquet   one row per scored (robot, order) candidate pair
outcomes.parquet    one row per order
run.json            provenance and the run_id
```

Expected on the shipped workload:

```text
decision rows    : 1973
outcome rows     : 100
makespan         : 268.6667 s
total distance   : 5798.0 m
```

The makespan and distance must match a run made without `--dataset`.
If they do not, recording is perturbing the simulation and the dataset
describes a different system than the baseline does.

The header prints the `run_id` and warns on a dirty tree:

```text
  dataset    : runs/astar
  run_id     : 4e44e2ede5cab045
  git        : DIRTY TREE -- this run is not reproducible
```

A dirty-tree run is still useful, but it cannot be reproduced, so the
warning is not cosmetic. Commit before recording a run you intend to
cite.

### Recording a live run

The ROS node records through the same recorder, so the live path and the
headless path produce the same dataset:

```bash
ros2 launch simulation_env warehouse_sim.launch.py \
    orders:=true dataset:=runs/live

# headless, no Gazebo
ros2 run simulation_env fleet_node --ros-args \
    -p run_workload:=true -p load_orders:=true -p use_gazebo:=false \
    -p progress_interval_s:=0.0 -p dataset:=runs/live
```

The node logs the run_id up front and the row counts at the end:

```text
[INFO] recording dataset: runs/live (run_id 4e44e2ede5cab045)
[WARN] dirty tree: this run is not reproducible
[INFO] dataset written: runs/live (1973 decision rows, 100 outcome rows)
```

The two paths must agree. On the shipped workload they are
byte-identical:

```bash
cmp runs/live/decisions.csv runs/astar/decisions.csv
```

The node runs on the system interpreter under `/opt/ros/jazzy`, not in
the pixi environment, so pyarrow is usually absent there and the live
run writes CSV instead of Parquet. That is expected, not a failure. The
manifest says which:

```bash
grep '"format"' runs/live/run.json      # "csv"
grep '"format"' runs/astar/run.json     # "parquet"
```

Both are lossless -- the exact columns are already
`"numerator/denominator"` strings -- and `read_run` reads either.

The launch file also exposes the dispatch quantum, so a live run can use
the batched scenario:

```bash
ros2 launch simulation_env warehouse_sim.launch.py \
    orders:=true quantum:=3.0 dataset:=runs/live_batched
```

A negative `quantum` (the default) keeps `allocation.dispatch_quantum_s`
from the config, so `quantum:=0.0` stays a real override meaning
continuous dispatch.

### Reading it back out

Parquet is not readable with `less`. Add `--export` to also write text
beside the tables:

```bash
python3 src/simulation_env/scripts/run_workload.py \
    --quiet --progress 0 --dataset runs/astar --export
```

```text
decisions.csv   both tables as text, for grepping and diffing
outcomes.csv
bk.pl           ground facts: one run's decision-time state
exs.pl          pos(assign(D, R, O)) for the chosen pair,
                neg(...) for each eligible pair passed over
```

Sanity check on the shipped workload:

```bash
grep -c '^pos(' runs/astar/exs.pl    # 100, one per order
grep -c '^neg(' runs/astar/exs.pl    # 1873
```

Storage sizes show why Parquet is stored and text is exported:

```text
decisions.parquet     56 KB
decisions.csv        530 KB
bk.pl                981 KB
```

`bk.pl` and `exs.pl` are ILP *input*, not a configured learner. No mode
or bias declarations are generated: the hypothesis space is the
experiment and belongs to the ILP phase.

### Comparing predicted against realized

```bash
PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management \
    python3 -c "
from simulation_env.dataset_export import read_run

decisions, outcomes, manifest = read_run('runs/astar')

print(manifest['run_id'], manifest['strategy'])

for row in outcomes[:5]:
    print(
        row['order_id'],
        row['predicted_distance_m'],
        row['realized_distance_m'],
        row['realized_waiting_s'],
    )
"
```

Predicted and realized distance agree exactly, because `C_static` and
the realized counter cover the same route including the post-drop-off
retreat. Waiting is where the two legitimately diverge: it is an
uncertain realized outcome and is never a decision-time feature.

## 4d. Long runs: verifying battery, charging and liveness

The shipped 100-order workload **never charges a robot**. 22 robots
drain about 13% of SOC over it, so nothing reaches the 10% charge-due
threshold and the whole B3/B4/B5 workflow sits idle. To verify
charging you need a workload long enough to drain the fleet several
times over.

### Generate a long workload

The generator is seeded, so this is reproducible byte for byte:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/generate_orders.py \
    --orders 2000 --seed 42 \
    --config src/simulation_env/test/fixtures/v2_reference.yaml \
    --output src/simulation_env/test/fixtures/orders_2000.csv
```

### Run the soak

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
    --config src/simulation_env/test/fixtures/v2_reference.yaml --orders-file orders_2000.csv \
    --progress 250 --strategy nearest_astar \
    --dataset runs/soak_astar
```

About 40 s of wall clock for 4296 s of simulated time. Drop `--quiet`
(the default here) to watch every battery event as it happens:
`CHARGE_DUE`, `RESERVE`, `CHARGE_START`, `CHARGE_FULL`, `REJOIN`,
`PROMOTE`.

Expected (22 robots, 2000 orders, seed 42, `nearest_astar`):

```text
completed            2000/2000
parked at stage      22/22
makespan             4296.2591 s
total distance       107306.0 m
reservation viol.    0
plan deferrals       0

charge sessions      44 started, 44 robot(s) back in the fleet
promotions           18 out of holding onto a freed charger
preemptions          0
saturation events    0

final SOC            min 16.6448  mean 26.9711  max 37.6824
lowest minimum SOC   7.9263
below 0 % SOC        none
SOC identity         exact (rational arithmetic)
battery-ineligible   4452 of 47052 pairs (9.5 %)
```

### What to check, and what not to be alarmed by

**`lowest minimum SOC 7.9263` is below the 10% reserve, and that is
correct.** The reserve is a *decision-time* guarantee: a robot is only
given a new order if it can finish it and still reach a charger on 10%.
It may legitimately dip below while finishing an order it already owns
and driving to the charger. The invariant that must hold is:

```text
below 0 % SOC    : none
SOC identity     : exact
```

**Preemption stays at 0 even here.** 8 chargers served 44 sessions
without saturating, so B5 preemption is not exercised by order volume.
Exercising it needs more robots or fewer chargers, not more orders:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/check_b5_queue.py
PYTHONPATH=$PP $PY src/simulation_env/scripts/check_charger_evacuation.py
```

### ⚠️ Known: `random` deadlocks at sustained load

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
    --config src/simulation_env/test/fixtures/v2_reference.yaml --orders-file orders_500.csv \
    --quiet --progress 0 --strategy random --seed 42
```

Exits 1 with `STALLED` and a cycle:

```text
CYCLES  robot_007 -> robot_019 -> robot_005 -> robot_016 -> robot_007
```

This is a **cyclic wait**, not the head-on deadlock the one-way policy
removes -- every robot is moving in a legal direction. It is
load- and policy-dependent: `random` completes on seeds 1, 7 and 99 at
500 orders and stalls on 42, while both nearest policies complete at
500 and at 2000.

It is a traffic-layer gap, not a strategy defect, and it is
pre-existing: a strategy only chooses which robot takes which order,
and keeping any legal assignment set live is the traffic layer's job.
`random` is just the first strategy that scatters work across the hall.

Tracked in
`.scratch/traffic-liveness/issues/01-cyclic-deadlock-under-scattered-allocation.md`.
**Do not build a high-load throughput table until it is resolved**: the
deadlock is likelier under scattering policies, so such a table would
flatter the grid-aware strategy for a traffic reason rather than an
allocation reason.

### Compare strategies at scale

```bash
for s in nearest_astar nearest_manhattan; do
    PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
        --config src/simulation_env/test/fixtures/v2_reference.yaml --orders-file orders_2000.csv \
        --quiet --progress 0 --strategy $s \
        | grep -E "completed |makespan|total distance"
done
```

```text
nearest_astar       2000/2000   4296.2591 s   107306.0 m
nearest_manhattan   2000/2000   4294.6516 s   107406.0 m
```

At this scale the two nearest policies converge: 0.09% apart on
distance. The gap that was visible at 100 orders (+3.0%) washes out
once every robot is saturated with work and the binding constraint is
traffic and charging rather than pairing quality. That is itself a
result worth reporting, and it is the argument for comparing
strategies across *several* load points rather than one.

---

## 5. Gazebo

Gazebo is a **mirror**, never a planner. The engine owns all state;
`fleet_node` pushes poses out over gz-transport and never reads back.
A Gazebo run reproduces the headless numbers exactly.

**The tested, step-by-step Gazebo procedure is [START_HERE.md](START_HERE.md),
step 9**, including the build flag CMake needs on this machine and a
table of every failure seen. In short:

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash

# one command
ros2 launch simulation_env warehouse_sim.launch.py orders:=true robots:=20

# or two terminals: Gazebo once, runs many times
ros2 launch simulation_env gazebo.launch.py robots:=30          # terminal 1
ros2 launch simulation_env warehouse_sim.launch.py \
    start_gazebo:=false orders:=true robots:=30                  # terminal 2
```

The world file holds no robots. `gazebo.launch.py` stops any Gazebo
already running this world, writes a temporary copy of the world with
the robots parked on their start cells (`/tmp/mrta_gazebo_<uid>/`) and
starts Gazebo on it, so the robots load with the warehouse. Terminal 2
prints `[OK] Gazebo already shows these 30 robot(s)`, then
`[OK] 30 robot poses pushed to Gazebo world 'warehouse'`.

### 5.2 Launch arguments

| Launch arg | Default | Meaning |
|---|---|---|
| `orders` | `false` | run the workload; without it the fleet is placed and stands still, even if a `strategy` is named |
| `strategy` | `nearest_astar` | allocation policy |
| `speed` | `0.0` = config | m/s override |
| `headless` | `false` | `gz sim -s`, no GUI |
| `start_gazebo` | `true` | include `gazebo.launch.py`; set false to attach to a Gazebo started in terminal 1 |
| `config_file` | the source tree's `config/warehouse.yaml` | drives map, fleet size, order file, world name |
| `robots` | `0` = config | number of robots; Gazebo is given exactly this many |
| `world_file` | *derived from the config* | override only to force a specific `.world` |

### ⚠️ Exactly one server

Two `gz sim` servers both advertise `set_pose_vector` for the same
world. gz-transport hands each request to one of them, poses get
split, and robots appear to stall mid-aisle. The fleet node refuses to
draw when it sees two. `gazebo.launch.py` (and the one-command launch,
which includes it) first stops every `gz sim` of this world on the same
`GZ_PARTITION`, so starting Gazebo that way always leaves exactly one.

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

SOC falls while a robot works and rises while it charges (Battery V1 B4). A
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
# -> 207 passed
#
# or, equivalently, straight from the project environment:
#   pixi run test

# map topology: strongly connected, no orphans, every terminal
# enterable and leavable, every station reachable, no shortcut
PYTHONPATH=$PP $PY src/simulation_env/scripts/check_map_topology.py \
    --config src/simulation_env/test/fixtures/v2_reference.yaml
# -> 309 transit, 485 edges, 309/309, 0 unreachable, status : OK

# traffic topology: the core must stay fully one-way
PYTHONPATH=$PP $PY src/simulation_env/scripts/validate_directional_highways.py
# -> uncontrolled bidi edges : 0 ... status : OK

# RMF graph must mirror the lane policy exactly
# (the nav graph is the current map's, so check it against the one config)
PYTHONPATH=$PP $PY src/rmf_grid_traffic/scripts/verify_nav_graph.py \
    --config src/simulation_env/config/warehouse.yaml \
    --graph src/rmf_grid_traffic/config/warehouse_nav_graph.yaml
# -> W100: 4244 lanes, 0 illegal, 0 missing, 0 two-way core,
#    0 terminal chain, status : OK

# full workload
PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
    --config src/simulation_env/test/fixtures/v2_reference.yaml --orders 100 --progress 0
# -> 100/100, 22/22 parked, makespan 268.6667 s, reservation viol. 0

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
# -> W100: 2148 named waypoints, 4244 directed lanes, status OK

ros2 run rmf_grid_traffic route_check \
    src/rmf_grid_traffic/config/warehouse_nav_graph.yaml shelf_045 staging_001
```

Route lengths are deliberately **asymmetric**. On W100 `shelf_045 →
staging_001` is 33 waypoints and the reverse is 57 (on V2 they were 23
and 27). That is the one-way
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
| unit tests | 207 passed |
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
  offered only while FREE.
* on a charger it then **charges to exactly 100%, reverses into the
  aisle cell it came from and rejoins the fleet** (B4). The slot goes
  VACATING before it moves and FREE only once it is physically clear,
  so an entering and a leaving robot never meet on the access edge. The
  configured rate is the net gain: no idle drain over a charge. Every
  run prints a `CHARGING` table
* a robot that reached a *holding* cell is a **waiting** robot. The
  instant a charger becomes FREE the best waiting robot is **promoted**
  onto it -- lowest SOC, then earliest charge request, then `robot_id`
  as the final engineering tie-break (B5). It reverses off its holding
  cell, that cell is released only once it is physically clear, and it
  then drives to the charger by the production router
* when **every charger is occupied** and a CHARGE_DUE robot needs one,
  the charging robot with the **highest SOC** gives it up, and only if
  that SOC is strictly above the requester's (B5). The cut session
  books exactly the time it charged. The freed charger is **held for
  the requester** until it can physically enter, so the victim --
  standing one cell away with the lowest SOC in the bank -- cannot take
  it straight back. A CHARGE_NEEDED robot never preempts. A preempted
  robot rejoins the fleet if it is fit for work, and otherwise waits in
  a holding cell

  ```bash
  PYTHONPATH=$PP $PY src/simulation_env/scripts/check_charger_evacuation.py
  # -> five controlled scenarios (4 and 12 robots at 9%, a late
  #    CHARGE_NEEDED robot, saturation, the B2 deadlock fleet),
  #    each "checks: OK"; "B3/B4 charger evacuation and charging:
  #    5/5 scenarios OK"

  PYTHONPATH=$PP $PY src/simulation_env/scripts/check_b4_charging.py
  # -> single / four / twelve / recycle / slow / contested, each
  #    "checks: OK"; "B4 charging and fleet rejoin: 6/6 scenarios OK"

  PYTHONPATH=$PP $PY src/simulation_env/scripts/check_b5_queue.py
  # -> twelve / late / preempt / preempt_wait / contested, each
  #    "checks: OK"; "B5 promotion and preemption: 5/5 scenarios OK"
  ```

  To watch it in Gazebo (§5.1 terminal 1 running), terminal 2.

  **Rebuild first.** `simulation_env` installs its Python as *copies*,
  not symlinks, so `ros2 launch` runs the last build, not the working
  tree. Editing the executor and launching without this silently
  replays the previous increment:

  ```bash
  colcon build --packages-select mrta_core task_management simulation_env
  source install/setup.bash

  # robot_001..004 at 9%: four distinct chargers
  ros2 launch simulation_env warehouse_sim.launch.py start_gazebo:=false \
      orders:=true \
      config_file:=$PWD/src/simulation_env/test/fixtures/warehouse_sim_b3_four_low.yaml

  # robot_001..012 at 9%: 8 chargers, 4 wait in holding and are then
  # promoted onto the chargers the first eight leave (B5)
  ros2 launch simulation_env warehouse_sim.launch.py start_gazebo:=false \
      orders:=true \
      config_file:=$PWD/src/simulation_env/test/fixtures/warehouse_sim_b3_twelve_low.yaml
  # -> "promotions : 4 out of holding onto a freed charger"; 22/22
  #    parked; holding bank 14 FREE at the end
  ```

  These two configs are `v2_reference.yaml` plus
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
