# Shared engine / Gazebo / RMF audit notes (2026-10-28)

Repository: `/home/ibrahim/rnd_mrta_ilp_ws`. Read-only audit: no production/test source edits, no commits, no Gazebo or ROS services started/stopped. Only this note file is owned by this agent. Existing Git status at entry: `?? .codex/`, `?? 26.2.2`.

## Executive findings for documentation freeze

1. **Use explicit small commands for demos.** The current default is **W100, 100 robots, all 2000 orders**, not README's 22 robots / 100 orders. The latter is the V2 reference fixture and requires `--config src/simulation_env/test/fixtures/v2_reference.yaml`. W100 service is 1s shelf/1s drop-off, V2 reference is 2s/2s. Both have speed 1.5m/s and cell resolution 1m.
2. **Gazebo is a pose mirror of the Python scientific engine.** There is no native RMF traffic scheduler in the workload path. `rmf_grid_traffic` has a generated mirror graph, offline C++ native RMF checks and independent pedagogical demos; its package.xml description says “adapter” but no fleet adapter is instantiated in production.
3. **A successful ROS workload does not exit.** It finishes inside the `FleetNode` constructor, prints `RUN COMPLETE`, writes artifacts, then creates a publisher timer and enters `rclpy.spin`. Stop the run terminal with Ctrl+C after seeing the summary/artifacts. In a two-terminal demo this leaves the separate Gazebo terminal running. In one-command mode Ctrl+C stops launch's Gazebo too.
4. **Starting Gazebo can stop an existing server.** `gazebo.launch.py` calls `stop_gazebo`, which scans this user's `/proc` for same-world filename on the same `GZ_PARTITION`, sends SIGINT, waits up to 10s, then SIGKILL. This behavior must be visible in instructions; it is why launches were not run in this audit.
5. **Terminal summaries still call fleet settled time “makespan.”** Both headless CLI and ROS summaries print legacy `makespan_s`; the frozen scientific headline is `order_makespan_s` (final drop-off), with fleet settled time reported separately. Documentation can clarify this without code changes.
6. **One obsolete Gazebo demo is broken.** `src/rmf_grid_traffic/scripts/real_warehouse_gazebo_demo.py` imports deleted `simulation_env.fleet_playback`; even `--help` fails with `ModuleNotFoundError`. Do not offer it as a supported command. Supported Gazebo demos use `warehouse_sim.launch.py`; supported terminal traffic demo is `real_warehouse_traffic_demo.py`.
7. **No claims of comprehensive arbitrary config support.** The shipped 1m resolution is the verified condition. Runner and node call `load_warehouse_xlsx(config.map_path)` without passing config resolution; engine timing / travel / Gazebo use config.resolution_m while route distances and nav graph use loaded map's default 1.0. Non-unit resolution therefore needs a separately reviewed fix/check before claiming scientific parity.

## Exact verified safe commands

These were run in the research repository on 2026-10-28 with `PYTHONDONTWRITEBYTECODE=1` and the source packages, no ROS/Gazebo launches.

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws
export PY=.pixi/envs/default/bin/python
export PP=src/mrta_core:src/simulation_env:src/task_management

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PP $PY \
  src/simulation_env/scripts/run_workload.py \
  --robots 6 --orders 12 --quiet --progress 0
```

Observed: nearest_astar seed42; first12 of2000 logical orders /23 line items; W=6; quantum0; completed12/12; parked6/6; fleet settled212.3333s; distance1402m; waiting0; service35s; zero reservation violations/plan deferrals/charges; 7 allocation decisions (6 choice-bearing); min SOC84.6%; exit0; about0.8s wall for whole process.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PP $PY \
  src/simulation_env/scripts/run_workload.py \
  --config src/simulation_env/test/fixtures/v2_reference.yaml \
  --orders 100 --quiet --progress 0
```

