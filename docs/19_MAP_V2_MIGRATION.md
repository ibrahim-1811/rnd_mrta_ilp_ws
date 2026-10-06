# Map V2 migration

Record of the change from `CSV_To_Gazebo.xlsx` (V1) to
`CSV_To_Gazebo_v2.xlsx` (V2), which is now the current map.

V2 adds a charging holding bank and widens the robot home bank. The
one-way core, the directional A*, the traffic controller and Gazebo
are unchanged in behaviour; what changed is the map, the semantics
needed to read it, and one hard-coded constant that had to stop being
hard-coded.

Research boundary is unaffected. Nothing here touches allocation.
Everything below is fixed infrastructure shared identically by every
MRTA strategy.

---

## 1. What V2 changes on the map

| | V1 | V2 |
|---|---|---|
| grid | 17 × 35 | 17 × 35 |
| blocked cells | 118 | 112 |
| shelves | 72 | 72 (identical cells) |
| dropoffs | 40 | **36** |
| home bank (`Stage Zone`) | 18, y=13..21 | **22, y=12..22** |
| chargers | 16 | 16 (identical cells) |
| charge queue | — | **16** |

Two edits, in this order:

**Charging holding bank.** 16 new purple `charge queue` cells, a
two-cell column beside each charger pair:

```
x = 5, 8, 11, 14   at   y = 1, 2   and   y = 32, 33
```

**Wider home bank.** The `Stage Zone` merges grew from `B15:B23` /
`R15:R23` to `B14:B24` / `R14:R24`, so the bank spans y=12..22 on both
sides — 22 home cells, hence up to 22 robots.

### Consequence: the dropoff columns shrank

Widening the bank turned the x=2 and x=15 cells on rows 12 and 22 into
frontage lane, which is what those columns already are on every other
row of the bank. Each of the four dropoff merges lost one cell:

* `dropoff_037 .. dropoff_040` no longer exist
* **every remaining dropoff id points at a different cell**
  (`dropoff_001` moved from `(2,22)` to `(2,23)`)
* shelf ids are untouched

`orders.csv` and `orders_500.csv` were regenerated against the new map
with the same seed 42. The V1 workload numbers are therefore **not**
comparable — see §6.

---

## 2. Code: semantics

### `ZoneType.CHARGE_QUEUE`

Charging-specific rather than a generic task queue: these are the
holding cells where a robot waits for a slot, and where a preempted
robot goes when it is not energy-eligible to rejoin the fleet.

Accepted map labels, normalised case/underscore/hyphen-insensitively,
in **both** `warehouse_map_xlsx.py` and `warehouse_map_io.py`:

```
charge queue      charging queue      charge holding      charging holding
+ the same four with a trailing "zone"
```

The CSV loader was also missing `STAGE` / `STAGE ZONE`, which the XLSX
loader had always accepted. The two alias tables had silently drifted;
they now agree.

### `HOME_TYPES` — one concept, two spellings

The robot home / start / parking bank is `STAGING` on V1 and V2 maps
and may be relabelled `COLLECTION ZONE`, which is the real warehouse
term. Both are accepted:

```python
HOME_TYPES = (ZoneType.COLLECTION, ZoneType.STAGING)
```

`fleet.home_bank()` returns the bank in id order, preferring
COLLECTION when a map carries both. `staging_stations` is kept as an
alias. "Home" is the stable concept, so `home_stations()`,
`home_bank()` and the error messages are named after it rather than
after either map spelling.

`fleet.initial_position_mode` now accepts `home` as well as the legacy
`staging`.

---

## 3. Code: routing

### The frontage band now follows the map

This was the bug that broke V2.

The frontage circulation rules stop robots meeting head-on directly in
front of the parking bank. They were policed over a hard-coded
`STAGING_FRONTAGE_Y_MIN/MAX = 13/21`, written against the V1 bank.
Widening the bank to y=12..22 left the two new rows ungoverned, and
the frontage lanes at x=1, x=2 and x=15 were stranded there:

