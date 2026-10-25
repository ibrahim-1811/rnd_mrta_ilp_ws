"""The V2 charging bank: one cell deep, reachable, never transit.

These pin the B0 map correction on the real map. The apron was
authored two cells deep against the wall, and with chargers and
holding cells out of transit half of it could not be both entered and
left. The bank is now a single row of terminals facing the aisle, like
every other station bank, and every cell in it must be enterable from
the core, leavable back to it, and never crossed on the way somewhere
else.
"""

from pathlib import Path

import pytest

from mrta_core.grid import astar_path
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import (
    navigation_grid,
    non_transit_cells,
    plan_from_cell,
    traversable_lane_edges,
)


CONFIG = Path(__file__).resolve().parent / "fixtures" / "v2_reference.yaml"

BANK_ROWS = (2, 32)

CHARGER_COLS = (4, 7, 10, 13)
HOLDING_COLS = (5, 6, 8, 9, 11, 12, 14)

# The aisle rows the two banks face.
AISLE_ROW = {2: 3, 32: 31}

# Interior aisle floor in the middle band, well away from any bank.
CORE_CELL = (8, 17)


@pytest.fixture(scope="module")
def warehouse():
    return load_warehouse_xlsx(load_sim_config(CONFIG).map_path)


@pytest.fixture(scope="module")
def terminals(warehouse):
    return non_transit_cells(warehouse)


def cells_of(warehouse, zone_type):
    return {min(zone.cells) for zone in warehouse.zones_of_type(zone_type)}


def zone_at(warehouse, cell):
    return warehouse.zone_at(cell).zone_id


def leg(warehouse, terminals, start, goal):
    """One mission leg, planned exactly as the executor plans it."""

    return astar_path(
        navigation_grid(
            warehouse,
            occupied=terminals,
            start=start,
            goal=goal,
        ),
        start,
        goal,
    )


def test_bank_is_one_cell_deep(warehouse):
    """8 chargers and 14 holding cells in one row, wall behind them."""

    assert cells_of(warehouse, ZoneType.CHARGING) == {
        (x, y) for x in CHARGER_COLS for y in BANK_ROWS
    }

    assert cells_of(warehouse, ZoneType.CHARGE_QUEUE) == {
        (x, y) for x in HOLDING_COLS for y in BANK_ROWS
    }

    for y in (1, 33):
        for x in range(4, 15):
            assert (x, y) in warehouse.grid.obstacles, (
                f"outer apron cell {(x, y)} is not wall"
            )


@pytest.mark.parametrize(
    "zone_type",
    [
        ZoneType.CHARGING,
        ZoneType.CHARGE_QUEUE,
    ],
)
def test_every_bank_cell_can_be_entered_and_left(warehouse, terminals, zone_type):
    """From the core in, and from the cell back out, via its aisle."""

    for cell in sorted(cells_of(warehouse, zone_type)):
        aisle = (cell[0], AISLE_ROW[cell[1]])

        inbound = leg(warehouse, terminals, CORE_CELL, cell)
        outbound = leg(warehouse, terminals, cell, CORE_CELL)

        assert inbound.found, f"cannot reach {cell}"
        assert outbound.found, f"cannot leave {cell}"

        # Drive in from the aisle cell outside, reverse back into it.
        assert inbound.path[-2] == aisle
        assert outbound.path[1] == aisle


def test_holding_to_neighbouring_charger_goes_through_the_aisle(
    warehouse,
):
    """The one move A* used to take directly, bank cell to bank cell."""

    plan = plan_from_cell(
        warehouse,
        (5, 2),
        [zone_at(warehouse, (4, 2))],
    )

    assert list(plan.path) == [(5, 2), (5, 3), (4, 3), (4, 2)]


def test_no_route_crosses_the_charging_bank(warehouse, terminals):
    """Bank to bank, and bank to stations, never via another terminal.

    Every move must also be a lane the RMF nav graph carries, so the
    Gazebo mirror and the scientific planner agree on these legs.
    """

    lanes = traversable_lane_edges(warehouse)

    bank = sorted(
        cells_of(warehouse, ZoneType.CHARGING)
        | cells_of(warehouse, ZoneType.CHARGE_QUEUE)
    )

    stations = []

    for zone_type in (ZoneType.SHELF, ZoneType.DROPOFF, ZoneType.STAGING):
        zones = sorted(
            warehouse.zones_of_type(zone_type),
            key=lambda zone: zone.zone_id,
        )
        stations += [min(zones[0].cells), min(zones[-1].cells)]

    pairs = [(a, b) for a in bank for b in bank + stations if a != b] + [
        (a, b) for a in stations for b in bank
    ]

    for start, goal in pairs:
        result = leg(warehouse, terminals, start, goal)

        assert result.found, f"{start} -> {goal} unreachable"

        path = result.path

        assert not any(cell in terminals for cell in path[1:-1]), (
            f"{start} -> {goal} crosses a terminal: {path}"
        )

        for move in zip(path, path[1:]):
            assert move in lanes, f"{start} -> {goal}: {move} is not a nav graph lane"
