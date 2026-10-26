#!/usr/bin/env python3
"""Build the code map: every source file, every class and function, who
calls whom, the traced flows, and a mind map.

    python3 docs/tools/code_map.py

writes

    docs/code_map.json     the map as data
    docs/CODE_MAP.md       the same map as a page to read
    docs/REPO_MAP.xmind    the same map for XMind (2020 or later)

and refreshes the data block inside docs/REPO_ATLAS.html, which draws
the interactive system map, flows, code explorer and mind map from it.
Run it after any code change, then republish the atlas.

Everything is read from the source with Python's ast module: names,
line numbers and docstrings are the code's own. Calls are resolved
statically, so they are a close approximation, not a trace: a call
through a variable whose type the code does not name is not drawn. The
flows are written by hand below, but every step names a real file and
function, and the script stops if one no longer exists.
"""

import ast
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
ATLAS = ROOT / "docs" / "REPO_ATLAS.html"
JSON_OUT = ROOT / "docs" / "code_map.json"
MD_OUT = ROOT / "docs" / "CODE_MAP.md"
XMIND_OUT = ROOT / "docs" / "REPO_MAP.xmind"

PACKAGES = {
    "mrta_core": SRC / "mrta_core" / "mrta_core",
    "simulation_env": SRC / "simulation_env" / "simulation_env",
    "task_management": SRC / "task_management" / "task_management",
}

# ----------------------------------------------------------------------
# Layers: where a file sits in the system, top (inputs) to bottom.
# ----------------------------------------------------------------------

LAYERS = [
    ("input", "Inputs", "The map, the one config, the orders and the Gazebo world. Everything starts here."),
    ("entry", "Commands", "What you run: the headless runner, experiments, plots, map tools."),
    ("ros", "ROS and Gazebo", "The live node, launch files, Gazebo bridge, messages, RMF graph and the C++ pose transport."),
    ("sim", "Simulator", "The decision loop and the event engine: robots, traffic, reservations, charging, parking, battery ledger."),
    ("strategy", "Strategies", "The research variable: how robots are paired with orders."),
    ("core", "Domain core", "Maps, roads and directed A*, orders, the battery model, the config. No ROS."),
    ("data", "Recording", "Decision and outcome datasets, exports and run metrics."),
    ("check", "Simulator checks", "Standalone validators run at phase boundaries."),
    ("test", "Tests", "pytest suites (pixi run test)."),
    ("legacy", "Legacy", "Early modules nothing in the simulator uses any more."),
]

