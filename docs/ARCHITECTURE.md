# System Architecture

How this repository is actually wired, as of **2026-10-11** (Battery V1
through B5). Every claim here was read from the code, not from older
documentation.

This is the map to follow when you need to find where something lives,
what owns it, and what is allowed to touch it.

---

## 0. Read this first

```text
CLAUDE.md                     the development contract
docs/CURRENT_PHASE.md         what is implemented right now
docs/DECISIONS.md             frozen choices; do not silently redesign
docs/ROADMAP.md               phase plan
docs/EXPERIMENT_PROTOCOL.md   experimental methodology
docs/00_RUNBOOK.md            every command, verified
docs/ARCHITECTURE.md          this file
```

Documents `01`–`17` describe the **pre-freeze** system that was deleted
in commit `7a07208`. They are historical. Where they disagree with this
file, this file is right. `docs/README.md` lists their stale claims.

---

## 1. The one-paragraph version

A YAML config names a warehouse map, a fleet and an order CSV. Orders
are loaded once, all PENDING. A FIFO queue decides *which* orders an
allocator may look at; an allocation strategy decides *which robot gets
which of them*; everything below that is fixed infrastructure shared by
every strategy. The chosen pairs are turned into static directed-A*
routes and handed to an event-driven engine that runs on one rational
clock, gates every move through a reservation table and a local traffic
controller, and books travel, service, waiting and charging energy into
a single battery ledger. Gazebo mirrors the result; it never decides
anything.

The **research variable** is the allocation strategy. Everything else is
deliberately fixed so that comparisons mean something.

---

## 2. Packages

| Package | Language | Role | ROS dependency |
|---|---|---|---|
| `mrta_core` | Python | Domain core: map, orders, routing, battery model, config. No ROS. | none |
| `task_management` | Python | Candidate features and allocation strategies. The research variable. | none |
| `simulation_env` | Python | Execution: engine, traffic, reservations, charging, parking, executor, ROS node. | `rclpy` for the node only |
| `rmf_grid_traffic` | C++ / Python | Nav-graph generation and verification; RMF graph checks. | ROS |
| `robot_interfaces` | msg | `RobotState`, `RobotStateArray`. | ROS |
| `gazebo_fleet_pose_transport` | C++ | Pose sink into Gazebo. | ROS / gz |

`mrta_core` and `task_management` are importable with plain Python. That
is why the headless runner and the whole test suite work without ROS.

> **colcon installs Python packages as copies.** After any Python
> change, rebuild all four:
> `colcon build --packages-select mrta_core task_management simulation_env rmf_grid_traffic`

---

## 3. Execution path, end to end

```text
                      run_workload.py            fleet_node.py
                      (headless CLI)             (ROS 2 node)
                              │                        │
                              └────────┬───────────────┘
                                       ▼
                         load_sim_config   (mrta_core/sim_config.py)
                                       │
                     ┌─────────────────┼──────────────────┐
                     ▼                 ▼                  ▼
          load_warehouse_xlsx    load_orders_csv     BatteryConfig
          (warehouse_map_xlsx)      (orders.py)      (battery.py)
                     │                 │
                     ▼                 ▼
               WarehouseMap        OrderQueue  ──── FIFO: which orders
               grid + zones        all PENDING       are on the table
                     │                 │
                     └────────┬────────┘
                              ▼
                     WorkloadExecutor.run()          ← orchestration
                              │
      ┌───────────────────────┼───────────────────────────────┐
      ▼                       ▼                               ▼
  dispatch()            engine.run()                      _tick()
      │                       │                               │
      │  1 available_robots() │  pops the next timestamp      │ retire
      │  2 _battery_gate()    │  applies every event there    │ finishers,
      │  3 _evacuate()        │  releases, then claims        │ settle,
      │  4 queue.eligible(n)  │  arbitrates traffic           │ charge,
      │  5 allocator.assign() │  books travel/service/wait    │ rejoin,
      │  6 _check_battery_…   │  books charge                 │ then
      │  7 launch_batch()     │                               │ dispatch()
      ▼                       ▼                               ▼
   OrderAssignment      Event (Fraction time)            Progress
      │                       │
      ▼                       ▼
  plan_from_cell        ReservationTable + LocalTrafficController
  (directed A*)         MOVE / WAIT / YIELD
      │                       │
      ▼                       ▼
  MissionPlan           GridRobot.cell advances, BatteryLedger booked
                              │
                              ▼
                        Order COMPLETED at the drop-off service
                              │
                              ▼
                   stdout report  +  (ROS) /mrta/robot_states  +  Gazebo poses
```

