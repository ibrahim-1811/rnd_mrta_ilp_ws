"""ROS 2 node that executes orders as deterministic warehouse missions.

Orders come from shared_memory_node, which owns their lifecycle. This
node assigns one, builds a mission from it, drives the robot and reports
each transition back. It keeps no order state of its own.

Authoritative pipeline:

    WarehouseMap -> A* -> MissionPlanner -> GridRobot
                                              |
                              +---------------+---------------+
                              |                               |
                    /mrta/robot_states                 Gazebo set_pose
                    (ROS, authoritative)              (visualization only)

GridRobot owns the scientific state. Simulation time comes from
distance / speed plus deterministic service durations. The wall-clock
delay between cells is for human eyes and never reaches ``sim_time``,
and robot state is never read back from Gazebo.
"""

from collections import Counter
from pathlib import Path
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
from robot_interfaces.srv import GetOrderStates, UpdateOrderState

from mrta_core.orders import Order, OrderItem, OrderStatus
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.gazebo_bridge import (
    DEFAULT_ROBOT_Z,
    provider_count,
    read_robot_z,
    set_pose,
)
from simulation_env.gazebo_view import grid_to_gazebo
from simulation_env.grid_robot import GridRobot, GridRobotStatus
from simulation_env.mission_builder import build_order_mission
from simulation_env.mission_planner import (
    best_path_to_zone,
    is_service_zone,
    plan_zone_mission,
    service_cells,
)
from simulation_env.order_assignment import FirstPendingAssigner


DEFAULT_CONFIG = "src/simulation_env/config/warehouse_sim.yaml"

# Which status a service event puts the robot into.
SERVICE_STATUS = {
    ZoneType.SHELF: GridRobotStatus.PICKING,
    ZoneType.DROPOFF: GridRobotStatus.DROPPING,
}


# ------------------------------------------------------
# Validation
# ------------------------------------------------------


def check_zones(warehouse, zone_ids) -> list[str]:

    problems = []

    for zone_id in zone_ids:
        try:
            warehouse.zone_by_id(zone_id)
        except KeyError:
            problems.append(
                f"mission zone does not exist: {zone_id}"
            )

    return problems


def check_path(warehouse, path) -> list[str]:

    problems = []

    grid = warehouse.grid

    for cell in path:

        x, y = cell

        if not (0 <= x < grid.width and 0 <= y < grid.height):
            problems.append(
                f"path cell out of bounds: {cell}"
            )

        if cell in grid.obstacles:
            problems.append(
                f"path cell is an obstacle: {cell}"
            )

    for previous, current in zip(path, path[1:]):

        step = (
            abs(current[0] - previous[0])
            + abs(current[1] - previous[1])
        )

        if step != 1:
            problems.append(
                f"non-Manhattan step {previous} -> {current}"
            )

    return problems


def check_legs(warehouse, plan) -> list[str]:
    """Shelf and drop-off cells are service cells, never transit."""

    problems = []

    services = service_cells(warehouse)

    for segment in plan.segments:

        leg = f"leg {segment.from_zone} -> {segment.to_zone}"

        path = segment.path

        if not is_service_zone(warehouse, segment.to_zone):

            # A transit leg may not touch a service cell.
            for cell in path:

                if cell in services:
                    problems.append(
                        f"{leg} crosses "
                        f"{warehouse.zone_at(cell).zone_id} "
                        f"at {cell}"
                    )

            continue

        target = segment.goal_cell

        if path[-1] != target or path.count(target) != 1:
            problems.append(
                f"{leg}: target {target} must appear "
                f"only as the final cell"
            )

        for cell in path[:-1]:

            if cell in services:
                problems.append(
                    f"{leg} crosses "
                    f"{warehouse.zone_at(cell).zone_id} "
                    f"at {cell}"
                )

        if len(path) < 2:
            problems.append(
                f"{leg}: no approach cell to retreat to"
            )
            continue

        approach = path[-2]

        if approach in services:
            problems.append(
                f"{leg}: approach cell {approach} "
                f"is itself a service cell"
            )

        if not warehouse.is_traversable(approach):
            problems.append(
                f"{leg}: approach cell {approach} "
                f"is not traversable"
            )

        if segment.exit_cell != approach:
            problems.append(
                f"{leg}: exit cell {segment.exit_cell} "
                f"is not the approach cell {approach}"
            )

    return problems


