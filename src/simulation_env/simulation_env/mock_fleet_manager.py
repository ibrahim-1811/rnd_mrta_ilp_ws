#!/usr/bin/env python3
"""Grid-based headless fleet simulator.

Replaces Gazebo + Nav2 + the real ``fleet_manager`` for large-scale evaluation.
Robots move on a shared :class:`~mrta_core.world.WorldGrid` by following A* paths
cell-by-cell. Live cell occupancy is tracked and fed back into A* as congestion cost,
so robots route around congestion — the grid-aware behaviour the Euclidean simulator
never had. Battery drains per travelled metre.

ROS interface (task-manager compatible; node name is ``fleet_manager``):
  Services : /get_robot_fleet_status (GetRobotFleetStatus)
             task_list               (TaskList)
             /reset_simulation        (Trigger)
  Topics   : fleet_status              (FleetStatus, 1 Hz, transient-local)
             /fleet_travel_stats       (String / JSON, per completed task)
             /simulation/battery_updates (String / JSON, 5 s)
             /simulation/congestion    (String / JSON, 1 Hz)
             /central_logs             (Logs)

Shelf and drop-off data are owned by shared_memory_node; this node only simulates
robot motion. Task waypoints arrive as world poses inside the task_list request.
"""

import json
import threading
import time

import rclpy
from geometry_msgs.msg import Pose
from mrta_core.grid import astar_path
from mrta_core.kinematics import RobotModel, path_energy_percent
from mrta_core.world import WorldGrid, world_grid_from_ascii
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from robot_interfaces.msg import FleetStatus, Logs, RobotStatus
from robot_interfaces.srv import GetRobotFleetStatus, TaskList
from std_msgs.msg import String
from std_srvs.srv import Trigger


class MockRobot:
    """Simulated robot state, positioned on the grid by integer cell."""

    def __init__(self, robot_id: int, cell: tuple[int, int], battery: float = 100.0):
        self.robot_id = robot_id
        self.initial_cell = cell
        self.initial_battery = battery
        self.cell = cell
        self.battery = battery
        self.is_available = True
        self.status = 'idle'
        self.task_lock = threading.Lock()
        self.generation = 0

    def reset(self):
        with self.task_lock:
            self.generation += 1
            self.cell = self.initial_cell
            self.battery = self.initial_battery
            self.is_available = True
            self.status = 'idle'