# Plain-language role of each file, in one or two sentences. Files not
# listed fall back to their docstring.
ROLES = {
    # inputs
    "src/simulation_env/config/warehouse.yaml": ("input", "The one config: which map, how many robots, speed, service times, battery model, allocation window, Gazebo world name. Read by every run."),
    "src/simulation_env/config/orders.csv": ("input", "The order list for the current map: one row per line item (order_id, item_id, shelf_id, quantity, dropoff_id); rows sharing an order_id are one order."),
    "src/simulation_env/maps/W100.xlsx": ("input", "The research map, drawn in Excel: 40 x 54 cells, 552 shelves, 36 drop-offs, 24 chargers, 12 holding cells, 100 Stage Zones, one-way lane arrows."),
    "src/simulation_env/maps/my_map.xlsx": ("input", "An example map made with map_tool.py example."),
    "src/simulation_env/maps/my_map_20.xlsx": ("input", "A small example map (not referenced by any doc or code)."),
    "src/simulation_env/maps/my_map_60.xlsx": ("input", "A medium example map (not referenced by any doc or code)."),
    "src/simulation_env/worlds/warehouse.world": ("input", "The one Gazebo world, the warehouse only (floor, shelves, stations). gazebo.launch.py adds the robots to a temporary copy."),
    "src/rmf_grid_traffic/config/warehouse_nav_graph.yaml": ("ros", "The RMF navigation graph of the current map, generated from its lanes."),
    # core
    "src/mrta_core/mrta_core/sim_config.py": ("core", "Reads warehouse.yaml into one SimConfig (map, orders, fleet, speed, service, battery, allocation, Gazebo) and rewrites it for a new map."),
    "src/mrta_core/mrta_core/warehouse_map_xlsx.py": ("core", "Reads an Excel map into a WarehouseMap: blocked cells, zones (shelves, drop-offs, chargers, holding, Stage Zones) and the drawn lane arrows."),
    "src/mrta_core/mrta_core/warehouse_map.py": ("core", "The map object: grid, zones and lane edges, with lookups of zones by id or type and of cell sets, built once."),
    "src/mrta_core/mrta_core/warehouse_routing.py": ("core", "Roads and routes: which cells robots may drive through, the legal one-way edges, directed A* to a zone, whole missions (shelves, drop-off, retreats) and a route cache."),
    "src/mrta_core/mrta_core/orders.py": ("core", "Loads the order CSV, groups rows into logical orders, and picks the first N orders for a shorter run."),
    "src/mrta_core/mrta_core/order_queue.py": ("core", "The FIFO queue of orders and their status: PENDING, ASSIGNED, IN_PROGRESS, COMPLETED, FAILED."),
    "src/mrta_core/mrta_core/battery.py": ("core", "Battery V1 model: energy for driving, service and standing still, charge time, and the energy an order needs (the eligibility test)."),
    "src/mrta_core/mrta_core/map_check.py": ("core", "Checks a map: lanes, connectivity, reachable terminals, rotaries. map_tool.py check and build_scenario.py run it."),
    "src/mrta_core/mrta_core/map_builder.py": ("core", "Draws example maps and writes map layouts to Excel (map_tool.py example)."),
    "src/mrta_core/mrta_core/world.py": ("core", "WorldGrid: the obstacle grid inside every map."),
    "src/mrta_core/mrta_core/world_view.py": ("core", "Converts grid cells to world metres and back."),
    "src/mrta_core/mrta_core/grid.py": ("core", "Cell type and grid helpers used everywhere; also holds early cost functions."),
    "src/mrta_core/mrta_core/warehouse_map_io.py": ("legacy", "An older map loader. Nothing imports it."),
    "src/mrta_core/mrta_core/kinematics.py": ("legacy", "Early kinematics helpers. Only its own test uses it."),
    "src/mrta_core/mrta_core/symbolic.py": ("legacy", "Early symbolic predicates. Only its own test uses it."),
    "src/mrta_core/mrta_core/models.py": ("legacy", "Early RobotState/TaskState models, used by symbolic.py and grid.py's old cost functions."),
    # strategies
    "src/task_management/task_management/allocation_strategy.py": ("strategy", "The strategies: nearest by directed A*, nearest by Manhattan, random. Each scores robot-order pairs, drops battery-ineligible ones and pairs greedily."),
    "src/task_management/task_management/features.py": ("strategy", "What a strategy knows about a robot-order pair at decision time: directed-A* route cost, Manhattan cost, energy and battery eligibility."),
    # simulator
    "src/simulation_env/simulation_env/workload_executor.py": ("sim", "The decision loop: finds free robots, sends low robots to charge, offers the FIFO window to the strategy, turns assignments into missions, retires finished orders, and reports."),
    "src/simulation_env/simulation_env/fleet_engine.py": ("sim", "The event clock: moves every robot cell by cell, asks traffic who may move, books time and battery, runs services and charging, detects deadlock."),
    "src/simulation_env/simulation_env/grid_robot.py": ("sim", "One robot: its cell, path, status and time, distance and energy counters."),
    "src/simulation_env/simulation_env/fleet.py": ("sim", "Builds the fleet: one robot per Stage Zone start cell."),
    "src/simulation_env/simulation_env/reservations.py": ("sim", "Who holds which cell and edge, and when; refuses unsafe moves; audits the whole run at the end (no shared cell, no swap)."),
    "src/simulation_env/simulation_env/local_traffic_controller.py": ("sim", "When several robots want cells at the same instant, decides who moves and who waits."),
    "src/simulation_env/simulation_env/local_yield_selector.py": ("sim", "Picks the cell a blocked robot steps aside into."),
    "src/simulation_env/simulation_env/charging.py": ("sim", "Charger and holding cells as slots (FREE, RESERVED, OCCUPIED, VACATING) and every charging state a robot can be in."),
    "src/simulation_env/simulation_env/stage_parking.py": ("sim", "Stage Zone parking: claims the nearest free Stage Zone by directed A* for a robot going home."),
    "src/simulation_env/simulation_env/battery_ledger.py": ("sim", "Exact SOC bookkeeping per robot (rational arithmetic) and the battery report."),
    # recording
    "src/simulation_env/simulation_env/dataset.py": ("data", "Records every decision (all candidate pairs) and every outcome; writes decisions.parquet, outcomes.parquet and run.json."),
    "src/simulation_env/simulation_env/dataset_export.py": ("data", "Reads a run back and exports CSV and Prolog (bk.pl facts, exs.pl examples) for ILP."),
    "src/simulation_env/simulation_env/run_metrics.py": ("data", "The protocol's run-level metrics (makespan, throughput, waiting, energy, charging...) as metrics.json."),
    # ROS and Gazebo
    "src/simulation_env/simulation_env/fleet_node.py": ("ros", "The ROS 2 node: runs the same decision loop live, paces it for Gazebo, streams robot poses, publishes /mrta topics, writes the report."),
    "src/simulation_env/simulation_env/gazebo_bridge.py": ("ros", "Talks to Gazebo: lists, creates and removes robot models, holds the robot model, and feeds the pose transport process."),
    "src/simulation_env/simulation_env/gazebo_world.py": ("ros", "Starting Gazebo: writes the world plus the parked robots to a temporary copy, and stops any Gazebo already running this world."),
    "src/simulation_env/simulation_env/gazebo_view.py": ("ros", "Grid cell to Gazebo XY and back."),
    "src/simulation_env/launch/gazebo.launch.py": ("ros", "Terminal 1: stops an old Gazebo, writes the world with the parked fleet, starts Gazebo."),
    "src/simulation_env/launch/warehouse_sim.launch.py": ("ros", "Terminal 2 (or one command): the fleet node with orders, strategy, robots, order_limit; includes gazebo.launch.py unless start_gazebo:=false."),
    "src/robot_interfaces/msg/RobotState.msg": ("ros", "One robot on /mrta/robot_states: cell, status, battery, current order, charging state."),
    "src/robot_interfaces/msg/RobotStateArray.msg": ("ros", "The whole fleet on /mrta/robot_states."),
    "src/robot_interfaces/msg/FleetStatus.msg": ("ros", "The run at a glance on /mrta/fleet_status: order and robot counts, chargers, SOC, who charges."),
    "src/gazebo_fleet_pose_transport/src/gazebo_fleet_pose_transport.cpp": ("ros", "C++ program: reads one FRAME line per visual frame on stdin and sets every robot pose in Gazebo with one set_pose_vector call."),
    "src/gazebo_fleet_pose_transport/src/frame_parser.cpp": ("ros", "Parses the FRAME lines the Python side writes."),
    "src/rmf_grid_traffic/scripts/generate_nav_graph.py": ("ros", "Writes the RMF navigation graph from the map's lanes (build_scenario.py runs it)."),
    "src/rmf_grid_traffic/scripts/verify_nav_graph.py": ("ros", "Checks the nav graph matches the lane policy."),
    # commands
    "src/simulation_env/scripts/run_workload.py": ("entry", "One headless run: loads config, map, fleet and orders, runs the decision loop, prints the report, records the dataset and metrics."),
    "src/simulation_env/scripts/run_experiment.py": ("entry", "An experiment grid (strategies x robots x seeds), in parallel; one summary.csv."),
    "src/simulation_env/scripts/plot_experiment.py": ("entry", "Figures for every core metric of an experiment, and summary.md."),
    "src/simulation_env/scripts/plot_dataset.py": ("entry", "Figures from recorded datasets (decision shape, predicted vs realized, trade-off)."),
    "src/simulation_env/scripts/build_scenario.py": ("entry", "Makes a map the current one: checks it, rewrites the config, regenerates orders, world and nav graph."),
    "src/simulation_env/scripts/map_tool.py": ("entry", "Map helper: show a map, check it, or make an example map."),
    "src/simulation_env/scripts/generate_orders.py": ("entry", "Generates the order CSV from the map's own shelves and drop-offs, seeded."),
    "src/simulation_env/scripts/generate_gazebo_grid_world.py": ("entry", "Writes the Gazebo world file from the map."),
    "src/simulation_env/scripts/discover_directional_highways.py": ("legacy", "A one-off tool from the V2 map migration."),
    "src/simulation_env/scripts/validate_directional_highways.py": ("entry", "Validates the directional highway layout of a map."),
}

# Files nothing in the simulator imports any more, by path.
LEGACY_SCRIPTS = re.compile(r"src/rmf_grid_traffic/scripts/(local_\w+_demo|real_warehouse_\w+_demo)\.py$")

