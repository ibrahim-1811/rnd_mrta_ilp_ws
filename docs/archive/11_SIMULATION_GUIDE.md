# 11 — Simulation guide

Source: `src/simulation_env/simulation_env/mock_fleet_manager.py` (378 lines).

## What the simulator is

A headless, thread-based grid simulator that replaces Gazebo + Nav2 + a real fleet
manager. It keeps the same node name (`fleet_manager`) and the same `task_list` service
contract as the physical stack, so nothing downstream knows the difference.

It is **not** a physics simulator: there is no collision checking, no dynamics
integration, and no sensor model. Robots teleport between adjacent cells on a timer.

## Lifecycle of one task

1. `task_manager` calls the `/task_list` service with two `Task` waypoints (pickup at
   the shelf pose, dropoff at the drop-off pose).
2. `_handle_task_list` (`mock_fleet_manager.py:174`) validates the robot is available,
   marks it `busy`, captures a `generation` counter, and spawns a **daemon thread**.
3. `_simulate_navigation` (`:228`) processes each waypoint:
   - convert the world pose to a cell (`to_cell`),
   - snapshot current occupancy as congestion,
   - run A\* from the robot's current cell to the goal,
   - compute leg energy with `path_energy_percent` and divide it evenly per cell,
   - walk the path cell by cell, sleeping `step_time` per cell and updating occupancy,
     position and battery under the robot's lock.
4. On completion, the robot returns to `idle` — or enters `charging` if battery has
   fallen to `battery_low_threshold`.
5. A single JSON message is published on `/fleet_travel_stats` **per task** (not per
   leg), carrying `robot_id`, `robot_name`, `travel_dist`, `battery_consumed`.

### Time scaling

```python
step_time = resolution / speed / time_scale
```

At the defaults (1 m cells, 2 m/s, `time_scale:=20.0`) that is 25 ms of wall clock per
cell. `time_scale` is a pure wall-clock accelerator — it does not change distances,
energies or path choices.

**There is no simulation clock.** Everything runs on `time.sleep` against real time,
and all timestamps come from `get_clock().now()`, i.e. wall clock. This is the root
cause of the reproducibility limitation in [12_EXPERIMENTS.md](12_EXPERIMENTS.md).

### Concurrency

Each in-flight task owns a thread; the node spins a `MultiThreadedExecutor`. Shared
state is protected by two locks: `occ_lock` for the occupancy dict and a per-robot
`task_lock`. The `generation` counter lets `/reset_simulation` abandon in-flight threads
safely — a stale thread notices `robot.generation != generation` and returns.

✅ Concurrent execution verified: 8 robots executing simultaneously across 40 orders.

## Robot spawning

`_spawn_cells` (`:347`) enumerates all free cells, sorts them by Manhattan distance from
the world origin (the drop-off), and takes the first `n`. Robots therefore **always
start clustered around the drop-off**, deterministically, with no randomisation.

Two consequences:

- Initial congestion is concentrated at the drop-off — the congestion values seen in
  logs (0–3) mostly reflect this cluster, not route contention.
- There is **no way to randomise starting positions**, which removes an obvious source
  of scenario variation for experiments.

## Battery

| Behaviour | Detail |
|---|---|
| Initial charge | `initial_battery` (default 100 %) |
| Drain | physics model — `path_energy_percent` on the actual path |
| Low threshold | at/below `battery_low_threshold` (20 %) → status `charging`, unavailable |
| Recharge | `battery_charge_rate_per_s × battery_charge_tick_s` per tick (default 5 %/s) |
| Resume | at/above `battery_resume_threshold` (80 %) → `idle`, available |
| Floor | battery clamps at 0.0, never negative |

Measured drain is 4.6–22 % per task on the default map, so a robot completes roughly
5–15 tasks before charging. Recharge from 20 % to 80 % takes ~12 s of wall clock and is
**not** affected by `time_scale` — at `time_scale:=20.0` charging is effectively 20×
slower relative to driving. Bear this in mind when interpreting battery-stress runs.

> `battery_drain_per_m` (default 0.7) is declared and read but **never used**. It is a
> leftover from the previous workspace's linear drain model.

## Congestion

Occupancy of robots' **current cells only**, republished at 1 Hz. There are no path
reservations, no time-indexed occupancy, no collision avoidance, and no re-planning
during motion — robots pass through each other freely. Full analysis in
[07_GRID_AND_PATH_COST.md](07_GRID_AND_PATH_COST.md).

## Obstacles

`grid_map_file` accepts an ASCII map (`#` = obstacle). However:

- no map files ship with the repository,
- neither launch file exposes the parameter,
- and the **task manager never loads obstacles at all**, so allocator and simulator
  would disagree about the world even if you supplied one.

**Every documented run path uses a completely empty grid.**

## Reset support

`/reset_simulation` (`std_srvs/Trigger`) restores every robot to its initial cell and
battery, clears occupancy, and bumps `generation` to abandon in-flight threads.
`shared_memory_node` offers the matching `/reset_shared_memory` to reload inventory.

Together these are the building blocks for multi-trial batch experiments **within one
process** — but no script or node calls either. `run_experiment.sh` restarts the whole
stack per run instead. See [12_EXPERIMENTS.md](12_EXPERIMENTS.md).

## Fidelity limitations

Worth stating plainly for the thesis write-up:

| Aspect | Reality in this simulator |
|---|---|
| Kinematics | none — cell teleport at a fixed tick |
| Collisions | none — robots overlap freely |
| Turning cost | modelled in *energy and time estimates*, but motion is still uniform per cell |
| Localisation error | none |
| Pickup/drop duration | **zero** — no service time at shelf or drop-off |
| Payload effect | constant 20 kg assumed for the whole path, including the empty outbound leg |
| Robot failure | `RobotStatus.FAILED` exists in the model but is **never set** by the simulator |
| Shelf capacity | inventory decremented, but never blocks a task |

The pickup/drop service-time omission matters most for makespan realism: task duration
is pure travel time, so a strategy that minimises distance is automatically optimal for
makespan in a way that would not hold with realistic handling times.