There is **no file output**. Everything is stdout or a ROS topic. That
is the single biggest gap before experiments (see §12).

---

## 4. Component map

| Component | File | Main entity | Input | Output | Used by |
|---|---|---|---|---|---|
| Config | `mrta_core/sim_config.py` | `load_sim_config` → `SimConfig` | YAML path | map path, fleet, service, battery, Gazebo | both entry points |
| Battery model | `mrta_core/battery.py` | `BatteryConfig`, `travel/service/stationary/charge_energy`, `energy_requirement`, `BatteryDecisionContext` | distances, durations, SOC | exact `Fraction` percentages | ledger, features, executor |
| Map | `mrta_core/warehouse_map_xlsx.py` → `warehouse_map.py` | `load_warehouse_xlsx` → `WarehouseMap` | XLSX | `GridMap` + typed zones | everything |
| Grid / A* | `mrta_core/grid.py` | `GridMap`, `astar_path` | grid, start, goal | `PathResult` | routing |
| Lane policy + routing | `mrta_core/warehouse_routing.py` | `directional_blocked_edges`, `navigation_grid`, `best_path_to_zone`, `plan_from_cell`, `RouteCache`, `nearest_reachable_zone` | cell, zone ids | `MissionPlan` | allocator, executor, charging, parking, nav graph |
| Orders | `mrta_core/orders.py` | `load_orders_csv` → `Order` | CSV | logical orders | queue |
| FIFO queue | `mrta_core/order_queue.py` | `OrderQueue.pending`, `.eligible(n)` | orders | the candidate set | executor |
| Candidates | `task_management/features.py` | `order_route_cost`, `build_order_candidates` → `OrderCandidate` | robots, orders | cost + route + energy | strategies |
| **Strategy** | `task_management/allocation_strategy.py` | `BatchAllocationStrategy`, `NearestAStarAllocator`, `BATCH_STRATEGIES` | candidates | `OrderAssignment` | executor |
| Orchestration | `simulation_env/workload_executor.py` | `WorkloadExecutor` | everything | `WorkloadResult` | entry points |
| Engine / clock | `simulation_env/fleet_engine.py` | `FleetSimulationEngine`, `EventType`, `Event` | `MissionPlan`s | events, timing | executor |
| Reservations | `simulation_env/reservations.py` | `ReservationTable` | claims | conflict-free occupancy | engine |
| Traffic | `simulation_env/local_traffic_controller.py`, `local_yield_selector.py` | `LocalTrafficController` | move intents | MOVE / WAIT / YIELD | engine |
| Robot state | `simulation_env/grid_robot.py` | `GridRobot`, `GridRobotStatus` | moves | cell, counters | engine |
| SOC ledger | `simulation_env/battery_ledger.py` | `BatteryLedger`, `BatterySnapshot` | engine bookings | SOC(t) | engine, executor, node |
| Chargers | `simulation_env/charging.py` | `TerminalBank`, `ChargeRecord`, `ChargeState`, `ResourceState` | request cells | reservations, slot lifecycle | executor |
| Parking | `simulation_env/stage_parking.py` | `StageParking.assign` | idle robots | Stage Zone claims | executor |
| ROS node | `simulation_env/fleet_node.py` | `FleetNode` | config param | `/mrta/robot_states`, Gazebo poses | launch |
| Gazebo bridge | `simulation_env/gazebo_bridge.py`, `gazebo_view.py` | `FleetPoseSink`, `grid_to_world` | engine state | poses | node |
| Nav graph | `rmf_grid_traffic/scripts/generate_nav_graph.py`, `verify_nav_graph.py` | generator + verifier | map + lane policy | `warehouse_nav_graph.yaml` | RMF checks |

---

## 5. Layer by layer

### 5.1 Configuration

`SimConfig` is frozen and authoritative. One YAML drives map, fleet,
battery, service durations, orders, visualization and Gazebo. There are
no duplicate constants in the executor.

Strictness is deliberate: `BatteryConfig.from_mapping` **refuses unknown
keys**, and `load_sim_config` refuses the superseded
`fleet.initial_battery_percent`. A typo must fail loudly rather than
silently drain at a rate nobody asked for.

