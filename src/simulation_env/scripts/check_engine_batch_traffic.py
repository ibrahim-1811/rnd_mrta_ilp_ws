#!/usr/bin/env python3

from fractions import Fraction

from simulation_env.fleet_engine import (
    EventType,
    FleetSimulationEngine,
)
from simulation_env.grid_robot import GridRobotStatus


class FakeRobot:
    """Minimum robot interface needed by _try_moves_batch()."""

    def __init__(self, robot_id, path):
        self.robot_id = robot_id
        self.cell = path[0]
        self.path = list(path)
        self.path_index = 0
        self.speed_mps = 1.5
        self.status = GridRobotStatus.IDLE

        self.wait_event_count = 0
        self.vertex_conflict_count = 0
        self.edge_conflict_count = 0
        self.waiting_time_s = 0.0

    @property
    def finished(self):
        return self.path_index >= len(self.path) - 1

    def book_wait(self, seconds):
        self.waiting_time_s += float(seconds)


engine = FleetSimulationEngine(
    warehouse=object(),
    resolution=1.0,
    service_seconds={},
)


robots = [
    FakeRobot(
        "robot_001",
        [(2, 1), (3, 1)],
    ),
    FakeRobot(
        "robot_002",
        [(1, 1), (2, 1)],
    ),
    FakeRobot(
        "robot_003",
        [(0, 1), (1, 1)],
    ),
]


for robot in robots:
    engine.robots[robot.robot_id] = robot

    engine.reservations.occupy(
        robot.cell,
        robot.robot_id,
        Fraction(0),
    )


requests = [
    (
        "robot_001",
        EventType.MOVE_COMPLETE,
        "",
    ),
    (
        "robot_002",
        EventType.MOVE_COMPLETE,
        "",
    ),
    (
        "robot_003",
        EventType.MOVE_COMPLETE,
        "",
    ),
]


print()
print("=" * 72)
print("FLEET SIMULATION ENGINE BATCH TRAFFIC")
print("=" * 72)


started = engine._try_moves_batch(requests)


print()
print("started:")
for robot_id in sorted(started):
    print(f"  {robot_id}")


print()
print("reservation ownership after batch start:")

for cell in [
    (0, 1),
    (1, 1),
    (2, 1),
    (3, 1),
]:
    print(f"  {cell}: {engine.reservations.owner(cell)}")


print()
print("scheduled events:")

events = sorted(
    (item[2] for item in engine._queue),
    key=lambda event: (
        event.time,
        event.robot_id,
        event.kind.value,
    ),
)

for event in events:
    print(f"  {event.robot_id} {event.kind.value} t={float(event.time):.6f}")


assert started == {
    "robot_001",
    "robot_002",
    "robot_003",
}

assert engine.reservations.owner((3, 1)) == "robot_001"

assert engine.reservations.owner((2, 1)) == "robot_002"

assert engine.reservations.owner((1, 1)) == "robot_003"

assert engine.reservations.owner((0, 1)) is None

assert len(events) == 3

assert all(event.time == Fraction(2, 3) for event in events)

assert all(robot.status is GridRobotStatus.MOVING for robot in robots)


print()
print("engine arbitration       : PASS")
print("three robots started     : PASS")
print("atomic cell handoff      : PASS")
print("common completion time   : PASS")
print("scientific move time     : 0.666667 s")

print()
print("status                   : OK")
