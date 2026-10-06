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
graph that is one-way but cut into pieces cannot pass, and it checks
the half of the lane policy a static graph cannot apply for itself:
terminals are leg endpoints, never intermediates. A lane joining two
terminal cells is a route across the station bank -- along the home
bank, or down a shelf column -- that the scientific planner never
takes, because ``navigation_grid()`` blocks terminals per leg.

Expected lanes therefore come from ``traversable_lane_edges()``, the
same function the generator writes from, so the two cannot drift.
"""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

import yaml

from mrta_core.sim_config import load_sim_config
from mrta_core.world_view import world_to_grid
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import (
    non_transit_cells,
    traversable_lane_edges,
)


def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument("--map", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--graph", required=True, type=Path)

    args = parser.parse_args()

    if args.map is None and args.config is None:
        parser.error("give --map or --config")

    map_path = (
        args.map
        if args.map is not None
        else load_sim_config(args.config).map_path
    )

    warehouse = load_warehouse_xlsx(map_path)
    grid = warehouse.grid

    service_cells = non_transit_cells(warehouse)
    expected = traversable_lane_edges(warehouse)

    with args.graph.open() as handle:
        document = yaml.safe_load(handle)

    level = document["levels"]["L1"]

    # Map each RMF vertex back to the grid cell it was placed on.
    cell_of_index = {}

    for index, vertex in enumerate(level["vertices"]):

        cell_of_index[index] = world_to_grid(
            (vertex[0], vertex[1]),
            grid.resolution,
            grid.origin_x,
            grid.origin_y,
        )

    graph_lanes = set()

    for lane in level["lanes"]:
        graph_lanes.add(
            (
                cell_of_index[lane[0]],
                cell_of_index[lane[1]],
            )
        )

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

    # A lane between two terminals is a route across the station bank.
    # Scored on the graph itself, not inferred from `extra`, so it is
    # named as the specific regression it is.
    terminal_chain = sorted(
        (source, target)
        for (source, target) in graph_lanes
        if source in service_cells
        and target in service_cells
    )

    # Removing the chain must not leave a station the fleet cannot
    # drive into, or cannot reverse back out of.
    entered = {target for _, target in graph_lanes}
    exited = {source for source, _ in graph_lanes}

    stranded_terminals = sorted(
        cell
        for cell in service_cells
        if cell not in entered or cell not in exited
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
    print(f"  terminal chain lanes     : {len(terminal_chain)}")
    print()
    print(f"  terminal vertices        : {len(service_cells)}")
    print(f"  stranded terminals       : {len(stranded_terminals)}")
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
        ("TERMINAL CHAIN", terminal_chain),
    ):
        if rows:
            print()
            print(label)
            for source, target in rows[:20]:
                print(f"  {source} -> {target}")

    if stranded_terminals:
        print()
        print("STRANDED TERMINALS")
        for cell in stranded_terminals[:20]:
            print(f"  {cell}")

    print()

    if (
        extra
        or missing
        or two_way_core
        or terminal_chain
        or stranded_terminals
        or not strongly_connected
    ):
        print("status : FAIL, RMF graph does not mirror the lane policy")
        return 1

    print("lane-for-lane match      : PASS")
    print("no two-way core lanes    : PASS")
    print("no terminal chain lanes  : PASS")
    print("terminal access          : PASS")
    print("strong connectivity      : PASS")
    print()
    print("status                   : OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