Observed:22robots;100 logical orders/200 line items; W=22; quantum0;100/100;22/22parked; legacy-labeled makespan268.6667s (fleet settled);5798m;305.3333s waiting;600s service;0 reservation violations;0 plan deferrals. This reproduces README's old five-minute numbers **only with the explicit fixture**.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PP $PY \
  src/rmf_grid_traffic/scripts/verify_nav_graph.py \
  --config src/simulation_env/config/warehouse.yaml \
  --graph src/rmf_grid_traffic/config/warehouse_nav_graph.yaml
```

Observed:2148vertices;4244 graph/policy lanes;0illegal,missing,two-way-core,terminal-chain,stranded-terminal;724terminal vertices;1424/1424forward and reverse reachable; statusOK; exit0.

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 run rmf_grid_traffic graph_check \
  src/rmf_grid_traffic/config/warehouse_nav_graph.yaml
ros2 run rmf_grid_traffic route_check \
  src/rmf_grid_traffic/config/warehouse_nav_graph.yaml shelf_045 staging_001
```

Observed graph_check:2148namedwaypoints,4244directedlanes,statusOK. route_check:33waypoints/32moves/32m;21.333s distance/speed and RMFcost;statusOK. Native parser emits missing-door/missing-lift-information warnings; this one-floor graph has no doors/lifts. Both exit0. These are offline native checks; they do not start a ROS graph/server or scientific fleet.

```bash
./build/gazebo_fleet_pose_transport/test_frame_parser
```

Observed8/8GTests pass,exit0. Uses an existing compiled binary; no rebuild was attempted.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PP $PY -m pytest -q -p no:cacheprovider \
  src/simulation_env/test/test_gazebo_world.py \
  src/simulation_env/test/test_charging_bank.py \
  src/simulation_env/test/test_battery_ledger.py \
  src/simulation_env/test/test_traffic_rotation.py \
  src/simulation_env/test/test_reservation_audit.py
```

Observed73passed in2.99s,exit0. These test synthetic Gazebo process inventories; they do not start/stop real Gazebo.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PP $PY \
  src/rmf_grid_traffic/scripts/real_warehouse_traffic_demo.py --delay 0
```

Observed exit0/statusOK; bounded384legs/66240pairs;43merge and217intersection conflicts,0edge swaps; chosen intersection tick24,t16s,cell(17,35); staging_019->shelf_024 (58m) vs staging_038->shelf_162(28m); loser wait0.667s; completion39.333s. This script prints ANSI terminal animation and finishes automatically; `--delay0` skips prompt/sleeps. It uses production static routes but a separate two-robot demo arbitration loop, so it is visualization, **not an acceptance validator**.

Failure confirmation:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PP $PY \
  src/rmf_grid_traffic/scripts/real_warehouse_gazebo_demo.py --help
```

Exit1 at line23: `ModuleNotFoundError: No module named 'simulation_env.fleet_playback'`.

## Reproducible Gazebo procedure (source-verified; not launched during audit)

Preconditions: Ubuntu24.04/Linux, ROS2Jazzy, GazeboHarmonic (`gz sim`), ROS Python `/usr/bin/python3` (not pixi shell), C++17 and ament build tools, `rmf_traffic`/`rmf_fleet_adapter`, `gz-msgs10`/`gz-transport13` via Jazzy vendor packages, generated robot_interfaces. Source manifests list rclpy/std_msgs and local package dependencies. `openpyxl` is declared in pixi but not as mrta_core ROS package dependency; fresh machine setup must ensure system ROS Python can import openpyxl/yaml/scipy (actual existing overlay works; a clean machine install was not tested).

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws
source /opt/ros/jazzy/setup.bash
colcon build --packages-select \
  mrta_core task_management simulation_env rmf_grid_traffic \
  robot_interfaces gazebo_fleet_pose_transport \
  --cmake-args -DPython3_EXECUTABLE=/usr/bin/python3
source install/setup.bash
```

This is the documented build (`docs/START_HERE.md:374`, `docs/DECISIONS.md:278`). CMake otherwise found `~/.local/bin/python3.10` lacking catkin_pkg on this machine. START_HERE says never add `--symlink-install`. No build was rerun in audit.

Two terminals, explicit short W100 run:

