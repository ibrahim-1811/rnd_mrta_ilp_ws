#!/usr/bin/env python3
"""ILP dataset logger.

Subscribes to /ilp/task_allocation_event and writes one CSV row per candidate robot
per decision. This is the trace log the ILP pipeline (WP6/WP7) later converts into
Prolog facts and positive/negative examples.
"""

import csv
import json
import os
from datetime import datetime

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

FIELDS = [
    'timestamp', 'task_id', 'order_id', 'shelf_id', 'priority', 'strategy',
    'decision', 'chosen_robot', 'runtime_ms', 'candidate_robot', 'is_chosen',
    'reachable', 'distance', 'travel_time', 'congestion', 'energy_pct',
    'battery', 'workload', 'cumulative_load', 'battery_feasible', 'explanation',
]


class IlpDatasetLogger(Node):
    def __init__(self):
        super().__init__('ilp_dataset_logger')
        self.declare_parameter('out_dir', '/tmp/mrta_run/ilp_logs')
        out_dir = os.path.expanduser(os.path.expandvars(self.get_parameter('out_dir').value))
        os.makedirs(out_dir, exist_ok=True)

        stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        self.path = os.path.join(out_dir, f'ilp_dataset_{stamp}.csv')
        self._file = open(self.path, 'w', newline='')
        self._writer = csv.DictWriter(self._file, fieldnames=FIELDS)
        self._writer.writeheader()
        self._file.flush()
        self._rows = 0

        self.create_subscription(String, '/ilp/task_allocation_event', self._on_event, 50)
        self.get_logger().info(f'ILP dataset logger writing {self.path}')

    def _on_event(self, msg):
        e = json.loads(msg.data)
        ts = self.get_clock().now().nanoseconds / 1e9
        for c in e['candidates']:
            self._writer.writerow({
                'timestamp': f'{ts:.3f}',
                'task_id': e['task_id'],
                'order_id': e['order_id'],
                'shelf_id': e['shelf_id'],
                'priority': e['priority'],
                'strategy': e['strategy'],
                'decision': e['decision'],
                'chosen_robot': e['chosen_robot'],
                'runtime_ms': e['runtime_ms'],
                'candidate_robot': c['robot_id'],
                'is_chosen': int(c['robot_id'] == e['chosen_robot']),
                'reachable': int(c['reachable']),
                'distance': c['distance'],
                'travel_time': c['travel_time'],
                'congestion': c['congestion'],
                'energy_pct': c['energy_pct'],
                'battery': c['battery'],
                'workload': c['workload'],
                'cumulative_load': c['cumulative_load'],
                'battery_feasible': int(c['battery_feasible']),
                'explanation': e['explanation'],
            })
            self._rows += 1
        self._file.flush()

    def destroy_node(self):
        try:
            self._file.close()
        finally:
            super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = IlpDatasetLogger()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.get_logger().info(f'ILP dataset logger wrote {node._rows} rows to {node.path}')
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
