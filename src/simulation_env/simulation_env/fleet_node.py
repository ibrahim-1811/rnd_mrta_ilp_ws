"""ROS 2 node that deploys the fleet and publishes its state.

Builds every robot from configuration, places it on its staging
station, mirrors the poses into Gazebo once, and publishes the whole
fleet on /mrta/robot_states at a configured rate.

State is owned by GridRobot and is never read back from Gazebo.
The selected FIFO/MRTA workload is executed here.

The terminal stays short: a header, one status line every
progress_interval_s simulated seconds, warnings, and a summary at the
end, with the full report written to a file. The detail is on topics:

    /mrta/robot_states   every robot: cell, status, battery, current
                         order, charging state
    /mrta/fleet_status   order and robot counts, chargers, SOC, who is
                         charging and who waits for a charger
    /mrta/events         one line per assignment, completed order and
                         battery event

verbose:=true prints the full trace to the terminal as well.
"""

from dataclasses import replace
from datetime import datetime
import os
import subprocess
from math import ceil
from pathlib import Path
from time import perf_counter, sleep
import traceback

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)

from robot_interfaces.msg import FleetStatus, RobotState, RobotStateArray
from std_msgs.msg import String

from mrta_core.order_queue import OrderQueue
from mrta_core.orders import describe_order_selection, load_orders_csv, select_orders
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import HOME_TYPES
from simulation_env.battery_ledger import battery_report_lines
from simulation_env.charging import ChargeState, ResourceState, WAITING_STATES
from simulation_env.dataset import RunRecorder, build_manifest
from simulation_env.failures import (
    EXECUTION_ERROR,
    classify,
    failure_record,
    write_failure,
)
from simulation_env.playback import Player
from simulation_env.run_metrics import run_metrics, write_metrics
from simulation_env.fleet import build_fleet, home_stations
from simulation_env.grid_robot import GridRobotStatus
from simulation_env.workload_executor import (
    WorkloadExecutor,
    battery_event_line,
    charging_report_lines,
    eligibility_report_lines,
)
from simulation_env.gazebo_bridge import (
    DEFAULT_PLAYBACK_HZ,
    DEFAULT_ROBOT_Z,
    FleetPose,
    FleetPoseSink,
    create_robots,
    fleet_changes,
    provider_count,
    remove_robot,
    robot_models,
)
from simulation_env.gazebo_view import grid_to_gazebo


DEFAULT_CONFIG = "src/simulation_env/config/warehouse.yaml"

# How much of the FIFO workload to echo at startup (verbose only).
PREVIEW_ORDERS = 18

# A charge record in one of these states is no longer in the workflow.
OUT_OF_WORKFLOW = {ChargeState.REJOINED, ChargeState.PROMOTED, ChargeState.PREEMPTED}

# Battery events worth a terminal line even when not verbose.
ALERT_EVENTS = {"OUT_OF_REACH"}


