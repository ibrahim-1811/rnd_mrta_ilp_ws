#!/usr/bin/env python3

"""Topology gate for a warehouse map, under the production lane policy.

This is the check that has to pass before a map is allowed to become
the current one. It answers:

  * is every transit cell strongly connected, so no robot can drive
    into a corner it cannot legally leave
  * are there uncontrolled two-way core edges, the structural head-on
    the one-way core exists to remove
  * can every terminal -- shelf, dropoff, Stage Zone, charger and
    charge-queue holding cell -- be entered from the core AND left back
    to it. A local "has a lane in and a lane out" test is not enough: a
    terminal whose only aisle neighbour is a one-way dead end has both
    lanes and is still a trap or unreachable
  * can every station reach every other station
  * does any of those routes use a terminal as an intermediate cell,
    move directly from one terminal to another, or take a move the RMF
    nav graph does not have

Terminals are blocked as intermediate cells, so a leg is always: leave
the start terminal, drive the core, enter the goal terminal. Station
legs are therefore audited in one pass over the core and each station's
entrances and exits (audit_station_legs). --exhaustive plans all
n(n-1) legs with A* instead: minutes on W100, over an hour on a
79 x 104 map.

It deliberately imports the production routing policy rather than
restating the lane rules:

    non_transit_cells()          which cells are terminals
    directional_blocked_edges()  which directed edges are legal
    navigation_grid()            what A* actually plans on
    traversable_lane_edges()     what the RMF nav graph is built from

so this validates the graph the planner executes, not a paraphrase of
it.

Usage
-----
    python3 src/simulation_env/scripts/check_map_topology.py \\
        --config src/simulation_env/config/warehouse.yaml [--exhaustive]
"""

from __future__ import annotations

import argparse
import itertools
from collections import Counter, deque
from pathlib import Path
from time import perf_counter

from mrta_core.grid import astar_path
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import (
    directional_blocked_edges,
    navigation_grid,
    non_transit_cells,
    non_transit_types,
    traversable_lane_edges,
)


def _reach(start, adjacency):
    seen = {start}
    queue = deque([start])

    while queue:
        current = queue.popleft()

        for neighbor in adjacency[current]:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)

    return seen


def _neighbors(cell):
    x, y = cell
    return ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))


def audit_station_legs(stations, transit_set, forward_adj, blocked_edges, lane_edges, strongly_connected):
    """Audit every station leg without planning each one.

    stations maps zone id to its cell. A leg exists when a legal exit of
    the start and a legal entrance of the goal lie in the same reachable
    part of the core; every move of a leg is a core edge, an exit or an
    entrance, so each of those is held against the nav graph once. This
    is stricter than the exhaustive audit, which sees only the moves
    shortest paths happen to use. Returns (unreachable, terminal_hop,
    off_graph), the exhaustive audit's lists.
    """

    exits = {
        zone: [n for n in _neighbors(cell) if n in transit_set and (cell, n) not in blocked_edges]
        for zone, cell in stations.items()
    }
    entrances = {
        zone: [n for n in _neighbors(cell) if n in transit_set and (n, cell) not in blocked_edges]
        for zone, cell in stations.items()
    }

    zone_at = {cell: zone for zone, cell in stations.items()}
    terminal_hop = [
        (zone, zone_at[n], (cell, n))
        for zone, cell in stations.items()
        for n in _neighbors(cell)
        if n in zone_at and (cell, n) not in blocked_edges
    ]
    direct = {(source, target) for source, target, _ in terminal_hop}

    reached = {}

    def reachable_from(zone):
        if strongly_connected:
            return transit_set if exits[zone] else frozenset()
        cells = set()
        for cell in exits[zone]:
            if cell not in reached:
                reached[cell] = _reach(cell, forward_adj)
            cells |= reached[cell]
        return cells

    unreachable = []
    for source in stations:
        cells = reachable_from(source)
        unreachable.extend(
            (source, target)
            for target in stations
            if target != source
            and (source, target) not in direct
            and not cells.intersection(entrances[target])
        )

    moves = [(cell, n) for cell, neighbors in forward_adj.items() for n in neighbors]
    for zone, cell in stations.items():
        moves.extend((cell, n) for n in exits[zone])
        moves.extend((n, cell) for n in entrances[zone])
    off_graph = [("road", "edge", move) for move in moves if move not in lane_edges]

    return unreachable, terminal_hop, off_graph