```bash
# Terminal 1: start the world with 30 parked robots.
cd /home/ibrahim/rnd_mrta_ilp_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 launch simulation_env gazebo.launch.py robots:=30

# Terminal 2: execute only the first 50 logical orders.
cd /home/ibrahim/rnd_mrta_ilp_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 launch simulation_env warehouse_sim.launch.py \
  start_gazebo:=false orders:=true robots:=30 order_limit:=50 \
  strategy:=nearest_astar
```

Gazebo readiness: a single `/world/warehouse/set_pose_vector` provider. Expected setup messages: `[OK] Gazebo already shows these 30 robot(s)` or a created/removed fleet count, then `[OK] 30 robot poses pushed to Gazebo world 'warehouse' as one persistent fleet stream`. Node waits60s when started by warehouse launch; direct fleet_node default wait is0s. If0providers or >1provider, node disables visualization and continues the scientific workload. A transport/setup failure likewise disables Gazebo, does not prove the run failed scientifically, and does not prove visible motion.

One-command form (also starts/stops same-world Gazebo as described above):

```bash
ros2 launch simulation_env warehouse_sim.launch.py \
  orders:=true robots:=30 order_limit:=50 strategy:=nearest_astar
```

`headless:=true` on either launch means **Gazebo server without a GUI**, not the no-ROS scientific CLI. One-command runs can outlive scientific completion until Ctrl+C. For a quick independent scientific result, use `run_workload.py`.

Recording a live run:

```bash
ros2 launch simulation_env warehouse_sim.launch.py \
  start_gazebo:=false orders:=true robots:=30 order_limit:=50 \
  dataset:=runs/live_freeze_demo
```

Artifacts are written only on successful completion. CLI has `--export` for CSV/Prolog extras; the launch has no `export` argument. ROS Python without pyarrow uses recorder's CSV fallback (`runs/gazebo_10_50/run.json` says `format:csv`), while pixi normally writes Parquet. See other audit agent's dataset schema for precise export behavior.

## Actual flags/defaults and config lookup

Evidence: [run_workload.py](/home/ibrahim/rnd_mrta_ilp_ws/src/simulation_env/scripts/run_workload.py:44), [warehouse_sim.launch.py](/home/ibrahim/rnd_mrta_ilp_ws/src/simulation_env/launch/warehouse_sim.launch.py:74), [fleet_node.py](/home/ibrahim/rnd_mrta_ilp_ws/src/simulation_env/simulation_env/fleet_node.py:87), [sim_config.py](/home/ibrahim/rnd_mrta_ilp_ws/src/mrta_core/mrta_core/sim_config.py:136).

Headless CLI: `--config=src/simulation_env/config/warehouse.yaml`; `--strategy=nearest_astar`; `--orders=0` (all logical orders, never split; >available takes all; negative rejected); `--orders-file=None`; `--robots=0` (config; too many raises rather than silently caps, despite help saying “capped”); `--speed=0.0` (config); `--progress=10.0` simulated seconds,0off; `--window=None` (config); `--quantum=None` (config); `--seed=None` (config); `--dataset=None` off; `--export` false; `--quiet` false. Quiet suppresses per-assignment lines only: it still prints header, summary, battery events, final occupancy and per-robot battery table. `--export` without dataset is silently no-op. Negative `--speed` is truthy, reaches engine and is unsupported; no promised friendly validation.

Warehouse launch args: config_file=source-config if source package found, installed share fallback otherwise; world_file="" (derive from config); start_gazebo=true; headless=false; orders=false; strategy=nearest_astar; speed=0.0(config); window=""(config); quantum=-1.0(config); order_limit=0(all); dataset=""(off); verbose=false; progress=60.0simulateds; robots=0(config). **Naming strategy does not enable workload**: orders:=true sets both load_orders and run_workload. Launch does not expose allocation_seed, use_gazebo or playback_hz although node parameters exist.

Gazebo launch alone args: config_file(source/installed fallback),world_file="",robots=0,headless=false. It writes world+fleet temp copy then executes `gz sim -r <temp.world>` or `gz sim -s -r <temp.world>`.

