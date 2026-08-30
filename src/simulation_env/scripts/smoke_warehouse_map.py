#!/usr/bin/env python3

"""Smoke validation for the XLSX -> WarehouseMap -> A* pipeline.

Checks that the explicit XLSX coordinate headers drive the grid,
that every cell of a merged region becomes an individually
addressable station, and that the existing mission planner still
produces a legal Manhattan path.
"""

from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import (
    load_warehouse_xlsx,
    read_xlsx_coordinate_frame,
)
from simulation_env.mission_planner import (
    is_service_zone,
    plan_zone_mission,
    service_cells,
)


MAP_PATH = Path(
    "src/simulation_env/maps/CSV_To_Gazebo.xlsx"
)

# The original mission, kept for continuity.
MISSION_LEGACY = [
    "staging_001",
    "shelf_001",
    "shelf_002",
    "shelf_003",
    "dropoff_001",
    "charging_001",
    "staging_001",
]

# Stations drawn from four different physical shelf blocks.
MISSION_STATIONS = [
    "staging_001",
    "shelf_018",
    "shelf_037",
    "shelf_072",
    "dropoff_020",
    "charging_016",
    "staging_018",
]

EXPECTED_COUNTS = {
    ZoneType.SHELF: 72,
    ZoneType.CHARGING: 16,
    ZoneType.DROPOFF: 40,
    ZoneType.STAGING: 18,
}

PROBES = [
    "shelf_001",
    "shelf_009",
    "shelf_010",
    "shelf_018",
    "shelf_019",
    "shelf_072",
    "charging_001",
    "charging_016",
    "dropoff_001",
    "staging_001",
]