# The data files the map shows, with the code that reads or writes them.
DATA_FILES = {
    "src/simulation_env/config/warehouse.yaml": {
        "read_by": ["src/mrta_core/mrta_core/sim_config.py::load_sim_config", "src/simulation_env/simulation_env/gazebo_world.py::world_name_of"],
        "written_by": ["src/mrta_core/mrta_core/sim_config.py::rewrite_scenario"],
    },
    "src/simulation_env/config/orders.csv": {
        "read_by": ["src/mrta_core/mrta_core/orders.py::load_orders_csv"],
        "written_by": ["src/simulation_env/scripts/generate_orders.py::main"],
    },
    "src/simulation_env/maps/W100.xlsx": {
        "read_by": ["src/mrta_core/mrta_core/warehouse_map_xlsx.py::load_warehouse_xlsx"],
        "written_by": [],
    },
    "src/simulation_env/maps/my_map.xlsx": {"read_by": ["src/mrta_core/mrta_core/warehouse_map_xlsx.py::load_warehouse_xlsx"], "written_by": ["src/mrta_core/mrta_core/map_builder.py::write_layout_xlsx"]},
    "src/simulation_env/maps/my_map_20.xlsx": {"read_by": [], "written_by": []},
    "src/simulation_env/maps/my_map_60.xlsx": {"read_by": [], "written_by": []},
    "src/simulation_env/worlds/warehouse.world": {
        "read_by": ["src/simulation_env/simulation_env/gazebo_world.py::write_fleet_world"],
        "written_by": ["src/simulation_env/scripts/generate_gazebo_grid_world.py"],
    },
    "src/rmf_grid_traffic/config/warehouse_nav_graph.yaml": {
        "read_by": ["src/rmf_grid_traffic/scripts/verify_nav_graph.py::main"],
        "written_by": ["src/rmf_grid_traffic/scripts/generate_nav_graph.py::main"],
    },
}

# Attribute names whose class the code makes plain, for resolving calls
# such as self.engine.assign_missions_batch(...). A call is linked only
# when that class really has the method.
ATTR_TYPES = {
    "engine": "FleetSimulationEngine",
    "reservations": "ReservationTable",
    "local_traffic": "LocalTrafficController",
    "allocator": "BatchAllocationStrategy",
    "chargers": "TerminalBank",
    "holding": "TerminalBank",
    "parking": "StageParking",
    "recorder": "RunRecorder",
    "queue": "OrderQueue",
    "routes": "RouteCache",
    "cache": "RouteCache",
    "gazebo_sink": "FleetPoseSink",
    "workload": "WorkloadExecutor",
    "executor": "WorkloadExecutor",
    "battery": "BatteryLedger",
    "ledger": "BatteryLedger",
    "robot": "GridRobot",
    "warehouse": "WarehouseMap",
}

# ----------------------------------------------------------------------
# Traced flows. Each step: (file, symbol or None, what happens, what it
# produces). The script checks every named symbol exists.
# ----------------------------------------------------------------------

S = "src/simulation_env/simulation_env/"
SC = "src/simulation_env/scripts/"
C = "src/mrta_core/mrta_core/"
T = "src/task_management/task_management/"
L = "src/simulation_env/launch/"

