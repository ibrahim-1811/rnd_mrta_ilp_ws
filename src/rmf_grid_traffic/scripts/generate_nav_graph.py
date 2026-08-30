#!/usr/bin/env python3

"""Generate an Open-RMF navigation graph from the warehouse XLSX.

The XLSX remains the single source of truth.

RMF vertices are placed at the same cell centres used by Gazebo:

    world_x = origin_x + (grid_x + 0.5) * resolution
    world_y = origin_y + (grid_y + 0.5) * resolution

Each pair of orthogonally adjacent traversable cells becomes one
bidirectional RMF lane.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import yaml

from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--map",
        required=True,
        type=Path,
        help="Warehouse XLSX source of truth",
    )

    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Output RMF navigation graph YAML",
    )

    args = parser.parse_args()

    warehouse = load_warehouse_xlsx(args.map)
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

    lanes = []

    # Only inspect right and up.
    # is_bidirectional=true supplies both travel directions.
    for x, y in free_cells:

        for dx, dy in (
            (1, 0),
            (0, 1),
        ):

            neighbor = (
                x + dx,
                y + dy,
            )

            if neighbor not in free_set:
                continue

            a = cell_to_index[(x, y)]
            b = cell_to_index[neighbor]

            # RMF Graph lanes are directed.
            # Explicitly create both directions for each grid adjacency.
            lanes.append(
                [
                    a,
                    b,
                    {"is_bidirectional": False},
                ]
            )

            lanes.append(
                [
                    b,
                    a,
                    {"is_bidirectional": False},
                ]
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
    print(f"  semantic zones     : {dict(sorted(zone_counts.items()))}")
    print(f"  output             : {args.output}")


if __name__ == "__main__":
    main()