```
(1,12) (1,22)    no legal exit
(2,12) (2,22)    cut off from the core
(15,12) (15,22)  no legal entry
-> 634 unreachable station legs, strongly connected FAIL
```

New `frontage_band(warehouse)` reads the rows the home bank actually
occupies and threads them through `_directional_edge_allowed` and the
edge cache. It raises if the bank is not contiguous. The constants
remain as the fallback for a map with no home bank.

V1 derives to `(13, 21)` — identical to the old literals, so V1
behaviour is provably unchanged. Widening the bank again now costs
nothing.

### Policy resolved at call time, not def time

`non_transit_cells(warehouse, types=NON_TRANSIT_TYPES)` bound the
policy as a **default argument**, so changing the module constant did
nothing. It now resolves inside the function.

`directional_blocked_edges()` and `navigation_grid()` take an optional
`terminal_cells`, so a validator can score a candidate policy through
the production functions instead of growing a second copy of the lane
rules.

### One source of truth for terminal types

Three modules carried their own copy of `(SHELF, DROPOFF, STAGING)`.
They now import `NON_TRANSIT_TYPES`:

* `local_yield_selector.py` — a yield can no longer be selected into a
  cell A* treats as non-transit, charge-queue cells included
* `discover_directional_highways.py`
* `validate_directional_highways.py`

---

## 4. Code: tooling

### New — `scripts/check_map_topology.py`

The gate a map must pass before becoming current. Checks strong
connectivity of the transit core, uncontrolled two-way core edges, and
full station-to-station A* reachability.

It imports `non_transit_cells()`, `directional_blocked_edges()` and
`navigation_grid()`, so it validates the graph the planner actually
executes. (It carried a `--charging-apron` flag to score the
battery-era policy before production switched over; the flag went away
with the B0 correction in §5, when that policy became the live one.)

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/check_map_topology.py \
    --config src/simulation_env/config/warehouse_sim.yaml