FLOWS = [
    {
        "id": "build",
        "title": "Build: Excel map to config, orders and world",
        "intro": "build_scenario.py makes a map the current one. It stops before changing anything if the map has errors.",
        "steps": [
            (SC + "build_scenario.py", "main", "Read --map and the one config", "map path, config path"),
            (SC + "build_scenario.py", "check_map", "Run map_tool.py check on the drawing", "OK, or stop"),
            (SC + "map_tool.py", "cmd_check", "check: load the map and run every gate", "errors / warnings"),
            (C + "map_check.py", "check_map", "Lanes, connectivity, terminal reachability, rotaries", "a list of problems"),
            (C + "warehouse_map_xlsx.py", "load_warehouse_xlsx", "Read the Excel cells, zones and lane arrows", "WarehouseMap"),
            (S + "fleet.py", "home_bank", "Count the Stage Zones: the most robots the map can run", "robot_count"),
            (C + "sim_config.py", "rewrite_scenario", "Rewrite the map name and robot_count in warehouse.yaml", "config/warehouse.yaml"),
            (SC + "generate_orders.py", "main", "Generate 2000 orders from the map's shelves and drop-offs, seed 42", "config/orders.csv"),
            (SC + "generate_gazebo_grid_world.py", None, "Draw the warehouse as Gazebo models (the script runs top to bottom)", "worlds/warehouse.world"),
            ("src/rmf_grid_traffic/scripts/generate_nav_graph.py", "main", "Turn the lanes into an RMF graph", "warehouse_nav_graph.yaml"),
            ("src/rmf_grid_traffic/scripts/verify_nav_graph.py", "main", "Check the graph mirrors the lane policy", "OK"),
        ],
    },
    {
        "id": "run",
        "title": "A headless run, start to end",
        "intro": "run_workload.py, the same loop the ROS node runs. Every number in a report comes from these calls.",
        "steps": [
            (SC + "run_workload.py", "main", "Parse --robots --orders --strategy --seed --dataset ...", "args"),
            (C + "sim_config.py", "load_sim_config", "Read the one config", "SimConfig"),
            (C + "warehouse_map_xlsx.py", "load_warehouse_xlsx", "Read the map", "WarehouseMap"),
            (S + "fleet.py", "build_fleet", "One robot per Stage Zone start cell", "list[GridRobot]"),
            (C + "orders.py", "load_orders_csv", "Read orders.csv, group rows into orders", "tuple[Order]"),
            (C + "orders.py", "select_orders", "Keep the first N logical orders (--orders N)", "the run's orders"),
            (C + "order_queue.py", "OrderQueue", "Put them in FIFO order, all PENDING", "OrderQueue"),
            (T + "allocation_strategy.py", "get_batch_strategy", "Pick the strategy by name, with its seed", "BatchAllocationStrategy"),
            (S + "dataset.py", "build_manifest", "Record what is being run (only with --dataset)", "run.json fields, run_id"),
            (S + "workload_executor.py", "WorkloadExecutor.__init__", "Build the engine, battery ledger, charger and holding banks, Stage Zone parking, route cache", "WorkloadExecutor"),
            (S + "workload_executor.py", "WorkloadExecutor.run", "Loop: dispatch, then let the engine run, until every order is done and every robot parked", "WorkloadResult"),
            (S + "workload_executor.py", "WorkloadExecutor.totals", "Sum the robots' counters; audit every reservation", "makespan, distance, waiting, violations"),
            (S + "battery_ledger.py", "battery_report_lines", "The battery table", "BATTERY report"),
            (S + "workload_executor.py", "charging_report_lines", "The charger table and every charge session", "CHARGING report"),
            (S + "dataset.py", "RunRecorder.write", "Write the decision and outcome tables", "decisions.parquet, outcomes.parquet, run.json"),
            (S + "run_metrics.py", "write_metrics", "The run's results", "metrics.json"),
            (S + "dataset_export.py", "write_prolog", "Facts and examples for ILP (with --export)", "bk.pl, exs.pl"),
        ],
    },
    {
        "id": "decision",
        "title": "One allocation decision",
        "intro": "WorkloadExecutor._dispatch runs every time robots become free. Battery and charging come first, then the strategy sees the FIFO window.",
        "steps": [
            (S + "workload_executor.py", "WorkloadExecutor.retry_deferred", "Re-try assignments that could not be planned last time", "missions started"),
            (S + "workload_executor.py", "WorkloadExecutor.available_robots", "Robots that are idle and owe nothing", "free robots"),
            (S + "workload_executor.py", "WorkloadExecutor._battery_gate", "A robot that cannot do any pending order on its battery leaves the fleet", "CHARGE_NEEDED / CHARGE_DUE requests"),
            (S + "workload_executor.py", "WorkloadExecutor._promote", "Give every free charger to the waiting robot with the lowest SOC", "PROMOTE"),
            (S + "workload_executor.py", "WorkloadExecutor._evacuate", "Every requester reserves a charger, holding cell or Stage Zone and drives off", "RESERVE + charging missions"),
            (S + "workload_executor.py", "WorkloadExecutor._battery_context", "Each free robot's SOC and nearest charger", "BatteryDecisionContext"),
            (S + "workload_executor.py", "WorkloadExecutor._candidate_window", "The FIFO window of pending orders the strategy may see", "eligible orders"),
            (S + "workload_executor.py", "WorkloadExecutor._record_decision", "Count the decision's shape (robots x orders)", "decision log"),
            (T + "allocation_strategy.py", "NearestAStarAllocator.assign", "The strategy decides (here: nearest by directed A*)", "list[OrderAssignment]"),
            (T + "allocation_strategy.py", "BatchAllocationStrategy.score", "Score every robot-order pair", "OrderCandidate per pair"),
            (T + "features.py", "build_order_candidates", "Route cost and battery for each pair", "OrderCandidate"),
            (T + "features.py", "order_route_cost", "Directed A* from the robot through the shelves to the drop-off", "OrderRoute (metres)"),
            (C + "warehouse_routing.py", "plan_from_cell", "The real one-way route, leg by leg, with retreats", "MissionPlan"),
            (C + "battery.py", "energy_requirement", "Travel + service + reach a charger + reserve", "EnergyRequirement"),
            (T + "allocation_strategy.py", "greedy_pairing", "Take the best pairs, one order per robot", "assignments"),
            (S + "workload_executor.py", "WorkloadExecutor._check_battery_feasibility", "Re-check every returned pair independently", "refuses an ineligible pair"),
            (S + "workload_executor.py", "WorkloadExecutor._record_candidates", "Write every pair the strategy considered (with --dataset)", "decision rows"),
            (S + "workload_executor.py", "WorkloadExecutor.launch_batch", "Turn assignments into missions", "missions"),
            (S + "workload_executor.py", "WorkloadExecutor._build_mission", "Plan the static route for one assignment", "MissionPlan"),
            ("src/simulation_env/simulation_env/fleet_engine.py", "FleetSimulationEngine.assign_missions_batch", "Hand the missions to the engine", "robots start moving"),
        ],
    },
    {
        "id": "engine",
        "title": "The engine: one simulated instant",
        "intro": "FleetSimulationEngine.run calls step_once until nothing is scheduled. Robots move one cell at a time; nothing moves without a reservation.",
        "steps": [
            (S + "fleet_engine.py", "FleetSimulationEngine.run", "Step until the event queue is empty; watch for no progress", "simulated time advances"),
            (S + "fleet_engine.py", "FleetSimulationEngine.step_once", "Take every event at the next time", "event batch"),
            (S + "fleet_engine.py", "FleetSimulationEngine._handle", "MOVE_COMPLETE, SERVICE_COMPLETE, CHARGE_COMPLETE, ...", "robot states updated"),
            (S + "reservations.py", "ReservationTable.complete_move", "Release the cell the robot left", "edge and cell log"),
            (S + "fleet_engine.py", "FleetSimulationEngine._retry_waiting", "Robots that waited try again", "move requests"),
            (S + "fleet_engine.py", "FleetSimulationEngine._try_moves_batch", "All moves wanted at this instant, together", "approved moves"),
            (S + "reservations.py", "ReservationTable.check_move", "Is the target held, or would two robots swap?", "safe / vertex / edge"),
            (S + "local_traffic_controller.py", "LocalTrafficController.resolve", "Several robots want one cell: who goes", "MovementDecision"),
            (S + "local_yield_selector.py", "select_local_yield_cell", "A blocked robot steps aside", "yield cell"),
            (S + "reservations.py", "ReservationTable.begin_moves_batch", "Claim the targets, start the moves", "held cells"),
            (S + "fleet_engine.py", "FleetSimulationEngine._book_travel", "Count time, distance and battery for the move", "robot counters"),
            (S + "battery_ledger.py", "BatteryLedger.book_move", "Exact SOC: 1% per 20 m", "SOC"),
            (S + "fleet_engine.py", "FleetSimulationEngine._schedule", "Schedule the arrival", "next event"),
            (S + "workload_executor.py", "WorkloadExecutor._tick", "After the instant: flag low batteries, retire finished orders, settle charging robots", "dispatch again"),
            (S + "workload_executor.py", "WorkloadExecutor._retire", "An order's robot is done: record the outcome", "COMPLETED order"),
            (S + "workload_executor.py", "WorkloadExecutor._record_outcome", "What really happened to the order", "outcome row"),
        ],
    },
    {
        "id": "battery",
        "title": "Battery and charging",
        "intro": "Battery V1 is fixed infrastructure, the same for every strategy. A robot asks to charge when no pending order fits its battery.",
        "steps": [
            (S + "workload_executor.py", "WorkloadExecutor._battery_gate", "Test every free robot against every pending order", "eligible or not"),
            (S + "workload_executor.py", "WorkloadExecutor._has_eligible_order", "Is any pending order battery-feasible?", "yes / no"),
            (S + "workload_executor.py", "WorkloadExecutor._enter_request", "Leave the fleet", "REQUEST event, ChargeRecord"),
            (S + "workload_executor.py", "WorkloadExecutor._evacuate", "Charger, else holding cell, else Stage Zone (saturation)", "RESERVE"),
            (S + "charging.py", "TerminalBank.assign", "Cheapest free slot by directed A*", "zone_id, cost"),
            (S + "stage_parking.py", "StageParking.assign", "Saturation: the nearest free Stage Zone", "Stage Zone claim"),
            (S + "workload_executor.py", "WorkloadExecutor._settle", "Arrived: occupy the terminal", "ARRIVE"),
            (S + "workload_executor.py", "WorkloadExecutor._begin_charge", "Charge to exactly 100%", "CHARGE_START"),
            (C + "battery.py", "charge_seconds_to_full", "How long the charge takes", "seconds"),
            (S + "fleet_engine.py", "FleetSimulationEngine.begin_charge", "Book the session in the engine", "charge event"),
            (S + "workload_executor.py", "WorkloadExecutor._charge_complete", "Full: vacate the charger", "CHARGE_FULL"),
            (S + "workload_executor.py", "WorkloadExecutor._reverse_off_terminal", "Reverse one cell into the aisle", "exit move"),
            (S + "workload_executor.py", "WorkloadExecutor._rejoin", "Back in the fleet", "REJOIN"),
            (S + "workload_executor.py", "WorkloadExecutor._promote", "A charger frees: lowest-SOC waiting robot first (holding or Stage Zone)", "PROMOTE"),
            (S + "workload_executor.py", "WorkloadExecutor._grant_charger", "Reserve the charger; leave holding or the Stage Zone", "charger reserved"),
            (S + "workload_executor.py", "WorkloadExecutor._drive_to_charger", "The charger trip, by directed A*", "TO_CHARGER"),
            (S + "workload_executor.py", "WorkloadExecutor._preempt", "A CHARGE_DUE robot may cut the highest-SOC charge short", "PREEMPTED"),
        ],
    },
    {
        "id": "gazebo",
        "title": "Gazebo: two terminals",
        "intro": "Gazebo only draws what the simulator decided. The node runs the same WorkloadExecutor and paces it at 4x.",
        "steps": [
            (L + "gazebo.launch.py", "start_gazebo", "Terminal 1: gazebo.launch.py robots:=N", "launch actions"),
            (S + "gazebo_world.py", "stop_gazebo", "Stop a Gazebo already running this world", "one Gazebo"),
            (S + "gazebo_world.py", "fleet_poses", "Each robot's start cell, as the node will place it", "FleetPose list"),
            (S + "gazebo_world.py", "write_fleet_world", "The world plus N parked robots", "/tmp/mrta_gazebo_<uid>/warehouse.world"),
            (S + "gazebo_bridge.py", "robot_model", "The one robot model", "SDF"),
            (L + "warehouse_sim.launch.py", "generate_launch_description", "Terminal 2: orders:=true robots:=N strategy:=... order_limit:=N", "fleet_node with parameters"),
            (S + "fleet_node.py", "main", "Start the node; the run happens inside its constructor", "FleetNode"),
            (S + "fleet_node.py", "FleetNode.load_workload", "Load and limit the orders, the same rule as headless", "OrderQueue"),
            (S + "fleet_node.py", "FleetNode.place_in_gazebo", "Find the robots in Gazebo (create or remove only the difference)", "Gazebo already shows N robots"),
            (S + "gazebo_bridge.py", "FleetPoseSink", "Start the C++ pose transport process", "pose stream"),
            (S + "fleet_node.py", "FleetNode.run_order_workload", "The same WorkloadExecutor as headless, with Gazebo callbacks", "WorkloadResult"),
            (S + "fleet_node.py", "FleetNode.on_advance", "Each engine step: wait real time (4x) and draw", "paced frames"),
            (S + "fleet_node.py", "FleetNode._live_poses", "Where every robot is right now, mid-move included", "FleetPose list"),
            (S + "gazebo_bridge.py", "FleetPoseSink.send", "One FRAME line for all robots", "stdin of the C++ process"),
            ("src/gazebo_fleet_pose_transport/src/gazebo_fleet_pose_transport.cpp", None, "Set every pose in one Gazebo call", "/world/warehouse/set_pose_vector"),
            (S + "fleet_node.py", "FleetNode.on_tick", "Publish state at publish_hz", "/mrta/robot_states, /mrta/fleet_status"),
            (S + "fleet_node.py", "FleetNode.event", "Every assignment, completion and battery event", "/mrta/events"),
            (S + "fleet_node.py", "FleetNode.report_workload", "Short summary on the terminal, full report to a file", "~/.ros/mrta/<run>.txt"),
        ],
    },
    {
        "id": "experiment",
        "title": "Experiments, data and graphs",
        "intro": "How a comparison of strategies is produced, and where each file comes from.",
        "steps": [
            (SC + "run_experiment.py", "main", "--robots 30 40 50 --seeds 1 2 3 --export", "the run grid"),
            (SC + "run_experiment.py", "runs_for", "Every (strategy, robots, seed); deterministic strategies once", "run list"),
            (SC + "run_experiment.py", "run_one", "One run_workload.py per cell, in parallel; output to run.log", "runs/<name>/<strategy>/r<N>_s<seed>/"),
            (SC + "run_workload.py", "main", "The headless run, with --dataset", "decisions, outcomes, run.json, metrics.json"),
            (S + "dataset.py", "RunRecorder.decision", "One row per candidate pair at decision time", "decisions.parquet"),
            (S + "dataset.py", "RunRecorder.outcome", "One row per order when its robot retires", "outcomes.parquet"),
            (S + "run_metrics.py", "run_metrics", "Makespan, throughput, waiting, energy, charging...", "metrics.json"),
            (S + "dataset_export.py", "write_csv", "The tables as text", "decisions.csv, outcomes.csv"),
            (S + "dataset_export.py", "write_prolog", "Facts and pos/neg examples for ILP", "bk.pl, exs.pl"),
            (SC + "plot_experiment.py", "load", "Every run's metrics", "rows"),
            (SC + "plot_experiment.py", "metric_grid", "Line charts per metric vs robots", "figures/mrta.png, traffic.png, battery.png"),
            (SC + "plot_experiment.py", "completion_curves", "Orders completed over time", "figures/completion_curves.png"),
            (SC + "plot_experiment.py", "order_times", "Per-order duration and waiting", "figures/order_times.png"),
            (SC + "plot_experiment.py", "summary_table", "Every metric as a table", "summary.md"),
        ],
    },
]


