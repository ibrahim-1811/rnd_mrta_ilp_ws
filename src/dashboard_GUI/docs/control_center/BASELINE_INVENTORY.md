# Control Center baseline inventory

> Stage 0 design only — audited 2026-10-27. No application or telemetry implementation exists from this stage. The user's subsequent layout instruction overrides the supplied plan: all Control Center development belongs under `src/dashboard_GUI/`; these documents belong in its `docs/control_center/` subdirectory. Scientific sources and historical artifacts remain unchanged. Later stages require separate authorization.

## Authority and repository baseline

Read AGENTS.md, CLAUDE.md, README.md, docs/CURRENT_PHASE.md, docs/DECISIONS.md, docs/ROADMAP.md, docs/ARCHITECTURE.md and docs/EXPERIMENT_PROTOCOL.md. Current source and frozen research decisions govern; old narrative examples are not capability declarations. The supplied Stage 0 prompt and control-center plan are design inputs; only Stage 0 is authorized.

HEAD: `aaf0ab97e8ad35592dc23454332106ac18c60c20`  
Branch: `traffic/rmf-integration`. No staged differences. Initial porcelain status:

```text
 M .scratch/traffic-liveness/issues/01-cyclic-deadlock-under-scattered-allocation.md
 M docs/ARCHITECTURE.md
 M docs/CURRENT_PHASE.md
 M docs/DECISIONS.md
 M docs/EXPERIMENT_PROTOCOL.md
 M docs/tools/code_map.py
 M src/simulation_env/scripts/check_strategy_distinctness.py
 M src/simulation_env/scripts/compare_paired.py
 M src/simulation_env/scripts/run_experiment.py
 M src/simulation_env/simulation_env/local_traffic_controller.py
 M src/simulation_env/simulation_env/paired.py
 M src/simulation_env/simulation_env/workload_executor.py
 M src/simulation_env/test/test_battery_eligibility.py
 M src/task_management/task_management/allocation_strategy.py
 M src/task_management/task_management/features.py
?? .codex/
?? AGENTS.md
?? docs/RND_SYSTEM_EXPLORER.html
?? src/dashboard_GUI/
?? src/simulation_env/test/test_experiment_integrity.py
?? src/simulation_env/test/test_traffic_rotation.py
?? src/task_management/test/test_phase7_strategies.py
```

The 15 modified tracked files were already present (1,630 insertions / 47 deletions at baseline). Relevant diffs examined: executor DecisionTiming wiring; five strategy additions and feature timing; traffic rotation handling; runner resume/force; pairing signatures/completeness; comparison reporting; architecture, phase, decisions and protocol updates. These are the user's existing work, not Control Center changes. No approval/freeze of that work is implied.

Preservation baseline: SHA-256 recorded for 239 tracked/nonignored untracked files before document creation; a separate read-only inventory hashes 168 existing files under runs/. Temporary audit evidence is outside the repository in `/tmp/mrta-control-center-stage0/`. No git mutation commands used.

## Source inventory and exact responsibilities

Paths below are repository-relative for portability. Each names an examined source, with the symbol or section used.

