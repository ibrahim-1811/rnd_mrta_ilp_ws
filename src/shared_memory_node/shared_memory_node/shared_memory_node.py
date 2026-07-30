"""Shared Memory Node.

Centralized warehouse database for the grid-aware MRTA system. Loads shelves and the
drop-off pose from a CSV file and exposes them over ROS 2 services. Also tracks the
latest fleet status (from ``/fleet_status``) and updates inventory when orders complete.

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
from robot_interfaces.msg import FleetStatus, Logs, Order, ShelfStatus
from robot_interfaces.srv import (
    GetPose,
    GetRobotFleetStatus,
    GetRobotStatus,
    GetShelfList,
    InventoryUpdate,
    ShelfQuery,
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

        self.log_to_central('INFO', 'Database Module is ready.')

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
        response.success = True
        response.message = (
            f'reset shared memory inventory: {len(self.shelves)} shelves reloaded')
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
