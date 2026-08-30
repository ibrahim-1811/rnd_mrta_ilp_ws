# 04 — ROS 2 packages

Five packages, all building cleanly. Parameter defaults below were read from source,
not from documentation.

---

## `robot_interfaces` (ament_cmake)

Interface-only package. Generates 9 messages, 7 services, 2 actions
(`src/robot_interfaces/CMakeLists.txt`). Dependency: `geometry_msgs`.

**Status: COMPLETE**, with dead weight — `ProcessOrder.action`, `TaskList.action`,
`GoalStartMsg.msg` and `GoalReachMsg.msg` have no implementer or user anywhere in the
workspace.

Tests: 2 lint tests (`lint_cmake`, `xmllint`). `ament_copyright` and `ament_cpplint`
are explicitly disabled in `CMakeLists.txt`.

---

## `mrta_core` (ament_python)

Pure-Python engine. **No `rclpy` dependency** — importable and testable without ROS.
No console scripts.

| Module | Contents |
|---|---|
| `grid.py` | `GridMap`, `PathResult`, `PathFeatures`, `astar_path`, `extract_path_features`, `manhattan_distance`, `reconstruct_path` |
| `world.py` | `WorldGrid` (metre↔cell), `load_ascii_obstacles`, `world_grid_from_ascii` |
| `kinematics.py` | `RobotModel`, `path_time`, `path_energy_j/wh/percent` |
| `models.py` | `RobotState`, `TaskState`, `RobotStatus` enum |
| `symbolic.py` | Prolog predicate generation — **no caller in the workspace** |

Tests: 27, all passing.

**Status: COMPLETE** as a library. Two caveats:

- `extract_path_features()` (`grid.py:148`) computes only the **robot→pickup** leg and
  uses a naive `energy_per_cell=0.01` constant. The production pipeline does **not**
  use it — `task_management/features.py` implements its own two-leg version with the
  physics model. `extract_path_features` is used only by tests and by `symbolic.py`'s
  test fixtures. It is effectively a second, inferior code path.
- `TaskState.deadline` and `TaskState.release_time` exist but the task manager never
  constructs a `TaskState`; it uses plain dicts.

---

## `shared_memory_node` (ament_python)

Warehouse database. Executable: `shared_memory_node`.

| Parameter | Default | Meaning |
|---|---|---|
| `database_file` | `<share>/databases/headless_shelves_100.csv` | shelf/drop CSV |

CSV format: `id,X,Y,Z,Q_W,Q_X,Q_Y,Q_Z,product,capacity,inventory`. **The first data row
(`id=0`) is the drop-off pose**; every later row is a shelf.

Available layouts:

| File | Shelves | X range (m) | Y range (m) | Fits default 30 m world? |
|---|---:|---|---|---|
| `headless_shelves_100.csv` | 100 | −13.5 … 13.5 | 0.0 … 29.5 | ✅ yes |
| `headless_shelves_100_large.csv` | 100 | −45.0 … 45.0 | 0.0 … 95.0 | ❌ **no** |
| `headless_shelves_stress.csv` | 16 | −12.0 … 12.0 | 0.0 … 24.0 | ✅ yes |
| `shelves_database.csv` | 7 | −1.2 … 1.2 | 0.0 … 4.6 | ✅ (Gazebo-era, tiny) |

> **Trap:** `WorldGrid.to_cell()` (`world.py:56-61`) **silently clamps** out-of-range
> coordinates to the grid boundary. Loading `headless_shelves_100_large.csv` at the
> default `world_size_m:=30.0` therefore collapses every far shelf onto the grid edge
> with no warning. Raise `world_size_m` to at least 95 for that layout.

Tests: **0**. Status: **FUNCTIONAL BUT UNTESTED**.

---

## `simulation_env` (ament_python)

Headless grid simulator. Executable: `mock_fleet_manager` (node name `fleet_manager`).
Launch: `sim_only.launch.py`.

