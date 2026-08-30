# 15 — Troubleshooting

Known failure modes and traps, all confirmed during the audit.

## Build and environment

### `ros2: command not found` / packages not found
Source both overlays in **every** terminal:
```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash
```

### Code edits have no effect
All packages are `ament_python` installed **by copy**, not symlink. Rebuild after every
edit:
```bash
colcon build --packages-select task_management && source install/setup.bash
```
Or build once with `--symlink-install` for live editing.

### `ModuleNotFoundError: No module named 'task_management'` when running pytest directly
`task_management`'s tests import from the source tree:
```bash
PYTHONPATH=src/task_management:$PYTHONPATH python3 -m pytest src/task_management/test/ -q
```

---

## Runtime

### Nothing happens — no `[assign]` lines
`task_manager._ready()` requires **all three** of: drop-off pose, shelf list, and at
least one `/fleet_status` message. Check each:
```bash
ros2 service call /get_drop_off_pose robot_interfaces/srv/GetPose
ros2 topic echo /fleet_status --once
ros2 node list          # expect fleet_manager, shared_memory_node, task_manager_node, both loggers
```
A missing shelf list usually means `shared_memory_node` failed to parse its CSV — it
reports the error on `/central_logs`, **not** stdout:
```bash
ros2 topic echo /central_logs
```

### `task_manager_node` died but the launch kept running
An invalid strategy name raises `KeyError` in the constructor and the node exits 1,
while the other four nodes continue. You get a live stack that never assigns anything.
Check `launch.log` for the traceback and use one of the 13 valid names.

### Orders are published but never become tasks
`_on_order` skips any product whose `shelf_id` is not in the loaded shelf dict
(`task_manager.py:147-148`) — silently, with no log line. This happens when the order
generator and the task manager see different shelf databases. Both must use the same
`shelves_csv`.

### A run hangs / `n_completed` never reaches the order count
`run_experiment.sh` polls for 90 s and then **prints results and exits 0 anyway**. A
partial run looks like a successful one. Always confirm `n_completed` equals your order
count before using the numbers.

Common causes: a leftover `fleet_manager` from a previous run stealing `/task_list`
calls; all robots simultaneously charging; or `time_scale` set too low for the order
count.

### Stale processes corrupt the next run
```bash
pkill -f "mock_fleet_manager|shared_memory_node|task_manager_node|ilp_dataset_logger|metrics_logger|order_generator"
pgrep -af "mock_fleet_manager|task_manager_node"   # expect nothing
```
`run_experiment.sh` does this automatically; manual launches do not.

---

## Silent traps

These produce no error and wrong results. They are the dangerous ones.

### A literal `~` directory appears
ROS launch does not expand `~`. `out_dir:=~/results` creates a directory named `~` in
your CWD. **Always pass an absolute `out_dir`.**

### Shelves collapse onto the grid edge
`WorldGrid.to_cell` **silently clamps** out-of-range coordinates
(`world.py:59-60`). Using `headless_shelves_100_large.csv` (X ±45 m, Y to 95 m) at the
default `world_size_m:=30.0` snaps dozens of distinct shelves onto the same boundary
cells, with no warning.

Symptom: many tasks with suspiciously similar distances, clustered at the map edge.
Fix: `world_size_m:=95.0` — and pass the same value to the task manager.

### Allocator and simulator disagree about the world
`world_size_m`, `resolution_m` and `robot_speed_ms` are set **independently** on the
simulator and the task manager. `headless_warehouse.launch.py` keeps them in sync via
its `common` parameter list, but running the nodes separately does not. Mismatched
values mean the allocator scores a different grid than the robots drive.

### Obstacles appear to do nothing
Even if you pass `grid_map_file` to `mock_fleet_manager` directly, the **task manager
never loads obstacles at all** (`task_manager.py:61` calls `WorldGrid.square()` with no
obstacle argument). The allocator will plan through walls the simulator routes around.
Obstacle support is not usable end to end today.

### `congestion_weight` only half applies
It is a simulator parameter; the allocator's A\* calls in `features.py` always use the
default 1.0. Changing it desynchronises planner and allocator.

### Strategies produce identical results
Not a bug — expected. `nearest`, `least_busy`, `spt`, `edf`, `heuristic` and `greedy`
frequently collapse to one behaviour, as do `score`, `bid` and `auction`. See
[05_MRTA_ALGORITHMS.md](05_MRTA_ALGORITHMS.md).

### Repeated runs give different logged features
Expected. The simulator runs on wall clock with one thread per task, so congestion
sampled at decision time varies. Assignments are stable; `congestion`, `travel_time`,
`energy_pct` and `makespan_s` are not. See [12_EXPERIMENTS.md](12_EXPERIMENTS.md).

### `/get_robot_fleet_status` returns unexpected data
Advertised by **both** `shared_memory_node` and `fleet_manager`. A client binds to an
arbitrary one. No node currently calls it, but do not add a client without renaming one
of the servers.

---

## Things that look broken but are not

| Observation | Explanation |
|---|---|
| `/simulation/battery_updates` has no subscriber | diagnostic topic, intentionally unconsumed |
| `/central_logs` has no subscriber | same |
| `ProcessOrder.action`, `GoalStartMsg`, `GoalReachMsg` unused | leftovers from the Gazebo/Nav2 workspace |
| `battery_drain_per_m` has no effect | superseded by the physics energy model |
| `metrics_*.csv` has only one row | rewritten in place after each event by design |
| `n_defer > 0` with `n_completed` complete | deferral counts events; a re-queued task still completes later |
| `mean_alloc_runtime_ms ≈ 0.004` | it times `select()` only, excluding A\* in `build_candidates` |
| `configs/` and `data/*` are empty | unused scaffolding; nothing reads or writes them |
