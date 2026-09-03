#!/usr/bin/env python3

"""Verify the RMF nav graph is an exact mirror of the lane policy.

Gazebo and RMF must not hold an opinion about traffic direction. This
check re-derives the expected lane set from
``mrta_core.warehouse_routing`` and compares it, edge by edge, with the
lanes actually written into the YAML.

It fails on any disagreement in either direction:

  * a lane RMF has that the planner forbids
    -> a robot could drive the wrong way in Gazebo
  * a lane the planner allows that RMF lacks
    -> RMF could not follow a route the engine legitimately planned

It also re-checks strong connectivity on the RMF graph itself, so a
graph that is one-way but cut into pieces cannot pass.
"""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

import yaml

from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import (
    directional_blocked_edges,
    non_transit_cells,
)


def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument("--map", required=True, type=Path)
    parser.add_argument("--graph", required=True, type=Path)

    args = parser.parse_args()

    warehouse = load_warehouse_xlsx(args.map)
    grid = warehouse.grid

    blocked_edges = directional_blocked_edges(warehouse)
    service_cells = non_transit_cells(warehouse)

    with args.graph.open() as handle:
        document = yaml.safe_load(handle)

    level = document["levels"]["L1"]

    # Map each RMF vertex back to the grid cell it was placed on.
    cell_of_index = {}

    for index, vertex in enumerate(level["vertices"]):

        world_x, world_y = vertex[0], vertex[1]

        x = int(round((world_x - grid.origin_x) / grid.resolution - 0.5))
        y = int(round((world_y - grid.origin_y) / grid.resolution - 0.5))

        cell_of_index[index] = (x, y)

    graph_lanes = set()

    for lane in level["lanes"]:
        graph_lanes.add(
            (
                cell_of_index[lane[0]],
                cell_of_index[lane[1]],
            )
        )

    # What the lane policy says the graph should contain.
    def free(cell):
        x, y = cell
        return (
            0 <= x < grid.width
            and 0 <= y < grid.height
            and cell not in grid.obstacles
        )

    expected = set()

    for y in range(grid.height):
        for x in range(grid.width):

            cell = (x, y)

            if not free(cell):
                continue

            for neighbor in (
                (x + 1, y),
                (x - 1, y),
                (x, y + 1),
                (x, y - 1),
            ):

                if not free(neighbor):
                    continue

                if (cell, neighbor) not in blocked_edges:
                    expected.add((cell, neighbor))

    extra = sorted(graph_lanes - expected)
    missing = sorted(expected - graph_lanes)

    # Two-way core lanes are the specific regression to guard against.
    two_way_core = sorted(
        (source, target)
        for (source, target) in graph_lanes
        if source < target
        and (target, source) in graph_lanes
        and source not in service_cells
        and target not in service_cells
    )

    # Strong connectivity over transit vertices, on the RMF graph.
    transit = {
        cell
        for cell in cell_of_index.values()
        if cell not in service_cells
    }

    forward_adj = {cell: [] for cell in transit}
    reverse_adj = {cell: [] for cell in transit}

    for source, target in graph_lanes:
        if source in transit and target in transit:
            forward_adj[source].append(target)
            reverse_adj[target].append(source)

    def reach(start, adjacency):
        seen = {start}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        return seen

    seed = min(transit)
    forward = reach(seed, forward_adj)
    reverse = reach(seed, reverse_adj)

    strongly_connected = (
        len(forward) == len(transit)
        and len(reverse) == len(transit)
    )

    print()
    print("=" * 72)
    print("RMF NAV GRAPH vs WAREHOUSE LANE POLICY")
    print("=" * 72)
    print()
    print(f"  vertices                 : {len(cell_of_index)}")
    print(f"  lanes in graph           : {len(graph_lanes)}")
    print(f"  lanes required by policy : {len(expected)}")
    print(f"  illegal lanes (extra)    : {len(extra)}")
    print(f"  missing lanes            : {len(missing)}")
    print(f"  two-way core lanes       : {len(two_way_core)}")
    print()
    print(f"  transit vertices         : {len(transit)}")
    print(f"  forward reachable        : {len(forward)}/{len(transit)}")
    print(f"  reverse reachable        : {len(reverse)}/{len(transit)}")
    print(
        "  strongly connected       :",
        "PASS" if strongly_connected else "FAIL",
    )

    for label, rows in (
        ("ILLEGAL LANES", extra),
        ("MISSING LANES", missing),
        ("TWO-WAY CORE", two_way_core),
    ):
        if rows:
            print()
            print(label)
            for source, target in rows[:20]:
                print(f"  {source} -> {target}")

    print()

    if extra or missing or two_way_core or not strongly_connected:
        print("status : FAIL, RMF graph does not mirror the lane policy")
        return 1

    print("lane-for-lane match      : PASS")
    print("no two-way core lanes    : PASS")
    print("strong connectivity      : PASS")
    print()
    print("status                   : OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
