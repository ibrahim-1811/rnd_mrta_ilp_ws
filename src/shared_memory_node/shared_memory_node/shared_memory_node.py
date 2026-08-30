"""Shared Memory Node.

Centralized warehouse database for the grid-aware MRTA system. Loads shelves and the
drop-off pose from a CSV file and exposes them over ROS 2 services. Also tracks the
latest fleet status (from ``/fleet_status``) and updates inventory when orders complete.

Fleet registry (grid fleet, string robot ids):
  - ``/get_robot_states``   (GetRobotStates)  query robots by status
  - subscribes ``/mrta/robot_states`` (RobotStateArray)

  This is the grid fleet, keyed by string ids such as ``robot_001``.
  It sits alongside the legacy int-id ``/fleet_status`` cache rather
  than replacing it. Availability is derived: status == "IDLE".

Order registry (added for the config-driven order pipeline):
  - ``/get_order_states``    (GetOrderStates)   query orders by status
  - ``/update_order_state``  (UpdateOrderState) report a lifecycle transition
  - ``/mrta/order_states``   (OrderStateArray)  latched live order state

Orders are loaded from the central simulation config when the
``config_file`` parameter is set. This node is the single owner of
order lifecycle state; executors report transitions here rather than
keeping their own copy.

Services provided:
  - ``/shelf_query``            (ShelfQuery)          shelf pose/capacity/inventory by id
  - ``/update_inventory``       (InventoryUpdate)     set a shelf's inventory
  - ``/get_robot_state``        (GetRobotStatus)      one robot's cached state
  - ``/get_robot_fleet_status`` (GetRobotFleetStatus) whole fleet's cached state
  - ``/get_shelf_list``         (GetShelfList)        all shelves
  - ``/get_drop_off_pose``      (GetPose)             drop-off pose
  - ``/reset_shared_memory``    (Trigger)             reload inventory from CSV between trials

Subscriptions:
  - ``/end_order``    (Order)        decrement inventory on completion
  - ``/fleet_status`` (FleetStatus)  cache latest fleet snapshot

The CSV is in metres (world coordinates). The metre->grid-cell mapping is applied
downstream in ``simulation_env`` / feature extraction, not here.
"""

import csv
import os

import rclpy
from ament_index_python.packages import get_package_share_directory
from geometry_msgs.msg import Pose
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from mrta_core.orders import OrderStatus, load_orders_csv
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from robot_interfaces.msg import (
    FleetStatus,
    Logs,
    Order,
    OrderState,
    OrderStateArray,
    RobotState,
    RobotStateArray,
    ShelfStatus,
)
from robot_interfaces.srv import (
    GetOrderStates,
    GetPose,
    GetRobotStates,
    GetRobotFleetStatus,
    GetRobotStatus,
    GetShelfList,
    InventoryUpdate,
    ShelfQuery,
    UpdateOrderState,
)
from std_srvs.srv import Trigger


