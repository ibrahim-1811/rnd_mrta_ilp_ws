# Start here

> **28 October 2026 documentation freeze:** use [PROJECT_HANDOVER.md](PROJECT_HANDOVER.md) for current headless, Gazebo and dashboard demos. This older guide contains historical three-strategy/default/test-count references. Current code has eight strategies; research M1 has one failing report test.

From an empty computer to robots running in Gazebo, in plain steps.
Every command was run on 2026-10-23 and its real output is shown, so
you can tell whether yours worked.

## The whole system: one map, one config, one world

```text
   your Excel map  ──►  build  ──►  config/warehouse.yaml   (rewritten)
   maps/X.xlsx                      config/orders.csv       (regenerated)
                                    worlds/warehouse.world  (rebuilt)
                                         │
                       ┌─────────────────┴─────────────────┐
                       ▼                                   ▼
              headless runs                         Gazebo 3D view
              run_workload.py --robots 30           robots:=30
```

There is exactly **one config** (`src/simulation_env/config/warehouse.yaml`),
**one order list** (`config/orders.csv`) and **one Gazebo world**
(`worlds/warehouse.world`). They always describe the **current map**.
Switching to another map is one command that rewrites all three.

The world file holds the warehouse only. Starting Gazebo adds as many
robots as you ask for, parked on their start cells, so 10, 30 or 100
robots all use the same world.

```text
step 0   install                                once
step 1   check it works                         once
step 2   the map                                W100, or draw your own
step 3   build                                  config + orders + world
step 4   run it                                 headless
step 5   compare the three strategies
step 6   change the number of robots            10 ... 100
step 7   record data for learning
step 8   long runs: battery and charging
step 9   watch it in Gazebo
```

---

## What this program is

A simulated warehouse. Robots pick items from shelves and deliver them
to drop-off points. Roads are one-way. Robots use batteries and go to
chargers. The research question is: **which robot should get which
order?** That choice is made by an *allocation strategy*. Everything
else (map, roads, traffic, battery, speed) stays the same, so any
difference in results comes from the strategy.

| Strategy | How it decides |
|---|---|
| `nearest_astar` | nearest robot by **real driving distance** on the one-way roads |
| `nearest_manhattan` | nearest robot by **straight-line grid distance**, ignoring walls and road directions |
| `random` | a random robot; every strategy must beat this |

---

## Step 0 — install (once)