Direct ROS node extra parameters/defaults: use_gazebo=config.gazebo_enabled(true),publish_hz=config.robot_state_publish_hz(2),wait_for_gazebo=0s,gazebo_playback_hz=20,load_orders=false,run_workload=false,allocation_seed=-1(config). For ROS without Gazebo, direct node supports `-p use_gazebo:=false -p load_orders:=true -p run_workload:=true`, but it still paces by visualization.time_scale and still spins after completion.

Config paths: `_resolve` tries an absolute path as given, otherwise config directory then extra directories; maps additionally searched in sibling `maps/`, orders only beside config. Headless `--orders-file` first accepts an existing CWD path, otherwise config-directory path. Headless default config is **CWD-relative**; run from repo root or give an absolute `--config`. Launch `_source_tree` assumes normal isolated install shape `install/simulation_env/share/simulation_env` four levels under root; custom/merged layouts were not verified. Config/world source lookup means scenario changes need no rebuild; Python code and launch files are installed copies and need rebuild. **Byte-equality checked now** for source/install `fleet_engine.py`,warehouse.yaml,warehouse.world,warehouse_nav_graph.yaml:all equal. This does not guarantee future installed code remains current.

## Scientific model / map facts

Evidence: [fleet_engine.py](/home/ibrahim/rnd_mrta_ilp_ws/src/simulation_env/simulation_env/fleet_engine.py:1), [grid_robot.py](/home/ibrahim/rnd_mrta_ilp_ws/src/simulation_env/simulation_env/grid_robot.py:1), [warehouse_routing.py](/home/ibrahim/rnd_mrta_ilp_ws/src/mrta_core/mrta_core/warehouse_routing.py:1), [warehouse_map_xlsx.py](/home/ibrahim/rnd_mrta_ilp_ws/src/mrta_core/mrta_core/warehouse_map_xlsx.py:509).

- Event-driven shared scientific clock `Fraction`; **no fixed simulation dt**. A move is resolution/speed, exactly2/3s at1m/1.5mps. Same-time events handled in a batch, releases applied before next claims. No Nav2, acceleration, turn cost or physical collision dynamics in production. Generic `kinematics.py` has trapezoidal motion/Joule model but is not imported by the workload engine and must not be documented as active BatteryV1.
- Engine accepts per-robot speed but a simultaneous ordinary movement batch requires one common move duration; heterogeneous speed fleets are not supported in general (runtime assertion at fleet_engine.py~1080).
- Each shelf visit and final drop-off has configured fixed service dwell. Service approach cell remains exclusively held through service and one-cell retreat, so service footprint includes terminal+access cell. Retreat is a real charged movement included in cost/distance. An order becomes COMPLETED at drop-off service finish; robot becomes free only after retreat; outcome cost covers retreat.
- Four-connected directed A*, deterministic neighbor/tie order. Manhattan is heuristic/naive strategy only. Normal transit edges fully one-way. Terminal<->aisle edges allow entry+reverse; terminal->terminal disallowed. The per-leg router blocks all terminals except endpoints. Drawn map arrows (`warehouse.lane_edges is not None`) override built-in V2 coordinate rules; no arrows falls back to legacy V2 policy. Do not claim built-in V2 rules generalize to arbitrary sizes.
- W100 measured load:40x54,origin(0,0),resolution1,12obstacles,552shelf,36dropoff,100staging,24charger,12holding;1548explicit floor lane edges;724total terminal cells,1424transit; graph2148 free vertices/4244lanes. V2 measured17x35,134obstacles,72shelf,36dropoff,22staging,8charger,14holding; no drawn lanes (built-in rules),309transit. W100 is research map, V2 baseline/tests, held-out transfer map remains planned. No W20/W30/W50 study family anymore.
- XLSX parser reads active sheet unless explicit `sheet_name` (not exposed in config), orange coordinate frame and numeric axis labels define grid; top-of-sheet larger y; unknown nonempty text refused; red fill defines obstacles; aliases define semantics; merged semantic areas split into single-cell stations. IDs assigned deterministically by workbook scan, not learned predicates. Drawn arrows into wall/outside/station refused; unlabelled free cells remain floor. `world_x=origin_x+x*cell_size`, same y. No +0.5 offset. Default origins0. Transform supports origin in code but config has no origin keys.
- Fleet capacity:robot_count>=1 and <=home station cells, all starts unique/traversable/IDLE. Collection preferred over staging if a map has both home types; mixed banks do not sum. W100 spawn mode `spread` shares robots proportionally across contiguous banks, deterministic largest remainder and even spacing; full100uses every slot. V2 mode `home` uses first stations. Final parking always greedy nearest free by directed route (cost,zone_id,robot_id), claimed before departure; spawn confers no ownership. Robot ID only exact engineering tie-breaker.
- All orders exist at t0. CSV schema `order_id,item_id,shelf_id,quantity,dropoff_id`, same order rows grouped, same dropoff, positive integer quantity, map shelf/dropoff validation, FIFO first appearance. No priorities/releases/deadlines. Window default=fleet size,legacy=free count without liveness extension,positive int allowed. Window widens FIFO prefix only if no assignable pair; quantum0continuous or positive hold on exact scientific clock. Held robots are idle, not traffic waiting, and idle battery drains. Exact timer event `DISPATCH_WINDOW` does not move robots.

