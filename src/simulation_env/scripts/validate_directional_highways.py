#!/usr/bin/env python3
"""Validate the directed warehouse traffic topology.

This script deliberately imports the production lane policy instead of
restating it. It used to carry its own copy of the rules, which drifted
from ``mrta_core.warehouse_routing`` and reported PASS for a graph the
planner never actually used.

What is checked, against the exact ``blocked_edges`` set A* consumes:

  * every core transit edge is one-way
  * the core is strongly connected
  * every shelf, dropoff and staging cell can be entered and left
"""

import argparse
from collections import deque

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import (
    BIDIRECTIONAL_CONNECTORS,
    EASTBOUND_ROWS,
    NORTHBOUND_COLS,
    SHELF_BLOCK_RUNG_DX,
    SHELF_BLOCK_ROWS,
    SHELF_COLS,
    SOUTHBOUND_COLS,
    WESTBOUND_ROWS,
    directional_blocked_edges,
    non_transit_cells,
)


DEFAULT_CONFIG = "src/simulation_env/config/warehouse_sim.yaml"

_parser = argparse.ArgumentParser(description=__doc__)
_parser.add_argument("--config", default=DEFAULT_CONFIG)
CONFIG = _parser.parse_args().config

TERMINAL_TYPES = (
    ZoneType.SHELF,
    ZoneType.DROPOFF,
    ZoneType.STAGING,
)


config = load_sim_config(CONFIG)
warehouse = load_warehouse_xlsx(config.map_path)
grid = warehouse.grid

# The one directed-edge policy, exactly as the planner sees it.
blocked_edges = directional_blocked_edges(warehouse)
terminal_cells_set = non_transit_cells(warehouse)


def traversable(cell):
    x, y = cell

    return (
        0 <= x < grid.width
        and 0 <= y < grid.height
        and cell not in grid.obstacles
    )


def geometric_neighbors(cell):
    x, y = cell

    for neighbor in (
        (x + 1, y),
        (x - 1, y),
        (x, y + 1),
        (x, y - 1),
    ):
        if traversable(neighbor):
            yield neighbor


def move_allowed(source, target):
    """Whether one directed grid edge is permitted."""

    if not traversable(source) or not traversable(target):
        return False

    return (source, target) not in blocked_edges


# ============================================================
# Core traffic graph
#
# Shelf, dropoff and staging cells are not transit vertices.
# ============================================================

core = {
    (x, y)
    for y in range(grid.height)
    for x in range(grid.width)
    if traversable((x, y))
    and (x, y) not in terminal_cells_set
}


graph = {
    cell: [
        neighbor
        for neighbor in geometric_neighbors(cell)
        if neighbor in core
        and move_allowed(cell, neighbor)
    ]
    for cell in core
}


reverse_graph = {cell: [] for cell in core}

for source, targets in graph.items():
    for target in targets:
        reverse_graph[target].append(source)


def reachable(start, adjacency):
    seen = {start}
    queue = deque([start])

    while queue:
        current = queue.popleft()

        for neighbor in adjacency[current]:

            if neighbor in seen:
                continue

            seen.add(neighbor)
            queue.append(neighbor)

    return seen


seed = min(core)

forward = reachable(seed, graph)
reverse = reachable(seed, reverse_graph)

strongly_connected = (
    len(forward) == len(core)
    and len(reverse) == len(core)
)


# ============================================================
# Terminal access.
# ============================================================

terminal_cells = []

for zone_type_value in TERMINAL_TYPES:
    for zone in warehouse.zones_of_type(zone_type_value):
        for cell in zone.cells:
            terminal_cells.append(
                (zone.zone_id, zone_type_value, cell)
            )


bad_terminal_access = []

for zone_id, type_value, cell in terminal_cells:

    neighbors = [
        neighbor
        for neighbor in geometric_neighbors(cell)
        if neighbor in core
    ]

    incoming = [
        neighbor
        for neighbor in neighbors
        if move_allowed(neighbor, cell)
    ]

    outgoing = [
        neighbor
        for neighbor in neighbors
        if move_allowed(cell, neighbor)
    ]

    if not incoming or not outgoing:
        bad_terminal_access.append(
            (zone_id, type_value, cell, incoming, outgoing)
        )


# ============================================================
# Edge census.
# ============================================================

directed_edges = sum(len(targets) for targets in graph.values())

bidirectional_pairs = []
one_way_edges = 0