```

### Fixed — `--robots` could only shrink the fleet

`run_workload.py` applied `--robots N` as `robots[:N]` on an
already-built fleet, so asking for more than `fleet.robot_count`
silently did nothing. It now overrides `robot_count` before
`build_fleet`, so it raises as well as lowers, and exceeding the map's
home cells fails with a clear error.

### Fixed — the traffic stress test was pinned to 18

`check_18_robot_traffic_stress.py` hard-asserted `len(robots) != 18`,
so widening the bank broke it rather than exercising it. It now
stresses **the full home bank**, whatever size the map defines — the
busiest fleet the warehouse can hold. Currently 22.

### Other

* `generate_orders.py` dropoff pool 40 → 36, with a comment tying it
  to the map.
* `generate_gazebo_grid_world.py` gained a `charge_queue` colour
  (`0.56 0.49 0.76`, the purple XLSX fill).
* `check_local_yield_selector.py`, `check_local_headon_resolution.py`
  and `check_raw_mission_cycle.py` no longer pin the V1 map path or
  the literal `staging_001`; they follow the config.

---

## 5. Done — the charging apron (Battery V1, increment B0)

Chargers and charge-queue cells are dedicated service resources:
reachable as leg endpoints, never transit.
`CHARGING_APRON_NON_TRANSIT` in `warehouse_routing.py` is `True` since
B0 (2026-09-13).

**What was wrong.** The apron was authored two cells deep against the
wall: rows y=1/y=2 and y=32/y=33, chargers on the shelf columns
(x=4,7,10,13), queue cells on the southbound trunks (x=5,8,11,14),
plain floor only on the northbound trunks (x=6,9,12). It worked only
because those rows were one-way lanes running *through* the chargers.
With chargers and queue cells out of transit the leftover floor is
one-cell stubs, and a one-way stub with a single way in is either a
source or a sink. Measured through the production routing functions,
16 of the 32 cells could not be both entered and left:

| class | cells |
|---|---|
| isolated, no aisle neighbour | charger (4,1), charger (4,33), holding (14,1), holding (14,33) |
| enter-only traps | (5,33) (7,33) (8,33) (10,33) (11,33) (13,33) |
| exit-only, unreachable | (5,1) (7,1) (8,1) (10,1) (11,1) (13,1) |

The fix this section used to propose — relabel the twelve x=6,9,12
stub cells as `charge queue` — was **wrong**. It makes the transit
core pass at 309/309, which is what was measured, but seals off the
whole outer row: 22 terminals unreachable, 7066 unreachable legs.
A two-row apron against a wall can only serve one row of terminals.

**The correction.** The bank is now one cell deep, facing the aisle,
like every other station bank:

| XLSX edit | cells |
|---|---|
| unmerge the eight 1×2 `Charging` regions | F3:F4 I3:I4 L3:L4 O3:O4 F34:F35 I34:I35 L34:L35 O34:O35 |
| keep as `Charging` | F4 I4 L4 O4 F34 I34 L34 O34 |
| outer rows y=33 and y=1 become wall (red) | F3:P3 and F35:P35 |
| former stubs become `charge queue` | H4 K4 N4 H34 K34 N34 |

Each bank row reads `C Q Q C Q Q C Q Q C Q` (x=4..14): 8 chargers and
14 holding cells in total. Only charger and queue ids renumbered;
shelf, dropoff and Stage Zone ids are unchanged, so the order files
still load. The lane policy also forbids every terminal -> terminal
edge, so a leg between two neighbouring bank cells goes out through the
aisle instead of hopping across, exactly as the RMF graph does.

After the correction: 309 transit cells, 485 directed edges, strongly
connected, every terminal enterable and leavable, 0 of 22,952 station
legs unreachable, through a terminal, or off the nav graph. The 100-
and 500-order workloads are bit-identical to before — no route ever
used the apron. Current gate numbers live in
[00_RUNBOOK.md §2.7a](00_RUNBOOK.md).

---

## 6. Baseline

**Current — V2, 22 robots, `orders.csv` seed 42:**

| Measure | Value |
|---|---|
| orders completed | 100/100 |
| robots returned home | 22/22 |
| makespan | 266.0000 s |
| total distance | 6426.0 m |
| total waiting | 266.0000 s |
| total service | 600.0000 s |
| reservation violations | 0 |
| transit core cells | 353 |
| directed core edges | 569 |
| uncontrolled bidirectional core edges | 0 |
| RMF nav graph | 483 vertices, 1245 lanes |

500-order set, same fleet: 500/500, 22/22 home, makespan 1117.3333 s,
distance 29388.0 m, 0 violations.

**Superseded — V1, 18 robots:** 100/100, 18/18 home, makespan
315.3333 s, distance 6282.0 m, 347 transit cells / 559 edges.

Not comparable: different station layout, renumbered dropoffs,
regenerated workload, larger fleet. V1 itself still passes every gate
unchanged — it is a regression check, not a live scenario.

---

## 7. Verification

All green at the time of writing.

```bash
export PY=.pixi/envs/default/bin/python
export PP=src/mrta_core:src/simulation_env:src/task_management:/usr/lib/python3/dist-packages
CFG=src/simulation_env/config/warehouse_sim.yaml

PYTHONPATH=$PP $PY src/simulation_env/scripts/check_map_topology.py --config $CFG
PYTHONPATH=$PP $PY src/simulation_env/scripts/check_map_topology.py \
    --map src/simulation_env/maps/CSV_To_Gazebo.xlsx          # V1 regression
PYTHONPATH=$PP $PY src/simulation_env/scripts/validate_directional_highways.py
PYTHONPATH=$PP $PY src/rmf_grid_traffic/scripts/verify_nav_graph.py \
    --config $CFG --graph src/rmf_grid_traffic/config/warehouse_nav_graph.yaml
PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py --progress 0

for s in check_local_traffic_controller check_local_yield_selector \
         check_local_headon_resolution check_batch_reservations \
         check_engine_batch_traffic check_engine_headon_yield \
         check_raw_mission_cycle check_18_robot_traffic_stress; do
  PYTHONPATH=$PP $PY src/simulation_env/scripts/$s.py > /dev/null \
      && echo "$s PASS" || echo "$s FAIL"
