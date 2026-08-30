# 07 — Grid model and path cost

Sources: `src/mrta_core/mrta_core/world.py`, `grid.py`, `kinematics.py`, and
`src/task_management/task_management/features.py`.

## Grid representation

`WorldGrid` (`world.py:15-86`) is a frozen dataclass and the single source of truth for
the metre↔cell mapping. Both the simulator and the task manager construct it from the
same three parameters (`world_size_m`, `resolution_m`, `robot_speed_ms`).

```python
WorldGrid.square(world_size, resolution)
  -> from_bounds(-world_size/2, 0.0, world_size/2, world_size, resolution)
```

The world is centred on `x = 0` with `y` starting at 0 — the drop-off convention, since
the drop-off sits at the origin `(0, 0)`.

Grid dimensions: `width = ceil((x_max−x_min)/res) + 1`, likewise for height. The default
30 m world at 1 m resolution therefore gives a **31 × 31 grid** (961 cells), confirmed by
the simulator's own startup log.

### Coordinate conversion

```python
to_cell(x, y):                                  # world.py:56
    cx = round((x - origin_x) / resolution)
    cy = round((y - origin_y) / resolution)
    cx = min(max(cx, 0), width - 1)             # ← silent clamp
    cy = min(max(cy, 0), height - 1)            # ← silent clamp

to_world(cell):                                 # world.py:63
    return (origin_x + cx*resolution, origin_y + cy*resolution)
```

> ⚠ **The clamp is silent.** Any pose outside the world is snapped to the boundary with
> no warning and no error. This makes `headless_shelves_100_large.csv` (X to ±45 m,
> Y to 95 m) silently unusable at the default 30 m world size — dozens of distinct
> shelves collapse onto the same edge cells. See
> [15_TROUBLESHOOTING.md](15_TROUBLESHOOTING.md).

The conversion is lossy by design (`round` to the nearest cell): at 1 m resolution all
sub-metre shelf placement detail is discarded.

## Obstacles

`GridMap.obstacles` is a `frozenset[Cell]`; `is_free()` rejects out-of-bounds cells and
obstacle cells, and `neighbors()` returns only free 4-connected neighbours.

Obstacles can be loaded from an ASCII map (`load_ascii_obstacles`, `world.py:89`):
`#` = obstacle, anything else free, **first text line is the top row (highest y)**.

> ### ⚠ In practice the grid is always empty
>
> - **No ASCII map files ship with the repository.** `simulation_env/setup.py` globs
>   `maps/*`, but no `maps/` directory exists.
> - **Neither launch file exposes `grid_map_file`.** `headless_warehouse.launch.py` and
>   `sim_only.launch.py` declare no such argument and forward no such parameter.
> - The task manager **never loads obstacles at all**: `task_manager.py:61` calls
>   `WorldGrid.square(world_size, resolution)` with no obstacle argument. Even if the
>   simulator were given a map, **the allocator's world would still be empty** — the two
>   would silently disagree about which cells are traversable.
>
> Obstacle support is implemented and unit-tested in `mrta_core`, but it is **not
> reachable through the supported run paths**, and the allocator side is not wired at
> all. Every measurement in this repository was taken on an obstacle-free grid.

## A\* implementation

`astar_path` (`grid.py:63-124`): 4-connected, binary heap frontier, tie-broken by an
insertion counter for deterministic ordering, early exit when the goal is popped.

**Step cost** (`grid.py:93`):

```python
step_cost = 1.0 + congestion_weight * grid.congestion_at(next_cell)
```

**Heuristic** (`grid.py:98`): Manhattan distance, unweighted. Since every step costs
≥ 1.0 and congestion is non-negative, the heuristic is admissible and consistent, so
A\* returns an optimal path under this cost. ✅ Correct.

**Returned values** (`grid.py:113-124`):

```python
distance        = len(path) - 1                                   # cells, unweighted
congestion_cost = sum(congestion_at(c) for c in path[1:])          # excludes start
total_cost      = distance + congestion_weight * congestion_cost
```