class SharedMemoryNode(Node):
    def __init__(self):
        super().__init__('shared_memory_node')

        database_dir = get_package_share_directory('shared_memory_node')
        database_name = 'headless_shelves_100'
        default_database_file = os.path.join(
            database_dir, 'databases', database_name + '.csv')
        self.declare_parameter('database_file', default_database_file)
        self.database_file = self.get_parameter('database_file').value
        self.drop_off_pose = Pose()

        self.database = {'shelves': []}
        self.robots = []
        self.shelves = []

        # Order registry. Ordered by CSV appearance so 'first released
        # pending order' is deterministic.
        self.declare_parameter('config_file', '')
        self.config_file = self.get_parameter('config_file').value
        self.orders = {}
        self.order_sequence = []

        # Grid fleet, keyed by string robot_id, fed by the fleet node.
        self.robot_states = {}
        self.robot_sequence = []

        self.log_publisher = self.create_publisher(Logs, '/central_logs', 10)

        self.load_inventory(self.database_file)
        self.database['shelves'] = self.shelves
        self.database['robots'] = self.robots

        qos_profile = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )

        self.order_end_sub = self.create_subscription(
            Order, '/end_order', self.order_end_callback, 10)
        self.fleet_status_sub = self.create_subscription(
            FleetStatus, '/fleet_status', self.fleet_status_callback,
            qos_profile=qos_profile)

        self.database_query_service = self.create_service(
            ShelfQuery, '/shelf_query', self.shelf_query_callback)
        self.inventory_update_service = self.create_service(
            InventoryUpdate, '/update_inventory', self.inventory_update_callback)
        self.robot_state_service = self.create_service(
            GetRobotStatus, '/get_robot_state', self.robot_state_callback)
        self.robot_fleet_service = self.create_service(
            GetRobotFleetStatus, '/get_robot_fleet_status',
            self.get_fleet_status_callback)
        self.shelf_list_service = self.create_service(
            GetShelfList, '/get_shelf_list', self.get_shelf_list_callback)
        self.get_drop_off_pose_service = self.create_service(
            GetPose, '/get_drop_off_pose', self.get_drop_off_pose_callback)
        self.reset_service = self.create_service(
            Trigger, '/reset_shared_memory', self.reset_shared_memory_callback)

        # Depth 1: a state topic should hand a late subscriber the
        # current snapshot, not replay a backlog of stale ones.
        order_qos = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.robot_states_sub = self.create_subscription(
            RobotStateArray, '/mrta/robot_states',
            self.robot_states_callback, qos_profile=order_qos)
        self.get_robot_states_service = self.create_service(
            GetRobotStates, '/get_robot_states',
            self.get_robot_states_callback)

        self.order_states_pub = self.create_publisher(
            OrderStateArray, '/mrta/order_states', order_qos)
        self.get_order_states_service = self.create_service(
            GetOrderStates, '/get_order_states', self.get_order_states_callback)
        self.update_order_state_service = self.create_service(
            UpdateOrderState, '/update_order_state',
            self.update_order_state_callback)

        if self.config_file:
            self.load_orders(self.config_file)

        self.publish_order_states()

        self.log_to_central('INFO', 'Database Module is ready.')

    # ------------------------------------------------------------------
    # Fleet registry (grid fleet)
    # ------------------------------------------------------------------

    def robot_states_callback(self, msg):
        """Cache the authoritative fleet snapshot from the fleet node.

        State originates in GridRobot, never in Gazebo.
        """
        first = not self.robot_states

        for state in msg.robots:
            if state.robot_id not in self.robot_states:
                self.robot_sequence.append(state.robot_id)
            self.robot_states[state.robot_id] = state

        if first and self.robot_states:
            self.log_to_central(
                'INFO',
                f'Fleet registered: {len(self.robot_states)} robot(s)')

    def get_robot_states_callback(self, request, response):
        wanted = (request.status_filter or '').strip().upper()
        response.robots = [
            self.robot_states[rid]
            for rid in self.robot_sequence
            if not wanted or self.robot_states[rid].status == wanted
        ]
        return response

    # ------------------------------------------------------------------
    # Order registry
    # ------------------------------------------------------------------

    def load_orders(self, config_file):
        """Load orders named by the central simulation config.

        Shelf and drop-off ids are validated against the warehouse map,
        so a typo fails here rather than mid-mission.
        """
        try:
            config = load_sim_config(config_file)
            warehouse = load_warehouse_xlsx(config.map_path)
            loaded = load_orders_csv(config.orders_path, warehouse)
        except Exception as e:  # noqa: BLE001 - report any config/CSV failure
            self.log_to_central('ERROR', f'Error loading orders: {e}')
            return

        for order in loaded:
            self.orders[order.order_id] = order
            self.order_sequence.append(order.order_id)

        items = sum(len(o.items) for o in loaded)
        self.log_to_central(
            'INFO',
            f'Loaded {len(loaded)} order(s), {items} line item(s) '
            f'from {config.orders_path}')

    def order_to_msg(self, order):
        msg = OrderState()
        msg.order_id = order.order_id
        msg.release_time = float(order.release_time)
        msg.deadline = float(order.deadline)
        msg.dropoff_id = order.dropoff_id
        msg.status = order.status.value
        msg.assigned_robot = order.assigned_robot
        msg.item_ids = [i.item_id for i in order.items]
        msg.shelf_ids = [i.shelf_id for i in order.items]
        msg.quantities = [int(i.quantity) for i in order.items]
        msg.start_time = (
            -1.0 if order.start_time is None else float(order.start_time))
        msg.completion_time = (
            -1.0 if order.completion_time is None
            else float(order.completion_time))
        return msg

    def publish_order_states(self):
        msg = OrderStateArray()
        msg.orders = [
            self.order_to_msg(self.orders[oid]) for oid in self.order_sequence]
        self.order_states_pub.publish(msg)

    def get_order_states_callback(self, request, response):
        wanted = (request.status_filter or '').strip().upper()
        response.orders = [
            self.order_to_msg(self.orders[oid])
            for oid in self.order_sequence
            if not wanted or self.orders[oid].status.value == wanted
        ]
        return response

    def update_order_state_callback(self, request, response):
        """Apply one order lifecycle transition."""
        order = self.orders.get(request.order_id)

        if order is None:
            response.success = False
            response.message = f'unknown order: {request.order_id}'
            self.log_to_central('WARN', response.message)
            return response

        try:
            status = OrderStatus(request.status.strip().upper())
        except ValueError:
            response.success = False
            response.message = f'unknown status: {request.status}'
            self.log_to_central('WARN', response.message)
            return response

        order.status = status

        if request.assigned_robot:
            order.assigned_robot = request.assigned_robot

        if status is OrderStatus.IN_PROGRESS and order.start_time is None:
            order.start_time = float(request.sim_time)

        if status is OrderStatus.COMPLETED:
            order.completion_time = float(request.sim_time)

        self.publish_order_states()

        response.success = True
        response.message = (
            f'{order.order_id} -> {status.value}'
            + (f' ({order.assigned_robot})' if order.assigned_robot else ''))
        self.log_to_central('INFO', f'Order {response.message}')
        return response

    def log_to_central(self, level, message):
        """Publish a log line to the central logging topic."""
        log_msg = Logs()
        log_msg.timestamp = self.get_clock().now().to_msg()
        log_msg.node_name = 'Shared Memory'
        log_msg.log_level = level
        log_msg.message = message
        self.log_publisher.publish(log_msg)

    def load_inventory(self, database_file):
        """Read shelves and drop-off pose from the CSV database.

        Row with ``id == 0`` is the drop-off pose; all other rows are shelves.
        """
        try:
            with open(database_file, mode='r') as file:
                reader = csv.DictReader(file)
                for idx, row in enumerate(reader):
                    shelf_pose = Pose()
                    shelf_pose.position.x = float(row['X'])
                    shelf_pose.position.y = float(row['Y'])
                    shelf_pose.position.z = float(row['Z'])
                    shelf_pose.orientation.x = float(row['Q_X'])
                    shelf_pose.orientation.y = float(row['Q_Y'])
                    shelf_pose.orientation.z = float(row['Q_Z'])
                    shelf_pose.orientation.w = float(row['Q_W'])

                    if idx == 0:
                        self.drop_off_pose = shelf_pose
                        self.log_to_central(
                            'INFO', f"Drop off location loaded with id: {row['id']}")
                    else:
                        shelf = ShelfStatus()
                        shelf.shelf_id = int(row['id'])
                        shelf.shelf_location = shelf_pose
                        shelf.product = row['product']
                        shelf.shelf_capacity = int(row['capacity'])
                        shelf.current_inventory = int(row['inventory'])
                        self.shelves.append(shelf)

            self.log_to_central('INFO', 'Inventory successfully loaded from CSV.')
        except Exception as e:  # noqa: BLE001 - report any CSV/parse failure
            self.log_to_central('ERROR', f'Error loading inventory from CSV: {e}')

    def order_end_callback(self, order):
        """Decrement inventory for each product in a completed order."""
        if not order.product_list:
            self.log_to_central('WARN', 'No product list in order.')
            return
        self.update_inventory_status(order)

    def update_inventory_status(self, order):
        if not self.shelves:
            self.log_to_central('INFO', 'No shelves available to update inventory.')
            return
        for product in order.product_list:
            for idx, shelf in enumerate(self.shelves):
                if shelf.shelf_id == product.shelf_id:
                    shelf.current_inventory -= product.quantity
                    self.shelves[idx] = shelf
                    self.log_to_central(
                        'INFO',
                        f'Inventory updated: {shelf.current_inventory} items of type '
                        f'{shelf.product} left on shelf {shelf.shelf_id}')

    def fleet_status_callback(self, msg):
        self.robots = msg.robot_status_list

    def shelf_query_callback(self, request, response):
        shelf_id = request.shelf_id
        for shelf in self.shelves:
            if shelf_id == shelf.shelf_id:
                response.shelf_location = shelf.shelf_location
                response.shelf_capacity = shelf.shelf_capacity
                response.current_inventory = shelf.current_inventory
                self.log_to_central('INFO', f'Query successful for shelf_id: {shelf_id}')
                return response
        response.shelf_location = Pose()
        response.shelf_capacity = 0
        response.current_inventory = 0
        self.log_to_central('WARN', f'Shelf_id {shelf_id} not found in database.')
        return response

    def robot_state_callback(self, request, response):
        robot_id = request.robot_id
        for robot in self.robots:
            if robot_id == robot.robot_id:
                response.current_location = robot.current_location
                response.battery_level = float(robot.battery_level)
                response.is_available = robot.is_available
                response.status = robot.status
                self.log_to_central('INFO', f'Query successful for robot_id: {robot_id}')
                return response
        response.current_location = Pose()
        response.battery_level = 0.0
        response.is_available = False
        self.log_to_central('WARN', f'Robot_id {robot_id} not found in database.')
        return response

    def inventory_update_callback(self, request, response):
        shelf_id = request.shelf_id
        response.success = False
        for index, shelf in enumerate(self.shelves):
            if shelf_id == shelf.shelf_id:
                self.shelves[index].current_inventory = request.new_inventory
                response.success = True
                self.log_to_central('INFO', f'Inventory updated for shelf_id: {shelf_id}')
                break
        if not response.success:
            self.log_to_central('WARN', f'Shelf_id {shelf_id} not found in database.')
        return response

    def get_fleet_status_callback(self, request, response):
        if not self.robots:
            self.log_to_central('WARN', 'NO Robot Data')
            response.robot_status_list = []
        else:
            response.robot_status_list = self.robots
        return response

    def get_shelf_list_callback(self, request, response):
        if not self.shelves:
            self.log_to_central('WARN', 'NO Shelf Data to return.')
            response.shelf_status_list = []
            return response
        response.shelf_status_list = self.shelves
        return response

    def get_drop_off_pose_callback(self, request, response):
        response.pose = self.drop_off_pose
        return response

    def reset_shared_memory_callback(self, request, response):
        """Reload shelves/inventory from CSV between benchmark trials."""
        self.shelves = []
        self.database = {'shelves': [], 'robots': self.robots}
        self.load_inventory(self.database_file)
        self.database['shelves'] = self.shelves

        # Orders too, so a trial can be repeated without restarting.
        self.orders = {}
        self.order_sequence = []

        # Grid fleet, keyed by string robot_id, fed by the fleet node.
        self.robot_states = {}
        self.robot_sequence = []
        if self.config_file:
            self.load_orders(self.config_file)
        self.publish_order_states()

        response.success = True
        response.message = (
            f'reset shared memory: {len(self.shelves)} shelves and '
            f'{len(self.orders)} order(s) reloaded')
        self.log_to_central('INFO', response.message)
        return response


def main(args=None):
    rclpy.init(args=args)
    node = SharedMemoryNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
