#!/usr/bin/env python3

from __future__ import annotations

import argparse
import math
import time
from dataclasses import dataclass


DT = 2.0 / 3.0
WIDTH = 7
HEIGHT = 4

ROBOT_RADIUS_M = 0.30
GRID_RESOLUTION_M = 1.0

CORRIDOR_Y = 1

# Temporary local pull-aside cell.
YIELD_CELL = (4, 2)


@dataclass
class Robot:
    robot_id: int
    route: list[tuple[int, int]]
    busy: bool = True
    route_index: int = 0
    wait_ticks: int = 0
    yield_moves: int = 0

    in_yield_cell: bool = False

    @property
    def route_position(self):
        return self.route[self.route_index]

    @property
    def position(self):
        if self.in_yield_cell:
            return YIELD_CELL

        return self.route_position

    @property
    def finished(self):
        return (
            not self.in_yield_cell
            and self.route_index >= len(self.route) - 1
        )

    @property
    def next_route_position(self):
        if self.route_index >= len(self.route) - 1:
            return self.route_position

        return self.route[self.route_index + 1]

    @property
    def remaining_distance(self):
        return len(self.route) - 1 - self.route_index

    def move_route(self):
        if self.route_index < len(self.route) - 1:
            self.route_index += 1

    def enter_yield_cell(self):
        self.in_yield_cell = True
        self.yield_moves += 1

    def leave_yield_cell(self):
        self.in_yield_cell = False
        self.yield_moves += 1

    def wait(self):
        self.wait_ticks += 1


def priority_key(robot: Robot):
    """
    Smaller tuple gets right-of-way.

    Rule:
      1. busy beats idle
      2. smaller remaining distance wins
      3. lower robot_id breaks exact tie
    """
    return (
        0 if robot.busy else 1,
        robot.remaining_distance,
        robot.robot_id,
    )


def choose_winner(a: Robot, b: Robot):
    return min((a, b), key=priority_key)


def distance_m(a, b):
    dx = (a[0] - b[0]) * GRID_RESOLUTION_M
    dy = (a[1] - b[1]) * GRID_RESOLUTION_M

    return math.hypot(dx, dy)


def safe(a, b):
    return distance_m(a, b) >= 2.0 * ROBOT_RADIUS_M