| Source | Evidence / intended reuse |
|---|---|
| src/simulation_env/scripts/run_workload.py | main: CLI, construction, callbacks, final writes and failure return |
| src/simulation_env/scripts/run_experiment.py | main, runs_for, prepare, finished, write_workloads, run_one: grid and lifecycle |
| src/simulation_env/scripts/generate_orders.py | DEFAULT_CONFIG, stations_of, build: seeded workload source |
| src/simulation_env/simulation_env/fleet_engine.py | Event, EventType, constructor, step_once: exact clock and callback order |
| src/simulation_env/simulation_env/workload_executor.py | constructor, Progress, BatteryEvent, snapshot, _tick, _event, run: observation boundaries |
| src/simulation_env/simulation_env/grid_robot.py | GridRobot, GridRobotStatus: cells, status, path and counters |
| src/simulation_env/simulation_env/battery_ledger.py | BatterySnapshot, snapshot, soc: authoritative battery reads |
| src/simulation_env/simulation_env/charging.py | TerminalBank, TerminalSlot, ChargeRecord, ChargeState: reservation and workflow state |
| src/simulation_env/simulation_env/local_traffic_controller.py | existing rotation diff, resolve: frozen traffic behavior, not a UI control |
| src/simulation_env/simulation_env/fleet.py | home_bank, spawn_stations, build_fleet: capacity, spread spawn, count rejection |
| src/simulation_env/simulation_env/fleet_node.py | to_msg, fleet_status, publish_fleet: ROS observation reference only |
| src/simulation_env/simulation_env/dataset.py | DECISION_COLUMNS, OUTCOME_COLUMNS, RunRecorder, build_manifest, exact |
| src/simulation_env/simulation_env/dataset_export.py | read_run, _read_csv, export definitions: typed historical reads |
| src/simulation_env/simulation_env/run_metrics.py | run_metrics, _battery_metrics, METRICS_SCHEMA_VERSION |
| src/simulation_env/simulation_env/paired.py | settings_signature, check_complete, check_settings, paired_differences, interval |
| src/simulation_env/scripts/compare_paired.py | load, completeness, main: type restoration and incomplete banners |
| src/simulation_env/scripts/plot_experiment.py | output/summary paths and plot helpers: reuse existing outputs read-only |
| src/mrta_core/mrta_core/orders.py | REQUIRED_COLUMNS, load_orders_csv, select_orders, OrderStatus |
| src/mrta_core/mrta_core/order_queue.py | orders, order_ids, order, pending: current lifecycle and FIFO |
| src/mrta_core/mrta_core/sim_config.py | SimConfig, load_sim_config, path resolution |
| src/mrta_core/mrta_core/warehouse_map_xlsx.py | load_warehouse_xlsx, coordinate frame detection |
| src/mrta_core/mrta_core/warehouse_routing.py | traversable_lane_edges, non_transit_cells: authoritative display lanes |
| src/mrta_core/mrta_core/world_view.py | grid_to_world: centers at origin + cell × resolution |
| src/task_management/task_management/allocation_strategy.py | BATCH_STRATEGIES, get_batch_strategy and current strategy diff |
| src/task_management/task_management/features.py | existing DecisionTiming diff; no future waiting feature |
| src/simulation_env/config/warehouse.yaml | active W100 configuration |
| pixi.toml | headless environment and declared scientific dependencies |
| src/simulation_env/test/test_dataset.py | parity, truncation, exact values and CSV fallback test definitions |
| src/simulation_env/test/test_experiment_integrity.py | settings/completeness tests; end-to-end tests deliberately excluded |
| src/simulation_env/test/test_paired_seeds.py | paired t-interval, duplicate and mismatch tests |

## Executable runner contract

Use repository root as cwd, `.pixi/envs/default/bin/python`, and `PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management`. Direct installed interpreter avoids dependency resolution or root lockfile changes. Both actual `--help` commands passed. The unqualified `python` shell command is absent; use the explicit interpreter.

### Single run: run_workload.py

| Flag | Default / actual behavior |
|---|---|
| --config | src/simulation_env/config/warehouse.yaml |
| --strategy | nearest_astar; validated by get_batch_strategy, not argparse choices |
| --orders | integer 0 = all; negative rejected; prefix of logical orders, never split; above available = all |
| --orders-file | absent = configured CSV; relative existing path resolves from cwd, otherwise next to config |
| --robots | 0 = configured count; positive overrides config; build_fleet rejects count above homes (help says capped, but it does not silently clamp) |
| --speed | float 0 = configured speed; dashboard must validate finite positive effective speed |
| --progress | 10 simulated seconds; 0 off; rounded text, not exact time telemetry |
| --window | absent = config; fleet_size, legacy or positive integer |
| --quantum | absent = config; nonnegative simulated seconds |
| --seed | absent = config; allocation seed, does NOT generate a new workload |
| --dataset | absent = no persisted dataset/metrics; otherwise output directory |
| --export | with dataset, derived CSV and bk.pl/exs.pl |
| --quiet | suppresses dispatch announcements, not battery events or all output |
| -h / --help | exits before workload execution |

No CLI live pause/resume, arbitrary robot control, observer option or structured motion stream. Single-run output has no overwrite guard: the future job manager must allocate a fresh directory. RuntimeError in executor.run prints STALLED and returns 1; this catch is broader than traffic deadlock, so keep original error text and do not diagnose all STALLED lines as a proven deadlock. Other exceptions can exit with a traceback. Dataset and metrics are written after successful return from run(), not incrementally; cancellation/stall can leave no scientific artifacts. Even artifact presence alone is not proof of process success.