# ----------------------------------------------------------------------
# Reading the source.
# ----------------------------------------------------------------------


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def first_paragraph(text, limit):
    if not text:
        return ""
    para = re.split(r"\n\s*\n", text.strip(), maxsplit=1)[0]
    para = " ".join(para.split())
    return para if len(para) <= limit else para[: limit - 1].rsplit(" ", 1)[0] + " ..."


def source_files():
    files = []
    for path in sorted(SRC.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        name = rel(path)
        if path.suffix == ".py":
            if path.name in ("__init__.py", "setup.py"):
                continue
            files.append(path)
        elif path.suffix in (".cpp", ".msg"):
            files.append(path)
        elif name in DATA_FILES:
            files.append(path)
    return files


def module_name(path: Path):
    for package, directory in PACKAGES.items():
        if directory in path.parents:
            return package + "." + path.stem
    return None


def layer_of(name: str) -> str:
    if name in ROLES:
        return ROLES[name][0]
    if LEGACY_SCRIPTS.search(name):
        return "legacy"
    if "/test/" in name or "/tests/" in name:
        return "test"
    if "/scripts/check_" in name:
        return "check"
    if name.startswith("src/rmf_grid_traffic/") or name.startswith("src/gazebo_fleet_pose_transport/"):
        return "ros"
    return "entry"


class Analyzer:
    """Symbols and static calls of every Python file."""

    def __init__(self, paths):
        self.trees = {rel(p): ast.parse(p.read_text(), filename=str(p)) for p in paths if p.suffix == ".py"}
        self.modules = {}
        for p in paths:
            mod = module_name(p)
            if mod:
                self.modules[mod] = rel(p)

        # name -> id, per file; and class -> {method: id}
        self.top = defaultdict(dict)
        self.methods = defaultdict(dict)
        self.classes = defaultdict(list)  # class name -> [file]
        self.bases = {}

        for name, tree in self.trees.items():
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.top[name][node.name] = f"{name}::{node.name}"
                elif isinstance(node, ast.ClassDef):
                    self.top[name][node.name] = f"{name}::{node.name}"
                    self.classes[node.name].append(name)
                    self.bases[(name, node.name)] = [b.id for b in node.bases if isinstance(b, ast.Name)]
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            self.methods[(name, node.name)][item.name] = f"{name}::{node.name}.{item.name}"

    def class_file(self, cls, prefer=None):
        files = [f for f in self.classes.get(cls, []) if "/test" not in f]
        if prefer in files:
            return prefer
        return files[0] if len(files) == 1 else None

    def method(self, file, cls, name):
        seen = set()
        while (file, cls) not in seen and file:
            seen.add((file, cls))
            found = self.methods[(file, cls)].get(name)
            if found:
                return found
            bases = self.bases.get((file, cls), [])
            if not bases:
                return None
            cls = bases[0]
            file = self.class_file(cls, prefer=file)
        return None

    def imports(self, name, tree):
        """alias -> ('module', file) or ('symbol', file, symbol)."""

        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                target = self.modules.get(node.module)
                for alias in node.names:
                    local = alias.asname or alias.name
                    sub = self.modules.get(f"{node.module}.{alias.name}")
                    if sub:
                        aliases[local] = ("module", sub)
                    elif target:
                        aliases[local] = ("symbol", target, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    target = self.modules.get(alias.name)
                    if target:
                        aliases[alias.asname or alias.name.split(".")[0]] = ("module", target)
        return aliases

    def resolve(self, name, aliases, cls, func):
        """The symbol id a call goes to, or None."""

        if isinstance(func, ast.Name):
            if func.id in self.top[name]:
                return self._callable(name, func.id)
            hit = aliases.get(func.id)
            if hit and hit[0] == "symbol":
                return self._callable(hit[1], hit[2])
            return None

        if not isinstance(func, ast.Attribute):
            return None

        owner = func.value
        attr = func.attr

        if isinstance(owner, ast.Name):
            if owner.id in ("self", "cls") and cls:
                return self.method(name, cls, attr)
            hit = aliases.get(owner.id)
            if hit and hit[0] == "module":
                return self._callable(hit[1], attr)
            if hit and hit[0] == "symbol":
                return self.method(hit[1], hit[2], attr)
            if owner.id in self.top[name] and owner.id in self.classes:
                return self.method(name, owner.id, attr)
            typed = ATTR_TYPES.get(owner.id)
        elif isinstance(owner, ast.Attribute):
            typed = ATTR_TYPES.get(owner.attr)
        else:
            typed = None

        if typed:
            file = self.class_file(typed)
            if file:
                return self.method(file, typed, attr)
        return None

    def _callable(self, file, symbol):
        if (file, symbol) in self.methods and "__init__" in self.methods[(file, symbol)]:
            return f"{file}::{symbol}"
        return self.top[file].get(symbol)

    def file_entry(self, name):
        tree = self.trees[name]
        aliases = self.imports(name, tree)
        symbols = []

        def calls_in(node, cls):
            out = []
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    target = self.resolve(name, aliases, cls, sub.func)
                    if target and target not in out:
                        out.append(target)
            return out

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append({
                    "id": f"{name}::{node.name}", "name": node.name, "kind": "function",
                    "line": node.lineno, "end": node.end_lineno,
                    "doc": first_paragraph(ast.get_docstring(node), 260),
                    "args": [a.arg for a in node.args.args if a.arg != "self"],
                    "calls": calls_in(node, None),
                })
            elif isinstance(node, ast.ClassDef):
                symbols.append({
                    "id": f"{name}::{node.name}", "name": node.name, "kind": "class",
                    "line": node.lineno, "end": node.end_lineno,
                    "doc": first_paragraph(ast.get_docstring(node), 260),
                    "bases": [b.id for b in node.bases if isinstance(b, ast.Name)],
                    "calls": [],
                })
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbols.append({
                            "id": f"{name}::{node.name}.{item.name}", "name": f"{node.name}.{item.name}",
                            "kind": "method", "line": item.lineno, "end": item.end_lineno,
                            "doc": first_paragraph(ast.get_docstring(item), 260),
                            "args": [a.arg for a in item.args.args if a.arg != "self"],
                            "calls": calls_in(item, node.name),
                        })

        imports = sorted({hit[1] for hit in aliases.values()} - {name})
        return {
            "doc": first_paragraph(ast.get_docstring(tree), 520),
            "imports": imports,
            "symbols": symbols,
        }


def other_entry(path: Path):
    text = path.read_text(errors="replace") if path.suffix in (".cpp", ".msg", ".yaml", ".csv", ".world") else ""
    doc = ""
    if path.suffix == ".cpp":
        lines = text.splitlines()
        # The licence header says nothing about the code.
        if lines and lines[0].startswith("// Copyright"):
            while lines and lines[0].startswith("//"):
                lines.pop(0)
        comments = re.findall(r"^\s*//\s?(.*)$", "\n".join(lines), flags=re.MULTILINE)[:12]
        doc = " ".join(c for c in comments if c.strip())
    elif path.suffix == ".msg":
        fields = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
        doc = "Fields: " + "; ".join(fields)
    return {"doc": first_paragraph(doc, 520), "imports": [], "symbols": []}


def line_count(path: Path):
    if path.suffix == ".xlsx":
        return None
    try:
        return sum(1 for _ in path.open(errors="replace"))
    except OSError:
        return None


def build():
    paths = source_files()
    analyzer = Analyzer(paths)
    files = []

    for path in paths:
        name = rel(path)
        entry = analyzer.file_entry(name) if path.suffix == ".py" else other_entry(path)
        kind = {"py": "python", "cpp": "cpp", "msg": "msg"}.get(path.suffix[1:], "data")
        if name.endswith(".launch.py"):
            kind = "launch"
        files.append({
            "id": name,
            "name": path.name,
            "dir": rel(path.parent),
            "layer": layer_of(name),
            "kind": kind,
            "lines": line_count(path),
            "role": ROLES.get(name, (None, ""))[1] or entry["doc"].split(". ")[0],
            **entry,
        })

    by_id = {f["id"]: f for f in files}
    symbols = {s["id"]: (f, s) for f in files for s in f["symbols"]}

    # Who calls whom, and which tests exercise what.
    for f in files:
        f["imported_by"] = []
    for f in files:
        for target in f["imports"]:
            if target in by_id:
                by_id[target]["imported_by"].append(f["id"])
    for sid, (f, s) in symbols.items():
        s["called_by"] = []
        s["tested_by"] = []
    for sid, (f, s) in symbols.items():
        for target in s["calls"]:
            if target not in symbols:
                continue
            _, callee = symbols[target]
            if f["layer"] == "test":
                if f["id"] not in callee["tested_by"]:
                    callee["tested_by"].append(f["id"])
            elif sid not in callee["called_by"]:
                callee["called_by"].append(sid)

    # Data files: who reads and writes them.
    for name, links in DATA_FILES.items():
        if name in by_id:
            # A link names a function, or a whole script that has none.
            known = lambda x: x in symbols or x in by_id
            for key in ("read_by", "written_by"):
                bad = [x for x in links[key] if not known(x)]
                if bad:
                    sys.exit(f"{name}: {key} names code that does not exist: {bad}")
                by_id[name][key] = list(links[key])

    # Flows: every step must point at real code.
    flows = []
    missing = []
    for flow in FLOWS:
        steps = []
        for file, symbol, text, makes in flow["steps"]:
            sid = f"{file}::{symbol}" if symbol else None
            if file not in by_id:
                missing.append(file)
            elif sid and sid not in symbols:
                missing.append(sid)
            steps.append({"file": file, "symbol": sid, "name": symbol or by_id.get(file, {}).get("name", file), "text": text, "makes": makes})
        flows.append({"id": flow["id"], "title": flow["title"], "intro": flow["intro"], "steps": steps})

    if missing:
        sys.exit("flow steps name code that does not exist:\n  " + "\n  ".join(missing))

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "src"], cwd=ROOT, capture_output=True, text=True).stdout.strip())

    return {
        "generated": date.today().isoformat(),
        "commit": commit + (" + uncommitted changes" if dirty else ""),
        "layers": [{"id": i, "title": t, "blurb": b} for i, t, b in LAYERS],
        "files": files,
        "flows": flows,
    }


