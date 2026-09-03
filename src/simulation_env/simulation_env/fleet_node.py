"""ROS 2 node that deploys the fleet and publishes its state.

Builds every robot from configuration, places it on its staging
station, mirrors the poses into Gazebo once, and publishes the whole
fleet on /mrta/robot_states at a configured rate.

State is owned by GridRobot and is never read back from Gazebo.
The selected FIFO/MRTA workload is executed here.
"""

from math import ceil
from time import perf_counter, sleep

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)

from robot_interfaces.msg import RobotState, RobotStateArray

from mrta_core.order_queue import OrderQueue
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.fleet import build_fleet, home_stations
from simulation_env.grid_robot import GridRobotStatus
from simulation_env.workload_executor import WorkloadExecutor
from mrta_core.warehouse_routing import plan_return_to_zone, service_cells
from simulation_env.gazebo_bridge import (
    DEFAULT_ROBOT_Z,
    FleetPoseSink,
    provider_count,
    read_robot_z,
)
from simulation_env.gazebo_view import grid_to_gazebo
from simulation_env.fleet_playback import (
    DEFAULT_PLAYBACK_HZ,
    FleetPose,
    FleetTrajectoryPlayback,
)


DEFAULT_CONFIG = "src/simulation_env/config/warehouse_sim.yaml"

# How much of the FIFO workload to echo at startup.
PREVIEW_ORDERS = 18



