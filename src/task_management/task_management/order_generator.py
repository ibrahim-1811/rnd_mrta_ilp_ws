#!/usr/bin/env python3
"""Seeded order generator (experiment driver).

Publishes a reproducible stream of orders on /order_requests. Each order requests one
product from a random shelf. Same seed -> identical order sequence across strategies,
which is what makes the paired comparison fair.
"""

import random

import rclpy
from rclpy.node import Node
from robot_interfaces.msg import Order, Product
from robot_interfaces.srv import GetShelfList


class OrderGenerator(Node):
    def __init__(self):
        super().__init__('order_generator')
        self.declare_parameter('num_orders', 20)
        self.declare_parameter('rate_hz', 1.0)
        self.declare_parameter('seed', 42)

        self.num_orders = int(self.get_parameter('num_orders').value)
        rate = float(self.get_parameter('rate_hz').value)
        self.rng = random.Random(int(self.get_parameter('seed').value))

        self.pub = self.create_publisher(Order, '/order_requests', 10)
        self.shelf_client = self.create_client(GetShelfList, '/get_shelf_list')

        self.shelf_ids: list[int] = []
        self.sent = 0
        self.create_timer(1.0, self._fetch_shelves)
        self.period = max(0.05, 1.0 / rate if rate > 0 else 1.0)
        self._send_timer = None

    def _fetch_shelves(self):
        if self.shelf_ids or not self.shelf_client.service_is_ready():
            return
        fut = self.shelf_client.call_async(GetShelfList.Request())
        fut.add_done_callback(self._got_shelves)

    def _got_shelves(self, fut):
        self.shelf_ids = [s.shelf_id for s in fut.result().shelf_status_list]
        if self.shelf_ids and self._send_timer is None:
            self.get_logger().info(
                f'Order generator: {self.num_orders} orders over '
                f'{len(self.shelf_ids)} shelves at {1/self.period:.2f} Hz')
            self._send_timer = self.create_timer(self.period, self._send_one)

    def _send_one(self):
        if self.sent >= self.num_orders:
            self.get_logger().info(f'Order generator: sent all {self.sent} orders')
            self._send_timer.cancel()
            return
        self.sent += 1
        order = Order()
        order.order_id = self.sent
        product = Product()
        product.shelf_id = self.rng.choice(self.shelf_ids)
        product.quantity = 1
        order.product_list = [product]
        self.pub.publish(order)


def main(args=None):
    rclpy.init(args=args)
    node = OrderGenerator()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