# ----------------------------------------------------------------------
# XMind.
# ----------------------------------------------------------------------


def topic(title, children=(), notes=None, folded=False, key=None):
    node = {
        "id": hashlib.sha1((key or title).encode()).hexdigest()[:26],
        "class": "topic",
        "title": title,
    }
    if children:
        node["children"] = {"attached": list(children)}
    if notes:
        node["notes"] = {"plain": {"content": notes}}
    if folded:
        node["branch"] = "folded"
    return node


def xmind_sheets(data):
    layers = []
    for layer in data["layers"]:
        files = [f for f in data["files"] if f["layer"] == layer["id"]]
        if not files:
            continue
        file_topics = []
        for f in files:
            kids = []
            classes = [s for s in f["symbols"] if s["kind"] == "class"]
            for c in classes:
                methods = [s for s in f["symbols"] if s["kind"] == "method" and s["name"].startswith(c["name"] + ".")]
                kids.append(topic(
                    f"class {c['name']}",
                    [topic(m["name"].split(".", 1)[1] + "()", notes=m["doc"] or None, key=m["id"]) for m in methods],
                    notes=c["doc"] or None, folded=bool(methods), key=c["id"],
                ))
            for s in f["symbols"]:
                if s["kind"] == "function":
                    kids.append(topic(s["name"] + "()", notes=s["doc"] or None, key=s["id"]))
            notes = f["role"] + ("\n\n" + f["doc"] if f["doc"] and f["doc"] != f["role"] else "")
            notes += "\n\n" + f["id"]
            file_topics.append(topic(f["name"], kids, notes=notes, folded=bool(kids), key=f["id"]))
        layers.append(topic(layer["title"], file_topics, notes=layer["blurb"], folded=layer["id"] in ("test", "check", "legacy"), key="layer:" + layer["id"]))

    flows = []
    for flow in data["flows"]:
        steps = [
            topic(f"{i}. {step['text']}", [topic(f"→ {step['makes']}", key=f"{flow['id']}:{i}:makes")],
                  notes=f"{step['file']}" + (f" :: {step['symbol'].split('::')[1]}" if step["symbol"] else ""),
                  key=f"{flow['id']}:{i}")
            for i, step in enumerate(flow["steps"], 1)
        ]
        flows.append(topic(flow["title"], steps, notes=flow["intro"], folded=True, key="flow:" + flow["id"]))

    root_map = topic("MRTA workspace", layers, notes=f"Code map generated {data['generated']} from {data['commit']}", key="root:code")
    root_map["structureClass"] = "org.xmind.ui.map.unbalanced"
    root_flows = topic("Traced flows", flows, key="root:flows")
    root_flows["structureClass"] = "org.xmind.ui.logic.right"

    return [
        {"id": "sheet-code-map", "class": "sheet", "title": "Code map", "rootTopic": root_map},
        {"id": "sheet-flows", "class": "sheet", "title": "Flows", "rootTopic": root_flows},
    ]