## Traffic / safety / liveness

Evidence: [local_traffic_controller.py](/home/ibrahim/rnd_mrta_ilp_ws/src/simulation_env/simulation_env/local_traffic_controller.py:1), [reservations.py](/home/ibrahim/rnd_mrta_ilp_ws/src/simulation_env/simulation_env/reservations.py:173), [local_yield_selector.py](/home/ibrahim/rnd_mrta_ilp_ws/src/simulation_env/simulation_env/local_yield_selector.py:1), [fleet_engine.py](/home/ibrahim/rnd_mrta_ilp_ws/src/simulation_env/simulation_env/fleet_engine.py:1698).

Controller resolves only one-cell movement intentions, never order ownership. Priority busy(order work)>parking/support,then smaller remaining route distance,then numeric robot ID. Rotation rule protects closed movement cycles length>=3 before ordinary contention; no 2-robot head-on swaps. Dependency propagation lets whole same-direction chains advance atomically only when each occupied target is being vacated in same batch. Batches release all source claims before target claims; unlike protected service moves, ordinary motion does **not** retain its source throughout transit. reservations.py module opening docstring's blanket “holds both” describes conservative single movement but is incomplete for modern batch method.

Head-on conflicts (possible on terminal service access) require lower priority robot to take adjacent legal yield cell, preserving original mission/path. No shelf/dropoff/home/charge/holding terminal as yield bay; entry+reverse legal, unoccupied/unreserved, avoids winner's next escape cell. Winner waits for actual clearance. Rejoin uses corridor mutex (earliest wait_since,robot ID). Terminal access safety additionally enforced by charger/holding reservations.

Waiting adds time/energy, never distance/travel time. Live reservation history checks vertex overlap and reverse-edge swaps; touching intervals allowed. Audit optimized per-cell/per-edge, not all-pairs over whole run. A completed check reports observed reservation history violations, not a proof of physical Gazebo collision avoidance (models have no collisions).

Engine watchdogs:default max_events1,000,000; max_no_progress_s600 scientific seconds without mission completion; valid charge-only intervals exempt only when all noncharging robots finished/IDLE and no waiting/yielding remains. Queue exhaustion with waiting raises DeadlockError with robot/path/blocker/dependency/cycle diagnostics. Rotation rule does not cover wait cycles containing robot outside movement batch (servicing,charging,protected move); residual stalls remain possible and must be failed runs, never silently rescued. README/START_HERE rotary warning is historic context, not complete description after rotation rule.

