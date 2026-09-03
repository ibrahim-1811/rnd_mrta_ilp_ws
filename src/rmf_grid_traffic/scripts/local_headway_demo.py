#!/usr/bin/env python3

from __future__ import annotations

import argparse
import math
import time
from dataclasses import dataclass


DT = 2.0 / 3.0
WIDTH = 8
HEIGHT = 3

ROBOT_RADIUS_M = 0.30
GRID_RESOLUTION_M = 1.0
MIN_CENTER_DISTANCE_M = 2.0 * ROBOT_RADIUS_M


@dataclass
class Robot:
    robot_id: int
    route: list[tuple[int, int]]
    route_index: int = 0
    wait_ticks: int = 0

    @property
    def position(self):
        return self.route[self.route_index]

    @property
    def finished(self):
        return self.route_index >= len(self.route) - 1

    @property
    def next_position(self):
        if self.finished:
            return self.position
        return self.route[self.route_index + 1]

    @property
    def remaining_distance(self):
        return len(self.route) - 1 - self.route_index

    def move(self):
        if not self.finished:
            self.route_index += 1

    def wait(self):
        self.wait_ticks += 1


def distance_m(a, b):
    dx = (a[0] - b[0]) * GRID_RESOLUTION_M
    dy = (a[1] - b[1]) * GRID_RESOLUTION_M
    return math.hypot(dx, dy)


def render(a, b, sim_time, event):
    grid = [[" " for _ in range(WIDTH)] for _ in range(HEIGHT)]

    for x in range(WIDTH):
        grid[1][x] = "."

    ax, ay = a.position
    bx, by = b.position

    grid[ay][ax] = "A"
    grid[by][bx] = "B"

    separation = distance_m(a.position, b.position)
    clearance = separation - 2.0 * ROBOT_RADIUS_M

    print("\033[2J\033[H", end="")

    print("LOCAL SAME-DIRECTION HEADWAY DEMO")
    print("=" * 58)
    print(f"simulation time       : {sim_time:.3f} s")
    print(f"scientific move step  : {DT:.3f} s")
    print(f"robot radius          : {ROBOT_RADIUS_M:.2f} m")
    print()

    for y in reversed(range(HEIGHT)):
        print(f"{y:2d}  " + " ".join(grid[y]))

    print()
    print("    " + " ".join(str(x) for x in range(WIDTH)))
    print()

    print(
        f"A position={a.position} "
        f"remaining={a.remaining_distance} "
        f"wait={a.wait_ticks}"
    )

    print(
        f"B position={b.position} "
        f"remaining={b.remaining_distance} "
        f"wait={b.wait_ticks}"
    )

    print()
    print(f"center separation     : {separation:.3f} m")
    print(f"surface clearance     : {clearance:.3f} m")
    print()
    print(f"EVENT: {event}")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--delay",
        type=float,
        default=0.8,
    )

    args = parser.parse_args()

    # Independent robots travelling in the same direction.
    #
    # A happens to be one grid cell ahead of B.
    # There is no leader/follower relationship.

    robot_a = Robot(
        robot_id=1,
        route=[
            (1, 1),
            (2, 1),
            (3, 1),
            (4, 1),
            (5, 1),
            (6, 1),
            (7, 1),
        ],
    )

    robot_b = Robot(
        robot_id=2,
        route=[
            (0, 1),
            (1, 1),
            (2, 1),
            (3, 1),
            (4, 1),
            (5, 1),
            (6, 1),
        ],
    )

    sim_time = 0.0

    render(
        robot_a,
        robot_b,
        sim_time,
        "Both robots may move.",
    )

    time.sleep(args.delay)

    while not (robot_a.finished and robot_b.finished):

        a_next = robot_a.next_position
        b_next = robot_b.next_position

        # Predict where both robots will be after this tick.
        predicted_distance = distance_m(a_next, b_next)

        vertex_conflict = a_next == b_next

        edge_swap = (
            a_next == robot_b.position
            and b_next == robot_a.position
        )

        unsafe_distance = (
            predicted_distance < MIN_CENTER_DISTANCE_M
        )

        if vertex_conflict or edge_swap or unsafe_distance:

            # In this demo B is the trailing robot.
            # It brakes only if simultaneous motion would be unsafe.
            if not robot_a.finished:
                robot_a.move()

            robot_b.wait()

            event = (
                "Trailing robot brakes for one local interval "
                "because the predicted headway is unsafe."
            )

        else:
            if not robot_a.finished:
                robot_a.move()

            if not robot_b.finished:
                robot_b.move()

            event = (
                "Safe same-direction headway. "
                "Both robots move simultaneously."
            )

        sim_time += DT

        # Hard safety check.
        actual_distance = distance_m(
            robot_a.position,
            robot_b.position,
        )

        if actual_distance < MIN_CENTER_DISTANCE_M:
            raise RuntimeError(
                f"Safety violation: distance={actual_distance:.3f} m"
            )

        render(
            robot_a,
            robot_b,
            sim_time,
            event,
        )

        time.sleep(args.delay)

    print()
    print("RESULT")
    print("=" * 58)
    print(f"robot_01 waiting : {robot_a.wait_ticks * DT:.3f} s")
    print(f"robot_02 waiting : {robot_b.wait_ticks * DT:.3f} s")
    print(f"completion time  : {sim_time:.3f} s")
    print()
    print("status           : OK")


if __name__ == "__main__":
    main()