class FleetNode(Node):

    def __init__(self) -> None:
        super().__init__("mrta_fleet")

        self.declare_parameter("config_file", DEFAULT_CONFIG)

        self.config_path = str(
            self.get_parameter("config_file").value
        )

        self.config = load_sim_config(self.config_path)

        config = self.config

        self.declare_parameter("use_gazebo", config.gazebo_enabled)
        self.declare_parameter(
            "publish_hz", config.robot_state_publish_hz
        )
        self.declare_parameter("wait_for_gazebo", 0.0)
        self.declare_parameter(
            "gazebo_playback_hz", DEFAULT_PLAYBACK_HZ
        )
        self.declare_parameter("load_orders", False)
        self.declare_parameter("run_workload", False)
        self.declare_parameter("strategy", "nearest_astar")
        # 0 keeps the configured speed; any positive value
        # overrides it, so a run can be re-timed without
        # editing the YAML.
        self.declare_parameter("speed_mps", 0.0)
        self.declare_parameter("progress_interval_s", 10.0)

        def get(name):
            return self.get_parameter(name).value

        self.use_gazebo = bool(get("use_gazebo"))
        self.publish_hz = float(get("publish_hz"))
        self.wait_for_gazebo = float(get("wait_for_gazebo"))
        self.gazebo_playback_hz = float(get("gazebo_playback_hz"))
        self.load_orders = bool(get("load_orders"))
        self.run_workload = bool(get("run_workload"))
        self.strategy_name = str(get("strategy"))
        self.progress_interval_s = float(
            get("progress_interval_s")
        )

        override = float(get("speed_mps"))
        self.speed_mps = (
            override if override > 0.0 else config.speed_mps
        )

        self.world = config.gazebo_world_name
        self.resolution = config.resolution_m
        self.robot_z = DEFAULT_ROBOT_Z
        self.gazebo_sink: FleetPoseSink | None = None
        self.playback: FleetTrajectoryPlayback | None = None

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


        self.warehouse = load_warehouse_xlsx(config.map_path)

        self.robots = build_fleet(config, self.warehouse, self.speed_mps)

        self.homes = home_stations(config, self.warehouse)

        self.summary()

        # The whole workload is known at startup; nothing about it
        # depends on the clock.
        self.orders = (
            self.load_workload() if self.load_orders else None
        )

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
                ZoneType.STAGING,
            )
        }

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

            world_x, world_y = grid_to_gazebo(
                robot.cell, self.resolution
            )

            print(
                f"{robot.robot_id} -> "
                f"{self.homes[robot.robot_id]} -> "
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
                f"{expected} unique staging cells",
                len(cells) == expected,
            ),
            (
                "no robot on obstacle",
                all(
                    robot.cell not in warehouse.grid.obstacles
                    for robot in robots
                ),
            ),
            (
                "all robot positions in bounds",
                all(
                    grid_map.in_bounds(robot.cell)
                    for robot in robots
                ),
            ),
            (
                "all robots on staging cells",
                all(
                    warehouse.zone_type_at(robot.cell)
                    is ZoneType.STAGING
                    for robot in robots
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

        print()

        for label, ok in results:
            print(f"[{'OK' if ok else 'FAIL'}] {label}")

        failed = [label for label, ok in results if not ok]

        if failed:
            raise RuntimeError(
                f"fleet initialisation checks failed: {failed}"
            )

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

        self.robot_z = read_robot_z(
            self.robots[0].robot_id, DEFAULT_ROBOT_Z
        )

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
            world_x, world_y = grid_to_gazebo(
                robot.cell, self.resolution
            )
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

        world_x, world_y = grid_to_gazebo(
            robot.cell, self.resolution
        )

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
        msg.battery_percent = float(robot.battery_percent)
        msg.speed_mps = float(robot.speed_mps)
        msg.distance_m = float(robot.distance_m)

        return msg

    def publish_fleet(self) -> None:

        msg = RobotStateArray()

        msg.robots = [self.to_msg(r) for r in self.robots]

        self.states_pub.publish(msg)

    def park_zone_for(self, robot_id: str) -> str | None:
        """Where this robot parks when it has nothing to do."""

        mode = self.config.park_zone_mode

        if mode == "start_zone":
            return self.config.start_zone

        if mode == "home":
            return self.homes.get(robot_id)

        self.get_logger().warn(
            f"unknown park_zone_mode {mode!r}; not parking"
        )

        return None

    def park_policy(self, robot):
        """Send an idle robot home. Called when it runs out of work.

        Returning None leaves it standing where it is. This is an
        idle policy: it never runs between the legs of a mission.
        """

        if not self.config.park_when_idle:
            return None

        zone_id = self.park_zone_for(robot.robot_id)

        if zone_id is None:
            return None

        home_cell = min(self.warehouse.zone_by_id(zone_id).cells)

        if robot.cell == home_cell:
            return None

        print(
            f"PARKING: {robot.robot_id} {robot.cell} -> {zone_id}"
        )

        # Robots already parked are stationary, so route around them
        # rather than planning through them and then stalling on the
        # reservation table.
        blocked = service_cells(self.warehouse) | self.engine.blocked_by_others(
            robot.robot_id
        )

        return plan_return_to_zone(
            self.warehouse,
            robot.cell,
            zone_id,
            from_label=robot.robot_id,
            occupied=blocked,
        )

    def load_workload(self) -> OrderQueue | None:
        """Load the configured deterministic FIFO workload.

        The OrderQueue is the authoritative workload state.
        Every configured order is loaded PENDING in deterministic FIFO
        sequence.
        """

        try:
            queue = OrderQueue.from_csv(
                self.config.orders_path, self.warehouse
            )
        except (OSError, ValueError) as error:
            self.get_logger().error(f"orders not loaded: {error}")
            return None

        print()
        print("=" * 62)
        print("ORDERS")
        print("=" * 62)
        print(f"file          : {self.config.orders_path}")
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
            allocator = get_batch_strategy(self.strategy_name)
        except KeyError as error:
            self.get_logger().error(str(error))
            return

        print()
        print("=" * 74)
        print(
            f"100-ORDER WORKLOAD  |  {len(self.robots)} robots  |  "
            f"strategy={allocator.name}"
        )
        print("=" * 74)
        print(f"orders     : {len(self.orders)}")
        print(f"line items : {self.orders.line_item_count}")
        print(f"speed      : {self.speed_mps} m/s")
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

        executor = WorkloadExecutor(
            warehouse=self.warehouse,
            robots=self.robots,
            queue=self.orders,
            allocator=allocator,
            homes=self.homes,
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
            on_trajectory=self.show_trajectory,
            on_progress=self.announce_progress,
            progress_interval_s=self.progress_interval_s,
            on_perf=self.announce_perf,
        )

        self.engine = executor.engine

        started = perf_counter()

        try:
            result = executor.run()

        except RuntimeError as error:
            self.get_logger().error(f"workload stalled: {error}")
            return

        wall = perf_counter() - started

        self.report_workload(executor, result, wall)

    def show_trajectory(self, robot_id: str, trajectory) -> None:
        """Mirror a newly committed trajectory into Gazebo playback.

        Visualization only. Nothing here is read back into the
        simulation: the engine already holds the authoritative copy.
        """

        if self.playback is not None:
            self.playback.set_trajectory(robot_id, trajectory)

    def announce_perf(self, budget) -> None:
        """Report a dispatch that stalled the loop. CPU time only."""

        for line in budget.lines():
            print(line, flush=True)

    def announce_progress(self, progress) -> None:
        """One compact progress block, not a per-move trace."""

        for line in progress.lines():
            print(line, flush=True)

    def announce_dispatch(self, record) -> None:
        """One robot-order assignment, as it is dispatched."""

        shelves = ", ".join(record.shelf_sequence)

        print(
            f"  t={record.sim_time:8.3f}  ASSIGN  {record.robot_id} "
            f"-> {record.order_id}  C_static={record.static_cost_m:6.1f} m"
            f"  [{shelves}] -> {record.dropoff_id}"
        )

    def announce_order_status(
        self, order_id: str, status, sim_time: float, robot_id: str
    ) -> None:
        """Report one order lifecycle transition."""

        if status.value == "COMPLETED":
            print(
                f"  t={sim_time:8.3f}  DONE    {robot_id} "
                f"completed {order_id}"
            )

    def report_workload(self, executor, result, wall: float) -> None:

        totals = executor.totals()

        queue = self.orders

        completed = [
            order
            for order in queue.orders
            if order.status.value == "COMPLETED"
        ]

        homes = self.homes

        at_home = [
            robot
            for robot in self.robots
            if robot.cell
            == min(self.warehouse.zone_by_id(homes[robot.robot_id]).cells)
        ]

        idle = [
            robot
            for robot in self.robots
            if robot.status is GridRobotStatus.IDLE
        ]

        print()
        print("=" * 74)
        print("WORKLOAD SUMMARY")
        print("=" * 74)
        print(f"  orders completed      : {len(completed)}/{len(queue)}")
        print(f"  dispatches            : {len(result.dispatches)}")
        print(f"  robots at own staging : {len(at_home)}/{len(self.robots)}")
        print(f"  robots IDLE           : {len(idle)}/{len(self.robots)}")
        print()
        print(f"  makespan              : {totals['makespan_s']:.4f} s")
        print(f"  total distance        : {totals['distance_m']:.1f} m")
        print(f"  total travel time     : {totals['travel_time_s']:.4f} s")
        print(f"  total waiting         : {totals['waiting_time_s']:.4f} s")
        print(f"  total service time    : {totals['service_time_s']:.4f} s")
        print(f"  MAPF conflicts        : {totals['conflicts']}")
        print(f"  MAPF replans          : {totals['replans']}")
        print(f"  plan deferrals        : {result.plan_failures}")
        print(f"  peak in-progress      : {result.peak_in_progress}")
        print(f"  max deferred-now      : {result.max_deferred_now}")
        print()
        print("  planning CPU (wall clock, not simulated time):")
        for stage, spent in result.perf.items():
            print(f"    {stage:<14}      : {spent:.3f} s")
        print(f"    slow dispatches     : {result.slow_dispatches}")
        print(f"    worst dispatch      : {result.worst_dispatch_s:.3f} s")
        print(f"    static reuses       : {result.static_route_reuses}")
        print(f"  space-time expansions : {totals['expansions']}")
        print(f"  events processed      : {totals['events']}")
        print(f"  routes cached         : {executor.routes.stats()}")
        print(f"  wall clock            : {wall:.1f} s")
        print()

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

        elif self.playback is not None and self.use_gazebo:
            end_sim = self.engine.sim_time
            self.playback.play_interval(
                start_sim=end_sim - delta_sim,
                end_sim=end_sim,
                wall_seconds=wall,
                emit=self._emit_gazebo_poses,
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

        for index in range(1, frames + 1):

            fraction = index / frames

            if wall_seconds > 0.0:
                remaining = (
                    begun
                    + wall_seconds * fraction
                    - perf_counter()
                )

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
                world_x, world_y = grid_to_gazebo(
                    robot.cell,
                    self.resolution,
                )

            else:
                source, target, start, end = transit

                span = float(end) - float(start)

                if span <= 0.0:
                    fraction = 1.0
                else:
                    fraction = (
                        sim_time - float(start)
                    ) / span

                fraction = min(1.0, max(0.0, fraction))

                source_x, source_y = grid_to_gazebo(
                    source,
                    self.resolution,
                )

                target_x, target_y = grid_to_gazebo(
                    target,
                    self.resolution,
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

    def _emit_gazebo_poses(self, _sim_time: float, poses) -> None:
        """Submit a visual frame; failures never feed into simulation state."""

        if not self.use_gazebo or self.gazebo_sink is None:
            return

        try:
            self.gazebo_sink.send(poses)
        except (OSError, RuntimeError, ValueError) as error:
            self.get_logger().warn(
                f"Gazebo fleet playback disabled after transport error: "
                f"{error}"
            )
            self.gazebo_sink.close()
            self.gazebo_sink = None
            self.use_gazebo = False

    def on_tick(self, sim_time: float, batch) -> None:
        """Observe the new state. Never advances the clock."""

        # MAPF interpolation already ended this interval with one complete
        # fleet pose at exactly ``sim_time``.  The legacy path runner has no
        # timed trajectory, so give it one batched endpoint update here.
        if self.use_gazebo and self.playback is None:

            moved = {event.robot_id for event in batch}

            by_id = {r.robot_id: r for r in self.robots}

            poses = []

            for robot_id in sorted(moved):
                robot = by_id[robot_id]
                world_x, world_y = grid_to_gazebo(
                    robot.cell, self.resolution
                )
                poses.append(
                    FleetPose(
                        robot_id,
                        world_x,
                        world_y,
                        self.robot_z,
                    )
                )

            if poses:
                self._emit_gazebo_poses(sim_time, poses)

        self.publish_fleet()

        for event in batch:
            robot = {r.robot_id: r for r in self.robots}[
                event.robot_id
            ]
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
        node = FleetNode()

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
