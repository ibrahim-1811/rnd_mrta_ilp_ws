#!/usr/bin/env python3

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx

from simulation_env.local_yield_selector import (
    select_local_yield_cell,
)


# Follow the configured map rather than pinning a version.
CONFIG = "src/simulation_env/test/fixtures/v2_reference.yaml"

warehouse = load_warehouse_xlsx(load_sim_config(CONFIG).map_path)

grid = warehouse.grid


def neighbors(cell):
    x, y = cell

    for dx, dy in (
        (1, 0),
        (-1, 0),
        (0, 1),
        (0, -1),
    ):
        n = (x + dx, y + dy)

        if (
            0 <= n[0] < grid.width
            and 0 <= n[1] < grid.height
            and n not in grid.obstacles
        ):
            yield n


def acceptable_traffic_cell(cell):
    return warehouse.zone_type_at(cell) not in (
        ZoneType.SHELF,
        ZoneType.DROPOFF,
    )


found = None

# Find an actual warehouse arrangement:
#
# A ----> B
#         |
#         Y
#
# A and B are adjacent traversable corridor cells.
# B has another free adjacent cell that can be used as Y.

for y in range(grid.height):
    for x in range(grid.width):
        a = (x, y)

        if a in grid.obstacles:
            continue

        if not acceptable_traffic_cell(a):
            continue

        for b in neighbors(a):
            if not acceptable_traffic_cell(b):
                continue

            occupied = {a, b}

            yield_cell = select_local_yield_cell(
                warehouse=warehouse,
                source=b,
                intended_target=a,
                occupied=occupied,
                reserved_targets=frozenset(),
                forbidden={a},
            )

            if yield_cell is None:
                continue

            found = (
                a,
                b,
                yield_cell,
            )

            break

        if found:
            break

    if found:
        break


if found is None:
    raise RuntimeError(
        "No valid one-cell local yield configuration found in the real warehouse."
    )


winner_cell, yielder_cell, yield_cell = found

print()
print("=" * 68)
print("REAL WAREHOUSE LOCAL YIELD SELECTOR")
print("=" * 68)

print()
print(f"higher-priority robot cell : {winner_cell}")
print(f"yielding robot cell        : {yielder_cell}")
print(f"selected local yield cell  : {yield_cell}")

distance = abs(yield_cell[0] - yielder_cell[0]) + abs(yield_cell[1] - yielder_cell[1])

print()
print(f"yield displacement         : {distance} grid cell")

assert distance == 1
assert yield_cell not in grid.obstacles
assert yield_cell not in {winner_cell, yielder_cell}

assert warehouse.zone_type_at(yield_cell) not in (
    ZoneType.SHELF,
    ZoneType.DROPOFF,
)

print()
print("checks:")
print("  one-cell displacement : PASS")
print("  traversable           : PASS")
print("  currently unoccupied  : PASS")
print("  not service cell      : PASS")

print()
print("status                  : OK")
