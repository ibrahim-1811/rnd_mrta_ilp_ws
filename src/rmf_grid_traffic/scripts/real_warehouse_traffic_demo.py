#!/usr/bin/env python3

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from itertools import combinations

from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import best_path_to_zone, service_cells


DT = 2.0 / 3.0


@dataclass
class Robot:
    robot_id: int
    start_name: str
    goal_name: str
    route: list[tuple[int, int]]
    route_index: int = 0
    busy: bool = True
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


def zone_cells_by_prefix(warehouse, prefix):
    result = {}

    for zone in warehouse.zones:
        if zone.zone_id.startswith(prefix):
            if not zone.cells:
                continue

            cells = sorted(zone.cells)

            if len(cells) != 1:
                raise RuntimeError(
                    f"{zone.zone_id} expected exactly 1 cell, "
                    f"found {len(cells)}: {cells}"
                )

            result[zone.zone_id] = tuple(cells[0])

    return result


def priority_key(robot):
    # Temporary deterministic traffic right-of-way:
    #
    # 1. busy beats idle
    # 2. smaller remaining distance wins
    # 3. lower robot id breaks exact tie
    return (
        0 if robot.busy else 1,
        robot.remaining_distance,
        robot.robot_id,
    )


def choose_winner(a, b):
    return min((a, b), key=priority_key)


def route_conflict(route_a, route_b):
    """
    Find the first synchronous local conflict.

    Returns:
        (type, tick, cell)
    or:
        None
    """

    max_t = min(len(route_a), len(route_b)) - 1

    for t in range(max_t):

        a_now = route_a[t]
        b_now = route_b[t]

        a_next = route_a[t + 1]
        b_next = route_b[t + 1]

        if a_next == b_next:
            return (
                "VERTEX",
                t + 1,
                a_next,
            )

        if (
            a_next == b_now
            and b_next == a_now
        ):
            return (
                "EDGE_SWAP",
                t + 1,
                (a_now, a_next),
            )

    return None


def find_real_conflicting_pair(warehouse):
    staging = zone_cells_by_prefix(
        warehouse,
        "staging_",
    )

    shelves = zone_cells_by_prefix(
        warehouse,
        "shelf_",
    )

    blocked_services = service_cells(warehouse)

    # Keep this diagnostic bounded.
    #
    # Enough combinations to find a real conflict without
    # turning this into a benchmark.
    staging_items = list(sorted(staging.items()))[:8]
    shelf_items = list(sorted(shelves.items()))[:24]

    candidates = []

    for staging_name, start in staging_items:
        for shelf_name, _ in shelf_items:

            try:
                result = best_path_to_zone(
                    warehouse,
                    start,
                    shelf_name,
                    blocked_services,
                )
            except Exception:
                continue

            if not result.path:
                continue

            candidates.append(
                (
                    staging_name,
                    shelf_name,
                    list(result.path),
                )
            )

    # Prefer two different staging robots and two different goals.
    # Also avoid immediate start-area conflicts.
    for first, second in combinations(candidates, 2):

        a_start, a_goal, a_route = first
        b_start, b_goal, b_route = second

        if a_start == b_start:
            continue

        if a_goal == b_goal:
            continue

        conflict = route_conflict(
            a_route,
            b_route,
        )

        if conflict is None:
            continue

        conflict_type, tick, cell = conflict

        # We want a meaningful conflict after both robots
        # have started travelling.
        if tick < 2:
            continue

        return {
            "a_start": a_start,
            "a_goal": a_goal,
            "a_route": a_route,
            "b_start": b_start,
            "b_goal": b_goal,
            "b_route": b_route,
            "conflict_type": conflict_type,
            "conflict_tick": tick,
            "conflict_cell": cell,
        }

    raise RuntimeError(
        "No synchronous conflict found in the bounded search set."
    )


