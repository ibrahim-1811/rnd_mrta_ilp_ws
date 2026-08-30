"""Nearest A* picks the expected pairing on a controlled grid.

One focused test, not a suite: it pins the two things that make the
allocator scientific rather than incidental -- that cost is grid
distance and not Euclidean, and that the greedy pairing is fully
determined by the data.
"""

from mrta_core.warehouse_map import (
    WarehouseMap,
    WarehouseZone,
    ZoneType,
)
from mrta_core.world import WorldGrid
from task_management.allocation_strategy import get_batch_strategy


class FakeOrder:
    """The two fields the allocator reads off an order."""

    def __init__(self, order_id, shelf_ids, dropoff_id):
        self.order_id = order_id
        self.shelf_ids = tuple(shelf_ids)
        self.dropoff_id = dropoff_id


def corridor_warehouse():
    """A 9x3 hall with a wall forcing a detour.

    Row 1 is walled off between x=2 and x=6, so the straight line from
    the left bay to shelf_right crosses the wall: a Euclidean cost
    would call it near, while the real route has to go around.

        y=2  . . . . . . . . .
        y=1  . . # # # # # . .
        y=0  . . . . . . . . .
    """

    obstacles = frozenset((x, 1) for x in range(2, 7))

    zones = (
        WarehouseZone("shelf_near", ZoneType.SHELF, frozenset({(1, 2)})),
        WarehouseZone("shelf_far", ZoneType.SHELF, frozenset({(8, 0)})),
        WarehouseZone("dropoff_a", ZoneType.DROPOFF, frozenset({(0, 0)})),
        WarehouseZone("dropoff_b", ZoneType.DROPOFF, frozenset({(8, 2)})),
    )

    return WarehouseMap(
        grid=WorldGrid(
            origin_x=0.0,
            origin_y=0.0,
            resolution=1.0,
            width=9,
            height=3,
            obstacles=obstacles,
        ),
        zones=zones,
    )


def test_nearest_astar_pairs_each_robot_with_its_grid_nearest_order():
    warehouse = corridor_warehouse()

    allocator = get_batch_strategy("nearest_astar")

    # robot_001 sits by the near shelf, robot_002 by the far one.
    robots = [("robot_001", (0, 2)), ("robot_002", (7, 0))]

    orders = [
        FakeOrder("O0001", ["shelf_near"], "dropoff_a"),
        FakeOrder("O0002", ["shelf_far"], "dropoff_b"),
    ]

    fifo = {"O0001": 0, "O0002": 1}

    assignments = allocator.assign(warehouse, robots, orders, fifo)

    paired = {a.order_id: a.robot_id for a in assignments}

    assert paired == {"O0001": "robot_001", "O0002": "robot_002"}

    # Reported in FIFO order regardless of which robot won.
    assert [a.order_id for a in assignments] == ["O0001", "O0002"]

    # Every robot and every order used at most once.
    assert len({a.robot_id for a in assignments}) == 2


def test_cost_is_grid_distance_not_euclidean():
    """The wall must show up in the cost, or A* is not being used."""

    warehouse = corridor_warehouse()

    allocator = get_batch_strategy("nearest_astar")

    # From (0,2), shelf_far at (8,0) is ~8.2 away in a straight line,
    # but the only route runs around the wall.
    assignments = allocator.assign(
        warehouse,
        [("robot_001", (0, 2))],
        [FakeOrder("O0002", ["shelf_far"], "dropoff_b")],
        {"O0002": 0},
    )

    cost = assignments[0].cost

    assert cost > 8.25, (
        f"cost {cost} is at most the straight-line distance; "
        f"the wall was not routed around"
    )


def test_multi_shelf_order_takes_the_best_visiting_permutation():
    warehouse = corridor_warehouse()

    allocator = get_batch_strategy("nearest_astar")

    order = FakeOrder("O0001", ["shelf_far", "shelf_near"], "dropoff_a")

    assignments = allocator.assign(
        warehouse,
        [("robot_001", (8, 2))],
        [order],
        {"O0001": 0},
    )

    route = assignments[0].route

    # Starting at the far right and dropping at the far left, the
    # cheapest order of visits is far shelf first, near shelf second.
    assert route.shelf_sequence == ("shelf_far", "shelf_near")
    assert route.zone_sequence == (
        "shelf_far",
        "shelf_near",
        "dropoff_a",
    )


def test_pairing_is_deterministic_across_repeated_calls():
    warehouse = corridor_warehouse()

    orders = [
        FakeOrder("O0001", ["shelf_near"], "dropoff_a"),
        FakeOrder("O0002", ["shelf_far"], "dropoff_b"),
    ]

    fifo = {"O0001": 0, "O0002": 1}

    # Robots offered in opposite order must not change the outcome.
    forward = get_batch_strategy("nearest_astar").assign(
        warehouse,
        [("robot_001", (0, 2)), ("robot_002", (7, 0))],
        orders,
        fifo,
    )

    backward = get_batch_strategy("nearest_astar").assign(
        warehouse,
        [("robot_002", (7, 0)), ("robot_001", (0, 2))],
        list(reversed(orders)),
        fifo,
    )

    assert [(a.robot_id, a.order_id) for a in forward] == [
        (a.robot_id, a.order_id) for a in backward
    ]