class MockFleetManager(Node):
    def __init__(self):
        super().__init__('fleet_manager')  # same node name as the real fleet_manager

        self.declare_parameter('num_robots', 4)
        self.declare_parameter('world_size_m', 30.0)
        self.declare_parameter('resolution_m', 1.0)
        self.declare_parameter('robot_speed_ms', 2.0)
        self.declare_parameter('time_scale', 1.0)
        self.declare_parameter('battery_drain_per_m', 0.7)
        self.declare_parameter('battery_low_threshold', 20.0)
        self.declare_parameter('battery_resume_threshold', 80.0)
        self.declare_parameter('battery_charge_rate_per_s', 5.0)
        self.declare_parameter('battery_charge_tick_s', 1.0)
        self.declare_parameter('initial_battery', 100.0)
        self.declare_parameter('congestion_weight', 1.0)
        self.declare_parameter('grid_map_file', '')

        num_robots = int(self.get_parameter('num_robots').value)
        self.world_size = float(self.get_parameter('world_size_m').value)
        self.resolution = float(self.get_parameter('resolution_m').value)
        self.speed = float(self.get_parameter('robot_speed_ms').value)
        self.time_scale = max(1e-6, float(self.get_parameter('time_scale').value))
        self.drain_per_m = float(self.get_parameter('battery_drain_per_m').value)
        self.low_threshold = float(self.get_parameter('battery_low_threshold').value)
        self.resume_threshold = float(self.get_parameter('battery_resume_threshold').value)
        self.charge_rate_per_s = float(self.get_parameter('battery_charge_rate_per_s').value)
        self.charge_tick_s = float(self.get_parameter('battery_charge_tick_s').value)
        self.initial_battery = float(self.get_parameter('initial_battery').value)
        self.congestion_weight = float(self.get_parameter('congestion_weight').value)
        grid_map_file = str(self.get_parameter('grid_map_file').value)

        # Build the world grid (single source of truth, shared with feature extraction).
        half = self.world_size / 2.0
        if grid_map_file:
            self.world_grid = world_grid_from_ascii(
                grid_map_file, origin_x=-half, origin_y=0.0, resolution=self.resolution)
        else:
            self.world_grid = WorldGrid.square(self.world_size, self.resolution)

        # Physics motion/energy model: the SAME model the allocators estimate with,
        # so the battery the sim drains matches what strategies predicted.
        self.model = RobotModel(v_max=self.speed)

        # Live cell occupancy -> congestion.
        self.occupancy: dict[tuple[int, int], int] = {}
        self.occ_lock = threading.Lock()

        self.robots: dict[int, MockRobot] = {}
        for i, cell in enumerate(self._spawn_cells(num_robots), start=1):
            self.robots[i] = MockRobot(i, cell, battery=self.initial_battery)
            self._occ_inc(cell)

        qos = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.fleet_pub = self.create_publisher(FleetStatus, 'fleet_status', qos)
        self.travel_stats_pub = self.create_publisher(String, '/fleet_travel_stats', 10)
        self.battery_pub = self.create_publisher(String, '/simulation/battery_updates', 10)
        self.congestion_pub = self.create_publisher(String, '/simulation/congestion', 10)
        self.log_pub = self.create_publisher(Logs, '/central_logs', 10)

        self.create_timer(1.0, self._publish_fleet_status)
        self.create_timer(1.0, self._publish_congestion)
        self.create_timer(5.0, self._publish_battery_updates)
        self.create_timer(max(0.1, self.charge_tick_s), self._charge_batteries)

        self.create_service(TaskList, 'task_list', self._handle_task_list)
        self.create_service(
            GetRobotFleetStatus, '/get_robot_fleet_status', self._handle_fleet_status)
        self.create_service(Trigger, '/reset_simulation', self._handle_reset)

        self.get_logger().info(
            f'MockFleetManager (grid): {num_robots} robots on '
            f'{self.world_grid.width}x{self.world_grid.height} grid '
            f'(res={self.resolution}m), speed={self.speed}m/s, '
            f'time_scale={self.time_scale}, '
            f'battery={self.model.battery_capacity_wh}Wh (physics energy model)')

    # ------------------------------------------------------------------
    # Occupancy / congestion
    # ------------------------------------------------------------------
    def _occ_inc(self, cell):
        with self.occ_lock:
            self.occupancy[cell] = self.occupancy.get(cell, 0) + 1

    def _occ_dec(self, cell):
        with self.occ_lock:
            value = self.occupancy.get(cell, 0) - 1
            if value <= 0:
                self.occupancy.pop(cell, None)
            else:
                self.occupancy[cell] = value

    def _congestion_snapshot(self) -> dict[tuple[int, int], float]:
        with self.occ_lock:
            return {cell: float(count) for cell, count in self.occupancy.items()}

    # ------------------------------------------------------------------
    # Service handlers
    # ------------------------------------------------------------------
    def _handle_task_list(self, request, response):
        if not request.task_list:
            response.success = False
            return response

        robot_id = request.task_list[0].robot_id
        robot = self.robots.get(robot_id)
        if robot is None or not robot.is_available:
            response.success = False
            return response

        with robot.task_lock:
            generation = robot.generation
            robot.is_available = False
            robot.status = 'busy'

        self._publish_fleet_status()

        waypoints = []
        for task in request.task_list:
            p = task.shelf_location.position
            waypoints.append((p.x, p.y))

        thread = threading.Thread(
            target=self._simulate_navigation,
            args=(robot, waypoints, generation),
            daemon=True,
        )
        thread.start()

        response.success = True
        return response

    def _handle_fleet_status(self, request, response):
        response.robot_status_list = [self._to_ros_status(r) for r in self.robots.values()]
        return response

    def _handle_reset(self, request, response):
        for robot in self.robots.values():
            robot.reset()
        with self.occ_lock:
            self.occupancy.clear()
        for robot in self.robots.values():
            self._occ_inc(robot.cell)
        self._publish_fleet_status()
        self._publish_battery_updates()
        response.success = True
        response.message = f'Reset {len(self.robots)} robots to initial cells and battery'
        self.get_logger().info(response.message)
        return response

    # ------------------------------------------------------------------
    # Navigation on the grid
    # ------------------------------------------------------------------
    def _simulate_navigation(self, robot: MockRobot, waypoints: list, generation: int):
        step_time = (self.resolution / self.speed / self.time_scale) if self.speed > 0 else 0.0
        total_cells = 0
        total_energy = 0.0

        for wx, wy in waypoints:
            goal = self.world_grid.to_cell(wx, wy)
            with robot.task_lock:
                if robot.generation != generation:
                    return
                start = robot.cell

            grid = self.world_grid.grid_map(self._congestion_snapshot())
            result = astar_path(grid, start, goal, self.congestion_weight)
            if not result.found:
                self.get_logger().warn(
                    f'Robot {robot.robot_id}: no grid path {start} -> {goal}; skipping leg')
                continue

            # Physics energy for this leg, spread evenly across its cell steps.
            steps = max(1, len(result.path) - 1)
            leg_energy = path_energy_percent(result.path, self.resolution, self.model)
            per_cell_energy = leg_energy / steps

            for cell in result.path[1:]:
                if step_time > 0:
                    time.sleep(step_time)
                with robot.task_lock:
                    if robot.generation != generation:
                        return
                    self._occ_dec(robot.cell)
                    self._occ_inc(cell)
                    robot.cell = cell
                    total_cells += 1
                    total_energy += per_cell_energy
                    robot.battery = max(0.0, robot.battery - per_cell_energy)

        travel_dist = total_cells * self.resolution
        battery_consumed = round(total_energy, 3)

        with robot.task_lock:
            if robot.generation != generation:
                return
            if robot.battery <= self.low_threshold:
                robot.is_available = False
                robot.status = 'charging'
                self.get_logger().info(
                    f'Robot {robot.robot_id} entering charging at {robot.battery:.1f}%')
            else:
                robot.is_available = True
                robot.status = 'idle'

        self._publish_fleet_status()

        msg = String()
        msg.data = json.dumps({
            'robot_id': robot.robot_id,
            'robot_name': f'robot_{robot.robot_id}',
            'travel_dist': round(travel_dist, 3),
            'battery_consumed': battery_consumed,
        })
        self.travel_stats_pub.publish(msg)

    # ------------------------------------------------------------------
    # Periodic publishers
    # ------------------------------------------------------------------
    def _publish_fleet_status(self):
        msg = FleetStatus()
        msg.robot_status_list = [self._to_ros_status(r) for r in self.robots.values()]
        self.fleet_pub.publish(msg)

    def _publish_congestion(self):
        snapshot = self._congestion_snapshot()
        msg = String()
        msg.data = json.dumps({f'{cx},{cy}': count for (cx, cy), count in snapshot.items()})
        self.congestion_pub.publish(msg)

    def _publish_battery_updates(self):
        updates = {str(r.robot_id): round(r.battery, 1) for r in self.robots.values()}
        msg = String()
        msg.data = json.dumps(updates)
        self.battery_pub.publish(msg)

    def _charge_batteries(self):
        changed = False
        charge_delta = self.charge_rate_per_s * max(0.1, self.charge_tick_s)
        for robot in self.robots.values():
            with robot.task_lock:
                if robot.status != 'charging':
                    continue
                old = robot.battery
                robot.battery = min(100.0, robot.battery + charge_delta)
                changed = changed or robot.battery != old
                if robot.battery >= self.resume_threshold:
                    robot.is_available = True
                    robot.status = 'idle'
                    changed = True
                    self.get_logger().info(
                        f'Robot {robot.robot_id} charged to {robot.battery:.1f}% and is available.')
        if changed:
            self._publish_fleet_status()
            self._publish_battery_updates()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _to_ros_status(self, robot: MockRobot) -> RobotStatus:
        s = RobotStatus()
        s.robot_id = robot.robot_id
        wx, wy = self.world_grid.to_world(robot.cell)
        p = Pose()
        p.position.x = float(wx)
        p.position.y = float(wy)
        s.current_location = p
        s.battery_level = float(robot.battery)
        s.is_available = robot.is_available
        s.status = robot.status
        return s

    def _spawn_cells(self, n: int) -> list[tuple[int, int]]:
        """Pick n free cells nearest the drop-off (world origin)."""
        gm = self.world_grid.grid_map()
        cx0, cy0 = self.world_grid.to_cell(0.0, 0.0)
        free = [
            (x, y)
            for x in range(self.world_grid.width)
            for y in range(self.world_grid.height)
            if gm.is_free((x, y))
        ]
        free.sort(key=lambda c: (abs(c[0] - cx0) + abs(c[1] - cy0), c[1], c[0]))
        if len(free) < n:
            raise RuntimeError(f'Grid has {len(free)} free cells but {n} robots requested')
        return free[:n]


def main(args=None):
    rclpy.init(args=args)
    node = MockFleetManager()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
