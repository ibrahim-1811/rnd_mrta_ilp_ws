#!/usr/bin/env python3

"""Build the held-out transfer map H100 (DECISIONS.md, "Warehouse Scalability").

W100 is the example layout for 100 robots: vertical racking units, chargers
spread along the top wall, drop-offs along the bottom wall, parking on both
side walls. H100 is never trained on and exists only to test whether rules
learned on W100 transfer, so it changes the structure, not just the size:

    racking       horizontal units [east-aisle | shelf row | west-aisle],
                  stacked bottom to top
    ring road     one-way, anticlockwise, just inside the wall (as W100)
    cross-bands   vertical column pairs, south-bound left of north-bound
    drop-offs     on the left and right walls (W100: bottom wall)
    chargers      24 chargers + 12 holding cells in one block at the
                  top-right corner (W100: spread over the whole top wall)
    parking       top-left wall segment and bottom wall, 100 cells
                  (W100: both side walls)

Why the band order is not cosmetic: a unit's west-aisle row sits directly
below the next unit's east-aisle row. With the north-bound column LEFT of the
south-bound one, those two rows and two columns would form a 2 x 2 rotary in
which four robots can wait for each other forever. South-bound left of
north-bound leaves every such square an exit (see example_layout's bands).

Usage:
    python3 src/simulation_env/scripts/make_heldout_map.py \
        --out src/simulation_env/maps/H100.xlsx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

for package in ("mrta_core", "simulation_env", "task_management"):
    candidate = ROOT / "src" / package

    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from mrta_core.map_builder import (  # noqa: E402
    CHARGER,
    DROPOFF,
    EAST,
    FLOOR,
    HOLDING,
    NORTH,
    PARKING,
    SHELF,
    SOUTH,
    WEST,
    Layout,
    write_layout_xlsx,
)


def heldout_layout(
    units: int = 8,
    width: int = 76,
    dropoffs_per_side: int = 18,
    chargers: int = 24,
    holding: int = 12,
    parking: int = 100,
) -> Layout:
    """H100: horizontal racking, side drop-offs, corner charger block."""

    height = 3 * units + 4
    layout = Layout(width=width, height=height)

    left, right = 1, width - 2
    bottom, top = 1, height - 2

    # ---- the ring road (anticlockwise, as in W100) -----------------------
    for x in range(left, right + 1):
        for y in (bottom, top):
            layout.kinds[(x, y)] = FLOOR

    for y in range(bottom, top + 1):
        for x in (left, right):
            layout.kinds[(x, y)] = FLOOR

    for x in range(left, right):
        layout.lane((x, bottom), EAST)

    for y in range(bottom, top):
        layout.lane((right, y), NORTH)

    for x in range(left + 1, right + 1):
        layout.lane((x, top), WEST)

    for y in range(bottom + 1, top + 1):
        layout.lane((left, y), SOUTH)

    # ---- cross-band columns ---------------------------------------------
    inner = right - left - 1
    bands = max(1, inner // 18)
    band_columns = []

    for index in range(bands):
        south = left + 1 + round((index + 1) * inner / (bands + 1)) - 1
        band_columns.append((south, south + 1))

    crossing = {column for pair in band_columns for column in pair}

    # ---- horizontal racking units ---------------------------------------
    for unit in range(units):
        east_row = 2 + 3 * unit
        shelf_row = east_row + 1
        west_row = east_row + 2

        for x in range(left + 1, right):
            layout.kinds[(x, east_row)] = FLOOR
            layout.lane((x, east_row), EAST)

            layout.kinds[(x, west_row)] = FLOOR
            layout.lane((x, west_row), WEST)

            layout.kinds[(x, shelf_row)] = FLOOR if x in crossing else SHELF

        # The ring feeds each east-aisle from the left and each west-aisle
        # from the right.
        layout.lane((left, east_row), EAST)
        layout.lane((right, west_row), WEST)

    for south, north in band_columns:
        for y in range(bottom + 1, top):
            layout.lane((south, y), SOUTH)
            layout.lane((north, y), NORTH)

        # Ring on-ramps into each band column.
        layout.lane((south, top), SOUTH)
        layout.lane((north, bottom), NORTH)

    # ---- stations on the walls ------------------------------------------
    rows = top - bottom + 1
    first = bottom + (rows - dropoffs_per_side) // 2

    for y in range(first, first + dropoffs_per_side):
        layout.kinds[(0, y)] = DROPOFF
        layout.kinds[(width - 1, y)] = DROPOFF

    # Charger block in the top-right corner: every third cell a holding cell.
    block = chargers + holding
    block_start = right - block + 1

    for index, x in enumerate(range(block_start, right + 1)):
        layout.kinds[(x, height - 1)] = HOLDING if index % 3 == 2 else CHARGER

    # Parking: the rest of the top wall, then the bottom wall, centred.
    top_parking = list(range(left, block_start))
    bottom_count = parking - len(top_parking)
    span = right - left + 1

    if bottom_count < 0 or bottom_count > span:
        raise ValueError(f"cannot place {parking} parking cells on this map")

    bottom_start = left + (span - bottom_count) // 2

    for x in top_parking:
        layout.kinds[(x, height - 1)] = PARKING

    for x in range(bottom_start, bottom_start + bottom_count):
        layout.kinds[(x, 0)] = PARKING

    return layout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "src/simulation_env/maps/H100.xlsx")
    args = parser.parse_args()

    layout = heldout_layout()
    write_layout_xlsx(layout, args.out)

    counts = {kind: layout.count(kind) for kind in (SHELF, DROPOFF, CHARGER, HOLDING, PARKING, FLOOR)}
    print(f"{args.out}: {layout.width} x {layout.height} cells")
    for kind, count in counts.items():
        print(f"  {kind:9s} {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