Note `distance` is the **raw hop count**, not the congestion-weighted cost — so a
detour around congestion shows up as a *longer* `distance`, which is what the
allocators consume.

Unreachable start/goal, or a goal never expanded, returns
`PathResult(found=False, distance=inf, …)`.

## Is `c(e,k) = α·distance + β·obstacle + γ·congestion` implemented?

**No — not in that form.** What exists is a **two-level** cost, and there is no `β`
obstacle term at all.

**Level 1 — per-edge, inside A\*:**

```
step_cost(e) = 1.0 + congestion_weight · congestion(target_cell)
```

Only **two** terms, with the distance coefficient fixed at exactly 1.0 (not a tunable
`α`). Obstacles are **not a cost term**: they are a hard constraint — an obstacle cell
is simply removed from `neighbors()`, so it has infinite cost, not a `β` penalty. There
is no soft obstacle cost anywhere.

**Level 2 — per robot–task pair, in `features.py`:**

```
C_ij = λ1·T_ij + λ2·D_ij + λ3·G_ij + λ4·E_ij + λ5·W_i − λ6·q_j
```

This is where distance, time, congestion, energy, workload and priority are combined —
but at the *allocation* level, not the *edge* level, and it is used only by `score`,
`bid` and `auction`.

So the closest thing to the proposed formulation exists, with these differences: the
distance coefficient is not configurable; the obstacle term does not exist; congestion
appears at both levels (path shape *and* allocation cost); and energy, time, workload
and priority are additional terms the proposal's edge cost does not have.

## Complete weight inventory

| Weight | Value | Where | Configurable? |
|---|---:|---|---|
| A\* distance coefficient | `1.0` | `grid.py:93`, literal | ❌ hard-coded |
| `congestion_weight` (A\*) | `1.0` | `mock_fleet_manager` param | ✅ ROS param, **but not exposed in either launch file** |
| `congestion_weight` (allocator) | `1.0` | `grid.py:67` default | ❌ `features.py:90,99` never passes it |
| λ1 time | `0.05` | `CostWeights` | ❌ hard-coded |
| λ2 distance | `0.03` | `CostWeights` | ❌ hard-coded |
| λ3 congestion | `1.0` | `CostWeights` | ❌ hard-coded |
| λ4 energy | `0.10` | `CostWeights` | ❌ hard-coded |
| λ5 workload | `1.0` | `CostWeights` | ❌ hard-coded (and inert) |
| λ6 priority | `1.0` | `CostWeights` | ❌ hard-coded (and inert) |
| `min_battery_after` | `20.0` | task_manager param | ✅ ROS param, not in launch |
| `energy_per_cell` | `0.01` | `grid.py:153` | ❌ hard-coded (**dead path** — `extract_path_features` is unused in production) |

> **Every cost weight that matters for the research is hard-coded.** Running a
> weight-sensitivity or ablation study today requires editing `features.py` and
> rebuilding. This is a P0 blocker — see [17_RESEARCH_GAPS.md](17_RESEARCH_GAPS.md).
>
> Note also the **asymmetry**: the simulator's `congestion_weight` is a parameter, but
> the allocator's A\* calls always use the default 1.0. Changing the simulator's value
> makes planner and allocator disagree.

## Congestion model

**Implemented** — but far weaker than the term suggests.

Mechanism (`mock_fleet_manager.py:155-169`):

1. The simulator keeps `occupancy: dict[cell, int]`, incremented/decremented as each
   robot moves (`_occ_inc` / `_occ_dec` under a lock).
2. Every 1 s, `_publish_congestion` publishes the snapshot as JSON on
   `/simulation/congestion`.
3. `task_manager._on_congestion` parses it into `{(x,y): float}`.
4. `build_candidates` passes it to `world_grid.grid_map(congestion)`, so A\* prices
   congested cells.

What this means in practice:

