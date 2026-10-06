#!/usr/bin/env python3

"""Generate an Open-RMF navigation graph from the warehouse XLSX.

The XLSX remains the single source of truth for geometry, and
``mrta_core.warehouse_routing`` remains the single source of truth for
traffic direction. This script only joins the two; it decides nothing.

RMF vertices are placed at the same cell centres used by Gazebo, via
the one shared transform in ``mrta_core.world_view``:

    world_x = origin_x + grid_x * resolution
    world_y = origin_y + grid_y * resolution

Lanes come from the directed lane policy, not from raw adjacency, via
``traversable_lane_edges()``. That resolves both halves of the policy:

  * a one-way aisle yields exactly one lane
  * a shelf, dropoff or staging service edge yields both, because a
    robot has to drive in and reverse back out
  * a terminal-to-terminal edge yields none, because terminals are leg
    endpoints and never a route across the station bank

This is what keeps Gazebo a mirror of the scientific engine rather than
a second planner with its own opinion. Emitting both directions
unconditionally, as this script once did, put every head-on back into
RMF that the one-way core was built to remove. Emitting the terminal
chain, as it did until the baseline freeze, let a static RMF route walk
the length of the home bank -- a shortcut the scientific planner never
takes, because ``navigation_grid()`` blocks terminals per leg.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import yaml

from mrta_core.sim_config import load_sim_config
from mrta_core.world_view import grid_to_world
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import (
    non_transit_cells,
    traversable_lane_edges,
)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--map",
        type=Path,
        help="Warehouse XLSX source of truth "
             "(or give --config and it is read from there)",
    )

    parser.add_argument(
        "--config",
        type=Path,
        help="Simulation config to take the map path from",
    )

    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Output RMF navigation graph YAML",
    )

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

    obstacles = set(grid.obstacles)

    free_cells = [
        (x, y)
        for y in range(grid.height)
        for x in range(grid.width)
        if (x, y) not in obstacles
    ]

    free_set = set(free_cells)

    # Semantic station name for each labelled warehouse cell.
    zone_name_by_cell = {}

    for zone in warehouse.zones:
        for cell in zone.cells:
            zone_name_by_cell[cell] = zone.zone_id

    # Deterministic vertex numbering.
    cell_to_index = {
        cell: index
        for index, cell in enumerate(free_cells)
    }

    vertices = []

    for x, y in free_cells:

        world_x, world_y = grid_to_world(
            (x, y),
            grid.resolution,
            grid.origin_x,
            grid.origin_y,
        )

        name = zone_name_by_cell.get(
            (x, y),
            f"cell_{x}_{y}",
        )

        vertices.append(
            [
                float(world_x),
                float(world_y),
                {"name": name},
            ]
        )

    # The one lane policy, both halves resolved, exactly as the
    # scientific planner would drive it.
    service_cells = non_transit_cells(warehouse)
    allowed_edges = traversable_lane_edges(warehouse)

    lanes = []

    one_way_lanes = 0
    service_lanes = 0
    two_way_core = []
    terminal_chain = []

    # Only inspect right and up; each direction is decided separately.
    for x, y in free_cells:

        for dx, dy in (
            (1, 0),
            (0, 1),
        ):

            cell = (x, y)

            neighbor = (
                x + dx,
                y + dy,
            )

            if neighbor not in free_set:
                continue

            a = cell_to_index[cell]
            b = cell_to_index[neighbor]

            forward = (cell, neighbor) in allowed_edges
            reverse = (neighbor, cell) in allowed_edges

            # RMF Graph lanes are directed, so each permitted direction
            # is its own record.
            if forward:
                lanes.append(
                    [
                        a,
                        b,
                        {"is_bidirectional": False},
                    ]
                )

            if reverse:
                lanes.append(
                    [
                        b,
                        a,
                        {"is_bidirectional": False},
                    ]
                )

            both_terminal = (
                cell in service_cells
                and neighbor in service_cells
            )

            if forward and reverse:

                if both_terminal:
                    terminal_chain.append((cell, neighbor))

                # Legitimate only where one end is a service cell.
                elif (
                    cell in service_cells
                    or neighbor in service_cells
                ):
                    service_lanes += 1

                else:
                    two_way_core.append((cell, neighbor))

            elif forward or reverse:

                if both_terminal:
                    terminal_chain.append((cell, neighbor))
                else:
                    one_way_lanes += 1

    if two_way_core:
        raise SystemExit(
            f"refusing to write a graph with {len(two_way_core)} "
            f"two-way core edges, such as {two_way_core[:3]}; "
            f"the lane policy and this generator disagree"
        )

    if terminal_chain:
        raise SystemExit(
            f"refusing to write a graph with {len(terminal_chain)} "
            f"terminal-to-terminal lanes, such as {terminal_chain[:3]}; "
            f"terminals are leg endpoints, never a route across the "
            f"station bank"
        )

    # A station nobody can drive into, or drive back out of, is a
    # station the fleet cannot serve.
    entered = {target for _, target in allowed_edges}
    exited = {source for source, _ in allowed_edges}

    stranded = sorted(
        cell
        for cell in service_cells
        if cell not in entered or cell not in exited
    )

    if stranded:
        raise SystemExit(
            f"refusing to write a graph with {len(stranded)} "
            f"unreachable terminal cells, such as {stranded[:6]}; "
            f"every station needs an aisle lane in and a lane back out"
        )

    graph = {
        "building_name": "rnd_warehouse",
        "levels": {
            "L1": {
                "vertices": vertices,
                "lanes": lanes,
            }
        },
    }

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with args.output.open("w") as f:
        yaml.safe_dump(
            graph,
            f,
            sort_keys=False,
        )

    zone_counts = Counter(
        zone.zone_type.value
        for zone in warehouse.zones
    )

    print("RMF navigation graph generated")
    print(f"  grid               : {grid.width} x {grid.height}")
    print(f"  total cells        : {grid.width * grid.height}")
    print(f"  blocked cells      : {len(obstacles)}")
    print(f"  vertices           : {len(vertices)}")
    print(f"  lane records       : {len(lanes)}")
    print(f"  one-way transit    : {one_way_lanes}")
    print(f"  two-way service    : {service_lanes}")
    print(f"  two-way core       : {len(two_way_core)}")
    print(f"  terminal chain     : {len(terminal_chain)}")
    print(f"  terminal cells     : {len(service_cells)}")
    print(f"  semantic zones     : {dict(sorted(zone_counts.items()))}")
    print(f"  output             : {args.output}")


if __name__ == "__main__":
    main()