### Experiment: run_experiment.py

| Flag | Default / actual behavior |
|---|---|
| --name | experiment |
| --out | runs; destination ROOT / out / name |
| --strategies | nearest_astar nearest_manhattan random |
| --robots | list: 30 40 50 (not the frozen main-study sweep) |
| --seeds | list: 1 2 3 |
| --orders | 0 = all logical orders of each file |
| --jobs | max(1, host CPU count // 2); propose explicit 1 in dashboard |
| --export | optional CSV/Prolog outputs |
| --workload-orders | 2000 generated logical orders per seed |
| --workloads | seeded (default) or reference |
| --resume / --force | mutually exclusive; see SCIENTIFIC_PARITY.md |
| -h / --help | exits without experiment creation |

There is NO --config, --orders-file, --speed, --window, --quantum or --progress here. Children use the default warehouse config, --quiet and --progress 0. The protocol's q=3 condition is valid science but cannot currently be selected through this matrix CLI without external config changes. Dashboard must disable it, not rewrite config or invent a CLI option. Custom CSV is supported for single runs only. Seeded grid = all strategies × robots × seeds; reference grid = deterministic strategies on first seed only and random on every seed. Empty/duplicate arrays and unsafe names need API validation; preview uses runs_for instead of assuming a Cartesian product in reference mode.

## Current scenario and capabilities

Read-only load measured W100 width 40, height 54, 100 home stations and 4,244 directed lane edges (including terminal access). This is a loader check, NOT a fresh topology or reachability acceptance gate. Render dimensions, zones, obstacles, lane edges and resolution from source, never these literal counts. Cell coordinates are grid coordinates, with a separate screen Y transform; terminals cannot be transit shortcuts even where access edges are bidirectional.

Active YAML: 100 robots, spread spawn, 1.5 m/s, 1 m cells, 1 s shelf/dropoff service, 2,000 logical reference orders, allocation window fleet_size, quantum 0, seed 42, Battery V1 enabled. README's 22-robot/100-order quick-start and architecture/roadmap statements that strategies or experiments do not exist are stale. Preserve those documents; display live config and registry.

Registry import returns eight names: auction, earliest_finish, hungarian, milp, multi_criteria, nearest_astar, nearest_manhattan, random. Catalogue must query this registry each environment session. Phase 7 slice 2 and traffic liveness are implemented but awaiting review according to CURRENT_PHASE; B6 remainder/G6 and ILP learning remain unimplemented. Installed Popper does not imply an integrated learner.

## Existing artifacts and schema

Local scan: 20 run.json, 17 metrics.json, zero experiment.json under runs/. Metric versions: 16 legacy v1 (no version field), one v2. Inspected runs/learn_astar_30r_300o/{run.json,metrics.json} and runs/fleet_sweep/summary.csv. These are historical observations, not runs executed in Stage 0. The teacher manifest has 8,566 decision rows / 300 outcomes; its historical wall time is about 5.85 s for 888 simulated seconds, illustrating why recorded sampled playback can matter.

| Artifact | Actual contract |
|---|---|
| run.json | dataset schema_version=1; git commit/branch/dirty, input paths/hashes, effective config/seed/window/quantum/battery, hashed run_id; format and row counts added after execution |
| decisions.parquet | 39 declared typed columns, one scored candidate pair per row; selected/eligible labels; not only winning assignments |
| outcomes.parquet | 22 columns, one retired order; completion is dropoff, attribution includes retreat |
| decisions.csv / outcomes.csv | typed fallback on ImportError or derived exports; empty exact values are empty strings |
| metrics.json | current metrics_schema_version=2; legacy absence means v1; independent of dataset version |
| bk.pl / exs.pl | optional ground decision facts and selected vs eligible-unselected examples, not trained ILP rules |
| run.log | created by matrix runner; single CLI needs external log capture |
| experiment.json | new matrix spec/grid + creation/resume git records; absent in scanned history |
| workloads/orders_sN.csv, workloads.json | generated seed files and hashes/counts |
| summary.csv | completed-file rows plus workload, orders_sha256, line_items, window_width, settings_sha256, run_dir |
| paired.csv / paired.md | generated comparison outputs; CLI writes them, not a read-only GET operation |
| optional telemetry | proposed only; existing dataset is not a robot trajectory recording |

Use dataset_export.read_run for format/type semantics, but it eagerly loads whole tables and prefers Parquet presence over manifest format. Future pagination must use bounded Parquet batches / CSV scanning with the existing declared types; flag conflicting/missing artifacts rather than guessing. No original files may be normalized or repaired by indexing.

Metric units: _s simulated seconds except wall_clock_s, allocator_cpu_s, mission_build_cpu_s and worst_dispatch_s; _m metres; _percent SOC percentage points (fleet sums can exceed 100); _per_h per simulated hour. waiting_share is a ratio. Headline order_makespan_s is final dropoff; makespan_s is legacy fleet settled time = fleet_recovery_time_s. v1 cannot supply absent productive/support/recovery metrics. Battery-disabled output contains battery_enabled=false and omits battery values; missing is not zero. Full current field lists follow, transcribed from source declarations or the inspected v2 output.


### DECISION_COLUMNS (current source)

| Field | Type |
|---|---|
| run_id | string |
| decision_id | int32 |
| sim_time | float64 |
| sim_time_exact | string |
| strategy | string |
| window | string |
| window_width | int32 |
| dispatch_quantum_s | float64 |
| free_robots | int32 |
| window_orders | int32 |
| choice_bearing | bool_ |
| pending_orders | int32 |
| robots_busy | int32 |
| robots_charging | int32 |
| robot_id | string |
| order_id | string |
| robot_x | int32 |
| robot_y | int32 |
| robot_soc_percent | float64 |
| robot_soc_exact | string |
| fifo_position | int32 |
| order_shelf_count | int32 |
| order_dropoff_id | string |
| c_static_m | float64 |
| route_reachable | bool_ |
| route_cells | int32 |
| predicted_travel_energy_percent | float64 |
| predicted_service_energy_percent | float64 |
| predicted_charger_distance_m | float64 |
| predicted_charger_energy_percent | float64 |
| reserve_percent | float64 |
| required_soc_percent | float64 |
| required_soc_exact | string |
| energy_margin_percent | float64 |
| energy_margin_exact | string |
| nearest_charger_id | string |
| battery_eligible | bool_ |
| eligible | bool_ |
| selected | bool_ |

### OUTCOME_COLUMNS (current source)

| Field | Type |
|---|---|
| run_id | string |
| order_id | string |
| decision_id | int32 |
| robot_id | string |
| launched_at | float64 |
| launched_at_exact | string |
| completed_at | float64 |
| completed_at_exact | string |
| realized_distance_m | float64 |
| realized_travel_time_s | float64 |
| realized_waiting_s | float64 |
| realized_service_s | float64 |
| realized_duration_s | float64 |
| retreat_finished_at | float64 |
| energy_consumed_percent | float64 |
| energy_consumed_exact | string |
| soc_at_launch_percent | float64 |
| soc_at_completion_percent | float64 |
| charge_sessions_before | int32 |
| yield_events_during | int32 |
| predicted_distance_m | float64 |
| success | bool_ |

### Current v2 metric keys

```text
metrics_schema_version
strategy
seed
robots
orders
orders_completed
makespan_s
throughput_orders_per_h
order_makespan_s
fleet_recovery_time_s
recovery_tail_s
orders_exhausted_at_s
order_throughput_per_h
mean_completion_time_s
median_completion_time_s
total_distance_m
productive_distance_m
support_distance_m
recovery_distance_m
distance_per_order_m
productive_distance_per_order_m
total_travel_time_s
total_waiting_s
productive_waiting_s
support_waiting_s
recovery_waiting_s
total_service_s
allocator_cpu_s
mission_build_cpu_s
decisions
choice_bearing_decisions
worst_dispatch_s
wall_clock_s
reservation_violations
waiting_per_order_s
productive_waiting_per_order_s
waiting_share
yield_events
plan_deferrals
peak_in_progress
battery_enabled
energy_consumed_percent
productive_energy_percent
support_energy_percent
recovery_energy_percent
energy_per_order_percent
min_soc_percent
mean_final_soc_percent
below_zero_robots
charge_requests
charge_sessions
charging_time_s
recovery_charging_s
charge_energy_percent
out_of_fleet_time_s
charger_wait_time_s
saturation_events
promotions
stage_promotions
preemptions
```