- Congestion is **instantaneous occupancy of robots' current cells only**. A cell's
  value is the number of robots standing on it *right now* — typically 0 or 1.
- **No path reservations.** A robot's *planned route* contributes nothing. Two robots
  can plan straight through each other with no penalty.
- **No temporal reasoning.** There is no notion of *when* a cell will be occupied — no
  time-indexed occupancy, no space-time A\*.
- **No collision avoidance.** Multiple robots may occupy the same cell simultaneously;
  `occupancy` merely counts them. Robots pass through one another.
- **No waiting cost, no re-planning in motion.** A\* runs once per leg at dispatch; the
  path is then followed blindly cell by cell regardless of what other robots do.
- **Stale by up to 1 s** at the allocator, since congestion arrives at 1 Hz while
  decisions run at 5 Hz.

Measured congestion values in logged runs are **0.0 – 3.0**, and are dominated by robots
clustered near the drop-off at spawn rather than by genuine route contention.

With λ3 = 1.0, a congestion value of 1.0 adds 1.0 to `C_ij` — comparable to the entire
distance term for a 33 m path (λ2·D = 0.03 × 33 ≈ 1.0). So congestion is *not* negligible
in the cost function; it is simply not measuring route conflict.

**Assessment:** this is an occupancy-penalty heuristic, not a congestion model. For a
thesis claiming grid-aware congestion-sensitive allocation it is the weakest link in
the chain — see [17_RESEARCH_GAPS.md](17_RESEARCH_GAPS.md) (P1).

## Physics: time and energy

`RobotModel` (`kinematics.py:31-73`), a frozen dataclass. A path is split into straight
runs joined by in-place 90° turns (`_runs_and_turns`, `kinematics.py:76`).

**Defaults:**

| Parameter | Default | Unit |
|---|---:|---|
| `v_max` | 2.0 | m/s (overridden by `robot_speed_ms`) |
| `a_max` | 0.8 | m/s² |
| `omega_max` | 2.84 | rad/s |
| `mass` | 60.0 | kg |
| `payload` | 20.0 | kg |
| `rolling_resistance` | 0.08 | – |
| `idle_power` | 30.0 | W |
| `turn_power` | 40.0 | W |
| `drivetrain_efficiency` | 0.75 | – |
| `battery_capacity_wh` | 10.0 | Wh |

**Time** — trapezoidal velocity profile per straight run, starting and ending at rest:

```
d_acc = v²/(2a)
if 2·d_acc ≤ L:   t = 2v/a + (L − 2·d_acc)/v      # reaches cruise
else:             t = 2·sqrt(a·L)/a                # triangular profile
turn:             t = (π/2)/ω_max
```

**Energy** — rolling resistance plus idle draw:

```
F_roll   = μ·(mass + payload)·g                     # g = 9.81
E_run    = F_roll·L / η  +  idle_power · t_run      # joules
E_turn   = (turn_power + idle_power) · t_turn
E_%      = 100 · (ΣE / 3600) / battery_capacity_wh
```

The 10 Wh capacity is deliberately small so a single task drains a few percent, keeping
battery a discriminating feature. Measured: **4.6 – 22 %** per task on the default map —
which is realistic in effect if not in absolute terms (a real warehouse AMR carries
hundreds of Wh).

### The one genuinely strong property

`mock_fleet_manager._simulate_navigation` (`mock_fleet_manager.py:249`) drains battery
using **`path_energy_percent` — the same function, on the same path, with the same
model** that `features.py:107` used to score the candidate.

Predicted cost and executed cost therefore agree by construction rather than by
calibration. This is the most defensible engineering property in the repository and is
worth protecting through any future refactor.

Note one small artefact: leg energy is computed for the whole path, then **spread evenly
across its cells** (`per_cell_energy = leg_energy / steps`). Per-cell drain is therefore
uniform rather than reflecting where acceleration and turns actually occur. The total is
exact; only the intermediate trajectory is smoothed.