- The one config: `config/warehouse.yaml` (the current map, W100;
  any fleet size per run). Rewritten by `build_scenario.py --map`.
- Test fixtures in `test/fixtures/`: `v2_reference.yaml` (the V2 map,
  22 robots; every frozen V2 baseline and most tests), and
  `warehouse_sim_b3_four_low.yaml`, `warehouse_sim_b3_twelve_low.yaml`,
  identical except `battery.initial_soc_overrides`.
  **Never use the fixtures for experiments.**

Parsed but currently **unused**: `simulation.seed`, `simulation.deterministic`,
`orders.mode`. The system is deterministic without them.

### 5.2 Warehouse map and zones

A 17 × 35 XLSX grid. Zone types: `SHELF`, `DROPOFF`, `COLLECTION`/`STAGING`
(Stage Zone homes), `CHARGING`, `CHARGE_QUEUE` (holding).

```text
72 shelves | 36 dropoffs | 22 Stage Zones | 8 chargers | 14 holding | 134 blocked
309 transit cells | 485 directed edges | strongly connected both ways
```

All five zone types are **terminal**: enterable when targeted, never a
transit shortcut. Charging and holding form one-cell-deep banks at
`y=2` and `y=32`, entered from the aisle cell directly outside and left
by reversing into it.

### 5.3 Routing: the single source of truth

`warehouse_routing.py` is the one place that answers "how far, and by
what path". Both prediction and execution call it, which is what makes
predicted-vs-realized comparisons meaningful later.

Two rules combine:

1. `navigation_grid()` blocks every terminal **except the current leg's
   own start and goal**.
2. `directional_blocked_edges()` enforces one-way aisles and forbids
   every terminal→terminal edge.

`plan_from_cell()` walks legs and appends the one-cell **retreat** after
each shelf and dropoff service, because the robot really drives it. That
retreat is included in distance, cost and energy.

`RouteCache` memoizes legs for a run. It is a pure memo — same inputs,
same `PathResult` — so it changes cost and timing, never the route.

> **Known constraint.** The lane policy hard-codes V2 coordinates
> (`EASTBOUND_ROWS`, `SHELF_COLS`, frontage columns, …). No W30/W50 map
> can be legal until these are parameterised. This blocks the scalability
> family, not Battery V1.

### 5.4 Orders and the FIFO window

```csv
order_id,item_id,shelf_id,quantity,dropoff_id
```

Rows sharing an `order_id` form one logical order (1–3 shelves, one
dropoff). Every order exists at t=0 and starts PENDING. There is **no
priority, release time or deadline**, by frozen decision.

`OrderQueue.eligible(n)` returns the next `min(n, pending)` PENDING
orders in CSV sequence. `n` is the **candidate window width** W, set
from `allocation.window` (S0), not the number of available robots.

```text
fleet_size   W = robot_count. The frozen default.
legacy       the pre-S0 min(available_robots, pending). Kept only to
             reproduce the pre-S0 baselines.
<integer>    an explicit width, for controlled scenarios.
```

**Why.** Under the legacy rule the window was sized by the free pool,
and because dispatch is continuous robots come free one at a time, so
the window was usually one order. Measured on the 100-order run: 71
allocator calls, **62 of them 1 robot × 1 order**. On a 1×1 decision
every strategy — Random, Hungarian, ILP — returns the same assignment,
so no strategy comparison was defensible. With `W = fleet_size` the
same run has 64 decisions of which only 1 is forced. See §12, P0-1.

**FIFO liveness rule.** A strict prefix can hold nothing any free robot
can take — a dear order at the head with the fleet low on charge — while
a feasible order sits behind it. The allocator then returns nothing,
recovery parks the robots because they owe no work, and the run stalls
with orders outstanding. So when the prefix offers no assignable pair at
all, `_candidate_window` extends it down the FIFO sequence until one
appears. This is liveness, not priority: it widens the choice, never
reorders the queue, and runs only when the choice would otherwise be
empty. It is computed from routing and energy alone, so every strategy
is handed the same window. `legacy` is exempt, so the stall stays
reproducible (`test_s0_window.py`).

**Decision shape** is recorded per decision (`decision_shapes`,
`choice_bearing`, `window_extensions`) and printed by
`decision_report_lines`. A decision with `robots × orders == 1` is
*forced*; only choice-bearing decisions can distinguish strategies, and
they are the denominator the distinctness gate uses.

### 5.5 Allocation: the research variable

