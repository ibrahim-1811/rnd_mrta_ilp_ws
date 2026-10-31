# Handover XMind outline

Editable native file: `docs/PROJECT_HANDOVER.xmind`. Four sheets reflect the audited architecture, flow, data and planned learning work.

## System architecture

- System architecture
  Audited28October2026. Implemented apparatus; symbolic learner pending. Source of truth: docs/PROJECT_HANDOVER.md.
  - Two Git worktrees
    - GUI / operational workspace
      /home/ibrahim/rnd_mrta_ilp_ws · traffic/rmf-integration · Audited HEAD ae832f5 · Full dashboard, supported ROS/Gazebo demo, pre-M0 science.
    - Research workspace
      /home/ibrahim/rnd_mrta_ilp_research · research/outcome-aware-ilp · Audited HEAD 163c6a4 · M0 committed, local M1 diagnostics.
    - Integration pending
      M0 changes strategy identity, build_manifest, quantum/window/purpose, comparisons. GUI observer pin requires fresh parity.
  - Inputs
    - W100.xlsx
      src/simulation_env/maps/W100.xlsx · 40x54, 1m cells, 100 staging, 552 shelves, 36 dropoffs, 24 chargers, 12 holding.
    - warehouse.yaml
      src/simulation_env/config/warehouse.yaml · Default100 robots, 1.5m/s, q0, window=fleet_size, 1s service, battery fixed.
    - orders.csv
      src/simulation_env/config/orders.csv · 2,000 logical reference orders, generation seed42. Multiple line items per order.
  - Entry points
    - Headless CLI
      src/simulation_env/scripts/run_workload.py
    - Campaign CLI
      src/simulation_env/scripts/run_experiment.py
    - ROS / Gazebo
      src/simulation_env/launch/warehouse_sim.launch.py · orders:=true is required.
    - Dashboard
      src/dashboard_GUI/scripts/launch_control_center.py start · 127.0.0.1:5173 UI;127.0.0.1:8000 API
  - Scientific packages
    - mrta_core
      Map/config/order models, directed A*, battery energy requirement.
    - task_management
      features.py and allocation_strategy.py; eight conventional policies.
    - simulation_env
      workload_executor.py, fleet_engine.py; traffic/charging/recovery; datasets/metrics.
    - robot_interfaces
      ROS message definitions.
    - gazebo_fleet_pose_transport
      Persistent C++ batched pose mirror.
    - rmf_grid_traffic
      Static RMF graph/route checks and local demos; no native production fleet adapter.
  - Dashboard layers
    - React frontend
      Operations, Run history, Experiments, System, sampled recording playback.
    - FastAPI backend
      Adapters, preview/validation, process/job supervision, observation, storage, reports.
    - Offline reports
      Immutable HTML/PNG report folders; separate from dashboard screens.

## Allocation and execution

- Allocation and execution
  - Decision trigger
    Free robots and configured dispatch quantum. q0 continuous; q3 primary research batching.
  - FIFO candidate window
    W=fleet_size by default; extend when no eligible pair exists in prefix.
  - Shared candidate features
    - Directed C_static
      Exhaustive shelf permutations; legal entry/retreat; distance in metres.
    - Estimated finish T
      C_static/speed + shelf visits*service + dropoff service.
    - Hard energy mask
      Order travel + known service + directed charger leg + 10% reserve <= SOC; no future waiting.
  - Eight conventional strategies
    - random
      Canonical eligible-edge list; seeded shuffle; greedy matching. No uniform matching guarantee.
    - nearest_manhattan
      Greedy Manhattan shelf-chain ranking; A* route and battery mask still govern execution.
    - nearest_astar
      Greedy C_static; ties FIFO then robot_id.
    - earliest_finish
      Greedy T = C_static / speed + configured shelf/dropoff service. No future waiting.
    - multi_criteria
      0.5 N(T) + 0.2 N(FIFO) + 0.2 N(-energy margin) + 0.1 N(charger distance). Fixed heuristic weights.
    - auction
      FIFO sequential single-item auction; cheapest currently-free eligible robot bids C_static.
    - hungarian
      Maximum cardinality, then minimum summed C_static; SciPy linear_sum_assignment.
    - milp
      Maximum cardinality; minimize latest estimated finish, then summed finish; SciPy / HiGHS. Different from inductive logic programming.
  - Capture decision rows
    One row per scored pair; selected flag; before execution.
  - Check assignment contract
    At most one robot per order and one order per robot; only eligible offered pairs.
  - Execute shared mission
    - Travel through one-way lanes
    - Local traffic / reservations / rotation right-of-way
    - Shelf service
    - Dropoff fulfillment
    - Final retreat; robot becomes available
  - Record outcome at retirement
    Join run_id + original decision_id + robot_id + order_id. M0 fixes deferred-decision linkage.
  - Recovery and final artifacts
    Per-robot recovery can overlap other deliveries. Separate order_makespan from fleet settled time.

