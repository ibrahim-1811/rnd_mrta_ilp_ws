#!/usr/bin/env python3
"""Two-robot playback of a real synchronous traffic conflict.

Searches a bounded, deterministic set of Stage Zone -> shelf legs on
the current research map for two robots that, dispatched at the same
instant on their production directed A* routes, are scheduled into the
same cell at the same timestep. The chosen pair is then played back
through a two-robot local arbitration to show who gets right-of-way
and who waits.

Normal transit aisles are one-way (docs/DECISIONS.md). The structural
head-on edge swap this demo was first written to show therefore cannot
occur on transit any more, and searching for it finds nothing. The
search now targets the conflicts the one-way model really produces.
Each is a vertex contention -- two robots scheduled into one cell at
one timestep -- named by the shape of the lanes around that cell:

  MERGE         the robots arrive on two different incoming lanes and
                leave on the same outgoing lane
  INTERSECTION  the robots arrive on two perpendicular incoming lanes
                and leave on different lanes: the paths cross
  VERTEX        any other same-cell, same-timestep shape

A head-on EDGE_SWAP is still detected but never displayed: on one-way
transit it must not appear, so finding one is reported as a warning.

Routes are the production legs (``RouteCache``: directed A*, every
terminal except the leg's own endpoints blocked), the same computation
the allocator costs and the engine executes. Manhattan distance is not
used as route cost anywhere here.

This is a visualisation, not a validator. Head-on and yield behaviour
is asserted by check_local_headon_resolution.py,
check_engine_headon_yield.py and check_local_yield_selector.py.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import HOME_TYPES, RouteCache


DEFAULT_CONFIG = "src/simulation_env/config/warehouse.yaml"

# Bounded search set: this many Stage Zone cells and shelves, spread
# evenly over the sorted zone ids. Enough to find a real conflict on
# W100 and V2 without turning this into a benchmark.
DEFAULT_STARTS = 16
DEFAULT_GOALS = 24

# A meaningful conflict happens after both robots have started
# travelling, not while they leave their home cells.
MIN_CONFLICT_TICK = 2

DISPLAY_KINDS = ("MERGE", "INTERSECTION", "VERTEX")

HEADINGS = {
    (1, 0): "east",
    (-1, 0): "west",
    (0, 1): "north",
    (0, -1): "south",
}


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
    def after_next_position(self):
        if self.route_index + 2 < len(self.route):
            return self.route[self.route_index + 2]

        return None

    @property
    def remaining_distance(self):
        return len(self.route) - 1 - self.route_index

    def move(self):
        if not self.finished:
            self.route_index += 1

    def wait(self):
        self.wait_ticks += 1


@dataclass(frozen=True)
class Leg:
    start_name: str
    goal_name: str
    route: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class Conflict:
    kind: str
    tick: int
    cell: tuple[int, int]
    a_from: tuple[int, int]
    b_from: tuple[int, int]
    a_to: tuple[int, int] | None
    b_to: tuple[int, int] | None


@dataclass(frozen=True)
class Found:
    a: Leg
    b: Leg
    conflict: Conflict


@dataclass(frozen=True)
class SearchResult:
    legs: int
    pairs_checked: int
    counts: Counter
    examples: dict[str, Found]
    chosen: Found


def single_cell_zones(warehouse, types):
    result = {}

    for zone in warehouse.zones:
        if zone.zone_type not in types or not zone.cells:
            continue

        cells = sorted(zone.cells)

        if len(cells) != 1:
            raise RuntimeError(
                f"{zone.zone_id} expected exactly 1 cell, found {len(cells)}: {cells}"
            )

        result[zone.zone_id] = tuple(cells[0])

    return result


def spread(names, count):
    """``count`` names evenly spaced over the sorted list."""

    names = sorted(names)

    if count >= len(names):
        return names

    return [names[(i * len(names)) // count] for i in range(count)]


def heading(source, target):
    return HEADINGS.get(
        (target[0] - source[0], target[1] - source[1]),
        "?",
    )


def classify_shared_cell(a_from, b_from, cell, a_to, b_to):
    """Name the shape of two robots entering ``cell`` at one timestep."""

    if a_to is not None and a_to == b_to:
        return "MERGE"

    a_step = (cell[0] - a_from[0], cell[1] - a_from[1])
    b_step = (cell[0] - b_from[0], cell[1] - b_from[1])

    perpendicular = a_step[0] * b_step[0] + a_step[1] * b_step[1] == 0

    if perpendicular and a_to is not None and b_to is not None:
        return "INTERSECTION"

    return "VERTEX"


def route_conflict(route_a, route_b):
    """
    Find the first synchronous conflict of two routes dispatched together.

    ``route[t]`` is the cell a robot is scheduled to occupy at timestep
    ``t``. Past the shorter route the finished robot stands on its goal,
    a terminal the other leg never enters, so the search stops there.

    Returns a Conflict, or None.
    """

    last = min(len(route_a), len(route_b)) - 1

    for t in range(1, last + 1):
        a_from, b_from = route_a[t - 1], route_b[t - 1]
        a_now, b_now = route_a[t], route_b[t]

        if a_now == b_now:
            a_to = route_a[t + 1] if t + 1 < len(route_a) else None
            b_to = route_b[t + 1] if t + 1 < len(route_b) else None

            return Conflict(
                kind=classify_shared_cell(a_from, b_from, a_now, a_to, b_to),
                tick=t,
                cell=a_now,
                a_from=a_from,
                b_from=b_from,
                a_to=a_to,
                b_to=b_to,
            )

        if a_now == b_from and b_now == a_from:
            return Conflict(
                kind="EDGE_SWAP",
                tick=t,
                cell=a_now,
                a_from=a_from,
                b_from=b_from,
                a_to=a_now,
                b_to=b_now,
            )

    return None


def candidate_legs(warehouse, starts, goals):
    homes = single_cell_zones(warehouse, HOME_TYPES)
    shelves = single_cell_zones(warehouse, (ZoneType.SHELF,))

    # The production prediction view: directed A*, every terminal but
    # the leg's own endpoints blocked, no live robot occupancy.
    cache = RouteCache(warehouse)

    legs = []

    for start_name in spread(homes, starts):
        for goal_name in spread(shelves, goals):
            try:
                result = cache.leg(homes[start_name], goal_name)
            except RuntimeError:
                continue

            if not result.path:
                continue

            legs.append(Leg(start_name, goal_name, tuple(result.path)))

    return legs


def find_real_conflicting_pair(warehouse, *, starts, goals, kind):
    legs = candidate_legs(warehouse, starts, goals)

    counts = Counter()
    best = {}
    pairs_checked = 0

    for order, (first, second) in enumerate(combinations(legs, 2)):
        # Two different robots, two different goals.
        if first.start_name == second.start_name:
            continue

        if first.goal_name == second.goal_name:
            continue

        pairs_checked += 1

        conflict = route_conflict(first.route, second.route)

        if conflict is None or conflict.tick < MIN_CONFLICT_TICK:
            continue

        counts[conflict.kind] += 1

        # Per class, keep the earliest conflict; exact ties go to the
        # first pair in sorted iteration order.
        key = (conflict.tick, order)

        if conflict.kind not in best or key < best[conflict.kind][0]:
            best[conflict.kind] = (key, Found(first, second, conflict))

    wanted = DISPLAY_KINDS if kind == "ANY" else (kind,)
    shown = [best[k] for k in wanted if k in best]

    if not shown:
        summary = ", ".join(f"{k} {counts[k]}" for k in sorted(counts)) or "none"

        raise RuntimeError(
            f"No synchronous {kind.lower()} conflict found in the bounded "
            f"search set ({len(legs)} legs, {pairs_checked} pairs, "
            f"conflicts by class: {summary})."
        )

    _, chosen = min(shown, key=lambda entry: entry[0])

    return SearchResult(
        legs=len(legs),
        pairs_checked=pairs_checked,
        counts=counts,
        examples={k: entry[1] for k, entry in best.items()},
        chosen=chosen,
    )


def resolve_map(map_arg, config):
    """The map to load, and a note when ``--map`` had to be looked up.

    No ``--map`` means the config's ``warehouse.map``. A ``--map`` that
    does not exist as given is looked up by file name in maps/ and then
    test/fixtures/, where the V2 map now lives.
    """

    if map_arg is None:
        return config.map_path, None

    given = Path(map_arg)

    if given.exists():
        return given, None

    package = config.config_path.parent.parent

    for directory in (package / "maps", package / "test" / "fixtures"):
        candidate = directory / given.name

        if candidate.exists():
            return candidate, f"--map {map_arg} not found; using {candidate}"

    raise FileNotFoundError(
        f"map {map_arg!r} not found as given, nor in {package / 'maps'} "
        f"or {package / 'test' / 'fixtures'}"
    )


def priority_key(robot):
    # Deterministic right-of-way, the same rule as the production
    # LocalTrafficController:
    #
    # 1. busy beats idle
    # 2. smaller remaining distance wins
    # 3. lower robot id breaks an exact tie (engineering tie-break only)
    return (
        0 if robot.busy else 1,
        robot.remaining_distance,
        robot.robot_id,
    )


def choose_winner(a, b):
    return min((a, b), key=priority_key)


def describe_found(found, dt):
    c = found.conflict

    def leaves(cell):
        return "its goal" if cell is None else f"{cell}"

    return [
        f"A: {found.a.start_name} -> {found.a.goal_name}",
        f"B: {found.b.start_name} -> {found.b.goal_name}",
        f"conflict class: {c.kind}",
        f"conflict tick : {c.tick}  (t = {c.tick * dt:.3f} s)",
        f"conflict cell : {c.cell}",
        f"A enters from : {c.a_from} heading {heading(c.a_from, c.cell)},"
        f" leaves to {leaves(c.a_to)}",
        f"B enters from : {c.b_from} heading {heading(c.b_from, c.cell)},"
        f" leaves to {leaves(c.b_to)}",
    ]


def render_window(
    warehouse,
    robot_a,
    robot_b,
    sim_time,
    dt,
    event,
    conflict_cell,
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

    obstacles = set(warehouse.grid.obstacles)

    a_route = set(robot_a.route)
    b_route = set(robot_b.route)

    print("\033[2J\033[H", end="")

    print("REAL WAREHOUSE LOCAL TRAFFIC DEMO")
    print("=" * 72)

    print(f"simulation time : {sim_time:.3f} s")

    print(f"move interval   : {dt:.3f} s")

    print()

    print(f"A robot_01: {robot_a.start_name} -> {robot_a.goal_name}")

    print(f"B robot_02: {robot_b.start_name} -> {robot_b.goal_name}")

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

            elif cell == conflict_cell:
                char = "X"

            elif cell in a_route and cell in b_route:
                char = "+"

            elif cell in a_route:
                char = "a"

            elif cell in b_route:
                char = "b"

            else:
                char = "."

            row.append(char)

        print(f"{y:2d}  " + " ".join(row))

    print()

    print("    " + " ".join(f"{x % 10}" for x in range(xmin, xmax + 1)))

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
    print("  X predicted synchronous conflict cell")

    print()

    print(f"EVENT: {event}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])

    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="the one config; its warehouse.map is the default map",
    )

    parser.add_argument(
        "--map",
        default=None,
        help="override the config's map, e.g. CSV_To_Gazebo_v2.xlsx",
    )

    parser.add_argument(
        "--kind",
        choices=("any", "merge", "intersection", "vertex"),
        default="any",
        help="conflict class to play back (default: earliest of any)",
    )

    parser.add_argument(
        "--starts",
        type=int,
        default=DEFAULT_STARTS,
        help="Stage Zone cells in the bounded search set",
    )

    parser.add_argument(
        "--goals",
        type=int,
        default=DEFAULT_GOALS,
        help="shelves in the bounded search set",
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="wall seconds per frame; 0 runs straight through, no prompt",
    )

    args = parser.parse_args()

    config = load_sim_config(args.config)

    # One move takes resolution / speed, exactly as in the engine.
    dt = config.resolution_m / config.speed_mps

    map_path, map_note = resolve_map(args.map, config)

    warehouse = load_warehouse_xlsx(map_path)

    try:
        search = find_real_conflicting_pair(
            warehouse,
            starts=args.starts,
            goals=args.goals,
            kind=args.kind.upper(),
        )
    except RuntimeError as error:
        sys.exit(f"FAIL: {map_path}: {error}")

    pair = search.chosen

    print()
    print("REAL WAREHOUSE CONFLICT FOUND")
    print("=" * 72)

    if map_note:
        print(f"note          : {map_note}")

    print(f"map           : {map_path}")

    print(
        f"search        : {args.starts} Stage Zone x {args.goals} shelf "
        f"-> {search.legs} directed A* legs, {search.pairs_checked} pairs"
    )

    print(
        "found         : "
        + ", ".join(f"{k} {search.counts[k]}" for k in DISPLAY_KINDS)
        + f"  (EDGE_SWAP {search.counts['EDGE_SWAP']})"
    )

    if search.counts["EDGE_SWAP"]:
        print(
            "WARNING       : head-on edge swap on routes that should be "
            "one-way transit; check the lane policy"
        )

    print()

    for line in describe_found(pair, dt):
        print(line)

    print(f"A route length: {len(pair.a.route) - 1} m")

    print(f"B route length: {len(pair.b.route) - 1} m")

    print()

    others = [k for k in DISPLAY_KINDS if k in search.examples and k != pair.conflict.kind]

    for kind in others:
        other = search.examples[kind]
        print(
            f"also found    : {kind} at {other.conflict.cell} tick "
            f"{other.conflict.tick}, {other.a.start_name} -> "
            f"{other.a.goal_name} vs {other.b.start_name} -> "
            f"{other.b.goal_name}"
        )

    if others:
        print()

    if args.delay > 0 and sys.stdin.isatty():
        input("Press Enter to run the local traffic simulation...")

    robot_a = Robot(
        robot_id=1,
        start_name=pair.a.start_name,
        goal_name=pair.a.goal_name,
        route=list(pair.a.route),
    )

    robot_b = Robot(
        robot_id=2,
        start_name=pair.b.start_name,
        goal_name=pair.b.goal_name,
        route=list(pair.b.route),
    )

    conflict_cell = pair.conflict.cell

    sim_time = 0.0

    render_window(
        warehouse,
        robot_a,
        robot_b,
        sim_time,
        dt,
        "Both robots begin their assigned routes.",
        conflict_cell,
    )

    time.sleep(args.delay)

    resolved = []

    while not (robot_a.finished and robot_b.finished):
        a_next = robot_a.next_position
        b_next = robot_b.next_position

        both_moving = not robot_a.finished and not robot_b.finished

        edge_swap = (
            both_moving and a_next == robot_b.position and b_next == robot_a.position
        )

        if edge_swap:
            sys.exit(
                f"FAIL: head-on edge swap {robot_a.position} <-> "
                f"{robot_b.position} on one-way transit"
            )

        if both_moving and a_next == b_next:
            winner = choose_winner(
                robot_a,
                robot_b,
            )

            loser = robot_b if winner is robot_a else robot_a

            conflict_kind = classify_shared_cell(
                robot_a.position,
                robot_b.position,
                a_next,
                robot_a.after_next_position,
                robot_b.after_next_position,
            )

            winner.move()
            loser.wait()

            resolved.append((conflict_kind, a_next))

            event = (
                f"LOCAL {conflict_kind} CONTENTION at {a_next}. "
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

            event = "No local conflict. Both robots move."

        # Hard discrete safety invariant.
        if robot_a.position == robot_b.position:
            raise RuntimeError("Vertex collision detected.")

        sim_time += dt

        render_window(
            warehouse,
            robot_a,
            robot_b,
            sim_time,
            dt,
            event,
            conflict_cell,
        )

        time.sleep(args.delay)

    print()
    print("RESULT")
    print("=" * 72)

    print(f"predicted     : {pair.conflict.kind} at {conflict_cell}, tick {pair.conflict.tick}")

    for kind, cell in resolved:
        print(f"resolved      : {kind} contention at {cell}")

    print(f"robot_01 waiting : {robot_a.wait_ticks * dt:.3f} s")

    print(f"robot_02 waiting : {robot_b.wait_ticks * dt:.3f} s")

    print(f"completion time  : {sim_time:.3f} s")

    print()
    print("status           : OK")


if __name__ == "__main__":
    main()