def main() -> int:

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--map", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--exhaustive", action="store_true", help="plan all n(n-1) legs with A*")

    args = parser.parse_args()

    started = perf_counter()

    def progress(message):
        print(f"[{perf_counter() - started:7.2f} s] {message}", flush=True)

    if args.map is None and args.config is None:
        parser.error("give --map or --config")

    map_path = (
        args.map if args.map is not None else load_sim_config(args.config).map_path
    )

    types = non_transit_types()

    progress(f"loading {map_path}")
    warehouse = load_warehouse_xlsx(map_path)
    grid = warehouse.grid

    # One policy, resolved once, then handed to every production call
    # below so nothing can drift between them.
    terminal_cells = non_transit_cells(warehouse, types)
    blocked_edges = directional_blocked_edges(warehouse, terminal_cells)
    lane_edges = traversable_lane_edges(warehouse, terminal_cells)

    free_cells = [
        (x, y)
        for y in range(grid.height)
        for x in range(grid.width)
        if (x, y) not in grid.obstacles
    ]

    transit = [cell for cell in free_cells if cell not in terminal_cells]

    progress(f"building the road graph: {len(transit)} cells")

    transit_set = set(transit)

    forward_adj = {cell: [] for cell in transit}
    reverse_adj = {cell: [] for cell in transit}

    directed_edges = 0

    for x, y in transit:
        for neighbor in (
            (x + 1, y),
            (x - 1, y),
            (x, y + 1),
            (x, y - 1),
        ):
            if neighbor not in transit_set:
                continue

            if ((x, y), neighbor) in blocked_edges:
                continue

            forward_adj[(x, y)].append(neighbor)
            reverse_adj[neighbor].append((x, y))

            directed_edges += 1

    # A two-way edge between two transit cells is an uncontrolled
    # head-on. Between a transit cell and a terminal it is the
    # drive-in/reverse-out service connection, which is intended.
    two_way_core = sorted(
        (cell, neighbor)
        for cell in transit
        for neighbor in forward_adj[cell]
        if cell < neighbor and cell in forward_adj[neighbor]
    )

    # Seed from interior floor: a bank end is exactly the kind of cell
    # that goes orphaned, and seeding from one hides the failure.
    seed = min(
        transit,
        key=lambda cell: (
            -len(forward_adj[cell]) - len(reverse_adj[cell]),
            cell,
        ),
    )

    forward = _reach(seed, forward_adj)
    reverse = _reach(seed, reverse_adj)

    strongly_connected = len(forward) == len(transit) and len(reverse) == len(transit)

    orphans = sorted((transit_set - forward) | (transit_set - reverse))

    def plan(start, goal):
        """One mission leg, planned exactly as the executor plans it."""

        return astar_path(
            navigation_grid(
                warehouse,
                occupied=terminal_cells,
                start=start,
                goal=goal,
                terminal_cells=terminal_cells,
            ),
            start,
            goal,
        )

    zones = sorted(
        warehouse.zones,
        key=lambda zone: zone.zone_id,
    )

    progress(f"road graph strongly connected: {strongly_connected}")

    # Terminal access, globally: from the core into the terminal, and
    # from the terminal back out to the core.
    progress(f"planning in and out of {len(zones)} stations with A*")

    access = {}

    for zone in zones:
        cell = min(zone.cells)

        access[zone.zone_id] = (
            zone.zone_type,
            cell,
            plan(seed, cell).found,
            plan(cell, seed).found,
        )

    bad_access = sorted(
        (zone_id, zone_type, cell, entered, left)
        for zone_id, (zone_type, cell, entered, left) in access.items()
        if not (entered and left)
    )

    access_by_type = Counter(
        zone_type.value
        for zone_type, _, entered, left in access.values()
        if entered and left
    )

    # Station-to-station reachability, planned exactly as a mission
    # leg is planned, and audited move by move.
    through_terminal = []

    legs = len(zones) * (len(zones) - 1)

    if args.exhaustive:
        unreachable = []
        terminal_hop = []
        off_graph = []

        for done, (source, target) in enumerate(itertools.permutations(zones, 2)):
            if done % 100_000 == 0:
                progress(f"exhaustive A*: leg {done} of {legs}")

            start = min(source.cells)
            goal = min(target.cells)

            result = plan(start, goal)

            if not result.found:
                unreachable.append((source.zone_id, target.zone_id))
                continue

            path = result.path

            if any(cell in terminal_cells for cell in path[1:-1]):
                through_terminal.append((source.zone_id, target.zone_id))

            for move in zip(path, path[1:]):
                if move[0] in terminal_cells and move[1] in terminal_cells:
                    terminal_hop.append((source.zone_id, target.zone_id, move))

                if move not in lane_edges:
                    off_graph.append((source.zone_id, target.zone_id, move))
    else:
        progress(f"auditing {legs} station legs from the road graph")
        unreachable, terminal_hop, off_graph = audit_station_legs(
            {zone.zone_id: min(zone.cells) for zone in zones},
            transit_set,
            forward_adj,
            blocked_edges,
            lane_edges,
            strongly_connected,
        )

    progress("audit finished")

    zone_counts = Counter(zone.zone_type.value for zone in warehouse.zones)

    def cells_of(zone_type):
        return sorted(min(zone.cells) for zone in warehouse.zones_of_type(zone_type))

    print()
    print("=" * 72)
    print("WAREHOUSE MAP TOPOLOGY GATE")
    print("=" * 72)
    print()
    print(f"  map                      : {map_path}")
    print(f"  grid                     : {grid.width} x {grid.height}")
    print(f"  blocked cells            : {len(grid.obstacles)}")
    print(f"  semantic zones           : {dict(sorted(zone_counts.items()))}")
    print()
    print(f"  non-transit policy       : {', '.join(t.value for t in types)}")
    print()
    print(f"  chargers                 : {cells_of(ZoneType.CHARGING)}")
    print(f"  charge-queue holding     : {cells_of(ZoneType.CHARGE_QUEUE)}")
    print()
    print(f"  terminal cells           : {len(terminal_cells)}")
    print(f"  transit core cells       : {len(transit)}")
    print(f"  directed core edges      : {directed_edges}")
    print(f"  two-way core edges       : {len(two_way_core)}")
    print()
    print(f"  forward reachable        : {len(forward)}/{len(transit)}")
    print(f"  reverse reachable        : {len(reverse)}/{len(transit)}")
    print(
        "  strongly connected       :",
        "PASS" if strongly_connected else "FAIL",
    )
    print()
    print("  terminal access (enter from core AND leave to core)")

    for zone_type in types:
        total = zone_counts.get(zone_type.value, 0)

        if total:
            print(
                f"    {zone_type.value:<22}: "
                f"{access_by_type.get(zone_type.value, 0)}/{total}"
            )

    print()
    print(f"  station legs             : {len(zones) * (len(zones) - 1)}")
    print(f"  unreachable legs         : {len(unreachable)}")
    print(f"  legs through a terminal  : {len(through_terminal)}")
    print(f"  terminal->terminal moves : {len(terminal_hop)}")
    print(f"  moves not in nav graph   : {len(off_graph)}")

    if orphans:
        print()
        print("ORPHANED TRANSIT CELLS")
        print("  reachable but cannot get back, or vice versa. Each is")
        print("  free floor with no legal transit route, so a robot")
        print("  routed onto one is stuck.")
        print()

        for cell in orphans:
            no_exit = not forward_adj[cell]
            no_entry = not reverse_adj[cell]

            reason = (
                "no legal exit"
                if no_exit
                else "no legal entry"
                if no_entry
                else "cut off from the core"
            )

            print(f"  {str(cell):10s} {reason}")

    if bad_access:
        print()
        print("TERMINALS THAT CANNOT BE BOTH ENTERED AND LEFT")

        for zone_id, zone_type, cell, entered, left in bad_access:
            reason = (
                "isolated"
                if not entered and not left
                else "enter-only trap"
                if entered
                else "unreachable"
            )

            print(f"  {zone_id:18s} {str(cell):10s} {reason}")

    if two_way_core:
        print()
        print("UNCONTROLLED TWO-WAY CORE EDGES")
        for source, target in two_way_core[:20]:
            print(f"  {source} <-> {target}")

    if unreachable:
        kinds = Counter(
            (
                source.rsplit("_", 1)[0],
                target.rsplit("_", 1)[0],
            )
            for source, target in unreachable
        )

        print()
        print("UNREACHABLE STATION LEGS")
        for (source, target), count in sorted(kinds.items()):
            print(f"  {source} -> {target}: {count}")

    for title, rows in (
        ("LEGS THROUGH A TERMINAL", through_terminal),
        ("TERMINAL -> TERMINAL MOVES", terminal_hop),
        ("MOVES THE NAV GRAPH DOES NOT HAVE", off_graph),
    ):
        if rows:
            print()
            print(title)
            for row in rows[:10]:
                print(f"  {row}")

    print()

    if (
        not strongly_connected
        or two_way_core
        or bad_access
        or unreachable
        or through_terminal
        or terminal_hop
        or off_graph
    ):
        print("status                   : FAIL")
        return 1

    print("status                   : OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