## Battery, data and experiments

- Battery, data and experiments
  - Fixed Battery V1
    - SOC rates
      Initial100%; travel1%/20m;service1%/60s;idle/wait1%/300s;charge+1%/s.
    - Charge due / needed
      Due<=10%; active order finishes first; no assignment bypasses energy mask.
    - Charger workflow
      Reserve→travel→charge→physically leave→rejoin; holding/promotion/preemption fixed.
    - Remaining scope
      B6 rest incomplete; G6 partial recovery charging deferred.
  - Per-run artifacts
    - run.json
      Input hashes, effective settings, Git commit/dirty state, run identity.
    - metrics.json
      Schema2 separates order/recovery; legacy schema1 lacks order makespan.
    - decisions + outcomes
      Parquet in Pixi; CSV fallback in ROS without PyArrow.
    - bk.pl / exs.pl
      Raw teacher-choice facts; no learned rule or full multi-run learner dataset.
    - failure.json
      ResearchM0 classified execution failures; use fresh output directories.
    - run.log / console.log
      Campaign child run.log; standalone console.log only when redirected/teed.
  - Output roots
    - CLI runs/
      Ignored scientific output.
    - GUI var/runs/singles/
    - GUI var/runs/experiments/
    - GUI var/jobs/
      process.log, optional telemetry.ndjson and ownership/gate evidence.
    - GUI var/reports/
    - GUI jobs.sqlite / index.sqlite / app-index
    - Colcon log/ and external ~/.ros/log/
  - Saved GUI pilot
    50robots ×200orders ×8strategies ×seeds1–3=24/24; q0 pre-M0; no charging. A* mean ordermakespan393.22s; exploratory.
  - Historical fleet sweep
    30/40/50robots;3A*+3Manhattan+9Random=15;one reference workload;schema1/provenance limits.
  - Seed roles and pairing
    Training101–110;validation111–115;evaluation1–30;sealed final201–230. Shared workload perseed; strict grid/settings/identity checks. Holm/Wilcoxon pending.

## ILP research and demo commands

- ILP research and demo commands
  - ILP versus MILP
    Inductive logic programming learns rules. Mixed-integer linear programming optimizes assignments. They are different methods.
  - M0 committed
    Identity, classified failures, seed controls, deferred decision linkage.
  - M1 partial diagnostics
    Residual analysis, decision-time congestion sidecars, cloned alternatives. Current feature reporter has route_cells merge failure. Existing pilot predates fixes.
  - M2 planned
    Run-scoped atoms, learner predicates, bias, sampling, offline teacher relabeling.
  - M3–M6 planned
    - Reproduce Nearest A* with Popper
    - Single-Teacher ILP
    - Multi-Teacher ILP
    - Outcome-Aware ILP
      Freeze objective/labels first; evaluate alternatives; never label unexecuted candidates as observed bad outcomes.
  - M7–M9 planned
    Rule explanations, held-out policy evaluation, final report; target20December2026.
  - No leakage boundary
    State_t→Decision_t→Outcome. Future waits/energy/charging may label training examples, never become decision-time input.
  - Toolchain
    Popperv4.4.0 commit860cd0de;SWI10.0.0;Clingo5.8.2;Janus1.5.3;Python3.12. One learner problem per process.
  - Demo commands
    - Headless
      cd /home/ibrahim/rnd_mrta_ilp_ws · export PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management · .pixi/envs/default/bin/python src/simulation_env/scripts/run_workload.py --robots 6 --orders 12 --strategy nearest_astar --quiet --progress 0.
    - Gazebo
      source /opt/ros/jazzy/setup.bash · source install/setup.bash · ros2 launch simulation_env warehouse_sim.launch.py orders:=true robots:=10 order_limit:=50 strategy:=nearest_astar
    - Dashboard
      python3 src/dashboard_GUI/scripts/launch_control_center.py check · python3 src/dashboard_GUI/scripts/launch_control_center.py start · Ctrl+C stops owned services.
    - Full walkthrough
      docs/PROJECT_HANDOVER.md sections4–8; original audit evidence and exact report paths included.