def rule(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


frame = read_xlsx_coordinate_frame(MAP_PATH)

warehouse = load_warehouse_xlsx(MAP_PATH)

grid = warehouse.grid


# --------------------------------------------------
# Coordinate frame
# --------------------------------------------------

rule("COORDINATE FRAME (from XLSX headers)")

print(f"Axis rows        : {list(frame.axis_rows)}")
print(f"Axis columns     : {list(frame.axis_cols)}")

interior_cols = sorted(frame.excel_col_to_x)
interior_rows = sorted(frame.excel_row_to_y)

print(
    f"Warehouse cols   : "
    f"{interior_cols[0]}..{interior_cols[-1]} "
    f"-> X {frame.excel_col_to_x[interior_cols[0]]}"
    f"..{frame.excel_col_to_x[interior_cols[-1]]}"
)

print(
    f"Warehouse rows   : "
    f"{interior_rows[0]}..{interior_rows[-1]} "
    f"-> Y {frame.excel_row_to_y[interior_rows[0]]}"
    f"..{frame.excel_row_to_y[interior_rows[-1]]}"
)

print(f"Frame size       : {frame.width} x {frame.height}")


# --------------------------------------------------
# Grid
# --------------------------------------------------

rule("GRID")

print(f"Grid size        : {grid.width} x {grid.height}")
print(f"Blocked cells    : {len(grid.obstacles)}")
print(f"Resolution       : {grid.resolution}")
print(f"Stations         : {len(warehouse.zones)}")

assert (grid.width, grid.height) == (17, 35), (
    f"expected 17 x 35, got {grid.width} x {grid.height}"
)

for cell in grid.obstacles:
    assert 0 <= cell[0] < grid.width, cell
    assert 0 <= cell[1] < grid.height, cell


# --------------------------------------------------
# Stations
# --------------------------------------------------

rule("STATION COUNTS")

for zone_type, expected in EXPECTED_COUNTS.items():
    actual = len(warehouse.zones_of_type(zone_type))
    status = "OK " if actual == expected else "BAD"
    print(f"  [{status}] {zone_type.value:<11s} {actual:>3d} (expected {expected})")
    assert actual == expected

assert len(warehouse.zones) == sum(EXPECTED_COUNTS.values())


rule("STATION CELLS")

for zone_type in EXPECTED_COUNTS:

    print()
    print(f"  {zone_type.value.upper()}")

    entries = [
        f"{zone.zone_id} {next(iter(zone.cells))}"
        for zone in warehouse.zones_of_type(zone_type)
    ]

    for start in range(0, len(entries), 4):
        print(
            "    "
            + "".join(
                f"{entry:<24s}"
                for entry in entries[start:start + 4]
            ).rstrip()
        )

for zone in warehouse.zones:

    assert len(zone.cells) == 1, (
        f"{zone.zone_id} is not a single cell"
    )

    cell = next(iter(zone.cells))

    assert warehouse.is_traversable(cell), (
        f"{zone.zone_id} sits on a blocked cell {cell}"
    )


# --------------------------------------------------
# Explicit station probes
# --------------------------------------------------

rule("STATION ID -> GRID CELL")

for zone_id in PROBES:
    cell = next(iter(warehouse.zone_by_id(zone_id).cells))
    print(
        f"  {zone_id:<16s} -> grid {str(cell):<10s} "
        f"-> gazebo ({cell[0] + 0.5:>4.1f}, {cell[1] + 0.5:>4.1f})"
    )

assert next(iter(warehouse.zone_by_id("shelf_018").cells)) == (7, 28)


# --------------------------------------------------
# Explicit coordinate checks
# --------------------------------------------------

rule("XLSX -> WORLDGRID COORDINATE CHECKS")

workbook = load_workbook(MAP_PATH, data_only=True)
sheet = workbook.active

samples = [
    (interior_rows[-1], interior_cols[0], "warehouse origin"),
    (interior_rows[-1], interior_cols[-1], "bottom-right corner"),
    (interior_rows[0], interior_cols[0], "top-left corner"),
    (interior_rows[0], interior_cols[-1], "top-right corner"),
]

for merged in sorted(
    sheet.merged_cells.ranges,
    key=lambda r: (r.min_row, r.min_col),
):
    if not frame.contains(merged.min_row, merged.min_col):
        continue

    label = sheet.cell(
        row=merged.min_row,
        column=merged.min_col,
    ).value

    samples.append(
        (
            merged.min_row,
            merged.min_col,
            f"{label!r} top-left of {merged}",
        )
    )

for row, col, note in samples[:12]:

    wx, wy = frame.warehouse_coord(row, col)
    gx, gy = frame.to_grid(row, col)

    print(
        f"  {get_column_letter(col)}{row:<4} "
        f"warehouse ({wx:>2}, {wy:>2})  ->  "
        f"grid ({gx:>2}, {gy:>2})  ->  "
        f"gazebo ({gx + 0.5:>4.1f}, {gy + 0.5:>4.1f})   {note}"
    )

    assert (gx, gy) == (wx - 1, wy - 1)


# --------------------------------------------------
# A* missions
# --------------------------------------------------


def check_mission(title: str, zone_ids: list[str]) -> None:

    rule(title)

    plan = plan_zone_mission(warehouse, zone_ids)

    for segment in plan.segments:
        print(
            f"  {segment.from_zone:<16s} -> {segment.to_zone:<16s} "
            f"{str(segment.start_cell):>10s} -> {str(segment.goal_cell):<10s} "
            f"{len(segment.path):>3d} cells  "
            f"{segment.distance_m:>6.1f} m"
        )

    print()
    print(f"  Total path cells : {len(plan.path)}")
    print(f"  Total distance   : {plan.total_distance_m:.1f} m")
    print(f"  Total cost       : {plan.total_cost:.1f}")

    for cell in plan.path:
        x, y = cell

        assert 0 <= x < 17, f"path cell out of X bounds: {cell}"
        assert 0 <= y < 35, f"path cell out of Y bounds: {cell}"
        assert cell not in grid.obstacles, f"path crosses obstacle: {cell}"

    for previous, current in zip(plan.path, plan.path[1:]):

        step = (
            abs(current[0] - previous[0])
            + abs(current[1] - previous[1])
        )

        assert step == 1, (
            f"non-Manhattan step {previous} -> {current}"
        )

    # Racking and drop chutes are solid: a service cell may only
    # ever be the final cell of the leg that targets it.
    services = service_cells(warehouse)

    for segment in plan.segments:

        body = (
            segment.path[:-1]
            if is_service_zone(warehouse, segment.to_zone)
            else segment.path
        )

        for cell in body:
            assert cell not in services, (
                f"leg {segment.from_zone} -> {segment.to_zone} "
                f"crosses "
                f"{warehouse.zone_at(cell).zone_id} at {cell}"
            )

        if is_service_zone(warehouse, segment.to_zone):
            assert segment.exit_cell == segment.path[-2], (
                f"{segment.to_zone} has no aisle retreat cell"
            )

    # Every listed station must actually be visited now that
    # stations are single cells.
    for zone_id in zone_ids:
        cell = next(iter(warehouse.zone_by_id(zone_id).cells))
        assert cell in plan.path, (
            f"{zone_id} at {cell} was never visited"
        )

    print()
    print("  [OK ] all path cells inside 0 <= x < 17")
    print("  [OK ] all path cells inside 0 <= y < 35")
    print("  [OK ] no path cell is an obstacle")
    print("  [OK ] Manhattan moves only")
    print("  [OK ] no leg uses a service cell as transit")
    print("  [OK ] every requested station is on the path")


check_mission(
    "MISSION A / A* SMOKE TEST (original zone list)",
    MISSION_LEGACY,
)

check_mission(
    "MISSION B / A* SMOKE TEST (stations across blocks)",
    MISSION_STATIONS,
)

rule("SMOKE TEST PASSED")
print()
