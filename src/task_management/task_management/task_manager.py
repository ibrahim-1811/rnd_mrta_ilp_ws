#!/usr/bin/env python3
"""Task manager: online, non-blocking allocation loop.

For every incoming order it turns products into pickup->drop tasks, and on a fast
decision timer assigns each pending task to an idle robot using the selected
strategy. Unlike the old FIFO manager it dispatches to *all* free robots each tick
(no head-of-line blocking), and defers a task (re-queues it) when a strategy returns
no eligible robot.

Contracts (headless):
  subscribes : /order_requests (Order), /fleet_status (FleetStatus, latched),
               /simulation/congestion (String JSON), /fleet_travel_stats (String JSON)
  clients    : /get_shelf_list (GetShelfList), /get_drop_off_pose (GetPose),
               /task_list (TaskList)
  publishes  : /ilp/task_allocation_event (String JSON), /end_order (Order)

The WorldGrid and RobotModel are built from the same parameters as the simulator so
the features every strategy sees are exactly what the robots will do.
"""

import json
import time

import rclpy
from mrta_core.grid import Cell
from mrta_core.kinematics import RobotModel
from mrta_core.world import WorldGrid
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from robot_interfaces.msg import FleetStatus, Order, Product, Task
from robot_interfaces.srv import GetPose, GetShelfList, TaskList
from std_msgs.msg import String

from task_management.allocation_strategy import get_strategy
from task_management.features import build_candidates