def write_xmind(data, path):
    files = {
        "content.json": json.dumps(xmind_sheets(data), ensure_ascii=False),
        "metadata.json": json.dumps({"creator": {"name": "docs/tools/code_map.py", "version": "1"}}),
        "manifest.json": json.dumps({"file-entries": {"content.json": {}, "metadata.json": {}}}),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, text)


# ----------------------------------------------------------------------
# Markdown: the same map as a page to read.
# ----------------------------------------------------------------------


def anchor(file_id):
    return "f-" + re.sub(r"[^a-z0-9]+", "-", file_id.lower()).strip("-")


def cell(text):
    return " ".join((text or "").split()).replace("|", "\\|")


def source(file_id, line=None):
    """A link from docs/ to a file, or to one line of it."""
    return "../" + file_id + (f"#L{line}" if line else "")


def short(sid, here=None):
    """A symbol as a reader wants it: the bare name inside its own file,
    module.name elsewhere. A link may also name a whole file."""
    if "::" not in sid:
        return Path(sid).name
    file, name = sid.split("::", 1)
    return name if file == here else f"{Path(file).stem}.{name}"


def short_list(ids, here):
    return ", ".join(f"`{short(x, here)}`" for x in ids)


def symbol_link(sid, symbols):
    file = sid.split("::", 1)[0]
    line = symbols[sid]["line"] if sid in symbols else None
    return f"[`{short(sid)}`]({source(file, line)})"


def file_links(ids):
    return ", ".join(f"[{Path(i).name}](#{anchor(i)})" for i in ids)


def signature(s):
    if s["kind"] == "class":
        bases = f"({', '.join(s['bases'])})" if s.get("bases") else ""
        return f"**class** `{s['name']}{bases}`"
    return f"`{s['name']}({', '.join(a for a in s['args'] if a != 'cls')})`"