```python
candidates = build_order_candidates(warehouse, robots, orders, fifo, cache, battery)
# each OrderCandidate: cost (C_static), route (OrderRoute), energy (EnergyRequirement)
assignments = strategy.assign(...)   # must respect candidate.eligible
```

`C_static(r, o)` is the shortest static directed-A* route from the
robot's cell, through every shelf **in the best permutation** (at most
3! = 6 evaluated exhaustively), to the dropoff, including retreats.

It is deliberately static: no reservation table, no other robot's
timing, no realized waiting. Folding realized waiting back in would
destroy the predicted-vs-realized signal the research depends on.

**Registered strategies today: exactly one.**

```python
BATCH_STRATEGIES = {
    "nearest_astar":     NearestAStarAllocator,
    "nearest_manhattan": NearestManhattanAllocator,
    "random":            RandomAllocator,
}

# All three are greedy_pairing() over a different ordering, so two
# strategies differ by their ranking and by nothing else.
```

`nearest_astar`: drop ineligible pairs, sort by
`(cost, fifo_position, robot_id)`, greedy sweep. `robot_id` only ever
breaks a tie the first two keys left equal.

#### Adding a strategy

1. Subclass `BatchAllocationStrategy` in `allocation_strategy.py`.
2. Decorate with `@register_batch`; set a unique `name`.
3. Implement `assign(...)`, returning at most one order per robot and
   one robot per order, **only from `orders`**, and **only pairs whose
   `candidate.eligible` is true**.
4. Run it with `--strategy <name>`.
5. Prove it is behaviourally distinct from existing strategies on at
   least one controlled scenario (CLAUDE.md requires this).

A strategy may **not** touch routing, traffic, battery drain, charger
policy, speed or service duration.

### 5.6 Execution: one clock, one truth

`FleetSimulationEngine` owns the only clock. Time is `Fraction`, never
float: one grid edge at 1.5 m/s is exactly 2/3 s, so robots that start
together land on *identical* timestamps and are processed as one
concurrent batch forever.

```text
events at one timestamp  →  phase 1: every robot releases what it leaves
                         →  phase 2: only then does anyone claim
```

Without that split, a robot handled first would see a cell another is
about to vacate as occupied and report a phantom conflict.

Event types:

```text
MOVE_COMPLETE  WAIT_COMPLETE  SERVICE_COMPLETE  CHARGE_COMPLETE
RETREAT_COMPLETE  YIELD_COMPLETE  REJOIN_COMPLETE  MISSION_COMPLETE
```

`step_once()` calls `on_event` **before** `_handle`, then
`_continue_pending`, `_retry_waiting`, `_stamp_all`, and finally
`on_tick`. **Policy reactions belong in `on_tick`**, after the batch is
fully applied — that is where the executor settles arrivals, completes
charges and rejoins robots.

Wall-clock time appears in exactly two places, neither scientific:
`PerfBudget` (CPU profiling) and `fleet_node` playback pacing. No
`time.time`, no `datetime`.

### 5.7 Traffic and reservations

`ReservationTable` holds time-indexed cell intervals and directed-edge
transits. A robot traversing A→B holds **both** cells until it arrives,
which is what prevents a third robot slipping in mid-transit.

`LocalTrafficController` arbitrates simultaneous intents:

```text
1. busy robot beats non-busy        (order work outranks recovery moves)
2. smaller remaining distance wins
3. lower numeric robot_id           (engineering tie-break only)
```

One exception to that order: robots closing a loop of three or more,
each moving into the cell the next is leaving, rotate together, and a
loop member wins the cell it rotates into against any outsider
(DECISIONS.md, "Rotation Right-of-Way"). Without it a full ring could
wait forever.

Head-on swaps are never approved; the loser yields one cell and rejoins.
Structural head-on is prevented by the one-way topology, so yields are
rare — all current controlled runs report **0 yields**.

Traffic produces waiting, queues and contention. These are **valid
realized outcomes**. Traffic never changes robot-order ownership.

### 5.8 Battery V1

Two halves that must never drift apart:

- **`mrta_core/battery.py`** — pure functions, exact `Fraction`
  arithmetic, no state, no clock.
- **`simulation_env/battery_ledger.py`** — the one authoritative SOC
  record, written **only** by the engine.

Rates (all configurable):