class TaskManager(Node):
    def __init__(self):
        super().__init__('task_manager_node')

        self.declare_parameter('allocation_strategy', 'auction')
        self.declare_parameter('allocator_seed', 0)
        self.declare_parameter('world_size_m', 30.0)
        self.declare_parameter('resolution_m', 1.0)
        self.declare_parameter('robot_speed_ms', 2.0)
        self.declare_parameter('min_battery_after', 20.0)

        self.strategy_name = self.get_parameter('allocation_strategy').value.lower()
        seed = int(self.get_parameter('allocator_seed').value)
        world_size = float(self.get_parameter('world_size_m').value)
        resolution = float(self.get_parameter('resolution_m').value)
        speed = float(self.get_parameter('robot_speed_ms').value)
        self.min_battery_after = float(self.get_parameter('min_battery_after').value)

        self.world_grid = WorldGrid.square(world_size, resolution)
        self.model = RobotModel(v_max=speed)
        self.allocator = get_strategy(self.strategy_name, seed=seed)

        # State
        self._fleet = []                       # latest RobotStatus list
        self._congestion: dict[Cell, float] = {}
        self._pending: list[dict] = []         # queued tasks
        self._workload: dict[int, int] = {}
        self._cumulative_load: dict[int, float] = {}
        self._robot_task: dict[int, dict] = {}  # robot_id -> task in flight
        self._task_counter = 0
        self._orders_seen = 0
        self._completed = 0
        self._shelves: dict[int, object] = {}   # shelf_id -> Pose
        self._drop_pose = None

        latched = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST, depth=10,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)

        self.create_subscription(Order, '/order_requests', self._on_order, 10)
        self.create_subscription(String, '/simulation/congestion', self._on_congestion, 10)
        self.create_subscription(String, '/fleet_travel_stats', self._on_travel_done, 10)
        self.create_subscription(FleetStatus, '/fleet_status', self._on_fleet, latched)

        self.event_pub = self.create_publisher(String, '/ilp/task_allocation_event', 10)
        self.end_order_pub = self.create_publisher(Order, '/end_order', 10)

        self.shelf_client = self.create_client(GetShelfList, '/get_shelf_list')
        self.drop_client = self.create_client(GetPose, '/get_drop_off_pose')
        self.task_client = self.create_client(TaskList, '/task_list')

        self.create_timer(1.0, self._init_caches)   # fetch shelves + drop-off once
        self.create_timer(0.2, self._dispatch)       # 5 Hz decision loop
        self.create_timer(2.0, self._heartbeat)      # visible progress line

        self.get_logger().info(
            f'TaskManager: strategy={self.strategy_name}, seed={seed}, '
            f'grid={self.world_grid.width}x{self.world_grid.height}')

    # ------------------------------------------------------------------
    # Cache init
    # ------------------------------------------------------------------
    def _init_caches(self):
        if self._drop_pose is None and self.drop_client.service_is_ready():
            fut = self.drop_client.call_async(GetPose.Request())
            fut.add_done_callback(self._set_drop)
        if not self._shelves and self.shelf_client.service_is_ready():
            fut = self.shelf_client.call_async(GetShelfList.Request())
            fut.add_done_callback(self._set_shelves)

    def _set_drop(self, fut):
        self._drop_pose = fut.result().pose

    def _set_shelves(self, fut):
        for s in fut.result().shelf_status_list:
            self._shelves[s.shelf_id] = s.shelf_location
        self.get_logger().info(f'Loaded {len(self._shelves)} shelves')

    def _ready(self) -> bool:
        return self._drop_pose is not None and bool(self._shelves) and bool(self._fleet)

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------
    def _on_fleet(self, msg):
        self._fleet = msg.robot_status_list

    def _on_congestion(self, msg):
        raw = json.loads(msg.data)
        self._congestion = {
            (int(k.split(',')[0]), int(k.split(',')[1])): float(v) for k, v in raw.items()}

    def _heartbeat(self):
        in_flight = sum(self._workload.values())
        if self._orders_seen or self._pending or in_flight:
            self.get_logger().info(
                f'status: orders={self._orders_seen} pending={len(self._pending)} '
                f'in_flight={in_flight} completed={self._completed}')

    def _on_order(self, msg: Order):
        self._orders_seen += 1
        now = self.get_clock().now().nanoseconds / 1e9
        for product in msg.product_list:
            if product.shelf_id not in self._shelves:
                continue
            self._task_counter += 1
            self._pending.append({
                'task_id': f'order_{msg.order_id}_task_{self._task_counter}',
                'order_id': msg.order_id,
                'shelf_id': product.shelf_id,
                'quantity': product.quantity,
                'priority': 1,
                'deadline': now + 300.0,
                'release': now,
            })

    def _on_travel_done(self, msg):
        data = json.loads(msg.data)
        rid = int(data['robot_id'])
        self._workload[rid] = max(0, self._workload.get(rid, 0) - 1)
        self._completed += 1
        task = self._robot_task.pop(rid, None)
        if task is not None:
            order = Order()
            order.order_id = int(task['order_id'])
            p = Product()
            p.shelf_id = int(task['shelf_id'])
            p.quantity = int(task['quantity'])
            order.product_list = [p]
            self.end_order_pub.publish(order)

    # ------------------------------------------------------------------
    # Decision loop
    # ------------------------------------------------------------------
    def _dispatch(self):
        if not self._ready() or not self._pending:
            return

        idle = [r for r in self._fleet if r.is_available]
        if not idle:
            return

        if self.strategy_name == 'edf':
            self._pending.sort(key=lambda t: t['deadline'])

        drop_cell = self.world_grid.to_cell(
            self._drop_pose.position.x, self._drop_pose.position.y)
        assigned = set()
        remaining = []

        for task in self._pending:
            pool = [r for r in idle if r.robot_id not in assigned]
            if not pool:
                remaining.append(task)
                continue

            shelf_pose = self._shelves[task['shelf_id']]
            shelf_cell = self.world_grid.to_cell(shelf_pose.position.x, shelf_pose.position.y)
            robots = [(
                r.robot_id,
                self.world_grid.to_cell(r.current_location.position.x,
                                        r.current_location.position.y),
                float(r.battery_level),
                self._workload.get(r.robot_id, 0),
                self._cumulative_load.get(r.robot_id, 0.0),
            ) for r in pool]

            candidates = build_candidates(
                self.world_grid, self._congestion, robots, shelf_cell, drop_cell,
                self.model, priority=task['priority'],
                min_battery_after=self.min_battery_after)

            t0 = time.perf_counter()
            chosen = self.allocator.select(candidates)
            runtime_ms = (time.perf_counter() - t0) * 1000.0
            explanation = self.allocator.explain(candidates, chosen)
            self._publish_event(task, candidates, chosen, runtime_ms, explanation)

            if chosen is None:
                remaining.append(task)
                continue

            self._assign(chosen, task, candidates, shelf_pose)
            assigned.add(chosen)
            self.get_logger().info(f'[assign] {task["task_id"]} -> robot {chosen} | {explanation}')

        self._pending = remaining

    def _assign(self, robot_id, task, candidates, shelf_pose):
        chosen = next(c for c in candidates if c.robot_id == robot_id)

        pickup = Task()
        pickup.task_id = self._task_counter
        pickup.robot_id = robot_id
        pickup.shelf_id = int(task['shelf_id'])
        pickup.item = 'item'
        pickup.item_amount = int(task['quantity'])
        pickup.shelf_location = shelf_pose
        pickup.task_type = 'pickup'

        dropoff = Task()
        dropoff.robot_id = robot_id
        dropoff.shelf_location = self._drop_pose
        dropoff.task_type = 'dropoff'

        req = TaskList.Request()
        req.task_list = [pickup, dropoff]
        self.task_client.call_async(req)

        self._workload[robot_id] = self._workload.get(robot_id, 0) + 1
        self._cumulative_load[robot_id] = (
            self._cumulative_load.get(robot_id, 0.0) + chosen.proc_time)
        self._robot_task[robot_id] = task

    def _publish_event(self, task, candidates, chosen, runtime_ms, explanation):
        payload = {
            'task_id': task['task_id'],
            'order_id': task['order_id'],
            'shelf_id': task['shelf_id'],
            'priority': task['priority'],
            'strategy': self.strategy_name,
            'decision': 'assign' if chosen is not None else 'defer',
            'chosen_robot': chosen,
            'runtime_ms': round(runtime_ms, 4),
            'explanation': explanation,
            'candidates': [{
                'robot_id': c.robot_id,
                'reachable': c.reachable,
                'distance': round(c.distance, 3) if c.reachable else None,
                'travel_time': round(c.travel_time, 3) if c.reachable else None,
                'congestion': round(c.congestion, 3) if c.reachable else None,
                'energy_pct': round(c.energy_pct, 3) if c.reachable else None,
                'battery': round(c.battery, 2),
                'workload': c.workload,
                'cumulative_load': round(c.cumulative_load, 3),
                'battery_feasible': c.battery_feasible,
            } for c in candidates],
        }
        msg = String()
        msg.data = json.dumps(payload)
        self.event_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = TaskManager()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