| Parameter | Default | Used? | Meaning |
|---|---|---|---|
| `num_robots` | 4 | ✅ | fleet size |
| `world_size_m` | 30.0 | ✅ | square world side (m) |
| `resolution_m` | 1.0 | ✅ | metres per cell |
| `robot_speed_ms` | 2.0 | ✅ | `v_max` of the physics model |
| `time_scale` | 1.0 | ✅ | wall-clock speed-up factor |
| `congestion_weight` | 1.0 | ✅ | A\* penalty per unit congestion |
| `grid_map_file` | `""` | ✅ | ASCII obstacle map — **but see below** |
| `battery_low_threshold` | 20.0 | ✅ | enter charging at/below (%) |
| `battery_resume_threshold` | 80.0 | ✅ | return to service at/above (%) |
| `battery_charge_rate_per_s` | 5.0 | ✅ | recharge rate (%/s) |
| `battery_charge_tick_s` | 1.0 | ✅ | recharge timer period |
| `initial_battery` | 100.0 | ✅ | starting charge (%) |
| `battery_drain_per_m` | 0.7 | ❌ **DEAD** | declared and read into `self.drain_per_m`, then never used — superseded by the physics energy model. It is a vestige of the previous workspace, whose ICAPS results cite exactly this "0.7 %/m" figure. |

> **`grid_map_file` cannot be set through either launch file.** Neither
> `sim_only.launch.py` nor `headless_warehouse.launch.py` declares or forwards it, and
> **no ASCII map files ship with the repository** — `setup.py` globs `maps/*` but no
> `maps/` directory exists. Obstacles are therefore reachable only by running
> `mock_fleet_manager` directly with a hand-written map. In every launch-based run the
> grid is **completely empty**. See [07_GRID_AND_PATH_COST.md](07_GRID_AND_PATH_COST.md).

Robots spawn on the free cells nearest the world origin (`_spawn_cells`,
`mock_fleet_manager.py:347`), i.e. clustered around the drop-off. Each task runs in its
own daemon thread; the node uses a `MultiThreadedExecutor`.

Tests: **0**. Status: **FUNCTIONAL BUT UNTESTED**.

---

## `task_management` (ament_python)

The MRTA brain. Four executables and one launch file.

### `task_manager_node`

| Parameter | Default | Meaning |
|---|---|---|
| `allocation_strategy` | `auction` | one of the 13 registered names |
| `allocator_seed` | 0 | RNG seed (only `random` uses it) |
| `world_size_m` | 30.0 | **must match the simulator** |
| `resolution_m` | 1.0 | **must match the simulator** |
| `robot_speed_ms` | 2.0 | **must match the simulator** |
| `min_battery_after` | 20.0 | battery feasibility floor `b_min` (%) |

An unknown strategy name raises `KeyError` and the node **exits with status 1** before
spinning. Verified:

```
KeyError: "unknown strategy 'hungarian'; known: ['auction', 'bid', 'edf', 'greedy',
'heuristic', 'highest_battery', 'least_busy', 'lpt', 'nearest', 'random',
'round_robin', 'score', 'spt']"
```

This is a clean fail-fast, but note it happens *inside a launch file* where the other
four nodes keep running — you get a silently order-less system rather than an obvious
crash. See [15_TROUBLESHOOTING.md](15_TROUBLESHOOTING.md).

> **Hard-coded task attributes** (`task_manager.py:150-157`): every task is created
> with `'priority': 1` and `'deadline': now + 300.0`. These are literals, not
> parameters. Consequences are analysed in
> [05_MRTA_ALGORITHMS.md](05_MRTA_ALGORITHMS.md).

### `order_generator`

| Parameter | Default | Meaning |
|---|---|---|
| `num_orders` | 20 | total orders to publish |
| `rate_hz` | 1.0 | orders per second (period floored at 0.05 s) |
| `seed` | 42 | RNG seed for shelf choice |

Each order contains exactly one product, quantity 1, from a uniformly random shelf.
Order arrival is a **fixed-rate timer**, not a stochastic (e.g. Poisson) process.

### `ilp_dataset_logger`

| Parameter | Default |
|---|---|
| `out_dir` | `/tmp/mrta_run/ilp_logs` |

One CSV row per candidate robot per decision, 21 columns. Flushed after every event.

### `metrics_logger`

| Parameter | Default |
|---|---|
| `out_dir` | `/tmp/mrta_run` |

Rewrites a **single-row** summary CSV after every event (whole file overwritten each
time, `metrics_logger.py:83`).

Tests: 16 (strategies only — no node is tested). Status: **FUNCTIONAL**, with the
algorithmic caveats in [05_MRTA_ALGORITHMS.md](05_MRTA_ALGORITHMS.md).