```text
travel          1% / 20 m
service         1% / 60 simulated s
waiting, idle   1% / 300 simulated s
charging       +1% / simulated s   NET (no idle drain during a charge)
reserve         10%
charge due      SOC <= 10%
```

The ledger's time partition, and the identity it guarantees:

```text
elapsed = moving_s + service_s + waiting_s + charging_s + idle_s

SOC = initial - travel - service - waiting - idle + charge        exact
```

**Idle is never booked — it is the remainder.** That is why
`charging_s` must be subtracted: a charging robot is charging, not
idling. A negative remainder means something was booked twice, and
raises rather than hides.

`cross_check()` independently rebuilds SOC from `GridRobot`'s float
counters. Agreement to ~1e-14 % is asserted every run.

#### Decision-time eligibility (B2) — a hard mask for every strategy

```text
required = travel(order route incl. retreats)
         + service(2 s per shelf + 2 s dropoff)
         + travel(directed-A* leg from the retreat cell to the nearest charger)
         + reserve
eligible = SOC >= required          (equality is eligible)
```

**No predicted waiting.** Waiting is an uncertain realized outcome; it
drains the battery during execution instead.

The executor independently re-derives every returned pair and raises
`IneligibleAssignmentError` rather than execute or silently re-assign
one — infrastructure must never make an allocation decision.

Two withdrawal states:

```text
CHARGE_DUE      SOC <= 10%
CHARGE_NEEDED   SOC > 10% but no eligible order among ALL pending orders
```

### 5.9 The charging workflow (B3 + B4 + B5)

Slot lifecycle, one per charging/holding cell:

```text
FREE ─reserve─> RESERVED ─occupy─> OCCUPIED ─vacate─> VACATING ─cleared─> FREE
```

A slot is offered **only while FREE**. Every illegal transition raises.

Robot lifecycle:

```text
CHARGE_DUE / CHARGE_NEEDED
   → REQUEST            leaves the fleet; reserves before it moves
   → TO_CHARGER         cheapest FREE charger by directed A*
      (else TO_HOLDING, else TO_STAGE_FALLBACK on saturation)
   → OCCUPIED_CHARGER   arrival, same instant
   → CHARGING           to exactly 100%, duration = (100 - SOC) / rate
   → LEAVING_CHARGER    slot VACATING *before* the robot moves
   → REJOINED           slot FREE only once it stands on the aisle cell
   → ordinary robot; B2 eligibility re-tests actual post-exit SOC
```

The full charge reaches 100% on the terminal. Exit travel and any exit
waiting consume energy normally; eligibility uses that lower rejoin SOC.
The engine's mission-progress watchdog excludes only charge-only elapsed
intervals with exact finite completion events still queued. Unfinished
traffic keeps the watchdog active, and the event budget remains enforced.

**Why the access edge is safe.** The edge is two-way, so a swap would
need one robot entering while another leaves. Only the reservation
holder may enter, and the slot is not FREE again until the leaver has
physically cleared. Both halves of that are enforced by the slot
machine, which is why all controlled runs report 0 yields on access
edges.

A robot that reaches a **holding** cell is a *waiting* robot (B5):

```text
HOLDING
   → LEAVING_HOLDING    promoted onto a FREE charger; the holding slot
                        goes VACATING *before* the robot moves
   → PROMOTED           holding slot FREE once it stands on the aisle
                        cell; a NEW charger ChargeRecord takes over,
                        carrying the same original request
   → TO_CHARGER → ... → REJOINED, exactly as above
```

Promotion is one idempotent pass per dispatch, ordered by the frozen
queue discipline (lowest SOC, earliest request, `robot_id`), so the pair
(FREE charger, waiting robot) never survives a tick.

A CHARGE_DUE robot that finds every charger occupied preempts the
highest-SOC charging robot, and only if that SOC is strictly above its
own:

```text
CHARGING
   → PREEMPTED_LEAVING_CHARGER   session cut short; only the interval
                                 really charged is booked
   → REJOINED                    if above CHARGE_DUE and an eligible
                                 order exists
   → PREEMPTED                   otherwise: it waits in a holding cell
```

The charger freed by a preemption is **assigned to the requester** from
that instant (`TerminalSlot.promised_to`) and is not on general offer,
though the requester still may not enter until the victim is physically
clear. See docs/DECISIONS.md, "Preemption Hands the Charger Over".

Charger and parking trips are **recovery moves**: order work keeps right
of way (`is_parking=True`), and arrival is never an order completion.

### 5.10 Stage Zone parking

