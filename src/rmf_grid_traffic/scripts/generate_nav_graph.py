#!/usr/bin/env python3

"""Generate an Open-RMF navigation graph from the warehouse XLSX.

The XLSX remains the single source of truth for geometry, and
``mrta_core.warehouse_routing`` remains the single source of truth for
traffic direction. This script only joins the two; it decides nothing.

RMF vertices are placed at the same cell centres used by Gazebo:

    world_x = origin_x + (grid_x + 0.5) * resolution
    world_y = origin_y + (grid_y + 0.5) * resolution

Lanes come from the directed lane policy, not from raw adjacency. For
each pair of orthogonally adjacent traversable cells a lane is emitted
only in the directions ``directional_blocked_edges()`` permits, so:

  * a one-way aisle yields exactly one lane
  * a shelf, dropoff or staging service edge yields both, because a
    robot has to drive in and reverse back out

This is what keeps Gazebo a mirror of the scientific engine rather than
a second planner with its own opinion. Emitting both directions
unconditionally, as this script used to, put every head-on back into
RMF that the one-way core was built to remove.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import yaml

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import (
    directional_blocked_edges,
    non_transit_cells,
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

        world_x = (
            grid.origin_x
            + (x + 0.5) * grid.resolution
        )

        world_y = (
            grid.origin_y
            + (y + 0.5) * grid.resolution
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

    # The one directed-edge policy, exactly as A* consumes it.
    blocked_edges = directional_blocked_edges(warehouse)
    service_cells = non_transit_cells(warehouse)

    lanes = []

    one_way_lanes = 0
    service_lanes = 0
    two_way_core = []

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

            forward = (cell, neighbor) not in blocked_edges
            reverse = (neighbor, cell) not in blocked_edges

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

            if forward and reverse:

                # Legitimate only where one end is a service cell.
                if (
                    cell in service_cells
                    or neighbor in service_cells
                ):
                    service_lanes += 1
                else:
                    two_way_core.append((cell, neighbor))

            elif forward or reverse:
                one_way_lanes += 1

    if two_way_core:
        raise SystemExit(
            f"refusing to write a graph with {len(two_way_core)} "
            f"two-way core edges, such as {two_way_core[:3]}; "
            f"the lane policy and this generator disagree"
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
    print(f"  semantic zones     : {dict(sorted(zone_counts.items()))}")
    print(f"  output             : {args.output}")


if __name__ == "__main__":
    main()