Linux (Ubuntu 24.04), [pixi](https://pixi.sh), and for Gazebo (step 9)
**ROS 2 Jazzy** with Gazebo Harmonic.

```bash
curl -fsSL https://pixi.sh/install.sh | bash      # then open a new terminal
git clone git@github.com:ibrahim-1811/rnd_mrta_ilp_ws.git ~/rnd_mrta_ilp_ws
cd ~/rnd_mrta_ilp_ws
pixi install
```

**Every new terminal**, run these three lines first:

```bash
cd ~/rnd_mrta_ilp_ws
export PY=.pixi/envs/default/bin/python
export PP=src/mrta_core:src/simulation_env:src/task_management
```

Every command starting `PYTHONPATH=$PP $PY` needs them.

---

## Step 1 — check it works (once)

```bash
pixi run test
```

```text
406 passed
```

---

## Step 2 — the map

The research map **W100** is already in place:

```text
src/simulation_env/maps/W100.xlsx    40 x 54 cells, room for 100 robots
                                     552 shelves, 36 drop-offs,
                                     24 chargers, 12 holding cells
```

See it in the terminal:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/map_tool.py show \
    --map src/simulation_env/maps/W100.xlsx
```

**To use your own map instead**, draw it with
[MAKE_A_MAP.md](MAKE_A_MAP.md) and save it in `src/simulation_env/maps/`.
The quickest start is a ready-made map you then edit in Excel:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/map_tool.py example \
    --robots 100 --out src/simulation_env/maps/my_map.xlsx
```

Check it after every edit:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/map_tool.py check \
    --map src/simulation_env/maps/my_map.xlsx
```

```text
status                   : OK
```

---

## Step 3 — build: config, orders and world in one command

Make a map the current one:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/build_scenario.py \
    --map src/simulation_env/maps/W100.xlsx
```

Use your own map's name to switch to it. This one command:

1. **checks the map**, and stops without changing anything if it has errors
2. **rewrites the config**: the map, and the robot count (set to the
   map's number of parking cells, the most it can run)
3. **regenerates the orders**: 2000 orders from the map's own shelves
   and drop-offs, seed 42 (`--orders 5000` for a different number)
4. **rebuilds the Gazebo world** and its road graph, and checks them

It ends with:

```text
CONFIG
    warehouse.map     -> W100.xlsx
    fleet.robot_count -> 100  (every parking cell; pick fewer per run with --robots or robots:=N)
ORDERS (2000, seed 42)
...
READY
  map    : W100.xlsx
  world  : src/simulation_env/worlds/warehouse.world  (warehouse only; robots are added when Gazebo starts)
  robots : up to 100
```

Building the same map again gives byte-identical files, so you can
always run it to get back to a known state.

**Edited the current map in Excel?** Run the same command again. Its
station names may have changed, so the orders are regenerated too.

---

## Step 4 — run it (no graphics)

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
    --robots 10 --orders 50
```

No `--config` is needed: the one config is the default. Near the end:

```text
  completed        : 50/50
  makespan         : 470.6667 s
  total distance   : 5680.0 m
```

| Result line | Meaning |
|---|---|
| `completed 50/50` | every order was delivered |
| `makespan` | simulated seconds until all work was done |
| `total distance` | metres driven by the whole fleet |
| `total waiting` | seconds robots spent waiting for each other |
| `reservation viol.` | traffic safety violations; must be `0` |

Add `--quiet --progress 0` for just the summary. The same command
always gives the same numbers.

### Change anything for one run

```text
--robots 30           fleet size, 1 up to the map's parking cells
--orders 300          the first 300 logical orders (an order is never split; 0 = all)
--orders-file X.csv   a different order list
--strategy random     nearest_astar | nearest_manhattan | random
--seed 7              seed for the random strategy
--quantum 3.0         batch freed robots for 3 s before assigning
--speed 1.0           robot speed in m/s
--dataset runs/X      record decisions and outcomes (step 7)
```

Everything else is in `config/warehouse.yaml`, every setting explained
and marked FREE (yours to change) or FIXED (kept the same for fair
comparisons).

---

## Step 5 — compare the three strategies

```bash
for s in nearest_astar nearest_manhattan random; do
    echo "### $s"
    PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
        --robots 100 --orders 300 --strategy $s --quiet --progress 0 \
        | grep -E "completed |makespan|total distance"
done
```

Measured on W100, 300 orders:

```text
robots  strategy            completed   makespan      distance
  10    nearest_astar       300/300     2860.7969 s   36368.0 m
  10    nearest_manhattan   300/300     2885.3609 s   36962.0 m
  10    random              300/300     2930.8578 s   37612.0 m
 100    nearest_astar       300/300      416.6667 s   37416.0 m
 100    nearest_manhattan   300/300      460.6667 s   39106.0 m
 100    random              300/300      483.3333 s   41404.0 m
```

The busier the warehouse, the more the strategy matters: random takes
2.4% longer than A* with 10 robots, and 16.0% longer with 100.

To check the strategies really make different choices:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/check_strategy_distinctness.py \
    --robots 50 --orders 100
```

It passes when every pair disagrees on at least 5% of real choices.

---

## Step 6 — change the number of robots

```bash
for n in 10 20 30 50 70 100; do
    printf "robots %-4s" $n
    PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
        --robots $n --orders 300 --quiet --progress 0 \
        | grep -E "completed |makespan|total waiting" | tr -s ' ' | tr '\n' ' '
    echo
done
```

Measured on W100 with `nearest_astar`:

```text
robots   completed   makespan      total waiting
  10     300/300     2860.7969 s     105.9056 s
  20     300/300     1463.3844 s     219.7089 s
  30     300/300      931.3333 s     380.6667 s
  50     300/300      616.6667 s     687.3333 s
  70     300/300      483.3333 s    1910.6667 s
 100     300/300      416.6667 s    3399.3333 s
```

From 70 to 100 robots the work barely finishes sooner, because robots
spend the extra time waiting for each other: total waiting grows 32
times from 10 to 100 robots.

Run time grows with the fleet: 10 robots take seconds, 100 robots and
300 orders about 2 minutes.

---

## Step 7 — record data for learning

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
    --robots 50 --orders 200 --quiet --progress 0 \
    --dataset runs/astar_50 --export
```

`runs/astar_50/` then holds:

```text
decisions.parquet   every robot-order pair the strategy considered, and
                    what it knew at that moment (distance, battery, ...)
outcomes.parquet    what really happened to each order
run.json            which map, config, orders and code made it
decisions.csv       the same, as plain text
outcomes.csv
bk.pl, exs.pl       the decisions as Prolog facts and examples for ILP
```

Recording never changes the result.

---

## Step 7b — a full experiment, with graphs

Every strategy at several fleet sizes, every run recorded, then one
set of graphs:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/run_experiment.py \
    --name fleet_sweep --robots 30 40 50 --seeds 1 2 3 --export
PYTHONPATH=$PP $PY src/simulation_env/scripts/plot_experiment.py --name fleet_sweep
```

15 runs (`random` once per seed; the deterministic strategies once),
8 at a time, about 4 minutes. Where everything goes:

```text
runs/fleet_sweep/
  summary.csv, summary.md          one row per run: every metric
  figures/mrta.png                 makespan, throughput, completion time, distance, waiting, allocator CPU
  figures/traffic.png              waiting per order, waiting share, yields
  figures/battery.png              energy, lowest SOC, charge sessions, time charging, waiting for a charger
  figures/completion_curves.png    orders completed over time
  figures/order_times.png          per-order duration and waiting
  <strategy>/r<robots>_s<seed>/
    run.log                        the run's full terminal output
    run.json                       which map, config, orders and code made it
    metrics.json                   the run's results
    decisions.parquet / .csv       every robot-order pair considered, decision time
    outcomes.parquet / .csv        what happened to each order
    bk.pl, exs.pl                  Prolog facts and examples for ILP
```

With `--export` the folder is about 1 GB (the CSV and Prolog files);
leave it out to keep only Parquet.

---

## Step 8 — long runs: battery and charging

A robot only charges below 10%, so short runs rarely charge. Give each
robot plenty of work:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
    --robots 20 --orders 2000 --progress 250
```

Every battery event is printed as it happens: `CHARGE_DUE`,
`CHARGE_START`, `CHARGE_FULL`, `REJOIN`, `PROMOTE`. The battery report
at the end must show `below 0 % SOC : none`. A minimum a little under
10% is normal: the reserve is checked when a robot is given a new
order, not while it finishes one.

---

## Step 9 — watch it in Gazebo

Use a **plain terminal**: not `pixi shell`, and without the pixi Python
in front of `ros2`.

### 9.1 Build the ROS packages (once)

```bash
cd ~/rnd_mrta_ilp_ws
source /opt/ros/jazzy/setup.bash

colcon build --packages-select \
    mrta_core task_management simulation_env \
    rmf_grid_traffic robot_interfaces gazebo_fleet_pose_transport \
    --cmake-args -DPython3_EXECUTABLE=/usr/bin/python3
```

```text
Summary: 6 packages finished
```

Keep the `--cmake-args` part. Without it CMake can pick up another
Python (for example one in `~/.local/bin`) that lacks the ROS build
tools, and `rmf_grid_traffic` fails with `No module named 'catkin_pkg'`.
Never add `--symlink-install`.

Rebuild only if the code changes. A new map does **not** need a
rebuild: step 3 writes the config and world into the source tree,
and the launch reads them from there.

### 9.2 Run it

Starting Gazebo **always stops a Gazebo already running this world**
first, so there is only ever one, and it opens with the robots already
parked on their start cells. A run then makes them move.

**Option A — two terminals**, best for a demo: Gazebo stays open and
you start runs again and again.

Terminal 1, Gazebo with 30 robots parked:

```bash
cd ~/rnd_mrta_ilp_ws
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ros2 launch simulation_env gazebo.launch.py robots:=30
```

The warehouse appears with 30 robots standing on their Stage Zones.

Terminal 2, the run (strategy and orders):

```bash
cd ~/rnd_mrta_ilp_ws
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ros2 launch simulation_env warehouse_sim.launch.py \
    start_gazebo:=false orders:=true robots:=30 strategy:=nearest_astar

# or a short run: only the first 50 orders
ros2 launch simulation_env warehouse_sim.launch.py \
    start_gazebo:=false orders:=true robots:=30 order_limit:=50
```

**It is working when terminal 2 prints:**

```text
[OK] Gazebo already shows these 30 robot(s)
[OK] 30 robot poses pushed to Gazebo world 'warehouse' as one persistent fleet stream
2000-ORDER WORKLOAD  |  30 robots  |  strategy=nearest_astar
```

and the robots start driving, about 9 s after you press Enter with 100
robots. Stop with `Ctrl+C` and run terminal 2 again, with another
strategy or another number: every run starts from the start cells. A
different number than terminal 1 also works; Gazebo is topped up or
trimmed to match:

```text
[OK] Gazebo fleet set to 10 robot(s): 0 created, 20 removed
```

**Option B — one command**, Gazebo and the run together:

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ros2 launch simulation_env warehouse_sim.launch.py orders:=true robots:=20
```

`Ctrl+C` stops both.

On `Ctrl+C` you see `stopped by Ctrl+C` and then one ROS line
`[ERROR] ... process has died ... exit code -2`. That is ROS reporting
the stop, not a fault.

### 9.3 Options

Add to the `ros2 launch` line:

| Option | Does |
|---|---|
| `robots:=50` | number of robots, 1 up to the map's parking cells |
| `order_limit:=50` | run only the first 50 logical orders (5, 10, 50, 100, 500 ... ; default all 2000); the same orders as headless `--orders 50`. The terminal prints what was asked and what runs: `orders : first 50 of 2000 logical orders (92 line items) from orders.csv, limit 50` |
| `strategy:=random` | a different strategy |
| `speed:=0.4` | slower robots, so the one-way roads are easy to see |
| `dataset:=runs/live` | record data as in step 7 (written as CSV here), plus `metrics.json` and `report.txt` |
| `headless:=true` | Gazebo without a window |
| `progress:=120` | simulated seconds between terminal status lines (default 60) |
| `verbose:=true` | print everything to the terminal again: every assignment, robot move, battery event and per-robot table |

`orders:=true` is what makes robots move.

**What the terminal shows** (without `verbose`): a short header, one
status line every 60 simulated seconds, warnings, and a summary at the
end:

```text
[t=   1260 s] orders 1043/2000 done, 50 active, 907 pending | robots 41 busy, 0 free, 9 charging or queued | SOC min 7.8% mean 46.2%
...
RUN COMPLETE  |  nearest_astar  |  50 robots  |  2000 orders
  orders completed  : 2000/2000
  makespan          : 3733.3501 s simulated
  ...
  full report       : /home/<you>/.ros/mrta/20261025_141318_nearest_astar_50robots.txt
```

The full report (every robot's battery, every charge session) is in
that file; with `dataset:=DIR` it is `DIR/report.txt`. Everything else
is on ROS topics, in another terminal:

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ros2 topic echo /mrta/fleet_status      # orders pending/active/done, robots busy/charging/waiting, chargers free, SOC min/mean
ros2 topic echo /mrta/robot_states      # every robot: cell, status, battery_percent, current_order, charge_state
ros2 topic echo /mrta/events            # one line per assignment, completed order and battery event
```

### 9.4 When Gazebo does not work

| You see | Do this |
|---|---|
| `Package 'simulation_env' not found` | `source install/setup.bash`; if it still fails, do 9.1 |
| `No module named 'catkin_pkg'` during the build | 9.1 with the `--cmake-args` part |
| `No module named 'robot_interfaces'` or `'mrta_core'` | 9.1, all six packages |
| `Unable to find or download file ... warehouse.world` | step 3 (build) |
| `set_pose_vector not found; continuing without Gazebo` | Gazebo was not up within 60 s: start terminal 1 first and wait for the warehouse |
| Gazebo opens with no robots | fixed on 2026-10-24: rebuild (9.1). Gazebo now loads the robots with the warehouse |
| robots appear or start late | fixed on 2026-10-24: rebuild (9.1). With 100 robots the first move is about 9 s after the run starts |
| Gazebo opens, robots stand still | add `orders:=true` |
| robots stop mid-road or jump about | two Gazebo servers are running, started some other way: close them, then start with `gazebo.launch.py`, which allows only one |
| errors about `rclpy` or other Python modules | the terminal is inside `pixi shell`: open a new plain one |
| `the map has only N home station(s)` | `robots:=` is more than the map's parking cells |
| every robot stops for a moment, again and again | fixed on 2026-10-24: rebuild (9.1). One short stop at the very start is normal: every robot gets its first order at once |
| all orders done (`2000/2000 completed`), then nothing more is printed | fixed on 2026-10-24: rebuild (9.1). The summary now prints within a second |

Gazebo only shows what the simulator decided; it never decides
anything itself.

### 9.5 Watch the battery

Robots start full and only charge at 10% or less, so nothing charges in
the first part of a run. With 100 robots on W100 the first robot asks
for a charger at about t=1267 s of simulated time, roughly 5 minutes
into the Gazebo run.

Every battery step is one line on `/mrta/events`
(`ros2 topic echo /mrta/events`):

```text
t=1266.667  REQUEST       robot_039  SOC 14.4656% ... no battery-eligible order among 620 pending
t=1266.667  RESERVE       robot_039  ... charger charging_005 at (8, 53), directed A* 54.0 m
t=1303.333  CHARGE_START  robot_039  ... to 100% in 88.2367 simulated s
```

then `CHARGE_FULL` and `REJOIN`. The terminal status line counts them
(`9 charging or queued`), and `/mrta/fleet_status` lists who is
charging (`charging_robots`) and who waits for a charger
(`waiting_robots`). Every robot's state:

```bash
ros2 topic echo /mrta/robot_states --once | grep -E "robot_id|status|battery_percent|charge_state"
```

The end-of-run report has the full picture under `BATTERY` and
`CHARGING`. A robot that asks for a charger while every charger and
holding cell is taken waits on a Stage Zone (`SATURATION`). It is not
stuck there: when a charger frees, the waiting robot with the lowest
SOC, in holding or on a Stage Zone, gets it (`PROMOTE ... stage
staging_021 -> charger charging_001`), charges and rejoins. The report
counts them under `promotions` (`... from a Stage Zone`). A robot too
low to reach any free charger keeps waiting and is reported once as
`OUT_OF_REACH`.

---

## If something goes wrong (headless)

| You see | Do this |
|---|---|
| `No module named 'mrta_core'` | run the three lines from step 0 |
| `the arrow at E9 points ...` | fix that Excel cell ([MAKE_A_MAP.md](MAKE_A_MAP.md)) |
| `shelf 'shelf_115' is not in the warehouse map` | the orders are from another map: step 3 |
| `STALLED` and `CYCLES robot_a -> robot_b -> ...` | robots locked each other: run `map_tool.py check` and fix every `ROTARY` warning |

---

## Where everything is

```text
src/simulation_env/maps/W100.xlsx         the map (put new maps here)
src/simulation_env/config/warehouse.yaml  the one config
src/simulation_env/config/orders.csv      the orders for the current map
src/simulation_env/worlds/warehouse.world the one Gazebo world (warehouse only)
src/simulation_env/launch/                gazebo.launch.py (Gazebo + parked robots),
                                          warehouse_sim.launch.py (a run)
/tmp/mrta_gazebo_<uid>/warehouse.world    the world + robots Gazebo loads,
                                          rewritten on every start
src/simulation_env/scripts/               every command in this guide
runs/                                     recorded data (not in git)
```

`src/simulation_env/test/fixtures/` holds the old V2 map and its config.
They are not for experiments: the tests and the frozen V2 baselines are
measured on them.

More: [HOW_IT_WORKS.md](HOW_IT_WORKS.md) (data flow, demo script),
[REPO_ATLAS.html](REPO_ATLAS.html) (the whole repo, with diagrams),
[MAKE_A_MAP.md](MAKE_A_MAP.md) (drawing a map).