done

PYTHONPATH=$PP $PY -m pytest src/mrta_core/tests src/simulation_env/test -q
.pixi/envs/default/bin/ruff check src
```

| Gate | Result |
|---|---|
| `check_map_topology` (V2) | PASS — 353/353, 0 unreachable |
| `check_map_topology` (V1 regression) | PASS — 347/347, unchanged |
| `check_map_topology --charging-apron` | FAIL — expected, see §5 |
| `validate_directional_highways` | PASS — 0 uncontrolled bidi edges |
| `verify_nav_graph` | PASS — 0 illegal, 0 missing, 0 two-way core |
| 8 traffic behaviour checks | PASS |
| `pytest` | PASS — 69 passed |
| `ruff check src` | PASS |

Known pre-existing failures, unrelated to this work and unchanged by
it: 3 tests in `src/task_management/test/test_nearest_astar.py`.

---

## 8. Regenerating after a map edit

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/check_map_topology.py --config $CFG

PYTHONPATH=$PP $PY src/rmf_grid_traffic/scripts/generate_nav_graph.py \
    --config $CFG --output src/rmf_grid_traffic/config/warehouse_nav_graph.yaml

PYTHONPATH=$PP $PY src/simulation_env/scripts/generate_gazebo_grid_world.py --config $CFG
PYTHONPATH=$PP $PY src/simulation_env/scripts/generate_gazebo_grid_world.py \
    --config src/simulation_env/config/warehouse_sim_10.yaml

# only if the station counts changed
PYTHONPATH=$PP $PY src/simulation_env/scripts/generate_orders.py
PYTHONPATH=$PP $PY src/simulation_env/scripts/generate_orders.py \
    --orders 500 --output src/simulation_env/config/orders_500.csv

colcon build --packages-select simulation_env rmf_grid_traffic
```

The `colcon build` is not optional. `ros2 launch` reads configs, maps
and worlds from `install/share`, so a map or config edit that is not
rebuilt renders the previous scenario.

---

## 9. Changed files

**Map and semantics**
* `src/simulation_env/maps/CSV_To_Gazebo_v2.xlsx` — new
* `src/mrta_core/mrta_core/warehouse_map.py` — `ZoneType.CHARGE_QUEUE`
* `src/mrta_core/mrta_core/warehouse_map_xlsx.py` — aliases, prefix
* `src/mrta_core/mrta_core/warehouse_map_io.py` — aliases, `STAGE`

**Routing**
* `src/mrta_core/mrta_core/warehouse_routing.py` — `HOME_TYPES`,
  `frontage_band()`, `non_transit_types()`,
  `CHARGING_APRON_NON_TRANSIT`, call-time policy resolution

**Fleet**
* `src/simulation_env/simulation_env/fleet.py` — `home_bank()`
* `src/simulation_env/simulation_env/fleet_node.py` — home checks
* `src/simulation_env/simulation_env/local_yield_selector.py`

**Tooling**
* `src/simulation_env/scripts/check_map_topology.py` — new
* `run_workload.py`, `check_18_robot_traffic_stress.py`,
  `generate_orders.py`, `generate_gazebo_grid_world.py`,
  `build_scenario.py`, `discover_directional_highways.py`,
  `validate_directional_highways.py`,
  `check_local_yield_selector.py`,
  `check_local_headon_resolution.py`,
  `check_raw_mission_cycle.py`

**Generated artefacts**
* `src/rmf_grid_traffic/config/warehouse_nav_graph.yaml`
* `src/simulation_env/worlds/warehouse_grid.world`
* `src/simulation_env/worlds/warehouse_10.world`
* `src/simulation_env/config/orders.csv`, `orders_500.csv`

**Config and docs**
* `warehouse_sim.yaml`, `warehouse_sim_10.yaml`,
  `warehouse_sim_500.yaml` — map → V2, `robot_count` → 22
* `docs/00_RUNBOOK.md` — §2.7a, §7 baseline, gates
* `docs/19_MAP_V2_MIGRATION.md` — this file