def write_markdown(data, path):
    files = data["files"]
    symbols = {s["id"]: s for f in files for s in f["symbols"]}
    layers = [(layer, [f for f in files if f["layer"] == layer["id"]]) for layer in data["layers"]]
    layers = [(layer, members) for layer, members in layers if members]
    calls = sum(len(s["calls"]) for s in symbols.values())

    out = []
    w = out.append

    w("# Code map")
    w("")
    w("Every source file, class and function under `src/`: what it does, what it calls, "
      "what calls it and which tests reach it; which code reads and writes each input "
      "file; and the traced flows from a command to its result.")
    w("")
    w(f"Generated {data['generated']} from commit `{data['commit']}` by "
      f"[tools/code_map.py](tools/code_map.py): {len(files)} files, {len(symbols)} classes "
      f"and functions, {calls} resolved calls, {len(data['flows'])} flows. **Do not edit "
      "this file by hand.** After any code change run `python3 docs/tools/code_map.py`; "
      "it rewrites this page, [code_map.json](code_map.json), [REPO_MAP.xmind](REPO_MAP.xmind) "
      "and the interactive code map in [REPO_ATLAS.html](REPO_ATLAS.html).")
    w("")
    w("How to read it:")
    w("")
    w("- A name links to its line in the source.")
    w("- In *Calls* and *Called by*, a bare name lives in the same file; "
      "`module.name` lives in that module.")
    w("- Names, line numbers and descriptions are the code's own, read with Python's `ast`. "
      "Calls are resolved statically, so a call through a variable whose type the code does "
      "not name is missing: *Called by* can be incomplete, never wrong.")
    w("")

    w("## Contents")
    w("")
    w("1. [Layers: which file sits where](#layers)")
    w("2. [Input files: which code reads and writes them](#data-files)")
    w("3. [Traced flows](#flows)")
    for i, flow in enumerate(data["flows"], 1):
        w(f"   {i}. [{flow['title']}](#flow-{flow['id']})")
    w("4. [Files, layer by layer](#files)")
    for i, (layer, members) in enumerate(layers, 1):
        w(f"   {i}. [{layer['title']}](#layer-{layer['id']}) ({len(members)} files)")
    w("")

    w('<a id="layers"></a>')
    w("")
    w("## 1. Layers: which file sits where")
    w("")
    w("From the inputs at the top down to the domain core, then recording, checks, tests "
      "and legacy code.")
    w("")
    w("| Layer | What it holds | Files |")
    w("|---|---|---|")
    for layer, members in layers:
        w(f"| [**{layer['title']}**](#layer-{layer['id']}) | {cell(layer['blurb'])} | {file_links(f['id'] for f in members)} |")
    w("")

    w('<a id="data-files"></a>')
    w("")
    w("## 2. Input files: which code reads and writes them")
    w("")
    w("| File | What it is | Read by | Written by |")
    w("|---|---|---|---|")
    for f in files:
        if "read_by" not in f:
            continue
        read = ", ".join(symbol_link(x, symbols) for x in f["read_by"]) or "nothing"
        written = ", ".join(symbol_link(x, symbols) for x in f["written_by"]) or "no code"
        w(f"| [{f['name']}]({source(f['id'])}) | {cell(f['role'])} | {read} | {written} |")
    w("")

    w('<a id="flows"></a>')
    w("")
    w("## 3. Traced flows")
    w("")
    w("Each flow follows one path through the code, in the order it runs. Every step names "
      "a real file and function; the generator stops if one no longer exists.")
    w("")
    for i, flow in enumerate(data["flows"], 1):
        w(f'<a id="flow-{flow["id"]}"></a>')
        w("")
        w(f"### 3.{i} {flow['title']}")
        w("")
        w(cell(flow["intro"]))
        w("")
        w("| # | What happens | Code | Produces |")
        w("|---|---|---|---|")
        for n, step in enumerate(flow["steps"], 1):
            if step["symbol"]:
                code = symbol_link(step["symbol"], symbols)
            else:
                code = f"[`{Path(step['file']).name}`]({source(step['file'])})"
            w(f"| {n} | {cell(step['text'])} | {code} | {cell(step['makes'])} |")
        w("")

    w('<a id="files"></a>')
    w("")
    w("## 4. Files, layer by layer")
    w("")
    for i, (layer, members) in enumerate(layers, 1):
        w(f'<a id="layer-{layer["id"]}"></a>')
        w("")
        w(f"### 4.{i} {layer['title']}")
        w("")
        w(cell(layer["blurb"]))
        w("")
        for f in members:
            w(f'<a id="{anchor(f["id"])}"></a>')
            w("")
            w(f"#### {f['name']}")
            w("")
            lines = f" · {f['lines']} lines" if f["lines"] else ""
            w(f"[`{f['id']}`]({source(f['id'])}){lines}")
            w("")
            # A role taken from the docstring is its first sentence: show the whole.
            if f["doc"] and f["doc"].startswith(f["role"]):
                w(cell(f["doc"]))
                w("")
            else:
                if f["role"]:
                    w(cell(f["role"]))
                    w("")
                if f["doc"]:
                    w(f"> {cell(f['doc'])}")
                    w("")
            facts = []
            if f["imports"]:
                facts.append(("Tests" if f["layer"] == "test" else "Imports", file_links(f["imports"])))
            if f["imported_by"]:
                facts.append(("Imported by", file_links(f["imported_by"])))
            if "read_by" in f:
                facts.append(("Read by", ", ".join(symbol_link(x, symbols) for x in f["read_by"]) or "nothing"))
                facts.append(("Written by", ", ".join(symbol_link(x, symbols) for x in f["written_by"]) or "no code"))
            for label, text in facts:
                w(f"- **{label}:** {text}")
            if facts:
                w("")
            if not f["symbols"]:
                continue
            if f["layer"] == "test":
                tests = [s for s in f["symbols"] if s["kind"] != "class"]
                w(f"<details><summary>{len(tests)} tests</summary>")
                w("")
                w(", ".join(f"[`{s['name']}`]({source(f['id'], s['line'])})" for s in tests))
                w("")
                w("</details>")
                w("")
                continue
            w("| Symbol | What it does | Calls | Called by | Tested by |")
            w("|---|---|---|---|---|")
            for s in f["symbols"]:
                name = f"[{signature(s)}]({source(f['id'], s['line'])})"
                tested = ", ".join(Path(t).name for t in s["tested_by"])
                w(f"| {name} | {cell(s['doc'])} | {short_list(s['calls'], f['id'])} | {short_list(s['called_by'], f['id'])} | {tested} |")
            w("")

    path.write_text("\n".join(out) + "\n")


# ----------------------------------------------------------------------
# The atlas data block.
# ----------------------------------------------------------------------

BLOCK = re.compile(r'(<script id="codemap-data" type="application/json">)(.*?)(</script>)', re.DOTALL)


def inject(data, atlas):
    text = atlas.read_text()
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    if not BLOCK.search(text):
        sys.exit(f"{atlas}: no <script id=\"codemap-data\"> block to fill")
    atlas.write_text(BLOCK.sub(lambda m: m.group(1) + payload + m.group(3), text, count=1))


def main() -> int:
    data = build()
    JSON_OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    write_xmind(data, XMIND_OUT)
    write_markdown(data, MD_OUT)
    if ATLAS.exists() and "codemap-data" in ATLAS.read_text():
        inject(data, ATLAS)
        print(f"  refreshed {rel(ATLAS)}")

    symbols = sum(len(f["symbols"]) for f in data["files"])
    calls = sum(len(s["calls"]) for f in data["files"] for s in f["symbols"])
    print(f"  {len(data['files'])} files, {symbols} classes and functions, {calls} resolved calls, {len(data['flows'])} flows")
    print(f"  wrote {rel(JSON_OUT)}, {rel(MD_OUT)} and {rel(XMIND_OUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