Executor run max_rounds500. It retries deferred same ownership on changed traffic; static missions exclude dynamic robots, engine arbitrates actual access. Recovery may park idle unassigned robots to clear a standstill but never reallocates an order; if no recovery starts, raises workload-stalled RuntimeError. CLI catches RuntimeError from executor.run,printsSTALLED,returns1,does not write successful dataset/metrics. Other load/setup errors can traceback. ROS catches workload RuntimeError,logs “workload stalled”,returns from workload routine,then still publishes/spins; its process exit status is not scientific success. Inspect RUN COMPLETE and metrics/completions instead.

## Battery and completion semantics

Evidence: [battery.py](/home/ibrahim/rnd_mrta_ilp_ws/src/mrta_core/mrta_core/battery.py:1), [battery_ledger.py](/home/ibrahim/rnd_mrta_ilp_ws/src/simulation_env/simulation_env/battery_ledger.py:1), [charging.py](/home/ibrahim/rnd_mrta_ilp_ws/src/simulation_env/simulation_env/charging.py:1), [workload_executor.py](/home/ibrahim/rnd_mrta_ilp_ws/src/simulation_env/simulation_env/workload_executor.py:1316).

BatteryV1 exact normalized SOC percentage (not hardware-calibrated):initial100%;travel1%/20m;service1%/60simulateds;waiting+idle1%/300s;charging+1%/simulateds **net**;reserve10%;due<=10%;charge normal target100. Unknown battery config keys refused. Per-robot initial overrides only for controlled tests and unknown robot IDs refused. Ledger owns SOC only, no duplicate battery field on GridRobot; every move/service/wait/charge booked from engine events. Idle is elapsed remainder; open charge accounts continuously; live move/service SOC can lag by<=one action, final settled values exact. SOC not clamped at0; below-zero diagnostics and tests available.

Assignment hard mask:known order route energy incl every service retreat + known shelf/dropoff service + directed nearest charger trip from final retreat + reserve. Equality eligible. Future uncertain waiting excluded. Executor independently rechecks every selected pair, refuses ineligible rather than reallocating. CHARGE_DUE while busy does not interrupt order. CHARGE_NEEDED:idle robot cannot afford any pending order across whole pending queue, not just current candidate window. Availability/promotion/gate fixed across strategies.

Charging resources FREE->RESERVED->OCCUPIED->VACATING->FREE, reserve at assignment; free only after physical access edge clearance. Free chargers chosen greedy directed route cost,thenzoneID,robotID; holding next; if both full StageZone fallback; if none reachable/free raises ChargingSaturationError rather than leaving requester in aisle. Queue waiting order lowestSOC,earliestrequest,robotID. Promotion from holding or StageZone when free charger within travel SOC; records OUT_OF_REACH once if no candidate affordable. CHARGE_DUE requester may preempt highestSOC actively charging victim only when victimSOC>requesterSOC;CHARGE_NEEDED cannot preempt.80% is recorded for analysis only,not branching. Victim leaves physically; charger promised to requester prevents immediate re-reservation; promises expire if requester state no longer owed. Preempted victim rejoins only ifSOC>due and some pending order eligible (or no pendingwork); otherwiseholding. Full robots rejoin only after leaving charger.

Run finished iff no pending/deferred/active/returning work and every robot parked or in allowed settled charging-workflow states. CHARGING/travel/REQUEST/leaving not settled; holding/fallback can be settled only if no reachable free charger can promote. This means a run can complete with a robot on holding/fallback rather than all parked in some energy-constrained cases; do not equate all-orders-complete with all-fleet-recovered. G6 end-of-work partial charging **not implemented**: last-order due robot with no pendingwork parks normally; already active charge finishes to100. B6a phase accounting exists; remaining B6 battery decision/outcome additions and G6 are pending.

Order completion=final drop-off service finish; robot availability=post-retreat; order_makespan=max completion timestamps; fleet_recovery_time=engine endtime,legacy makespan_s preserved. Recovery phase begins for each robot at final allocation (when no pending orders) if it owes no order,otherwise when its final mission retires. It may overlap other robots' productive delivery. Productive+support+recovery partitions per-robot counters. Do not call recovery uniformly “after final delivery.”

## Runtime outputs / state / shutdown