The bank is a **shared fleet resource**, not robot-owned homes. A spawn
cell confers no title. While orders remain, a finished robot stays
available in the aisle where it retreated. Only when the fleet runs out
of work does it claim the nearest FREE Stage Zone by directed A*,
reserved at assignment time so two robots cannot pick the same cell.

### 5.11 Gazebo and RMF

Gazebo is a **mirror**. `FleetNode` runs the whole workload
synchronously in its constructor. Engine callbacks render, publish and
pace visualization during that execution; it is not a separate playback
after the workload. The periodic ROS timer is created after the run.
There is no second planner, allocator or traffic policy.

```text
grid cell (5, 15)  ──grid_to_world──>  world point (5.0, 15.0)
```

Identity mapping with 1 m cells, so a pose read back *is* the cell.

The RMF nav graph is **generated from the same lane policy** and
verified lane-for-lane (973 lanes, 0 illegal, 0 missing, 0 terminal
chains).

> **Launch gotcha.** `ros2 launch simulation_env warehouse_sim.launch.py`
> needs **`orders:=true`** (it sets both `load_orders` and `run_workload`;
> without it the node deploys the fleet and spins) and **`config_file:=`**,
> not `config:=`. There is no start/advance service or topic anywhere.

---

## 6. Invariants that must not regress

```text
one authoritative Fraction clock; no wall-clock in any scientific number
transit aisles one-way; 0 uncontrolled bidirectional core edges
terminals never used as transit; no terminal→terminal move
traffic never changes robot-order ownership
battery eligibility is a hard mask applied identically to every strategy
decision-time features contain no realized outcome
SOC identity exact; idle is the remainder
a terminal slot is offered only while FREE
robot_id is a tie-break, never a scientific predicate
Gazebo contains no planning logic
```

---

## 7. Determinism

Same config + CSV + map + strategy ⇒ byte-identical output. Guaranteed by:

- rational time, so concurrency is exact and stable;
- total orderings everywhere (`(time, seq)`, `(cost, fifo, robot_id)`,
  `(cost, zone_id, robot_id)`);
- sorted iteration over robots at every decision point;
- `RouteCache` as a pure memo.

Verified by re-running and diffing, and by determinism tests in the suite.

---

## 8. Testing

```text
pixi run test     189 tests   (mrta_core 98, simulation_env 86, task_management 5)
pixi run lint     ruff
```

| Area | Where |
|---|---|
| Battery model | `mrta_core/tests/test_battery.py` |
| SOC ledger, charging | `simulation_env/test/test_battery_ledger.py` |
| Eligibility, CHARGE_DUE/NEEDED | `simulation_env/test/test_battery_eligibility.py` |
| Charger travel, charging, rejoin | `simulation_env/test/test_charger_evacuation.py` |
| Slow charging, watchdog, reuse, clearance, repeated sessions | `simulation_env/test/test_b4_lifecycle.py` |
| Bank topology | `simulation_env/test/test_charging_bank.py` |
| Parking | `simulation_env/test/test_stage_parking.py` |
| Routing / lane policy | `mrta_core/tests/test_warehouse_routing.py` |
| Orders / FIFO | `mrta_core/tests/test_orders.py`, `test_order_queue.py` |
| Allocation | `task_management/test/test_nearest_astar.py`, `test_strategies.py` |

Gate scripts (all read-only):

```text
scripts/check_map_topology.py            connectivity, terminal access
scripts/validate_directional_highways.py one-way integrity
rmf_grid_traffic/scripts/verify_nav_graph.py   graph mirrors the planner
scripts/check_battery_eligibility.py     B2
scripts/check_charger_evacuation.py      B3/B4, 5 scenarios
scripts/check_b4_charging.py             B4, 6 scenarios including slow charge and actual reuse
scripts/check_b5_queue.py                B5, 5 scenarios including both preemption branches
+ 7 traffic behaviour scripts
```

> Tests in `test_grid` and `test_world` protect production routing and
> map-grid abstractions. `test_models`, `test_symbolic` and
> `test_kinematics` also cover older planning interfaces; distinguish
> their semantics from the active battery/order model. See §10.

---

## 9. Frozen baselines

Any change to these means something moved in the planner, map or model.