def render(a: Robot, b: Robot, sim_time: float, event: str):
    grid = [
        [" " for _ in range(WIDTH)]
        for _ in range(HEIGHT)
    ]

    for x in range(WIDTH):
        grid[CORRIDOR_Y][x] = "."

    yx, yy = YIELD_CELL
    grid[yy][yx] = "Y"

    ax, ay = a.position
    bx, by = b.position

    grid[ay][ax] = "A"
    grid[by][bx] = "B"

    separation = distance_m(
        a.position,
        b.position,
    )

    clearance = (
        separation
        - 2.0 * ROBOT_RADIUS_M
    )

    print("\033[2J\033[H", end="")

    print("LOCAL OPPOSITE-TRAFFIC YIELD DEMO")
    print("=" * 62)

    print(
        f"simulation time       : "
        f"{sim_time:.3f} s"
    )

    print(
        f"scientific move step  : "
        f"{DT:.3f} s"
    )

    print()

    print("A = robot_01")
    print("B = robot_02")
    print("Y = local free yield cell")

    print()

    for y in reversed(range(HEIGHT)):
        print(
            f"{y:2d}  "
            + " ".join(grid[y])
        )

    print()

    print(
        "    "
        + " ".join(
            str(x)
            for x in range(WIDTH)
        )
    )

    print()

    print(
        f"A position={a.position}"
        f" remaining={a.remaining_distance}"
        f" busy={a.busy}"
    )

    print(
        f"B position={b.position}"
        f" remaining={b.remaining_distance}"
        f" busy={b.busy}"
    )

    print()

    print(
        f"center separation     : "
        f"{separation:.3f} m"
    )

    print(
        f"surface clearance     : "
        f"{clearance:.3f} m"
    )

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

    # A travels left to right.
    robot_a = Robot(
        robot_id=1,
        route=[
            (0, 1),
            (1, 1),
            (2, 1),
            (3, 1),
            (4, 1),
            (5, 1),
            (6, 1),
        ],
        busy=True,
    )

    # B travels right to left.
    robot_b = Robot(
        robot_id=2,
        route=[
            (6, 1),
            (5, 1),
            (4, 1),
            (3, 1),
            (2, 1),
            (1, 1),
            (0, 1),
        ],
        busy=True,
    )

    sim_time = 0.0

    yielding_robot = None
    passing_robot = None

    render(
        robot_a,
        robot_b,
        sim_time,
        "Both robots moving.",
    )

    time.sleep(args.delay)

    while not (
        robot_a.finished
        and robot_b.finished
    ):

        # --------------------------------------------------
        # Robot currently waiting in local yield cell.
        # --------------------------------------------------

        if yielding_robot is not None:

            yielder = yielding_robot
            passer = passing_robot

            corridor_return = (
                yielder.route_position
            )

            # The yielder may return as soon as the passer
            # moves away from its original corridor cell.
            passer_next = (
                passer.next_route_position
                if not passer.finished
                else passer.position
            )

            can_rejoin = (
                passer.position != corridor_return
                and passer_next != corridor_return
            )

            if not passer.finished:
                passer.move_route()

            if can_rejoin:
                yielder.leave_yield_cell()

                event = (
                    f"robot_{yielder.robot_id:02d} "
                    f"rejoins immediately. "
                    f"Local corridor cell is free."
                )

                yielding_robot = None
                passing_robot = None

            else:
                yielder.wait()

                event = (
                    f"robot_{yielder.robot_id:02d} "
                    f"waits locally in yield cell. "
                    f"Passing robot has not cleared yet."
                )

        else:

            a_next = (
                robot_a.next_route_position
                if not robot_a.finished
                else robot_a.position
            )

            b_next = (
                robot_b.next_route_position
                if not robot_b.finished
                else robot_b.position
            )

            vertex_conflict = (
                not robot_a.finished
                and not robot_b.finished
                and a_next == b_next
            )

            edge_swap = (
                not robot_a.finished
                and not robot_b.finished
                and a_next == robot_b.position
                and b_next == robot_a.position
            )

            # --------------------------------------------------
            # Local right-of-way decision.
            # --------------------------------------------------

            if vertex_conflict or edge_swap:

                winner = choose_winner(
                    robot_a,
                    robot_b,
                )

                loser = (
                    robot_b
                    if winner is robot_a
                    else robot_a
                )

                # This tiny demo provides a local yield
                # cell beside B's current corridor cell.
                #
                # If A loses instead, it has no bay in this
                # particular toy layout, so it brakes.
                if (
                    loser is robot_b
                    and loser.position == (4, 1)
                ):

                    winner.move_route()
                    loser.enter_yield_cell()

                    yielding_robot = loser
                    passing_robot = winner

                    event = (
                        f"LOCAL CONFLICT. "
                        f"robot_{winner.robot_id:02d} "
                        f"gets right-of-way. "
                        f"robot_{loser.robot_id:02d} "
                        f"moves one cell aside."
                    )

                else:
                    winner.move_route()
                    loser.wait()

                    event = (
                        f"LOCAL CONFLICT. "
                        f"robot_{winner.robot_id:02d} "
                        f"gets right-of-way. "
                        f"robot_{loser.robot_id:02d} "
                        f"brakes locally."
                    )

            else:

                if not robot_a.finished:
                    robot_a.move_route()

                if not robot_b.finished:
                    robot_b.move_route()

                event = (
                    "No local conflict. "
                    "Both robots move."
                )

        sim_time += DT

        if not safe(
            robot_a.position,
            robot_b.position,
        ):
            raise RuntimeError(
                "Robot safety violation: "
                f"A={robot_a.position}, "
                f"B={robot_b.position}"
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
    print("=" * 62)

    print(
        f"robot_01 local waiting : "
        f"{robot_a.wait_ticks * DT:.3f} s"
    )

    print(
        f"robot_02 local waiting : "
        f"{robot_b.wait_ticks * DT:.3f} s"
    )

    print(
        f"robot_01 yield moves   : "
        f"{robot_a.yield_moves}"
    )

    print(
        f"robot_02 yield moves   : "
        f"{robot_b.yield_moves}"
    )

    print(
        f"completion time        : "
        f"{sim_time:.3f} s"
    )

    print()

    print("status                 : OK")


if __name__ == "__main__":
    main()