- Headless without `--dataset`:stdout only; no auto report/run directory; callbacks print assignments/progress/battery/perf. No ROS or Gazebo state. `--dataset DIR` writes after successful executor.run; relativeDIR relativeCWD. Dataset manifests/provenance/metrics owned by other audit; `--export` addsCSV+Prolog.
- ROS success with dataset:DIR report.txt plus recorder tables/run.json+metrics.json. Without dataset:full text report to `$ROS_HOME/mrta/YYYYMMDD_HHMMSS_<strategy>_<N>robots.txt`, `$ROS_HOME` default `~/.ros`. Timestamp local wallclock,not scientific time. ROS launch itself has normal ROS logdir under configured ROS logging home; existing repository `log/` is **colcon build history**, not scientific runs.
- ROS topics:configured robot_states_topic default`/mrta/robot_states` (`RobotStateArray`,each robot cell/worldXY/status/SOC/currentorder/charge state); hardcoded`/mrta/fleet_status` (`FleetStatus` counts,chargers,SOC,whocharging/waiting);hardcoded`/mrta/events` (`std_msgs/String` assignment/completion/batteryevents). State/statusQoS reliable transient-local KEEP_LASTdepth1; events streamdepth100,not latched. `ros.order_states_topic` is parsed into config but no order-state publisher in this node. `RobotState.current_target` always empty. Message comment says robot owns clock but engine actually owns clock.
- During synchronous workload no ROS spin/timer is running; fleet publish called manually from on_tick wall-throttled publish_hz(default2). After success constructor installs timer and spin keeps final state available. Topics are observations; no command subscriber modifies science.
- Gazebo repository world`src/simulation_env/worlds/warehouse.world` is warehouse only; generated floor,red blocked tiles,semantic station tiles allvisual/static,no collisions. Tempworld`/tmp/mrta_gazebo_<uid>/warehouse.world` includes requestedfleet;rewritten atomically `.partial` thenreplace eachstart. Customworldbasename retained. Tempcopy persists aftershutdown. Original one-world source not mutated duringlaunch.
- Fleet node sets exactvisualfleet using `/world/<world>/scene/info`,`create_multiple`,`remove`;models named`robot_<digits>`;cylinder radius0.30m,height0.15m,z0.155,identityquaternion,fixedorientation. `robot_prefix` configurable but model-discovery/removal regex assumes `robot_`—customprefix not broadlyvalidated. No posefeedback into science.
- FleetPoseSink starts persistenthelper via`ros2 run gazebo_fleet_pose_transport ... --world <world>`,Python latest-only queue(max1),writerthread; drops intermediatevisualframes; never feeds back to execution. `_live_poses` reads live source,target,start/end reservations and linearlyinterpolates. Default20Hz visualsampling;configtime_scale4means4xwallplayback;does not change scientifictime. Slow allocatorCPU canstill pause Gazebovisualupdate because scientificloop is synchronous.
- Transport stdin ASCIIprotocol `FRAME id count name x y z qx qy qz qw ...`; max1MiBline,10000poses,nonzeroquaternion,finitevalues,uniqueentities;blank/#ignored;EOF/QUITdrains up to2000ms;maxpending256;optional--ack emitsREADY/ACK/NACK/ERROR. Async`gz.msgs.Pose_V`to`/world/<world>/set_pose_vector`,replyBoolean. exit2badargs,3protocolerror,4delivery/drainfailure. READYmeansparserready,not service found. Python helperstdoutDEVNULL;successfulframelogs off. CloseworkerQUIT/wait then terminatehelperifneeded. Ctrl+C duringconstructor returns0quietly; node.destroy closeshelper afterspin. Standardlaunch maystillreport processsignal exit-2.

## Existing local evidence / output inventory (not new experiment evidence)