# ------------------------------------------------------
# Node
# ------------------------------------------------------


class MissionExecutor(Node):

    def __init__(self) -> None:
        super().__init__("mrta_mission_executor")

        # The config is read first so every other parameter can take
        # its default from it. There is therefore exactly one home for
        # each constant, and a launch or CLI override still wins.
        self.declare_parameter("config_file", DEFAULT_CONFIG)

        self.config_path = str(
            self.get_parameter("config_file").value
        )

        self.config = load_sim_config(self.config_path)

        config = self.config

        self.declare_parameter("use_gazebo", config.gazebo_enabled)
        self.declare_parameter(
            "time_scale", config.visualization_time_scale
        )
        self.declare_parameter("wait_for_gazebo", 0.0)
        self.declare_parameter("wait_for_shared_memory", 20.0)

        def get(name):
            return self.get_parameter(name).value

        self.world = config.gazebo_world_name
        self.robot_id = config.robot_id(0)
        self.resolution = config.resolution_m

        # Configured durations used exactly: visualization never
        # modifies scientific service time.
        self.service_seconds = {
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        }

        self.use_gazebo = bool(get("use_gazebo"))

        self.time_scale = float(get("time_scale"))
        self.publish_hz = config.robot_state_publish_hz

        self.wait_for_gazebo = float(get("wait_for_gazebo"))
        self.wait_for_shared_memory = float(get("wait_for_shared_memory"))

        self.assigner = FirstPendingAssigner()

        # Depth 1: hand a late monitor the current state, not a
        # backlog of stale ones.
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

        self.robot_pub = self.create_publisher(
            RobotState,
            f"/mrta/robots/{self.robot_id}/state",
            qos,
        )

        self.orders_client = self.create_client(
            GetOrderStates, "/get_order_states"
        )

        self.update_client = self.create_client(
            UpdateOrderState, "/update_order_state"
        )

        self.robot: GridRobot | None = None
        self.target = ""
        self.current_order = ""
        self.robot_z = DEFAULT_ROBOT_Z

    # -- shared memory ----------------------------------

    def _call(self, client, request, timeout: float = 10.0):

        future = client.call_async(request)

        rclpy.spin_until_future_complete(
            self, future, timeout_sec=timeout
        )

        return future.result()

    def fetch_orders(self, status_filter: str = "") -> list[Order]:
        """Read orders from shared memory. This node stores none."""

        request = GetOrderStates.Request()
        request.status_filter = status_filter

        response = self._call(self.orders_client, request)

        if response is None:
            raise RuntimeError(
                "/get_order_states did not answer"
            )

        orders = []

        for state in response.orders:

            items = tuple(
                OrderItem(
                    item_id=item_id,
                    shelf_id=shelf_id,
                    quantity=int(quantity),
                )
                for item_id, shelf_id, quantity in zip(
                    state.item_ids,
                    state.shelf_ids,
                    state.quantities,
                )
            )

            orders.append(
                Order(
                    order_id=state.order_id,
                    dropoff_id=state.dropoff_id,
                    items=items,
                    status=OrderStatus(state.status),
                    assigned_robot=state.assigned_robot,
                    start_time=(
                        None if state.start_time < 0.0
                        else state.start_time
                    ),
                    completion_time=(
                        None if state.completion_time < 0.0
                        else state.completion_time
                    ),
                )
            )

        return orders

    def report_order(
        self,
        order_id: str,
        status: OrderStatus,
        sim_time: float,
        assigned_robot: str = "",
    ) -> None:
        """Push one lifecycle transition into shared memory."""

        request = UpdateOrderState.Request()
        request.order_id = order_id
        request.status = status.value
        request.assigned_robot = assigned_robot
        request.sim_time = float(sim_time)

        response = self._call(self.update_client, request)

        if response is None or not response.success:
            detail = "no response" if response is None else response.message
            raise RuntimeError(
                f"could not set {order_id} to {status.value}: {detail}"
            )

    # -- publication ------------------------------------

    def publish_state(self) -> None:
        """Publish deterministic GridRobot state. Never Gazebo state."""

        robot = self.robot

        if robot is None:
            return

        world_x, world_y = grid_to_gazebo(robot.cell, self.resolution)

        msg = RobotState()

        msg.robot_id = robot.robot_id
        msg.sim_time = float(robot.sim_time)
        msg.grid_x = int(robot.cell[0])
        msg.grid_y = int(robot.cell[1])
        msg.world_x = float(world_x)
        msg.world_y = float(world_y)
        msg.status = robot.status.value
        msg.current_target = self.target
        msg.current_order = self.current_order
        msg.battery_percent = float(robot.battery_percent)
        msg.speed_mps = float(robot.speed_mps)
        msg.distance_m = float(robot.distance_m)

        fleet = RobotStateArray()
        fleet.robots = [msg]

        self.states_pub.publish(fleet)
        self.robot_pub.publish(msg)

    def trace(self) -> None:

        robot = self.robot

        world_x, world_y = grid_to_gazebo(robot.cell, self.resolution)

        print(
            f"  t={robot.sim_time:8.3f}s  "
            f"robot={robot.robot_id}  "
            f"cell=({robot.cell[0]:>2},{robot.cell[1]:>2})  "
            f"world=({world_x:>5.1f},{world_y:>5.1f})  "
            f"status={robot.status.value:<8s} "
            f"order={self.current_order or '-':<6s} "
            f"target={self.target}"
        )

    def push(self, to_gazebo: bool = True) -> None:

        if to_gazebo and self.use_gazebo:

            world_x, world_y = grid_to_gazebo(
                self.robot.cell, self.resolution
            )

            set_pose(
                self.world,
                self.robot_id,
                world_x,
                world_y,
                self.robot_z,
            )

        self.publish_state()
        self.trace()

    def wall_for(self, sim_seconds: float) -> float:
        """Wall-clock seconds to spend showing ``sim_seconds``."""

        if self.time_scale <= 0.0:
            return 0.0

        return sim_seconds / self.time_scale

    def hold(self, sim_seconds: float) -> None:
        """Wall-clock pause mirroring a simulated hold.

        Without this a service is instantaneous on screen: it costs
        simulation time but no wall time, so the robot never appears
        to stop. State keeps publishing throughout so monitors can
        actually observe PICKING / DROPPING.
        """

        budget = self.wall_for(sim_seconds)

        if budget <= 0.0:
            return

        slice_s = 1.0 / max(self.publish_hz, 1e-6)

        end = perf_counter() + budget

        while True:

            remaining = end - perf_counter()

            if remaining <= 0.0:
                break

            sleep(min(remaining, slice_s))

            self.publish_state()

    def advance(self) -> None:
        """One deterministic grid move, mirrored outward."""

        tick = perf_counter()

        self.robot.step(self.resolution)

        self.moves += 1

        self.push()

        # A move is shown for as long as it takes in simulation, so
        # movement and service holds share one timebase.
        budget = self.wall_for(
            self.resolution / self.robot.speed_mps
        )

        remaining = budget - (perf_counter() - tick)

        if remaining > 0:
            sleep(remaining)

    # -- execution --------------------------------------

    def preflight_gazebo(self) -> int:

        if not self.use_gazebo:
            print()
            print("  gazebo disabled: Gazebo will not be contacted")
            return 0

        deadline = perf_counter() + self.wait_for_gazebo

        providers = provider_count(self.world)

        while providers == 0 and perf_counter() < deadline:
            sleep(0.5)
            providers = provider_count(self.world)

        if providers == 0:
            print()
            print(f"  ERROR: /world/{self.world}/set_pose not found.")
            print("  Start the world first:")
            print(
                f"    gz sim src/simulation_env/worlds/"
                f"{self.world}.world"
            )
            return 2

        if providers > 1:
            print()
            print(
                f"  ERROR: {providers} gz sim servers are "
                f"advertising world '{self.world}'."
            )
            print(
                "  Pose updates would be split between them and "
                "the robot would stall."
            )
            print("  Leave exactly one running:")
            print("    pkill -f 'gz sim'   # then relaunch one world")
            return 3

        self.robot_z = read_robot_z(self.robot_id, DEFAULT_ROBOT_Z)

        print()
        print(
            f"  Gazebo world '{self.world}' is up, "
            f"robot z = {self.robot_z}"
        )

        return 0

    def drive(self, plan, order_id: str) -> None:
        """Execute a planned mission, servicing every service cell."""

        warehouse = self.warehouse

        robot = self.robot

        for segment in plan.segments:

            self.target = segment.to_zone

            robot.assign_path(list(segment.path))

            while not robot.finished:
                self.advance()

            print(f"ARRIVED: {segment.to_zone}")

            zone_type = warehouse.zone_by_id(
                segment.to_zone
            ).zone_type

            if segment.exit_cell is None:
                continue

            duration = self.service_seconds[zone_type]
            status = SERVICE_STATUS[zone_type]

            robot.status = status
            self.push(to_gazebo=False)

            print(
                f"SERVICE: {segment.to_zone}  duration={duration}s"
            )

            before = robot.cell

            # Visible hold: the robot really stops on screen.
            self.hold(duration)

            robot.serve(duration, status)

            # Pose deliberately not pushed: the robot has not moved.
            self.push(to_gazebo=False)

            if robot.cell != before:
                raise RuntimeError("robot moved during service")

            print(f"SERVICE COMPLETE: {segment.to_zone}")

            self.service_counts[zone_type.value] += 1
            self.service_time[zone_type.value] += duration

            service_cell = robot.cell

            robot.assign_path([service_cell, segment.exit_cell])

            self.advance()

            print(
                f"EXIT {zone_type.value.upper()}: "
                f"{segment.to_zone}  "
                f"{service_cell} -> {robot.cell}"
            )

    def run(self) -> int:

        config = self.config

        print()
        print("=" * 78)
        print("WAREHOUSE SIMULATION")
        print("=" * 78)
        print(f"Config         : {self.config_path}")

        self.warehouse = load_warehouse_xlsx(config.map_path)

        warehouse = self.warehouse

        grid = warehouse.grid

        print(f"Map            : {config.map_path.name}")
        print(f"Grid           : {grid.width} x {grid.height}")
        print(f"Blocked        : {len(grid.obstacles)}")
        print(f"Stations       : {len(warehouse.zones)}")
        print(
            f"Fleet          : {config.robot_count} robot "
            f"({self.robot_id}) at {config.speed_mps} m/s"
        )
        print(
            f"Service        : shelf "
            f"{config.shelf_pick_s}s, dropoff {config.dropoff_s}s"
        )
        print(
            f"Orders         : {config.orders_path.name} "
            f"(mode={config.orders_mode})"
        )
        print(
            f"Deterministic  : {config.deterministic} "
            f"(seed {config.seed})"
        )
        print(f"States topic   : {config.robot_states_topic}")

        # --- shared memory ---

        if not self.orders_client.wait_for_service(
            timeout_sec=self.wait_for_shared_memory
        ):
            print()
            print("  ERROR: /get_order_states not available.")
            print("  Start shared_memory_node with the same config:")
            print(
                "    ros2 run shared_memory_node shared_memory_node "
                f"--ros-args -p config_file:={self.config_path}"
            )
            return 4

        if not self.update_client.wait_for_service(timeout_sec=5.0):
            print()
            print("  ERROR: /update_order_state not available.")
            return 4

        orders = self.fetch_orders()

        if not orders:
            print()
            print("  ERROR: shared memory holds no orders.")
            return 5

        items = sum(len(order.items) for order in orders)

        print()
        print(
            f"Orders loaded  : {len(orders)} order(s), "
            f"{items} line item(s)"
        )

        for order in orders:
            print()
            print(f"  {order.order_id}")
            print(f"    dropoff  = {order.dropoff_id}")
            print(f"    status   = {order.status.value}")
            print("    items:")
            for item in order.items:
                print(
                    f"      {item.item_id} -> {item.shelf_id} "
                    f"x{item.quantity}"
                )

        # --- robot ---

        start_zone = warehouse.zone_by_id(config.start_zone)

        self.robot = GridRobot(
            robot_id=self.robot_id,
            cell=min(start_zone.cells),
            speed_mps=config.speed_mps,
            battery_percent=config.initial_battery_percent,
        )

        robot = self.robot

        self.target = config.start_zone

        self.moves = 0

        self.service_counts: Counter[str] = Counter()
        self.service_time: Counter[str] = Counter()

        code = self.preflight_gazebo()

        if code:
            return code

        print()
        print("=" * 78)
        print(
            f"START  {self.robot_id} at {config.start_zone} "
            f"cell {robot.cell}  status={robot.status.value}"
        )
        print("=" * 78)

        self.push()

        wall_start = perf_counter()

        # --- assignment ---

        assignment = self.assigner.select(
            orders, [self.robot_id], robot.sim_time
        )

        if assignment is None:
            print()
            print("  ERROR: the workload holds no PENDING order")
            return 6

        order, robot_id = assignment

        print()
        print(f"ORDER SELECTED: {order.order_id}")

        self.report_order(
            order.order_id,
            OrderStatus.ASSIGNED,
            robot.sim_time,
            assigned_robot=robot_id,
        )

        print(f"ORDER ASSIGNED: {order.order_id} -> {robot_id}")

        zones = build_order_mission(order, config.start_zone)

        problems = check_zones(warehouse, zones)

        if problems:
            print()
            for problem in problems:
                print(f"  INVALID: {problem}")
            return 1

        plan = plan_zone_mission(warehouse, zones)

        problems = (
            check_path(warehouse, plan.path)
            + check_legs(warehouse, plan)
        )

        if problems:
            print()
            for problem in problems:
                print(f"  INVALID: {problem}")
            return 1

        print(f"MISSION       : {' -> '.join(zones)}")
        print(
            f"  [OK ] {len(plan.path)} path cells, Manhattan, "
            f"no obstacle, no service-cell transit"
        )

        self.current_order = order.order_id

        start_time = robot.sim_time

        self.report_order(
            order.order_id,
            OrderStatus.IN_PROGRESS,
            start_time,
        )

        print(f"ORDER STARTED : {order.order_id}")
        print()

        self.drive(plan, order.order_id)

        completion_time = robot.sim_time

        self.report_order(
            order.order_id,
            OrderStatus.COMPLETED,
            completion_time,
        )

        print()
        print(f"ORDER COMPLETED: {order.order_id}")

        order_distance = robot.distance_m
        order_travel = robot.travel_time_s

        # --- parking: runtime policy, not part of the order ---

        self.current_order = ""

        if config.park_when_idle:

            self.target = config.start_zone

            print()
            print(f"PARKING: returning to {config.start_zone}")

            result = best_path_to_zone(
                warehouse,
                robot.cell,
                config.start_zone,
                service_cells(warehouse),
            )

            robot.assign_path(list(result.path))

            while not robot.finished:
                self.advance()

            print(f"PARKED: {config.start_zone}")

        robot.status = GridRobotStatus.IDLE
        self.target = ""
        self.publish_state()

        wall_elapsed = perf_counter() - wall_start

        # --- summary ---

        pickup_time = self.service_time[ZoneType.SHELF.value]
        drop_time = self.service_time[ZoneType.DROPOFF.value]
        total_service = pickup_time + drop_time

        execution_time = completion_time - start_time

        final = self.fetch_orders()

        final_status = {o.order_id: o.status.value for o in final}

        # With no deadline, success is simply whether the order
        # reached COMPLETED in shared memory.
        success = (
            final_status.get(order.order_id)
            == OrderStatus.COMPLETED.value
        )

        print()
        print("=" * 78)
        print("MISSION SUMMARY")
        print("=" * 78)
        print(f"  order_id              : {order.order_id}")
        print(f"  assigned_robot        : {robot_id}")
        print(f"  start_time            : {start_time:.3f} s")
        print(f"  completion_time       : {completion_time:.3f} s")
        print(f"  travel_distance       : {order_distance:.1f} m")
        print(f"  travel_time           : {order_travel:.3f} s")
        print(f"  pickup_service_time   : {pickup_time:.3f} s")
        print(f"  dropoff_service_time  : {drop_time:.3f} s")
        print(f"  total_service_time    : {total_service:.3f} s")
        print(f"  total_execution_time  : {execution_time:.3f} s")
        print(f"  success               : {success}")
        print()
        print(
            f"  shared memory order state : "
            f"{final_status.get(order.order_id)}"
        )
        print(
            f"  robot final status        : "
            f"{robot.status.value} at {robot.cell}"
        )
        print(
            f"  grid moves (incl parking) : {self.moves}, "
            f"total distance {robot.distance_m:.1f} m, "
            f"sim time {robot.sim_time:.3f} s"
        )
        print()
        print(
            f"  wall clock                : {wall_elapsed:.1f} s "
            f"(viewing only, not simulation time)"
        )
        print()

        return 0


def main(args=None) -> int:

    rclpy.init(args=args)

    code = 1

    try:
        node = MissionExecutor()

        try:
            code = node.run()
        finally:
            node.destroy_node()

    finally:
        rclpy.shutdown()

    return code


if __name__ == "__main__":
    raise SystemExit(main())
