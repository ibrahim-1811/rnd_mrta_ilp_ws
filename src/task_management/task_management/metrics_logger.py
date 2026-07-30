#!/usr/bin/env python3
"""Runtime metrics logger.

Aggregates operational metrics for the current run and writes a rolling summary CSV:
completed tasks, makespan, throughput, mean travel distance, mean energy consumed,
mean allocator runtime, and deferral count. Fed by /fleet_travel_stats (completions)
and /ilp/task_allocation_event (allocator runtime + deferrals).
"""

import csv
import json
import os
from datetime import datetime

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class MetricsLogger(Node):
    def __init__(self):
        super().__init__('metrics_logger')
        self.declare_parameter('out_dir', '/tmp/mrta_run')
        out_dir = os.path.expanduser(os.path.expandvars(self.get_parameter('out_dir').value))
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        self.path = os.path.join(out_dir, f'metrics_{stamp}.csv')

        self.strategy = 'unknown'
        self.n_completed = 0
        self.total_travel = 0.0
        self.total_energy = 0.0
        self.runtimes = []
        self.n_defer = 0
        self.n_assign = 0
        self.first_ts = None
        self.last_ts = None

        self.create_subscription(String, '/fleet_travel_stats', self._on_done, 50)
        self.create_subscription(String, '/ilp/task_allocation_event', self._on_event, 50)
        self.get_logger().info(f'Metrics logger writing {self.path}')

    def _now(self):
        return self.get_clock().now().nanoseconds / 1e9

    def _on_event(self, msg):
        e = json.loads(msg.data)
        self.strategy = e['strategy']
        self.runtimes.append(float(e['runtime_ms']))
        if e['decision'] == 'defer':
            self.n_defer += 1
        else:
            self.n_assign += 1
        if self.first_ts is None:
            self.first_ts = self._now()
        self._write()

    def _on_done(self, msg):
        d = json.loads(msg.data)
        self.n_completed += 1
        self.total_travel += float(d.get('travel_dist', 0.0))
        self.total_energy += float(d.get('battery_consumed', 0.0))
        self.last_ts = self._now()
        self._write()

    def _write(self):
        makespan = (self.last_ts - self.first_ts) if (self.first_ts and self.last_ts) else 0.0
        throughput = (self.n_completed / makespan) if makespan > 0 else 0.0
        mean_rt = (sum(self.runtimes) / len(self.runtimes)) if self.runtimes else 0.0
        mean_travel = (self.total_travel / self.n_completed) if self.n_completed else 0.0
        mean_energy = (self.total_energy / self.n_completed) if self.n_completed else 0.0
        row = {
            'strategy': self.strategy,
            'n_assign': self.n_assign,
            'n_defer': self.n_defer,
            'n_completed': self.n_completed,
            'makespan_s': round(makespan, 3),
            'throughput_ops': round(throughput, 4),
            'mean_travel_m': round(mean_travel, 3),
            'mean_energy_pct': round(mean_energy, 3),
            'mean_alloc_runtime_ms': round(mean_rt, 4),
        }
        with open(self.path, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            w.writeheader()
            w.writerow(row)


def main(args=None):
    rclpy.init(args=args)
    node = MetricsLogger()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
