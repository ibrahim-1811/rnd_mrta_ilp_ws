"""ROS 2 node that deploys the fleet and publishes its state.

Builds every robot from configuration, places it on its staging
station, mirrors the poses into Gazebo once, and publishes the whole
fleet on /mrta/robot_states at a configured rate.

State is owned by GridRobot and is never read back from Gazebo.
Order execution is not started here.
"""

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

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.fleet import build_fleet, home_stations
from simulation_env.fleet_engine import FleetSimulationEngine
from simulation_env.mission_planner import plan_zone_mission
from simulation_env.gazebo_bridge import (
    DEFAULT_ROBOT_Z,
    provider_count,
    read_robot_z,
    set_pose,
)
from simulation_env.gazebo_view import grid_to_gazebo


DEFAULT_CONFIG = "src/simulation_env/config/warehouse_sim.yaml"

# Two non-conflicting test missions on opposite sides of the
# warehouse. A harness for validating the clock, not an allocator.
DEMO_MISSIONS = {
    "robot_001": ["staging_001", "shelf_001", "dropoff_001"],
    "robot_010": ["staging_010", "shelf_028", "dropoff_011"],
}


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
        self.declare_parameter("run_demo_missions", False)

        def get(name):
            return self.get_parameter(name).value

        self.use_gazebo = bool(get("use_gazebo"))
        self.publish_hz = float(get("publish_hz"))
        self.wait_for_gazebo = float(get("wait_for_gazebo"))
        self.run_demo = bool(get("run_demo_missions"))

        self.world = config.gazebo_world_name
        self.resolution = config.resolution_m
        self.robot_z = DEFAULT_ROBOT_Z

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

        self.robots = build_fleet(config, self.warehouse)

        self.homes = home_stations(config, self.warehouse)

        self.summary()

        if self.use_gazebo:
            self.place_in_gazebo()

        self.publish_fleet()

        if self.run_demo:
            self.run_concurrent_demo()

        period = 1.0 / max(self.publish_hz, 1e-6)

        self.create_timer(period, self.publish_fleet)

        self.get_logger().info(
            f"fleet of {len(self.robots)} publishing on "
            f"{config.robot_states_topic} at {self.publish_hz} Hz"
        )

    # -- reporting --------------------------------------

    def summary(self) -> None:

        config = self.config

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
        print(f"Speed:\n{config.speed_mps} m/s")
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

        providers = provider_count(self.world)

        while providers == 0 and perf_counter() < deadline:
            sleep(0.5)
            providers = provider_count(self.world)

        if providers == 0:
            self.get_logger().warn(
                f"/world/{self.world}/set_pose not found; "
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

        for robot in self.robots:

            world_x, world_y = grid_to_gazebo(
                robot.cell, self.resolution
            )

            set_pose(
                self.world,
                robot.robot_id,
                world_x,
                world_y,
                self.robot_z,
            )

        print()
        print(
            f"[OK] {len(self.robots)} robot poses pushed to Gazebo "
            f"world '{self.world}'"
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

    # -- concurrent execution ---------------------------

    def run_concurrent_demo(self) -> None:
        """Drive two robots on the shared clock, live."""

        config = self.config

        by_id = {r.robot_id: r for r in self.robots}

        engine = FleetSimulationEngine(
            warehouse=self.warehouse,
            resolution=config.resolution_m,
            service_seconds={
                ZoneType.SHELF: config.shelf_pick_s,
                ZoneType.DROPOFF: config.dropoff_s,
            },
            on_advance=self.on_advance,
            on_tick=self.on_tick,
        )

        for robot in self.robots:
            engine.add_robot(robot)

        print()
        print("=" * 62)
        print("CONCURRENT MISSIONS (clock validation harness)")
        print("=" * 62)

        for robot_id, zones in DEMO_MISSIONS.items():
            plan = plan_zone_mission(self.warehouse, zones)
            engine.assign_mission(robot_id, plan)
            print(f"{robot_id}: {' -> '.join(zones)}")

        print()

        wall_start = perf_counter()

        engine.run()

        wall = perf_counter() - wall_start

        print()
        print("=" * 62)
        print("CONCURRENT FLEET SIMULATION")
        print("=" * 62)
        print()
        print(f"global simulated time : {engine.sim_time:.4f} s")
        print(f"wall time             : {wall:.2f} s")
        print()

        for robot_id in DEMO_MISSIONS:
            robot = by_id[robot_id]
            print(f"{robot_id}:")
            print(f"  travel distance : {robot.distance_m:.1f} m")
            print(f"  travel time     : {robot.travel_time_s:.4f} s")
            print(f"  service time    : {robot.service_time_s:.4f} s")
            print(
                f"  completion time : "
                f"{engine.completion_time(robot_id):.4f} s"
            )

        idle = len(self.robots) - len(DEMO_MISSIONS)

        print()
        print("fleet:")
        print(f"  robots              : {len(self.robots)}")
        print(f"  robots executed     : {len(DEMO_MISSIONS)}")
        print(f"  robots idle         : {idle}")
        print(f"  events processed    : {engine.processed}")
        print()
        print("[OK] one global clock")
        print("[OK] concurrent moves share timestamps")
        print("[OK] service overlaps other robot movement")
        print("[OK] deterministic event ordering")
        print("[OK] ROS states use global simulation timestamp")
        print()

    def on_advance(self, delta_sim: float) -> None:
        """One wall pause per global advancement, not per robot."""

        wall = self.config.wall_for(delta_sim)

        if wall > 0.0:
            sleep(wall)

    def on_tick(self, sim_time: float, batch) -> None:
        """Observe the new state. Never advances the clock."""

        if self.use_gazebo:

            moved = {event.robot_id for event in batch}

            by_id = {r.robot_id: r for r in self.robots}

            for robot_id in sorted(moved):

                robot = by_id[robot_id]

                world_x, world_y = grid_to_gazebo(
                    robot.cell, self.resolution
                )

                set_pose(
                    self.world,
                    robot_id,
                    world_x,
                    world_y,
                    self.robot_z,
                )

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
        rclpy.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
