# 02 — System architecture

Five ROS 2 packages. `mrta_core` is a **pure-Python library with no ROS dependency**
(`src/mrta_core/package.xml` declares no `rclpy` dep), imported by both the simulator
and the task manager so distance, time and energy are computed from one source of
truth.

## Runtime graph

```
                        order_generator
                              │ /order_requests (Order)
                              ▼
  /get_shelf_list      ┌──────────────────┐   /task_list (TaskList srv)
  /get_drop_off_pose   │ task_manager     │──────────────────────────►┌──────────────────┐
◄─────────────────────►│      _node       │                           │  fleet_manager   │
│ shared_memory_node   │ (decision loop,  │◄──────────────────────────│ (mock_fleet_mgr, │
│ (shelf/inventory)    │  5 Hz)           │  /fleet_status            │  grid simulator) │
│        ▲             └──────────────────┘  /fleet_travel_stats      └──────────────────┘
│ /end_order  │              │       │       /simulation/congestion            │
└─────────────┘              │       │                                         │ moves robots
             /ilp/task_allocation_event   /fleet_travel_stats                  │ cell-by-cell
                    │        │                    │                            │ via A*
                    ▼        ▼                    ▼
             ilp_dataset_logger           metrics_logger
             (per-candidate CSV)          (summary CSV)
```

## Interface inventory (verified against source)

### Topics

| Topic | Type | Publisher | Subscribers |
|---|---|---|---|
| `/order_requests` | `robot_interfaces/Order` | `order_generator` | `task_manager_node` |
| `/fleet_status` | `robot_interfaces/FleetStatus` | `fleet_manager` (1 Hz, transient-local) | `task_manager_node`, `shared_memory_node` |
| `/fleet_travel_stats` | `std_msgs/String` (JSON) | `fleet_manager` (per completed task) | `task_manager_node`, `metrics_logger` |
| `/simulation/congestion` | `std_msgs/String` (JSON) | `fleet_manager` (1 Hz) | `task_manager_node` |
| `/simulation/battery_updates` | `std_msgs/String` (JSON) | `fleet_manager` (5 s) | *none* |
| `/ilp/task_allocation_event` | `std_msgs/String` (JSON) | `task_manager_node` | `ilp_dataset_logger`, `metrics_logger` |
| `/end_order` | `robot_interfaces/Order` | `task_manager_node` | `shared_memory_node` |
| `/central_logs` | `robot_interfaces/Logs` | `fleet_manager`, `shared_memory_node` | *none* |

`/simulation/battery_updates` and `/central_logs` have **no subscriber** in this
workspace — they are diagnostic outputs only.

### Services

| Service | Type | Server | Clients |
|---|---|---|---|
| `/get_shelf_list` | `GetShelfList` | `shared_memory_node` | `task_manager_node`, `order_generator` |
| `/get_drop_off_pose` | `GetPose` | `shared_memory_node` | `task_manager_node` |
| `/task_list` | `TaskList` | `fleet_manager` | `task_manager_node` |
| `/shelf_query` | `ShelfQuery` | `shared_memory_node` | *none* |
| `/update_inventory` | `InventoryUpdate` | `shared_memory_node` | *none* |
| `/get_robot_state` | `GetRobotStatus` | `shared_memory_node` | *none* |
| `/reset_shared_memory` | `std_srvs/Trigger` | `shared_memory_node` | *none* |
| `/reset_simulation` | `std_srvs/Trigger` | `fleet_manager` | *none* |
| `/get_robot_fleet_status` | `GetRobotFleetStatus` | **`shared_memory_node` *and* `fleet_manager`** | *none* |

> **Name collision:** `/get_robot_fleet_status` is advertised by two nodes
> (`shared_memory_node.py:90` and `mock_fleet_manager.py:141`). ROS 2 permits this but
> a client's request goes to an arbitrary one of them. No node in this workspace calls
> it, so nothing is currently broken — but it is a latent trap for anyone adding a
> client. See [15_TROUBLESHOOTING.md](15_TROUBLESHOOTING.md).

### Actions

`robot_interfaces` defines `ProcessOrder.action` and `TaskList.action`, but **no node
implements an action server or client**. They are unused declarations retained from the
earlier Gazebo/Nav2 workspace. The same applies to the messages `GoalStartMsg` and
`GoalReachMsg`.

## Ownership map

Which package owns which concern:

| Concern | Owner | Source |
|---|---|---|
| Warehouse/grid model | `mrta_core` | `mrta_core/world.py` (`WorldGrid`) |
| A\* path planning | `mrta_core` | `mrta_core/grid.py` (`astar_path`) |
| Robot model | `mrta_core` | `mrta_core/models.py` (`RobotState`) |
| Task model | `mrta_core` | `mrta_core/models.py` (`TaskState`) |
| Kinematics | `mrta_core` | `mrta_core/kinematics.py` (`RobotModel`) |
| Energy modelling | `mrta_core` | `mrta_core/kinematics.py` (`path_energy_percent`) |
| Task allocation | `task_management` | `task_management/allocation_strategy.py` |
| Feature/cost model | `task_management` | `task_management/features.py` |
| **ILP** | *nobody* | **not implemented** |
| **Baseline batch algorithms** (Hungarian, GA) | *nobody* | **not implemented** |
| Symbolic/explainability vocabulary | `mrta_core` | `mrta_core/symbolic.py` — **library only, no caller** |
| Explanation strings | `task_management` | `AllocationStrategy.explain()` |
| Shared memory | `shared_memory_node` | `shared_memory_node/shared_memory_node.py` |
| Simulation | `simulation_env` | `simulation_env/mock_fleet_manager.py` |
| Experiment execution | `scripts/` | `scripts/run_experiment.sh` (single run only) |
| Metrics | `task_management` | `task_management/metrics_logger.py` |
| Dataset export | `task_management` | `task_management/ilp_dataset_logger.py` |
| **Visualization** | *nobody* | **not implemented** — no RViz config, no plotting script |

### The `symbolic.py` orphan

`mrta_core/symbolic.py` (127 lines) converts numeric robot/task/path features into
Prolog-style predicates (`battery_high/1`, `short_path/2`, `battery_feasible/2`, …) and
serialises them with `as_prolog_facts()`. It is exported from `mrta_core/__init__.py`
and covered by 12 unit tests.

**No node imports it.** It is a complete, tested, unused library — the intended bridge
to the ILP layer that has not been built. See
[08_EXPLAINABILITY.md](08_EXPLAINABILITY.md).

## Decision loop

`task_manager_node` runs three timers (`task_manager.py:95-97`):

| Timer | Period | Purpose |
|---|---|---|
| `_init_caches` | 1.0 s | fetch shelf list and drop-off pose once |
| `_dispatch` | 0.2 s (5 Hz) | the allocation decision loop |
| `_heartbeat` | 2.0 s | progress line on stdout |

`_dispatch` (`task_manager.py:178-230`) is **non-blocking and multi-assign**: each tick
it walks the whole pending queue and assigns as many tasks as there are free robots, so
one unassignable task does not block the rest. A task whose strategy returns `None` is
re-queued (deferred), not dropped.

**Candidate pool = currently idle robots not already assigned in this tick.** This is
the single most consequential design detail in the system, because it means a robot is
never a candidate while it has work in flight — which drives the strategy-collapse
effect documented in [05_MRTA_ALGORITHMS.md](05_MRTA_ALGORITHMS.md).