class FleetNode(Node):
    def __init__(self) -> None:
        super().__init__("mrta_fleet")

        self.declare_parameter("config_file", DEFAULT_CONFIG)

        self.config_path = str(self.get_parameter("config_file").value)

        self.config = load_sim_config(self.config_path)

        # Another order file for this run only, as run_workload.py's
        # --orders-file (e.g. an experiment's workloads/orders_s116.csv), so a
        # Gazebo run can replay exactly a recorded headless run. Empty keeps
        # the config's order file.
        self.declare_parameter("orders_file", "")
        orders_file = str(self.get_parameter("orders_file").value).strip()
        if orders_file:
            path = Path(orders_file).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"orders_file {orders_file}: no such file")
            self.config = replace(self.config, orders_path=path)

        # Fleet size for this run. 0 keeps fleet.robot_count; a positive
        # value overrides it. Gazebo is then given exactly these robots
        # when the run starts, so one config and one world serve every
        # fleet size on the map.
        self.declare_parameter("robots", 0)

        robots = int(self.get_parameter("robots").value)

        if robots > 0:
            self.config = replace(self.config, robot_count=robots)

        config = self.config

        self.declare_parameter("use_gazebo", config.gazebo_enabled)
        self.declare_parameter("publish_hz", config.robot_state_publish_hz)
        self.declare_parameter("wait_for_gazebo", 0.0)
        self.declare_parameter("gazebo_playback_hz", DEFAULT_PLAYBACK_HZ)
        # Smooth playback (display only): the simulation computes up to this many
        # wall seconds ahead while a separate thread draws the frames, so the
        # robots no longer freeze while an allocation decision is computed.
        # 0 draws on the simulation thread, as before.
        self.declare_parameter("playback_buffer_s", 30.0)
        # strategy ilp: explain up to this many picks of each decision on the
        # terminal (the rule that fired and why); 0 turns it off.
        self.declare_parameter("explain", 2)
        self.declare_parameter("load_orders", False)
        self.declare_parameter("run_workload", False)
        self.declare_parameter("strategy", "nearest_astar")
        # 0 keeps the configured speed; any positive value
        # overrides it, so a run can be re-timed without
        # editing the YAML.
        self.declare_parameter("speed_mps", 0.0)
        # Simulated seconds between terminal status lines.
        self.declare_parameter("progress_interval_s", 60.0)
        # Print the full trace (every assignment, robot event, battery
        # event and per-robot table) to the terminal too.
        self.declare_parameter("verbose", False)
        # Allocation candidate window. Empty keeps the configured
        # allocation.window, so a launch never silently changes a
        # scientific parameter the YAML is authoritative for.
        self.declare_parameter("window", "")
        # Dispatch quantum. Negative keeps the configured
        # allocation.dispatch_quantum_s, so 0.0 stays a usable override
        # meaning "continuous dispatch" rather than "unset".
        self.declare_parameter("dispatch_quantum_s", -1.0)
        # Phase 8 dataset. Empty = do not record. The live path records
        # through the same recorder as the headless path, because there
        # is one scientific engine and it may not have two datasets.
        self.declare_parameter("dataset", "")
        # Run only the first N logical orders of the order file, never
        # splitting one; 0 runs them all. The same rule as the headless
        # runner's --orders N. The order file is never rewritten.
        self.declare_parameter("order_limit", 0)
        # Allocation seed for stochastic strategies. Negative keeps the
        # configured simulation.seed.
        self.declare_parameter("allocation_seed", -1)
        # A learned rule set for strategy "ilp", exactly as run_workload.py's
        # --rule-artifact / --rule-matching; empty for every other strategy.
        self.declare_parameter("rule_artifact", "")
        self.declare_parameter("rule_matching", "")

        def get(name):
            return self.get_parameter(name).value

        self.use_gazebo = bool(get("use_gazebo"))
        self.publish_hz = float(get("publish_hz"))
        self.wait_for_gazebo = float(get("wait_for_gazebo"))
        self.gazebo_playback_hz = float(get("gazebo_playback_hz"))
        self.playback_buffer_s = float(get("playback_buffer_s"))
        self.explain = int(get("explain"))
        self.player = None
        self.allocator = None
        self._explained = 0
        self.load_orders = bool(get("load_orders"))
        self.run_workload = bool(get("run_workload"))
        self.strategy_name = str(get("strategy"))
        self.rule_artifact = str(get("rule_artifact")).strip()
        self.rule_matching = str(get("rule_matching")).strip()
        self.progress_interval_s = float(get("progress_interval_s"))
        self.verbose = bool(get("verbose"))

        window = str(get("window")).strip()
        self.window = window or config.allocation_window

        quantum = float(get("dispatch_quantum_s"))
        self.dispatch_quantum_s = (
            config.dispatch_quantum_s if quantum < 0.0 else quantum
        )

        self.dataset_dir = str(get("dataset")).strip()
        self.order_limit = int(get("order_limit"))
        self.orders_available = 0

        seed = int(get("allocation_seed"))
        self.allocation_seed = config.seed if seed < 0 else seed

        override = float(get("speed_mps"))
        self.speed_mps = override if override > 0.0 else config.speed_mps

        self.world = config.gazebo_world_name
        self.workload: WorkloadExecutor | None = None
        self._next_publish = 0.0
        self.resolution = config.resolution_m
        self.robot_z = DEFAULT_ROBOT_Z
        self.gazebo_sink: FleetPoseSink | None = None

        qos = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )

        self.states_pub = self.create_publisher(
            RobotStateArray,
            config.robot_states_topic,
            qos,
        )

        # Counts at a glance; latched like the robot states, so an echo
        # started late still gets the latest.
        self.status_pub = self.create_publisher(FleetStatus, "/mrta/fleet_status", qos)

        # A stream of one-line events. Not latched: it is a log.
        self.events_pub = self.create_publisher(String, "/mrta/events", 100)

        self.warehouse = load_warehouse_xlsx(config.map_path)

        self.robots = build_fleet(config, self.warehouse, self.speed_mps)

        # Spawn positions only. Final parking is the nearest free
        # Stage Zone, decided at return time by StageParking.
        self.spawn_stations = home_stations(config, self.warehouse)

        self.summary()

        # The whole workload is known at startup; nothing about it
        # depends on the clock.
        self.orders = self.load_workload() if self.load_orders else None

        if self.use_gazebo:
            self.place_in_gazebo()

        self.publish_fleet()

        if self.run_workload and self.orders is not None:
            self.run_order_workload()

        period = 1.0 / max(self.publish_hz, 1e-6)

        self.create_timer(period, self.publish_fleet)

        self.get_logger().info(
            f"fleet of {len(self.robots)} publishing on "
            f"{config.robot_states_topic} at {self.publish_hz} Hz"
        )

    # -- reporting --------------------------------------

    def summary(self) -> None:

        warehouse = self.warehouse

        grid = warehouse.grid

        counts = {
            zone_type: len(warehouse.zones_of_type(zone_type))
            for zone_type in (
                ZoneType.SHELF,
                ZoneType.DROPOFF,
                ZoneType.CHARGING,
                ZoneType.CHARGE_QUEUE,
                *HOME_TYPES,
            )
            if warehouse.zones_of_type(zone_type)
        }

        if not self.verbose:
            stations = ", ".join(f"{count} {kind.value}" for kind, count in counts.items())
            print()
            print("=" * 74)
            print(f"MRTA WAREHOUSE FLEET  |  {len(self.robots)} robots  |  {self.speed_mps} m/s")
            print("=" * 74)
            print(f"config : {self.config_path}")
            print(f"map    : {grid.width} x {grid.height}, {stations}")
            print(
                "topics : /mrta/robot_states  /mrta/fleet_status  /mrta/events"
                "  (ros2 topic echo <topic>)"
            )
            self.checks(counts)
            return

        print()
        print("=" * 62)
        print("MRTA WAREHOUSE FLEET")
        print("=" * 62)
        print()
        print(f"Config:\n{self.config_path}")
        print()
        print(f"Map:\n{grid.width} x {grid.height}")
        print()
        print("Stations:")
        for zone_type, count in counts.items():
            print(f"{zone_type.value:<9s} = {count}")
        print()
        print(f"Blocked:\n{len(grid.obstacles)}")
        print()
        print(f"Fleet:\n{len(self.robots)} robots")
        print()
        print(f"Speed:\n{self.speed_mps} m/s")
        print()
        print("Initial positions:")
        print()

        for robot in self.robots:
            world_x, world_y = self._world_xy(robot.cell)

            print(
                f"{robot.robot_id} -> "
                f"{self.spawn_stations[robot.robot_id]} -> "
                f"cell={robot.cell} "
                f"gazebo=({world_x:.1f}, {world_y:.1f})"
            )

        self.checks(counts)

    def checks(self, counts) -> None:

        warehouse = self.warehouse

        grid_map = warehouse.grid.grid_map()

        robots = self.robots

        ids = {robot.robot_id for robot in robots}
        cells = {robot.cell for robot in robots}

        expected = self.config.robot_count

        results = [
            (f"{expected} unique robots", len(ids) == expected),
            (
                f"{expected} unique home cells",
                len(cells) == expected,
            ),
            (
                "no robot on obstacle",
                all(robot.cell not in warehouse.grid.obstacles for robot in robots),
            ),
            (
                "all robot positions in bounds",
                all(grid_map.in_bounds(robot.cell) for robot in robots),
            ),
            (
                "all robots on home cells",
                all(
                    warehouse.zone_type_at(robot.cell) in HOME_TYPES for robot in robots
                ),
            ),
            (
                "all robots IDLE at t=0",
                all(
                    robot.status.value == "IDLE"
                    and robot.sim_time == 0.0
                    and robot.distance_m == 0.0
                    for robot in robots
                ),
            ),
        ]

        failed = [label for label, ok in results if not ok]

        if self.verbose or failed:
            print()

            for label, ok in results:
                print(f"[{'OK' if ok else 'FAIL'}] {label}")
        else:
            print(f"[OK] fleet checks: {len(results)}/{len(results)} passed")

        if failed:
            raise RuntimeError(f"fleet initialisation checks failed: {failed}")

    # -- outputs ----------------------------------------

    def place_in_gazebo(self) -> None:

        deadline = perf_counter() + self.wait_for_gazebo

        providers = provider_count(self.world, "set_pose_vector")

        while providers == 0 and perf_counter() < deadline:
            sleep(0.5)
            providers = provider_count(self.world, "set_pose_vector")

        if providers == 0:
            self.get_logger().warn(
                f"/world/{self.world}/set_pose_vector not found; "
                f"continuing without Gazebo"
            )
            self.use_gazebo = False
            return

        if providers > 1:
            self.get_logger().error(
                f"{providers} gz sim servers advertise world "
                f"'{self.world}'; poses would be split between them. "
                f"Leave exactly one running."
            )
            self.use_gazebo = False
            return

        starts = [
            FleetPose(robot.robot_id, *self._world_xy(robot.cell), DEFAULT_ROBOT_Z)
            for robot in self.robots
        ]

        # The world holds the warehouse only. Make Gazebo show exactly
        # this run's fleet: create the robots it lacks, remove any left
        # over from an earlier, larger run.
        try:
            create, remove = fleet_changes(robot_models(self.world), starts)

            for name in remove:
                remove_robot(self.world, name)

            create_robots(self.world, create)

        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            self.get_logger().warn(
                f"could not set up the robots in Gazebo: {error}; "
                "continuing without Gazebo"
            )
            self.use_gazebo = False
            return

        if create or remove:
            print(
                f"[OK] Gazebo fleet set to {len(self.robots)} robot(s): "
                f"{len(create)} created, {len(remove)} removed"
            )
        else:
            print(f"[OK] Gazebo already shows these {len(self.robots)} robot(s)")

        # Every robot is our own model at DEFAULT_ROBOT_Z, parked by
        # gazebo.launch.py or created above. Asking Gazebo for the
        # height back (gz model -p) cost about 10 s per run for the
        # same answer, so it is not asked.

        try:
            self.gazebo_sink = FleetPoseSink(self.world)
        except OSError as error:
            self.get_logger().warn(
                f"persistent Gazebo fleet transport unavailable: {error}; "
                "continuing without Gazebo"
            )
            self.use_gazebo = False
            return

        initial = []

        for robot in self.robots:
            world_x, world_y = self._world_xy(robot.cell)
            initial.append(
                FleetPose(
                    robot.robot_id,
                    world_x,
                    world_y,
                    self.robot_z,
                )
            )

        self._emit_gazebo_poses(0.0, initial)

        print()
        print(
            f"[OK] {len(self.robots)} robot poses pushed to Gazebo "
            f"world '{self.world}' as one persistent fleet stream"
        )

    def to_msg(self, robot) -> RobotState:

        world_x, world_y = self._world_xy(robot.cell)

        msg = RobotState()

        msg.robot_id = robot.robot_id
        msg.sim_time = float(robot.sim_time)
        msg.grid_x = int(robot.cell[0])
        msg.grid_y = int(robot.cell[1])
        msg.world_x = float(world_x)
        msg.world_y = float(world_y)
        msg.status = robot.status.value
        msg.current_target = ""
        msg.current_order = ""
        msg.charge_state = ""
        msg.battery_percent = self._soc_percent(robot.robot_id)
        msg.speed_mps = float(robot.speed_mps)
        msg.distance_m = float(robot.distance_m)

        executor = self.workload

        if executor is not None:
            msg.current_order = str(executor.active.get(robot.robot_id) or "")

            record = executor.charge.get(robot.robot_id)

            if record is not None and record.state not in OUT_OF_WORKFLOW:
                msg.charge_state = record.state.value

        return msg

    def _soc_percent(self, robot_id: str) -> float:
        """Live SOC from the engine's battery ledger. Display only.

        Before the workload engine exists every robot is at its
        configured initial SOC. With battery accounting disabled the
        configured initial value is shown and never changes.
        """

        engine = getattr(self, "engine", None)

        if engine is not None and engine.battery is not None:
            return float(engine.battery.soc(robot_id, engine.time))

        return float(self.config.battery.initial_soc_for(robot_id))

    def publish_fleet(self) -> None:

        msg = RobotStateArray()

        msg.robots = [self.to_msg(r) for r in self.robots]

        self.states_pub.publish(msg)

        if self.workload is not None:
            self.status_pub.publish(self.fleet_status())

    def fleet_status(self) -> FleetStatus:
        """The run at a glance, for /mrta/fleet_status. Reporting only."""

        executor = self.workload
        progress = executor.snapshot()
        msg = FleetStatus()

        msg.sim_time = float(progress.sim_time)
        msg.strategy = self.strategy_name
        msg.orders_total = progress.total
        msg.orders_pending = progress.pending
        msg.orders_assigned = progress.assigned
        msg.orders_active = progress.in_progress
        msg.orders_completed = progress.completed
        msg.orders_failed = progress.failed
        msg.robots_total = len(self.robots)
        msg.robots_busy = progress.busy
        msg.robots_available = progress.available
        msg.robots_returning = progress.returning
        msg.robots_parked = progress.parked

        workflow = {
            robot_id: record.state
            for robot_id, record in executor.charge.items()
            if record.state not in OUT_OF_WORKFLOW
        }

        msg.robots_in_charging = len(workflow)
        msg.charging_robots = sorted(r for r, s in workflow.items() if s is ChargeState.CHARGING)
        msg.waiting_robots = sorted(r for r, s in workflow.items() if s in WAITING_STATES)
        msg.robots_charging = len(msg.charging_robots)
        msg.robots_waiting_for_charger = len(msg.waiting_robots)

        socs = [self._soc_percent(robot.robot_id) for robot in self.robots]
        msg.soc_min = min(socs)
        msg.soc_mean = sum(socs) / len(socs)

        slots = executor.chargers.slots.values()
        msg.chargers_total = len(slots)
        msg.chargers_free = sum(1 for slot in slots if slot.state is ResourceState.FREE)
        msg.charge_sessions = executor.result.charge_sessions
        msg.promotions = executor.result.promotions
        msg.saturation_events = executor.result.saturation_events

        return msg

    def event(self, line: str, *, alert: bool = False) -> None:
        """One event: always on /mrta/events, on the terminal if verbose."""

        self.events_pub.publish(String(data=line.strip()))

        if self.verbose or alert:
            self.say(line)

    def say(self, line: str) -> None:
        """A terminal line, in step with the Gazebo playback when it is buffered."""
        if self.player is not None:
            self.player.say(line)
        else:
            print(line, flush=True)

    def load_workload(self) -> OrderQueue | None:
        """Load the configured deterministic FIFO workload.

        The OrderQueue is the authoritative workload state.
        Every configured order is loaded PENDING in deterministic FIFO
        sequence.
        """

        try:
            available = load_orders_csv(self.config.orders_path, self.warehouse)
            queue = OrderQueue(select_orders(available, self.order_limit))
        except (OSError, ValueError) as error:
            self.get_logger().error(f"orders not loaded: {error}")
            return None

        self.orders_available = len(available)

        # Requested and actual, in the same words as the headless runner.
        selection = describe_order_selection(
            self.order_limit, available, tuple(queue), self.config.orders_path.name
        )

        if not self.verbose:
            print(f"orders : {selection}")
            return queue

        print()
        print("=" * 62)
        print("ORDERS")
        print("=" * 62)
        print(f"file          : {self.config.orders_path}")
        print(f"selection     : {selection}")
        print(f"orders        : {len(queue)}")
        print(f"line items    : {queue.line_item_count}")
        print(f"pending       : {len(queue.pending())}")
        print()

        # FIFO sequence, so the head of this listing is the head of the
        # queue. Long workloads are summarised rather than dumped.
        for order in queue.orders[:PREVIEW_ORDERS]:
            print(
                f"  {order.order_id}  {len(order.items)} item(s) -> "
                f"{order.dropoff_id}  [{', '.join(order.shelf_ids)}]"
            )

        remaining = len(queue) - PREVIEW_ORDERS

        if remaining > 0:
            print(f"  ... {remaining} more, in FIFO sequence")

        print()

        return queue

    def run_order_workload(self) -> None:
        """Execute the whole FIFO workload with a real MRTA allocator.

        FIFO decides which orders are eligible, ``task_management``
        decides which robot takes which of them, and everything below
        is the existing execution stack unchanged.
        """

        from task_management.allocation_strategy import (
            get_batch_strategy,
        )

        config = self.config

        try:
            # Only the learned strategy takes these, so every other strategy
            # is constructed exactly as before (run_workload.py does the same).
            options = {}
            if self.strategy_name == "ilp":
                if not self.rule_artifact:
                    raise ValueError("strategy ilp needs rule_artifact:=<rule_artifact.json>")
                options["rule_artifact"] = self.rule_artifact
                if self.rule_matching:
                    options["matching"] = self.rule_matching
            elif self.rule_artifact or self.rule_matching:
                raise ValueError("rule_artifact and rule_matching are for strategy ilp")
            allocator = get_batch_strategy(
                self.strategy_name, seed=self.allocation_seed, **options
            )
        except (KeyError, ValueError) as error:
            self.get_logger().error(str(error))
            return

        print()
        print("=" * 74)
        print(
            f"{len(self.orders)}-ORDER WORKLOAD  |  {len(self.robots)} robots  |  "
            f"strategy={allocator.name}"
        )
        print("=" * 74)

        if self.verbose:
            print(f"orders     : {len(self.orders)}")
            print(f"line items : {self.orders.line_item_count}")
            print(f"speed      : {self.speed_mps} m/s")
        else:
            print(
                f"one status line every {self.progress_interval_s:g} simulated s; "
                "details: ros2 topic echo /mrta/fleet_status"
            )

        print()

        # Gazebo renders exactly what the engine executes.
        #
        # FleetTrajectoryPlayback is deliberately NOT used here. It
        # replays pre-baked timed trajectories, and it is fed through
        # the on_trajectory callback -- which WorkloadExecutor never
        # calls, because the reactive traffic layer decides each move
        # one cell at a time instead of planning a full space-time
        # trajectory up front.
        #
        # Creating it anyway used to freeze the visualisation: it held
        # no tracks, so it reported every robot parked on its staging
        # cell forever, while also suppressing the fallback emitter in
        # on_tick. The terminal showed the fleet working and Gazebo
        # showed eighteen robots standing still.
        #
        # _live_poses() reads the engine's own in-flight moves instead,
        # so the picture cannot drift from the simulation.

        # Built from the inputs only, before the run, so the run_id
        # names what is about to be measured.
        manifest = None
        recorder = None

        if self.dataset_dir:
            manifest = build_manifest(
                config,
                strategy=self.strategy_name,
                strategy_identity=allocator.identity(),
                window=self.window,
                window_width=WorkloadExecutor.resolve_window(
                    self.window, len(self.robots)
                ),
                seed=self.allocation_seed,
                speed_mps=self.speed_mps,
                dispatch_quantum_s=self.dispatch_quantum_s,
                orders=tuple(self.orders),
                order_limit=self.order_limit,
                orders_available=self.orders_available,
                repo=Path(__file__).resolve().parents[3],
            )

            recorder = RunRecorder(run_id=manifest["run_id"])

            self.get_logger().info(
                f"recording dataset: {self.dataset_dir} "
                f"(run_id {manifest['run_id']})"
            )

            if manifest["git"]["dirty"]:
                self.get_logger().warning(
                    "dirty tree: this run is not reproducible"
                )

        executor = WorkloadExecutor(
            warehouse=self.warehouse,
            robots=self.robots,
            queue=self.orders,
            allocator=allocator,
            resolution=config.resolution_m,
            speed_mps=self.speed_mps,
            service_seconds={
                ZoneType.SHELF: config.shelf_pick_s,
                ZoneType.DROPOFF: config.dropoff_s,
            },
            on_advance=self.on_advance,
            on_tick=self.on_tick,
            on_dispatch=self.announce_dispatch,
            on_order_status=self.announce_order_status,
            on_progress=self.announce_progress,
            progress_interval_s=self.progress_interval_s,
            on_perf=self.announce_perf,
            battery=config.battery,
            on_battery_event=lambda event: self.event(
                battery_event_line(event), alert=event.kind in ALERT_EVENTS
            ),
            window=self.window,
            dispatch_quantum_s=self.dispatch_quantum_s,
            recorder=recorder,
            strategy_name=self.strategy_name,
        )

        self.engine = executor.engine
        self.workload = executor
        self.allocator = allocator

        if self.use_gazebo and self.gazebo_sink is not None and self.playback_buffer_s > 0.0:
            self.player = Player(
                lambda poses: self._emit_gazebo_poses(0.0, poses),
                say=lambda line: print(line, flush=True),
                buffer_s=self.playback_buffer_s,
                hz=self.gazebo_playback_hz,
            )

        started = perf_counter()

        try:
            result = executor.run()
            self._finish_playback()

        except Exception as error:
            self._finish_playback()
            # The same classification as the headless runner, and the
            # same failure.json when a dataset was asked for.
            kind = classify(error)
            self.get_logger().error(f"workload failed [{kind}]: {error}")

            if kind == EXECUTION_ERROR:
                self.get_logger().error(traceback.format_exc())

            if self.dataset_dir:
                write_failure(
                    self.dataset_dir, failure_record(error, executor, manifest)
                )

            return

        wall = perf_counter() - started

        # The last state of the run, whatever the publish throttle did.
        self.publish_fleet()

        run_dir = None

        if recorder is not None:
            run_dir = recorder.write(self.dataset_dir, manifest)

            write_metrics(
                run_dir,
                run_metrics(
                    executor,
                    strategy=self.strategy_name,
                    seed=self.allocation_seed,
                    wall_s=wall,
                ),
            )

        self.report_workload(executor, result, wall, run_dir)

        if run_dir is not None:
            print(
                f"  dataset           : {run_dir} ({len(recorder.decisions)} "
                f"decision rows, {len(recorder.outcomes)} outcome rows, metrics.json)"
            )
            print()

    def announce_perf(self, budget) -> None:
        """Report a dispatch that stalled the loop. CPU time only."""

        if self.verbose:
            for line in budget.lines():
                print(line, flush=True)

    def announce_progress(self, progress) -> None:
        """One status line; the full block only when verbose."""

        if self.verbose:
            for line in progress.lines():
                print(line, flush=True)
            return

        status = self.fleet_status()
        robots = self.engine.robots.values()
        waiting = sum(r.waiting_time_s for r in robots)
        driven = sum(r.distance_m for r in robots) / 1000.0
        done = max(progress.completed, 1)

        lines = [
            f"[t={progress.sim_time:7.0f} s] orders {progress.completed}/"
            f"{progress.total} done, {progress.in_progress + progress.assigned} "
            f"active, {progress.pending} pending | robots {progress.busy} busy, "
            f"{progress.available} free, {status.robots_in_charging} charging or "
            f"queued | SOC min {status.soc_min:.1f}% mean {status.soc_mean:.1f}%",
            f"             decisions {self.workload.result.decisions} | driven {driven:.1f} km "
            f"| traffic waiting {waiting:,.0f} s in total ({waiting / done:.1f} s per delivered order)",
        ]
        trace = getattr(self.allocator, "rule_trace", None)
        if trace:
            by_rule = sum(1 for row in trace if row["matched_by_rule"])
            lines.append(
                f"             ILP picks so far: {by_rule} by learned rules ({by_rule / len(trace):.0%}), "
                f"{len(trace) - by_rule} by the fallback ({(len(trace) - by_rule) / len(trace):.0%})"
            )
        for line in lines:
            self.say(line)

    def announce_dispatch(self, record) -> None:
        """One robot-order assignment, as it is dispatched."""

        shelves = ", ".join(record.shelf_sequence)

        self.event(
            f"  t={record.sim_time:8.3f}  ASSIGN  {record.robot_id} "
            f"-> {record.order_id}  C_static={record.static_cost_m:6.1f} m"
            f"  [{shelves}] -> {record.dropoff_id}"
        )

    def announce_order_status(
        self, order_id: str, status, sim_time: float, robot_id: str
    ) -> None:
        """Report one order lifecycle transition."""

        if status.value == "COMPLETED":
            self.event(f"  t={sim_time:8.3f}  DONE    {robot_id} completed {order_id}")

    def report_workload(self, executor, result, wall: float, run_dir=None) -> None:
        """The full report to a file; a short summary on the terminal.

        The file is the report this node always printed, per-robot
        tables included: in the dataset directory when one is recorded,
        otherwise under $ROS_HOME/mrta/. verbose:=true prints it too.
        """

        lines = self.full_report_lines(executor, result, wall)

        if run_dir is not None:
            path = Path(run_dir) / "report.txt"
        else:
            home = Path(os.environ.get("ROS_HOME", Path.home() / ".ros"))
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = home / "mrta" / f"{stamp}_{self.strategy_name}_{len(self.robots)}robots.txt"

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")

        if self.verbose:
            for line in lines:
                print(line)

        m = run_metrics(executor, strategy=self.strategy_name, seed=self.allocation_seed, wall_s=wall)

        print()
        print("=" * 74)
        print(
            f"RUN COMPLETE  |  {self.strategy_name}  |  {m['robots']} robots  |  "
            f"{m['orders']} orders"
        )
        print("=" * 74)
        print(f"  orders completed  : {m['orders_completed']}/{m['orders']}")
        print(f"  makespan          : {m['makespan_s']:.4f} s simulated")
        print(f"  throughput        : {m['throughput_orders_per_h']:.1f} orders/h")
        print(f"  mean completion   : {m['mean_completion_time_s']:.1f} s")
        print(f"  total distance    : {m['total_distance_m']:.1f} m")
        print(f"  total waiting     : {m['total_waiting_s']:.4f} s")
        print(f"  safety            : {m['reservation_violations']} reservation violations")

        if m.get("battery_enabled"):
            print(
                f"  battery           : lowest SOC {m['min_soc_percent']:.2f}%, "
                f"final mean {m['mean_final_soc_percent']:.2f}%, "
                f"{m['below_zero_robots']} robot(s) below 0%"
            )
            print(
                f"  charging          : {m['charge_sessions']} sessions, "
                f"{m['promotions']} promotions ({m['stage_promotions']} from a "
                f"Stage Zone), {m['saturation_events']} saturation, "
                f"{m['preemptions']} preemptions"
            )

        print(
            f"  allocator CPU     : {m['allocator_cpu_s']:.2f} s, worst decision "
            f"{m['worst_dispatch_s']:.3f} s"
        )
        print(f"  wall clock        : {wall:.1f} s")
        print(f"  full report       : {path}")

    def full_report_lines(self, executor, result, wall: float) -> list[str]:
        """Everything about the run, per-robot tables included."""

        totals = executor.totals()

        queue = self.orders

        completed = [
            order for order in queue.orders if order.status.value == "COMPLETED"
        ]

        # The bank is shared, so "parked" means standing on the Stage
        # Zone this robot claimed on the way in -- not the one it
        # spawned on.
        claims = executor.parking.claims

        parked = [
            robot
            for robot in self.robots
            if robot.robot_id in claims
            and robot.cell == executor.parking.cell_for(robot.robot_id)
        ]

        moved = [
            robot
            for robot in parked
            if claims[robot.robot_id] != self.spawn_stations.get(robot.robot_id)
        ]

        idle = [robot for robot in self.robots if robot.status is GridRobotStatus.IDLE]

        lines = [
            "=" * 74,
            "WORKLOAD SUMMARY",
            "=" * 74,
            f"  strategy              : {self.strategy_name} (seed {self.allocation_seed})",
            f"  orders completed      : {len(completed)}/{len(queue)}",
            f"  dispatches            : {len(result.dispatches)}",
            f"  robots parked at stage: {len(parked)}/{len(self.robots)}",
            f"  parked off spawn stage: {len(moved)}/{len(self.robots)}",
            f"  robots IDLE           : {len(idle)}/{len(self.robots)}",
            "",
            f"  makespan              : {totals['makespan_s']:.4f} s",
            f"  total distance        : {totals['distance_m']:.1f} m",
            f"  total travel time     : {totals['travel_time_s']:.4f} s",
            f"  total waiting         : {totals['waiting_time_s']:.4f} s",
            f"  total service time    : {totals['service_time_s']:.4f} s",
            f"  plan deferrals        : {result.plan_failures}",
            f"  peak in-progress      : {result.peak_in_progress}",
            f"  max deferred-now      : {result.max_deferred_now}",
            "",
            "  planning CPU (wall clock, not simulated time):",
            *(f"    {stage:<14}      : {spent:.3f} s" for stage, spent in result.perf.items()),
            f"    slow dispatches     : {result.slow_dispatches}",
            f"    worst dispatch      : {result.worst_dispatch_s:.3f} s",
            f"    static reuses       : {result.static_route_reuses}",
            f"  reservation violations: {totals['reservation_violations']}",
            f"  events processed      : {totals['events']}",
            f"  routes cached         : {executor.routes.stats()}",
            f"  wall clock            : {wall:.1f} s",
            "",
        ]

        ledger = executor.engine.battery

        if ledger is None:
            lines.append("BATTERY: disabled (battery.enabled is false)")
        else:
            lines += battery_report_lines(ledger, executor.engine.time, executor.engine.robots)
            lines.append("")
            lines += eligibility_report_lines(executor)
            lines.append("")
            lines += charging_report_lines(executor)

        return lines

    def on_advance(self, delta_sim: float) -> None:
        """Pace one global interval and render it at about 20 Hz."""

        wall = self.config.wall_for(delta_sim)

        engine = getattr(self, "engine", None)

        if self.use_gazebo and engine is not None:
            self._render_interval(
                start_sim=engine.sim_time - delta_sim,
                end_sim=engine.sim_time,
                wall_seconds=wall,
            )

        elif wall > 0.0:
            sleep(wall)

    def _render_interval(
        self,
        start_sim: float,
        end_sim: float,
        wall_seconds: float,
    ) -> None:
        """Emit interpolated frames across one simulated interval.

        The engine calls on_advance *before* it processes the events at
        the end of the interval, so every move that spans this interval
        is still open in the reservation table. That is what makes a
        smooth in-between pose possible without inventing motion.
        """

        frames = 1

        if wall_seconds > 0.0:
            frames = max(
                1,
                int(ceil(wall_seconds * self.gazebo_playback_hz)),
            )

        begun = perf_counter()
        span = end_sim - start_sim

        if self.player is not None:
            # Buffered: compute every frame now, let the playback thread pace them.
            for index in range(1, frames + 1):
                sim_time = start_sim + span * index / frames
                self.player.frame(wall_seconds / frames if wall_seconds > 0.0 else 0.0,
                                  self._live_poses(sim_time))
            return

        for index in range(1, frames + 1):
            fraction = index / frames

            if wall_seconds > 0.0:
                remaining = begun + wall_seconds * fraction - perf_counter()

                if remaining > 0.0:
                    sleep(remaining)

            sim_time = start_sim + span * fraction

            self._emit_gazebo_poses(
                sim_time,
                self._live_poses(sim_time),
            )

    def _live_poses(self, sim_time: float) -> list:
        """Where every robot is at ``sim_time``, read from the engine.

        A robot mid-move is interpolated along the edge it reserved.
        Anything else -- waiting, picking, dropping, parked -- sits
        exactly on its cell.
        """

        transits = self.engine.reservations.transits()

        poses = []

        for robot in self.robots:
            transit = transits.get(robot.robot_id)

            if transit is None:
                world_x, world_y = self._world_xy(
                    robot.cell,
                )

            else:
                source, target, start, end = transit

                span = float(end) - float(start)

                if span <= 0.0:
                    fraction = 1.0
                else:
                    fraction = (sim_time - float(start)) / span

                fraction = min(1.0, max(0.0, fraction))

                source_x, source_y = self._world_xy(
                    source,
                )

                target_x, target_y = self._world_xy(
                    target,
                )

                world_x = source_x + (target_x - source_x) * fraction
                world_y = source_y + (target_y - source_y) * fraction

            poses.append(
                FleetPose(
                    robot.robot_id,
                    world_x,
                    world_y,
                    self.robot_z,
                )
            )

        return poses

    def _world_xy(self, cell):
        """Cell -> Gazebo XY, carrying the map origin.

        Every pose the node emits goes through here, so Gazebo, the RMF
        nav graph and the generated world all place a cell at the same
        metric point.
        """

        grid = self.warehouse.grid

        return grid_to_gazebo(
            cell,
            self.resolution,
            grid.origin_x,
            grid.origin_y,
        )

    def _emit_gazebo_poses(self, _sim_time: float, poses) -> None:
        """Submit a visual frame; failures never feed into simulation state."""

        if not self.use_gazebo or self.gazebo_sink is None:
            return

        try:
            self.gazebo_sink.send(poses)
        except (OSError, RuntimeError, ValueError) as error:
            self.get_logger().warn(
                f"Gazebo fleet playback disabled after transport error: {error}"
            )
            self.gazebo_sink.close()
            self.gazebo_sink = None
            self.use_gazebo = False

    def _finish_playback(self) -> None:
        """Let Gazebo and the terminal catch up with the finished simulation."""
        if self.player is not None:
            player, self.player = self.player, None
            player.finish()
            if player.stalls:
                self.get_logger().info(
                    f"playback waited for the simulation {player.stalls} time(s); "
                    "raise playback_buffer_s to avoid it"
                )

    def explain_decisions(self) -> None:
        """For strategy ilp: say which rule made each new pick and why (display only)."""
        if self.explain <= 0:
            return
        last = getattr(self.allocator, "last_decision", None)
        if last is None or last["decision_id"] < self._explained:
            return
        self._explained = last["decision_id"] + 1
        from ilp_learning.explain import explain_decision

        picks = explain_decision(last["program"], last["pairs"], last["picks"])
        robots = len({p.robot_id for p in last["pairs"]})
        orders = len({p.order_id for p in last["pairs"]})
        by_rule = sum(1 for p in picks if p["rule"] is not None)
        self.say(
            f"  decision {last['decision_id']} at t={self.engine.sim_time:.1f} s: "
            f"{robots} free robot(s) x {orders} order(s) -> {len(picks)} pick(s), "
            f"{by_rule} by learned rules, {len(picks) - by_rule} by the fallback"
        )
        for p in picks[: self.explain]:
            rule = f"rule {p['rule']}" if p["rule"] is not None else "fallback"
            self.say(f"    {p['robot']} -> {p['order']}  [{rule}] {p['text']}")
        if len(picks) > self.explain:
            self.say(f"    ... and {len(picks) - self.explain} more pick(s)")

    def on_tick(self, sim_time: float, batch) -> None:
        """Observe the new state. Never advances the clock."""

        if self.strategy_name == "ilp":
            self.explain_decisions()

        # At publish_hz of wall time, not once per event batch: a run
        # has hundreds of thousands of batches.
        now = perf_counter()

        if now >= self._next_publish:
            self._next_publish = now + 1.0 / max(self.publish_hz, 1e-6)
            self.publish_fleet()

        if not self.verbose:
            return

        robots = {r.robot_id: r for r in self.robots}

        for event in batch:
            robot = robots[event.robot_id]
            print(
                f"  t={sim_time:8.4f}  {event.robot_id}  "
                f"{event.kind.value:<17s} cell={robot.cell} "
                f"status={robot.status.value}"
            )

    def destroy_node(self):
        if self.gazebo_sink is not None:
            self.gazebo_sink.close()
            self.gazebo_sink = None
        return super().destroy_node()


def main(args=None) -> int:

    rclpy.init(args=args)

    try:
        try:
            node = FleetNode()
        except KeyboardInterrupt:
            # The workload runs inside the constructor, so Ctrl+C during
            # a run lands here. Stopping a run is a normal thing to do;
            # end quietly instead of with a traceback.
            try:
                print("\nstopped by Ctrl+C", flush=True)
            except (OSError, ValueError):
                pass
            return 0

        try:
            rclpy.spin(node)
        except KeyboardInterrupt:
            pass
        finally:
            node.destroy_node()

    finally:
        if rclpy.ok():
            rclpy.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