def render_window(
    warehouse,
    robot_a,
    robot_b,
    sim_time,
    event,
):
    ax, ay = robot_a.position
    bx, by = robot_b.position

    xmin = max(0, min(ax, bx) - 4)
    xmax = min(
        warehouse.grid.width - 1,
        max(ax, bx) + 4,
    )

    ymin = max(0, min(ay, by) - 4)
    ymax = min(
        warehouse.grid.height - 1,
        max(ay, by) + 4,
    )

    obstacles = set(
        warehouse.grid.obstacles
    )

    a_route = set(robot_a.route)
    b_route = set(robot_b.route)

    print("\033[2J\033[H", end="")

    print("REAL WAREHOUSE LOCAL TRAFFIC DEMO")
    print("=" * 72)

    print(
        f"simulation time : {sim_time:.3f} s"
    )

    print(
        f"move interval   : {DT:.3f} s"
    )

    print()

    print(
        f"A robot_01: {robot_a.start_name}"
        f" -> {robot_a.goal_name}"
    )

    print(
        f"B robot_02: {robot_b.start_name}"
        f" -> {robot_b.goal_name}"
    )

    print()

    for y in reversed(range(ymin, ymax + 1)):

        row = []

        for x in range(xmin, xmax + 1):

            cell = (x, y)

            if cell == robot_a.position:
                char = "A"

            elif cell == robot_b.position:
                char = "B"

            elif cell in obstacles:
                char = "#"

            elif (
                cell in a_route
                and cell in b_route
            ):
                char = "+"

            elif cell in a_route:
                char = "a"

            elif cell in b_route:
                char = "b"

            else:
                char = "."

            row.append(char)

        print(
            f"{y:2d}  "
            + " ".join(row)
        )

    print()

    print(
        "    "
        + " ".join(
            f"{x % 10}"
            for x in range(xmin, xmax + 1)
        )
    )

    print()

    print(
        f"A pos={robot_a.position}"
        f" remaining={robot_a.remaining_distance}"
        f" wait={robot_a.wait_ticks}"
    )

    print(
        f"B pos={robot_b.position}"
        f" remaining={robot_b.remaining_distance}"
        f" wait={robot_b.wait_ticks}"
    )

    print()

    print("Legend:")
    print("  # blocked")
    print("  a A-route")
    print("  b B-route")
    print("  + overlapping route cell")

    print()

    print(f"EVENT: {event}")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--map",
        default=(
            "src/simulation_env/maps/"
            "CSV_To_Gazebo.xlsx"
        ),
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
    )

    args = parser.parse_args()

    warehouse = load_warehouse_xlsx(
        args.map
    )

    pair = find_real_conflicting_pair(
        warehouse
    )

    print()
    print("REAL WAREHOUSE CONFLICT FOUND")
    print("=" * 72)

    print(
        f"A: {pair['a_start']}"
        f" -> {pair['a_goal']}"
    )

    print(
        f"B: {pair['b_start']}"
        f" -> {pair['b_goal']}"
    )

    print(
        f"conflict type : "
        f"{pair['conflict_type']}"
    )

    print(
        f"conflict tick : "
        f"{pair['conflict_tick']}"
    )

    print(
        f"conflict      : "
        f"{pair['conflict_cell']}"
    )

    print(
        f"A route length: "
        f"{len(pair['a_route']) - 1} m"
    )

    print(
        f"B route length: "
        f"{len(pair['b_route']) - 1} m"
    )

    print()

    input(
        "Press Enter to run the local traffic simulation..."
    )

    robot_a = Robot(
        robot_id=1,
        start_name=pair["a_start"],
        goal_name=pair["a_goal"],
        route=pair["a_route"],
    )

    robot_b = Robot(
        robot_id=2,
        start_name=pair["b_start"],
        goal_name=pair["b_goal"],
        route=pair["b_route"],
    )

    sim_time = 0.0

    render_window(
        warehouse,
        robot_a,
        robot_b,
        sim_time,
        "Both robots begin their assigned routes.",
    )

    time.sleep(args.delay)

    while not (
        robot_a.finished
        and robot_b.finished
    ):

        a_next = robot_a.next_position
        b_next = robot_b.next_position

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

            winner.move()
            loser.wait()

            conflict_kind = (
                "vertex"
                if vertex_conflict
                else "edge-swap"
            )

            event = (
                f"LOCAL {conflict_kind.upper()} CONFLICT. "
                f"robot_{winner.robot_id:02d} "
                f"gets right-of-way. "
                f"robot_{loser.robot_id:02d} "
                f"brakes locally."
            )

        else:

            if not robot_a.finished:
                robot_a.move()

            if not robot_b.finished:
                robot_b.move()

            event = (
                "No local conflict. "
                "Both robots move."
            )

        # Hard discrete safety invariant.
        if robot_a.position == robot_b.position:
            raise RuntimeError(
                "Vertex collision detected."
            )

        sim_time += DT

        render_window(
            warehouse,
            robot_a,
            robot_b,
            sim_time,
            event,
        )

        time.sleep(args.delay)

    print()
    print("RESULT")
    print("=" * 72)

    print(
        f"robot_01 waiting : "
        f"{robot_a.wait_ticks * DT:.3f} s"
    )

    print(
        f"robot_02 waiting : "
        f"{robot_b.wait_ticks * DT:.3f} s"
    )

    print(
        f"completion time  : "
        f"{sim_time:.3f} s"
    )

    print()
    print("status           : OK")


if __name__ == "__main__":
    main()
