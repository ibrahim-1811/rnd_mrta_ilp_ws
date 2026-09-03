#!/usr/bin/env python3

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx


CONFIG = "src/simulation_env/config/warehouse_sim.yaml"

config = load_sim_config(CONFIG)
warehouse = load_warehouse_xlsx(config.map_path)
grid = warehouse.grid


NON_TRANSIT = {
    ZoneType.SHELF,
    ZoneType.DROPOFF,
    ZoneType.STAGING,
}


def usable(cell):
    x, y = cell

    if not (
        0 <= x < grid.width
        and 0 <= y < grid.height
    ):
        return False

    if cell in grid.obstacles:
        return False

    zone_type = warehouse.zone_type_at(cell)

    if zone_type in NON_TRANSIT:
        return False

    return True


def contiguous_runs(cells):
    if not cells:
        return []

    cells = sorted(cells)

    runs = []
    current = [cells[0]]

    for value in cells[1:]:
        if value == current[-1] + 1:
            current.append(value)
        else:
            runs.append(current)
            current = [value]

    runs.append(current)

    return runs


horizontal = []
vertical = []


# ------------------------------------------------------------
# Horizontal aisle runs
# ------------------------------------------------------------

for y in range(grid.height):

    xs = [
        x
        for x in range(grid.width)
        if usable((x, y))
    ]

    for run in contiguous_runs(xs):

        if len(run) >= 5:
            horizontal.append(
                (
                    len(run),
                    y,
                    run[0],
                    run[-1],
                )
            )


# ------------------------------------------------------------
# Vertical aisle runs
# ------------------------------------------------------------

for x in range(grid.width):

    ys = [
        y
        for y in range(grid.height)
        if usable((x, y))
    ]

    for run in contiguous_runs(ys):

        if len(run) >= 5:
            vertical.append(
                (
                    len(run),
                    x,
                    run[0],
                    run[-1],
                )
            )


horizontal.sort(reverse=True)
vertical.sort(reverse=True)


print()
print("=" * 76)
print("REAL WAREHOUSE DIRECTIONAL HIGHWAY DISCOVERY")
print("=" * 76)

print()
print(
    f"grid: {grid.width} x {grid.height}"
)

print()
print("LONG HORIZONTAL AISLE RUNS")
print("length   y     x_start -> x_end")
print("-" * 40)

for length, y, x0, x1 in horizontal:
    print(
        f"{length:6d}  "
        f"{y:3d}     "
        f"{x0:2d} -> {x1:2d}"
    )


print()
print("LONG VERTICAL AISLE RUNS")
print("length   x     y_start -> y_end")
print("-" * 40)

for length, x, y0, y1 in vertical:
    print(
        f"{length:6d}  "
        f"{x:3d}     "
        f"{y0:2d} -> {y1:2d}"
    )


# ------------------------------------------------------------
# Highlight corridors implicated in our 18-robot deadlocks.
# ------------------------------------------------------------

print()
print("KNOWN STRESS-TEST CONFLICT CORRIDORS")
print("-" * 40)

for y in (15, 19):

    cells = [
        (x, y)
        for x in range(grid.width)
        if usable((x, y))
    ]

    print(
        f"y={y:2d}: "
        f"{cells}"
    )


print()
print("status: OK")