At audit time `runs/` contains 168 files across `astar`, `batched`, `legacy`, `fleet_sweep`, `gazebo_10_50`, and `learn_astar_30r_300o`. Old `astar`, `batched`, and `legacy` manifests predate order-limit provenance and metrics; do not assume all run folders match the current schema. `gazebo_10_50` has run.json (format csv), decisions.csv, outcomes.csv, metrics.json, report.txt. `fleet_sweep` has summary.csv, 15 run logs, per-run metrics/Parquet/CSV/Prolog and five PNG figures; another agent audits current experiment integrity. `/home/ibrahim/.ros/mrta/` has several existing reports including `20261027_160957_hungarian_100robots.txt`. Repository `log/` has 1257 files and latest_build points to `log/build_2026-10-27_16-06-58`. No top-level results or logs directory was found.

## Source/docs drift worth listing explicitly

- README still headlines three strategies, says experiment runner not implemented, and uses default V2 numbers despite the W100 default and more strategies; another agent owns strategy/research status.
- CURRENT_PHASE.md has newest status up front but many historical sections still call V2 current, describe hardcoded lanes as an open problem, say V2 is uncommitted, or name W20/W30/W50 as next. Mark these historical instead of treating all tables as current.
- `workload_executor.py` opening docstring still mentions MapfMissionPlanner, reservation-safe trajectories, window=min(available robots,pending), and return to own staging. Current code uses direct static missions plus reactive traffic, fleet-size window and shared nearest parking. Several method comments mention old space-time search; `on_trajectory` is stored but never called; the `plan_fleet` performance stage stays 0.
- README/START_HERE still say fix all rotaries for STALLED. Current rotation right-of-way can resolve fully closed batch cycles, while residual cycles through service or other robots outside the batch can still stall.
- Terminal metric names are misleading as explained above; clarify in documentation without production edits.
- `--robots` help says capped but build_fleet raises above home capacity.
- `ros.order_states_topic` config is present but unused; current_target is blank; no ROS control API exists in this node.
- Deprecated `real_warehouse_gazebo_demo.py` imports missing fleet_playback and hardcodes V2 staging endpoints. Do not repair it under this documentation-only freeze.
- `rmf_grid_traffic` CMake installs only C++ checks, config, generator/verifier; other Python demos run by source path, not installed `ros2 run` entry points.
- Native C++ route_check closes only shelf/dropoff intermediate stations. two_robot_check uses an in-process schedule database, temporary positive acceleration and holding points. Its outputs are not production scientific clock/makespan metrics, and it is not battery aware. No native RMF negotiation fleet adapter or end-to-end RMF workload execution is present in this workspace.

## Read coverage and limits

Fully read primary CLI/launch/node/Gazebo bridge/view/world and transport C++ parser/client/tests/CMake/manifests; the full fleet_engine; grid_robot, fleet, stage_parking, local controller, yield selector, charging; core grid, battery, orders, queue, sim_config, warehouse_map, CSV loader, world/view and kinematics; RMF graph generator/verifier, native C++ checks and terminal demo logic. Read workload_executor's critical constructor, dispatch, window/quantum, battery gates/charging/queue/preemption, mission build/launch/parking, completion/tick/run/report paths and candidate/outcome hooks. Read the XLSX loader contract plus load body, routing policy and route plan/cache core. Some large batches were tool-truncated; supplemental rereads focused on operational paths.

Did not read every line of map_builder/map_check/symbolic, all legacy V2 routing helper internals, all battery report helpers, all regression scripts/tests, or all 2313-line CURRENT_PHASE / 1538-line DECISIONS / roadmap history. Parent has file/hash catalog; do not claim this subtask read all 26k assigned lines. No full test gate, GUI, ROS fleet launch, fresh build, main campaign or held-out map test was performed.

## Final environment check

In the sourced ROS overlay, `/usr/bin/python3` is Python 3.12.3, openpyxl 3.1.2, scipy 1.11.4, and **pyarrow is absent**. `simulation_env.__file__` resolves to `/home/ibrahim/rnd_mrta_ilp_ws/install/simulation_env/lib/python3.12/site-packages/simulation_env/__init__.py`. These are measured imports, not inferred dependencies. They explain live recorder CSV fallback and preserve the documented risk that equal MILP optima may select different matchings across scipy/HiGHS versions. Final git status additionally showed parent-created handover docs and figures; this agent changed no repository files.
