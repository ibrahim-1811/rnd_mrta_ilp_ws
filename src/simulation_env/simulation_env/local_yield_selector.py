"""Select one nearby temporary yield cell for a local traffic conflict.

The selector performs no mission planning and no MRTA.

A yield cell must:
- be one Manhattan step from the robot
- be inside the warehouse
- be traversable
- not be a shelf or dropoff service cell
- not currently be occupied
- not be reserved by another simultaneous movement
- not be part of the immediate conflicting edge
- be legal to enter AND to leave again under the directed lane
  policy, because the rejoin is a single reverse step

The robot moves there temporarily, lets the higher right-of-way robot
pass, then returns to its route.
"""

from __future__ import annotations

from mrta_core.grid import Cell
from mrta_core.warehouse_map import WarehouseMap
from mrta_core.warehouse_routing import (
    NON_TRANSIT_TYPES,
    directional_blocked_edges,
)


DIRS = (
    (1, 0),
    (-1, 0),
    (0, 1),
    (0, -1),
)


def _neighbors(cell: Cell):
    x, y = cell

    for dx, dy in DIRS:
        yield (x + dx, y + dy)


def _manhattan(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def select_local_yield_cell(
    warehouse: WarehouseMap,
    source: Cell,
    intended_target: Cell,
    occupied: set[Cell] | frozenset[Cell],
    reserved_targets: set[Cell] | frozenset[Cell] = frozenset(),
    forbidden: set[Cell] | frozenset[Cell] = frozenset(),
) -> Cell | None:
    """Return one safe adjacent yield cell, or None.

    Preference:
    1. valid local cell
    2. minimum distance back toward the robot's intended target
    3. deterministic coordinate ordering
    """

    grid = warehouse.grid

    # Use exactly the same fixed directed-road policy as normal
    # warehouse routing.
    blocked_edges = directional_blocked_edges(
        warehouse
    )

    candidates = []

    for cell in _neighbors(source):

        x, y = cell

        if not (
            0 <= x < grid.width
            and 0 <= y < grid.height
        ):
            continue

        if cell in grid.obstacles:
            continue

        if cell in occupied:
            continue

        if cell in reserved_targets:
            continue

        if cell in forbidden:
            continue

        # The intended head-on target is not a yield cell.
        if cell == intended_target:
            continue

        # A bay must be legal to enter AND legal to leave by the same
        # edge, because the engine rejoins in exactly one reverse step
        # (``assign_path([yield_cell, corridor_cell])``). A bay whose
        # reverse edge is forbidden would put the robot the wrong way
        # up a one-way aisle.
        #
        # On the directed warehouse that leaves precisely one kind of
        # bay: the aisle on the far side of a service cell, since
        # shelf, dropoff and home edges stay two-way. That is also the only
        # place a head-on can still arise, so the two match up.
        #
        # A robot standing in a one-way aisle has no bay and simply
        # waits. That is safe rather than deadlocking, because two
        # robots can no longer face each other in an aisle at all.
        if (
            (source, cell) in blocked_edges
            or
            (cell, source) in blocked_edges
        ):
            continue

        zone_type = warehouse.zone_type_at(cell)

        # Service cells are endpoints for actual work, not traffic
        # bays. This is the routing policy's own list, so a yield can
        # never be selected into a cell A* treats as non-transit --
        # including charge-queue holding cells.
        if zone_type in NON_TRANSIT_TYPES:
            continue

        candidates.append(cell)

    if not candidates:
        return None

    candidates.sort(
        key=lambda cell: (
            _manhattan(cell, intended_target),
            cell[0],
            cell[1],
        )
    )

    return candidates[0]