| Run | Result |
|---|---|
| 100 orders, 22 robots | 100/100, 22/22 parked, **282.0000 s**, 6430.0 m, waiting 264.6667 s, 0 violations |
| 100-order final SOC | min 80.7222, mean 84.7322, max 86.6256 |
| 500 orders | 500/500, **1123.3333 s**, 29338.0 m, waiting 1242.6667 s, 0 violations |
| 500-order final SOC | min 27.8989, mean 30.7151, max 33.6000 |
| Topology | 309 transit cells, 485 edges, strongly connected |
| Nav graph | 461 waypoints, 973 lanes |

Both scientific runs have **0 charge sessions** — no robot ever gets low
enough on the standard workload. Charging is exercised only by the
controlled configs.

---

## 10. Legacy code — do not build on it

Some exported interfaces retain older planning assumptions. They must
not replace the active order and battery models, but the modules are
not all unused: production maps instantiate `WorldGrid`, `grid.py`
imports `models`, and package initialization imports exported modules.

| Module | Problem |
|---|---|
| `models.py` | Imported by `grid.py`; its older `TaskState` fields such as `priority`/`release_time`/`deadline` are not the active order schema |
| `symbolic.py` | Predicates built on those fields, 0–1 battery scale |
| `kinematics.py` | A **second energy model** (trapezoid profile, rolling resistance) whose docstring falsely claims to be the energy authority |
| `world.py` | Production `WorldGrid` used by `WarehouseMap` and its loaders; not superseded by `world_view` |
| `grid.extract_path_features` | `energy_per_cell=0.01`, unrelated to the battery model |

Before building the symbolic/ILP layer, review the older planning and
energy interfaces so the predicate library does not inherit forbidden
features or a contradictory energy model. Preserve the production grid
dependencies; removing whole modules indiscriminately would break them.

---

## 11. What does **not** exist yet

```text
MRTA strategies slice 2        Earliest Finish, Multi-Criteria,
                               Auction, Hungarian, MILP
experiment runner              no batch, no seeds in use, no manifests
statistical analysis, plots    none
symbolic predicate library     only the legacy stub
Popper / Prolog / ILP          none (swi-prolog is a declared dependency only)
explanations                   strategy.explain() returns one line
W30 / W50 maps                 lane policy is hard-coded to V2
B6 battery metrics, order_makespan split, G6 end-of-work charging
```

`is_chosen = 1` is still not an outcome, but the join now exists:
`outcomes.parquet` carries the `decision_id` that assigned each order,
so decision → execution → realized outcome is one join away. See
section 11b.

---

## 11b. The decision and outcome dataset

```text
simulation_env/dataset.py         schemas, RunRecorder, manifest
simulation_env/dataset_export.py  CSV and ground Prolog facts
```

One directory per run:

```text
decisions.parquet   one row per scored (robot, order) candidate pair
outcomes.parquet    one row per order
run.json            provenance; the run_id is a hash of the manifest,
                    so two runs collide exactly when every input that
                    could change a result is identical
```

Recording is opt-in (`run_workload.py --dataset DIR`) and observation
only: an instrumented run and a bare run agree on makespan, distance,
waiting, service, the decision log and the dispatch sequence.

**Where the boundary is.** A decision row is built in `_record_candidates`,
before `assign_missions_batch`, and is never revisited. The truncation
test aborts a run immediately after a decision and asserts its decision
rows are identical to the same run carried through to completion. A
leaked realized value could not survive that comparison.

**Attribution.** `C_static` includes the post-drop-off retreat because
the energy equation requires it, so the outcome row is written at
retirement rather than at the drop-off service; `completed_at` is still
the drop-off instant and `retreat_finished_at` records the rest.
Predicted and realized distance then agree exactly, which doubles as a
check that the router's prediction and the executor's realization agree
cell for cell.

**Raw by decision.** No discretized predicate is stored. Thresholds are
learner-side and will be tuned repeatedly; freezing one into the
dataset would mean re-running every experiment whenever it moved.
Exactness is preserved by storing a `float64` column and an exact
`"numerator/denominator"` string side by side, since Parquet has no
rational type.

---

## 12. Priority gaps before strategy research

**P0-1 — RESOLVED by S0a, 2026-10-22.** The FIFO window removed
allocation choice: 62 of 71 decisions were 1×1. With `W = fleet_size`
the same workload has 64 decisions of which 1 is forced (1.6%), and on
500 orders 409 decisions of which 1 is forced. A FIFO liveness rule
removes the matching stall, where an unaffordable head order hid the
feasible orders behind it. `order_queue.py:eligible`,
`workload_executor._candidate_window`, `sim_config.window_width`.

