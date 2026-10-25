"""Terminals are leg endpoints, never transit, for A* and RMF alike.

``directional_blocked_edges()`` answers which way an aisle runs, and
forbids every terminal -> terminal edge. ``navigation_grid()``
separately blocks every terminal except the current leg's own start
and goal. A* gets both rules; a static graph gets the edge set from
``traversable_lane_edges()``, which resolves them without the per-leg
context. These tests pin the contract the RMF nav graph generator,
the verifier and the executor all depend on.
"""

from mrta_core.warehouse_map import (
    WarehouseMap,
    WarehouseZone,
    ZoneType,
)
from mrta_core.warehouse_routing import (
    NON_TRANSIT_TYPES,
    directional_blocked_edges,
    non_transit_cells,
    plan_from_cell,
    traversable_lane_edges,
)
from mrta_core.world import WorldGrid


def bank_warehouse():
    """A home bank beside its frontage lane, on production coordinates.

    x=0 is the home bank, x=1 the northbound frontage lane and x=2 the
    southbound one, which is exactly the geometry that produced the
    shortcut: eleven adjacent home cells in a column.

        x=0  x=1  x=2
        H     .    .     y=14
        H     .    .     y=13
        H     .    .     y=12
    """

    rows = range(12, 15)

    zones = tuple(
        WarehouseZone(
            f"staging_{number:03d}",
            ZoneType.STAGING,
            frozenset({(0, y)}),
        )
        for number, y in enumerate(rows, start=1)
    )

    return WarehouseMap(
        grid=WorldGrid(
            origin_x=0.0,
            origin_y=0.0,
            resolution=1.0,
            width=3,
            height=16,
            obstacles=frozenset(
                (x, y) for x in range(3) for y in range(16) if y not in rows
            ),
        ),
        zones=zones,
    )


def test_terminal_chain_is_not_a_lane():
    """Adjacent home cells must not form a route along the bank."""

    warehouse = bank_warehouse()

    terminals = non_transit_cells(warehouse)
    edges = traversable_lane_edges(warehouse)

    chain = sorted(
        (source, target)
        for source, target in edges
        if source in terminals and target in terminals
    )

    assert chain == []

    # The lane policy itself forbids the chain too, in both directions,
    # so A* cannot take it even between two adjacent leg endpoints.
    blocked = directional_blocked_edges(warehouse)

    assert ((0, 12), (0, 13)) in blocked
    assert ((0, 13), (0, 12)) in blocked


def test_adjacent_terminals_are_joined_through_the_aisle():
    """A leg between two neighbouring bank cells leaves via the aisle.

    navigation_grid() unblocks both endpoints of the leg, so this is the
    one case the per-leg rule alone does not cover.
    """

    warehouse = bank_warehouse()

    terminals = non_transit_cells(warehouse)

    plan = plan_from_cell(
        warehouse,
        (0, 12),
        ["staging_002"],
    )

    path = list(plan.path)

    assert path[0] == (0, 12)
    assert path[-1] == (0, 13)
    assert len(path) > 2

    for source, target in zip(path, path[1:]):
        assert not (source in terminals and target in terminals), (
            f"{source} -> {target} hops between terminals"
        )


def test_endpoint_access_survives():
    """A robot must still drive into a station and reverse back out."""

    warehouse = bank_warehouse()

    terminals = non_transit_cells(warehouse)
    edges = traversable_lane_edges(warehouse)

    entered = {target for _, target in edges}
    exited = {source for source, _ in edges}

    for cell in sorted(terminals):
        assert cell in entered, f"no lane into {cell}"
        assert cell in exited, f"no lane out of {cell}"

    # Specifically the aisle pairing, in both directions.
    assert ((1, 13), (0, 13)) in edges
    assert ((0, 13), (1, 13)) in edges


def test_transit_lanes_are_left_alone():
    """The rule removes terminal chains and nothing else."""

    warehouse = bank_warehouse()

    terminals = non_transit_cells(warehouse)
    edges = traversable_lane_edges(warehouse)
    blocked = directional_blocked_edges(warehouse)

    grid = warehouse.grid

    for y in range(grid.height):
        for x in range(grid.width):
            source = (x, y)

            if source in grid.obstacles:
                continue

            for target in (
                (x + 1, y),
                (x - 1, y),
                (x, y + 1),
                (x, y - 1),
            ):
                if (
                    not (0 <= target[0] < grid.width and 0 <= target[1] < grid.height)
                    or target in grid.obstacles
                ):
                    continue

                permitted = (source, target) not in blocked

                both_terminal = source in terminals and target in terminals

                assert ((source, target) in edges) == (
                    permitted and not both_terminal
                ), f"{source} -> {target} disagrees with the policy"


def test_chargers_and_holding_cells_are_terminals():
    """The live policy treats the charging apron like any other bank.

    A charger and its neighbouring holding cell are both terminals: no
    lane joins them directly, and each keeps its two-way service
    connection to the aisle cell beside it.
    """

    assert ZoneType.CHARGING in NON_TRANSIT_TYPES
    assert ZoneType.CHARGE_QUEUE in NON_TRANSIT_TYPES

    zones = (
        WarehouseZone("charging_001", ZoneType.CHARGING, frozenset({(4, 2)})),
        WarehouseZone("charge_queue_001", ZoneType.CHARGE_QUEUE, frozenset({(5, 2)})),
    )

    warehouse = WarehouseMap(
        grid=WorldGrid(
            origin_x=0.0,
            origin_y=0.0,
            resolution=1.0,
            width=8,
            height=4,
            obstacles=frozenset(),
        ),
        zones=zones,
    )

    charger = (4, 2)
    holding = (5, 2)

    assert non_transit_cells(warehouse) == frozenset({charger, holding})

    lanes = traversable_lane_edges(warehouse)
    blocked = directional_blocked_edges(warehouse)

    assert (holding, charger) not in lanes
    assert (charger, holding) not in lanes
    assert (holding, charger) in blocked
    assert (charger, holding) in blocked

    # Drive in from the aisle and reverse back out, for both.
    for terminal in (charger, holding):
        aisle = (terminal[0], terminal[1] + 1)

        assert (aisle, terminal) in lanes
        assert (terminal, aisle) in lanes
