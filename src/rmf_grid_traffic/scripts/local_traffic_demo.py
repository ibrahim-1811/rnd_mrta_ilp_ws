#!/usr/bin/env python3

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass


DT = 2.0 / 3.0
WIDTH = 7
HEIGHT = 6

CONFLICT_CELL = (3, 2)


@dataclass
class Robot:
    robot_id: int
    route: list[tuple[int, int]]
    busy: bool = True
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


def priority_key(robot: Robot):
    """
    Smaller tuple wins.

    1. Busy robot beats idle robot.
    2. Smaller remaining distance wins.
    3. Smaller robot_id breaks an exact tie.
    """
    return (
        0 if robot.busy else 1,
        robot.remaining_distance,
        robot.robot_id,
    )


def choose_winner(a: Robot, b: Robot):
    return min((a, b), key=priority_key)


def render(a: Robot, b: Robot, sim_time: float, event: str):
    grid = [[" " for _ in range(WIDTH)] for _ in range(HEIGHT)]

    # Show the two intended routes.
    for x, y in a.route:
        grid[y][x] = "."

    for x, y in b.route:
        if grid[y][x] == ".":
            grid[y][x] = "+"
        else:
            grid[y][x] = "."

    cx, cy = CONFLICT_CELL

    if grid[cy][cx] not in ("A", "B"):
        grid[cy][cx] = "X"

    ax, ay = a.position
    bx, by = b.position

    grid[ay][ax] = "A"
    grid[by][bx] = "B"

    print("\033[2J\033[H", end="")

    print("LOCAL TRAFFIC RIGHT-OF-WAY DEMO")
    print("=" * 54)
    print(f"scientific move interval : {DT:.3f} s")
    print(f"simulation time          : {sim_time:.3f} s")
    print()
    print("A = robot_01")
    print("B = robot_02")
    print("X = local conflict cell")
    print()

    for y in reversed(range(HEIGHT)):
        print(f"{y:2d}  " + " ".join(grid[y]))

    print()
    print("    " + " ".join(str(x) for x in range(WIDTH)))
    print()

    print(
        f"A position={a.position}"
        f"  remaining={a.remaining_distance}"
        f"  wait={a.wait_ticks}"
    )

    print(
        f"B position={b.position}"
        f"  remaining={b.remaining_distance}"
        f"  wait={b.wait_ticks}"
    )

    print()
    print(f"EVENT: {event}")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--delay",
        type=float,
        default=0.8,
        help="Wall-clock delay between terminal frames",
    )

    args = parser.parse_args()

    # Robot A moves left to right.
    #
    # At the conflict decision it will have less remaining
    # distance than Robot B.
    robot_a = Robot(
        robot_id=1,
        route=[
            (0, 2),
            (1, 2),
            (2, 2),
            (3, 2),
            (4, 2),
        ],
        busy=True,
    )

    # Robot B moves top to bottom.
    robot_b = Robot(
        robot_id=2,
        route=[
            (3, 5),
            (3, 4),
            (3, 3),
            (3, 2),
            (3, 1),
            (3, 0),
        ],
        busy=True,
    )

    sim_time = 0.0

    render(
        robot_a,
        robot_b,
        sim_time,
        "Both robots moving.",
    )

    time.sleep(args.delay)

    while not (robot_a.finished and robot_b.finished):
        a_next = robot_a.next_position
        b_next = robot_b.next_position

        # --------------------------------------------------
        # Local vertex conflict.
        #
        # Both robots want the same cell during this tick.
        # Only that local cell needs arbitration.
        # --------------------------------------------------

        if not robot_a.finished and not robot_b.finished and a_next == b_next:
            winner = choose_winner(
                robot_a,
                robot_b,
            )

            loser = robot_b if winner is robot_a else robot_a

            winner.move()
            loser.wait()

            event = (
                f"CONFLICT at {a_next}. "
                f"robot_{winner.robot_id:02d} gets right-of-way. "
                f"robot_{loser.robot_id:02d} brakes for one grid interval."
            )

        else:
            if not robot_a.finished:
                robot_a.move()

            if not robot_b.finished:
                robot_b.move()

            event = "No local conflict. Both robots may move."

        sim_time += DT

        render(
            robot_a,
            robot_b,
            sim_time,
            event,
        )

        time.sleep(args.delay)

    print()
    print("RESULT")
    print("=" * 54)
    print(f"robot_01 waiting : {robot_a.wait_ticks * DT:.3f} s")
    print(f"robot_02 waiting : {robot_b.wait_ticks * DT:.3f} s")
    print(f"completion time  : {sim_time:.3f} s")
    print()
    print("status           : OK")


if __name__ == "__main__":
    main()