**S0b — RESOLVED, 2026-10-22.** The window fixed the *order* side of a
decision; the robot side is the free pool, usually 1 under continuous
dispatch, so Hungarian and MILP would still have reduced to argmin. A
dispatch quantum holds a freed robot up to T so several are allocated
together. Continuous dispatch (T=0) remains the default and the
headline configuration; `--quantum 3.0` gives the
measured T=3.0 s, under which mean robots per decision rises 1.56 → 3.03
and **all 33 decisions are choice-bearing, none forced**, at no makespan
cost. `EventType.DISPATCH_WINDOW` is the neutral timer that wakes the
clock; it is deliberately not a WAIT, so a batched robot books idle
drain rather than traffic waiting time.

**P0-2 — RESOLVED by Phase 8, 2026-10-22.** `simulation_env/dataset.py`
writes `decisions.parquet` (one row per scored candidate pair, 39
columns), `outcomes.parquet` (one row per order, 22 columns) and
`run.json`. On the shipped workload: 1973 decision rows, 100 outcome
rows, 100 positive and 1873 negative examples, makespan unchanged at
268.6667 s with recording on. No leakage is enforced structurally and
proven by a truncation test, not documented and hoped for.

**P0-3 — No experiment runner.** map × fleet × workload × strategy ×
seed, with CSV output and a manifest recording config hash and git commit.

**P0-4 — `makespan` includes recovery.** The printed value is the
engine's end time (282.0000 s) while the last dropoff is at 256.0000 s.
The `order_makespan` / `fleet_recovery_time` split is B6.

**P0-5 — RESOLVED 2026-10-23.** The lane policy was hard-coded to V2. A map may now draw its own lanes as arrows in Excel; `directional_blocked_edges` routes a drawn map by its arrows and an undrawn one by the built-in V2 policy. V2 exported as arrows is routed on the identical edge set (775 = 775). See DECISIONS.md, "Drawn Lane Maps" and "Rotaries".

**P1** — review legacy interfaces while preserving production grid dependencies; re-validate SOC when a deferred
assignment finally launches; the nearest-charger term assumes the
nearest charger is free; pin the G6 safety margin; banner docs 01–17.

---

## 13. File index

```text
src/mrta_core/mrta_core/
  battery.py              energy + eligibility + charging math (exact Fractions)
  grid.py                 GridMap, A*
  warehouse_map.py        WarehouseMap, Zone, ZoneType
  warehouse_map_xlsx.py   XLSX loader, including drawn lane arrows
  map_builder.py          make maps: blank, example, export with arrows
  map_check.py            the drawing checker (dead ends, rotaries, ...)
  warehouse_map_io.py     CSV/IO helpers
  warehouse_routing.py    lane policy, directed A*, MissionPlan, RouteCache
  orders.py               Order, load_orders_csv
  order_queue.py          FIFO eligibility
  sim_config.py           SimConfig
  world_view.py           grid ↔ world transform
  world.py                production WorldGrid used by maps and loaders
  models.py               older planning models, also imported by grid.py
  symbolic.py kinematics.py   older predicates / alternative energy model

src/task_management/task_management/
  features.py             OrderCandidate, C_static, decision-time energy
  allocation_strategy.py  BatchAllocationStrategy, NearestAStarAllocator

src/simulation_env/simulation_env/
  workload_executor.py    WorkloadExecutor — orchestration
  fleet_engine.py         the clock, events, booking
  reservations.py         ReservationTable
  local_traffic_controller.py / local_yield_selector.py
  grid_robot.py           GridRobot, GridRobotStatus
  battery_ledger.py       BatteryLedger — the one SOC record
  dataset.py              decision/outcome schemas, RunRecorder
  dataset_export.py       CSV and ground Prolog facts
  charging.py             TerminalBank, ChargeState, ChargeRecord
  stage_parking.py        StageParking
  fleet.py                build_fleet, home_stations
  fleet_node.py           ROS 2 node
  gazebo_bridge.py / gazebo_view.py

src/simulation_env/
  config/*.yaml           scientific + controlled configs
  maps/W100.xlsx (current map); test/fixtures/CSV_To_Gazebo_v2.xlsx (V2)
  worlds/*.world          generated Gazebo worlds
  scripts/                runner + every check_* gate
  launch/warehouse_sim.launch.py
```