for source, targets in graph.items():

    for target in targets:

        if source < target and source in graph[target]:
            bidirectional_pairs.append((source, target))

        if source not in graph.get(target, []):
            one_way_edges += 1


# A two-way core edge is only acceptable if it was declared as a
# turnaround connector, because those are the ones the local corridor
# mutex knows to serialise.
uncontrolled_bidirectional = [
    pair
    for pair in bidirectional_pairs
    if frozenset(pair) not in BIDIRECTIONAL_CONNECTORS
]


# ============================================================
# Report
# ============================================================

print()
print("=" * 76)
print("DIRECTIONAL HIGHWAY TOPOLOGY VALIDATION")
print("=" * 76)

print()
print("horizontal")
print("  eastbound rows :", sorted(EASTBOUND_ROWS))
print("  westbound rows :", sorted(WESTBOUND_ROWS))
print(
    "  shelf-block rungs:",
    f"{'west' if SHELF_BLOCK_RUNG_DX < 0 else 'east'}bound",
    f"on rows {min(SHELF_BLOCK_ROWS)}..{max(SHELF_BLOCK_ROWS)}",
)

print()
print("vertical")
print("  northbound cols:", sorted(NORTHBOUND_COLS))
print("  southbound cols:", sorted(SOUTHBOUND_COLS))
print("  shelf cols     :", sorted(SHELF_COLS), "(northbound, in-band only)")

print()
print("graph")
print(f"  core traffic cells      : {len(core)}")
print(f"  directed edges          : {directed_edges}")
print(f"  one-way edges           : {one_way_edges}")
print(f"  bidirectional edge pairs: {len(bidirectional_pairs)}")
print(f"  declared connectors     : {len(BIDIRECTIONAL_CONNECTORS)}")
print(f"  UNCONTROLLED bidi edges : {len(uncontrolled_bidirectional)}")

if uncontrolled_bidirectional:
    print()
    print("UNCONTROLLED BIDIRECTIONAL CORE EDGES")
    for source, target in sorted(uncontrolled_bidirectional)[:40]:
        print(f"  {source} <-> {target}")

print()
print("connectivity")
print(f"  forward reachable       : {len(forward)}/{len(core)}")
print(f"  reverse reachable       : {len(reverse)}/{len(core)}")
print(
    "  strongly connected      :",
    "PASS" if strongly_connected else "FAIL",
)

forward_missing = sorted(core - forward)
reverse_missing = sorted(core - reverse)

if forward_missing or reverse_missing:

    print()
    print("UNREACHABLE CORE CELLS")
    print("-" * 40)
    print("  not reachable from seed :", forward_missing)
    print("  cannot return to seed   :", reverse_missing)

    for cell in sorted(set(forward_missing) | set(reverse_missing)):

        print()
        print(f"  CELL {cell}")

        for neighbor in sorted(geometric_neighbors(cell)):
            print(
                f"    {cell} -> {neighbor}: "
                f"{'YES' if move_allowed(cell, neighbor) else 'NO '}   "
                f"{neighbor} -> {cell}: "
                f"{'YES' if move_allowed(neighbor, cell) else 'NO '}"
            )

        print("    outgoing graph:", sorted(graph[cell]))
        print("    incoming graph:", sorted(reverse_graph[cell]))

print()
print("terminal access")
print(f"  terminal cells checked  : {len(terminal_cells)}")
print(f"  bad terminal cells      : {len(bad_terminal_access)}")

if bad_terminal_access:

    print()
    print("BAD TERMINALS")

    for zone_id, type_value, cell, incoming, outgoing in (
        bad_terminal_access[:30]
    ):
        print(
            f"  {zone_id:12s} "
            f"{type_value.value:10s} "
            f"cell={cell} "
            f"in={incoming} "
            f"out={outgoing}"
        )

print()

if not strongly_connected:
    print("status: FAIL, directed core is not strongly connected")
    raise SystemExit(1)

if bad_terminal_access:
    print("status: FAIL, one or more terminal cells lost access")
    raise SystemExit(1)

if uncontrolled_bidirectional:
    print(
        f"status: FAIL, {len(uncontrolled_bidirectional)} "
        f"uncontrolled bidirectional core edges remain"
    )
    raise SystemExit(1)

print("uncontrolled bidi edges  : 0")
print("core strong connectivity : PASS")
print("shelf access             : PASS")
print("dropoff access           : PASS")
print("staging access           : PASS")
print("service retreat possible : PASS")
print()
print("status                   : OK")
