"""Nearest A* picks the expected pairing on a controlled warehouse.

One focused test file, not a suite: it pins the two things that make
the allocator scientific rather than incidental -- that cost is the
legal directed route and not a straight line, and that the greedy
pairing is fully determined by the data.

Why the practice map looks like the real one
--------------------------------------------
The lane policy in ``mrta_core.warehouse_routing`` is written against
the production 17-wide layout and reads absolute grid coordinates:
``EASTBOUND_ROWS``, ``WESTBOUND_ROWS``, ``NORTHBOUND_COLS``,
``SOUTHBOUND_COLS``, ``SHELF_COLS`` and the frontage lanes are fixed
column and row numbers, not a description a smaller hall can satisfy.

A map drawn on arbitrary coordinates is therefore not a smaller
warehouse -- it is an illegal one. The earlier 9x3 fixture put its
cells where the policy grants no direction at all (row y=0 has no
east/west rule, columns 0..2 no north/south rule), so most of it had
no outgoing edges and the allocator correctly refused to route.

``practice_warehouse`` is instead a genuine one-band slice of the
production map: the same column roles, the same alternating row
directions, the same left/right home banks closing the circulation
loop. ``test_practice_map_obeys_the_production_lane_policy`` asserts
that, so this fixture cannot silently rot again.
"""

import math

from mrta_core.warehouse_map import (
    WarehouseMap,
    WarehouseZone,
    ZoneType,
)
from mrta_core.warehouse_routing import (
    directional_blocked_edges,
    non_transit_cells,
)
from mrta_core.world import WorldGrid
from task_management.allocation_strategy import get_batch_strategy


WIDTH = 17
HEIGHT = 7

# The open band. y=0 and y=6 are the blocked border rows, exactly as
# rows 0 and 34 are on the production map.
OPEN_ROWS = range(1, 6)

# Home banks, one per side, spanning the whole band. The bank rows are
# what ``frontage_band()`` reads to switch on the frontage lanes at
# x=1, x=2 and x=15, which is what closes the one-way loop at both
# ends of the hall.
HOME_COLS = (0, 16)

# Stations, one per shelf column so no column is cut into fragments.
# West cluster: shelf_west + dropoff_west. East cluster: the other two.
SHELF_WEST = (7, 4)
SHELF_EAST = (10, 3)
DROPOFF_WEST = (4, 2)
DROPOFF_EAST = (13, 5)

# Home cells the two test robots park on.
WEST_HOME = (0, 3)
EAST_HOME = (16, 3)


class FakeOrder:
    """The two fields the allocator reads off an order."""

    def __init__(self, order_id, shelf_ids, dropoff_id):
        self.order_id = order_id
        self.shelf_ids = tuple(shelf_ids)
        self.dropoff_id = dropoff_id


def practice_warehouse():
    """A one-band miniature of the production warehouse.

        y=6  # # # # # # # # # # # # # # # # #
        y=5  H . . . . . . . . . . . . D . . H
        y=4  H . . . . . . S . . . . . . . . H
        y=3  H . . . . . . . . . S . . . . . H
        y=2  H . . . D . . . . . . . . . . . H
        y=1  H . . . . . . . . . . . . . . . H
        y=0  # # # # # # # # # # # # # # # # #

    Rows alternate one-way (y=1 west, y=2 east, y=3 west, y=4 east,
    y=5 west) and columns alternate (x=3,6,9,12 north; x=5,8,11,14
    south; x=4,7,10,13 shelf columns, north within the band). x=1 and
    x=2 are the left frontage lanes, x=15 the right one.
    """

    obstacles = frozenset(
        (x, y)
        for x in range(WIDTH)
        for y in (0, HEIGHT - 1)
    )

    homes = tuple(
        WarehouseZone(
            f"staging_{number:03d}",
            ZoneType.STAGING,
            frozenset({(x, y)}),
        )
        for number, (x, y) in enumerate(
            (
                (x, y)
                for x in HOME_COLS
                for y in OPEN_ROWS
            ),
            start=1,
        )
    )

    stations = (
        WarehouseZone("shelf_west", ZoneType.SHELF, frozenset({SHELF_WEST})),
        WarehouseZone("shelf_east", ZoneType.SHELF, frozenset({SHELF_EAST})),
        WarehouseZone(
            "dropoff_west", ZoneType.DROPOFF, frozenset({DROPOFF_WEST})
        ),
        WarehouseZone(
            "dropoff_east", ZoneType.DROPOFF, frozenset({DROPOFF_EAST})
        ),
    )

    return WarehouseMap(
        grid=WorldGrid(
            origin_x=0.0,
            origin_y=0.0,
            resolution=1.0,
            width=WIDTH,
            height=HEIGHT,
            obstacles=obstacles,
        ),
        zones=homes + stations,
    )


def west_order():
    return FakeOrder("O0001", ["shelf_west"], "dropoff_west")


def east_order():
    return FakeOrder("O0002", ["shelf_east"], "dropoff_east")


def solo_cost(warehouse, start, order):
    """What one robot standing at ``start`` would pay for one order."""

    assignments = get_batch_strategy("nearest_astar").assign(
        warehouse,
        [("robot_001", start)],
        [order],
        {order.order_id: 0},
    )

    assert assignments, f"no legal route for {order.order_id} from {start}"

    return assignments[0].cost


# ----------------------------------------------------------------------
# The fixture itself
# ----------------------------------------------------------------------


def test_practice_map_obeys_the_production_lane_policy():
    """The practice map must be a legal warehouse, not just a drawing.

    Scored through the production policy functions rather than a
    restatement of the lane rules, so this fails the moment the
    fixture and the real lane policy drift apart.
    """

    warehouse = practice_warehouse()

    terminals = non_transit_cells(warehouse)
    blocked = directional_blocked_edges(warehouse)

    core = {
        (x, y)
        for y in range(HEIGHT)
        for x in range(WIDTH)
        if (x, y) not in warehouse.grid.obstacles
        and (x, y) not in terminals
    }

    forward = {cell: set() for cell in core}
    backward = {cell: set() for cell in core}
    two_way = set()

    for (x, y) in core:
        for target in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):

            if target not in core:
                continue

            if ((x, y), target) in blocked:
                continue

            forward[(x, y)].add(target)
            backward[target].add((x, y))

            if (target, (x, y)) not in blocked:
                two_way.add(frozenset({(x, y), target}))

    # The structural head-on the one-way core exists to remove.
    assert two_way == set(), (
        f"uncontrolled two-way core edges: {sorted(map(sorted, two_way))}"
    )

    def reachable(graph, source):
        seen = {source}
        stack = [source]

        while stack:
            for neighbor in graph[stack.pop()]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)

        return seen

    source = min(core)

    assert reachable(forward, source) == core, (
        "transit core is not forward reachable: "
        f"{sorted(core - reachable(forward, source))}"
    )

    assert reachable(backward, source) == core, (
        "transit core is not reverse reachable: "
        f"{sorted(core - reachable(backward, source))}"
    )


# ----------------------------------------------------------------------
# The allocator
# ----------------------------------------------------------------------


def test_nearest_astar_pairs_each_robot_with_its_grid_nearest_order():
    warehouse = practice_warehouse()

    # Each robot's own cheapest order really is the one on its side,
    # so the expected pairing is a property of the routes and not of
    # the order the robots happen to be offered in.
    assert solo_cost(warehouse, WEST_HOME, west_order()) < solo_cost(
        warehouse, WEST_HOME, east_order()
    )

    assert solo_cost(warehouse, EAST_HOME, east_order()) < solo_cost(
        warehouse, EAST_HOME, west_order()
    )

    allocator = get_batch_strategy("nearest_astar")

    robots = [("robot_001", WEST_HOME), ("robot_002", EAST_HOME)]

    orders = [west_order(), east_order()]

    fifo = {"O0001": 0, "O0002": 1}

    assignments = allocator.assign(warehouse, robots, orders, fifo)

    paired = {a.order_id: a.robot_id for a in assignments}

    assert paired == {"O0001": "robot_001", "O0002": "robot_002"}

    # Reported in FIFO order regardless of which robot won.
    assert [a.order_id for a in assignments] == ["O0001", "O0002"]

    # Every robot and every order used at most once.
    assert len({a.robot_id for a in assignments}) == 2


def test_cost_is_grid_distance_not_euclidean():
    """The one-way network must show up in the cost, or A* is not used."""

    warehouse = practice_warehouse()

    # The east robot reaching the west cluster is the clearest case:
    # row y=3 runs west, so the outbound leg is cheap, but the shelf
    # column and the return to dropoff_west force a detour no
    # straight-line measure can see.
    cost = solo_cost(warehouse, EAST_HOME, west_order())

    euclidean = (
        math.dist(EAST_HOME, SHELF_WEST)
        + math.dist(SHELF_WEST, DROPOFF_WEST)
    )

    manhattan = (
        abs(EAST_HOME[0] - SHELF_WEST[0])
        + abs(EAST_HOME[1] - SHELF_WEST[1])
        + abs(SHELF_WEST[0] - DROPOFF_WEST[0])
        + abs(SHELF_WEST[1] - DROPOFF_WEST[1])
    )

    assert cost > euclidean, (
        f"cost {cost} is at most the straight-line distance "
        f"{euclidean:.2f}; the lane policy was not routed through"
    )

    # Manhattan is the A* heuristic and an admissible lower bound. A
    # legal route through one-way aisles has to beat it strictly here,
    # which is the whole reason Manhattan may not stand in for the
    # production cost.
    assert cost > manhattan, (
        f"cost {cost} is at most the Manhattan distance {manhattan}; "
        f"the one-way detour is missing"
    )


def test_multi_shelf_order_takes_the_best_visiting_permutation():
    warehouse = practice_warehouse()

    allocator = get_batch_strategy("nearest_astar")

    # Shelves are handed over west-first, but dropping at dropoff_west
    # makes visiting the east shelf first the cheaper route.
    order = FakeOrder("O0001", ["shelf_west", "shelf_east"], "dropoff_west")

    assignments = allocator.assign(
        warehouse,
        [("robot_001", WEST_HOME)],
        [order],
        {"O0001": 0},
    )

    route = assignments[0].route

    assert route.shelf_sequence == ("shelf_east", "shelf_west")

    assert route.zone_sequence == (
        "shelf_east",
        "shelf_west",
        "dropoff_west",
    )

    # The visiting order comes from the route cost, not from the order
    # the shelves arrived in: reversing the input must not move it.
    reversed_input = allocator.assign(
        warehouse,
        [("robot_001", WEST_HOME)],
        [FakeOrder("O0001", ["shelf_east", "shelf_west"], "dropoff_west")],
        {"O0001": 0},
    )

    assert reversed_input[0].route.shelf_sequence == route.shelf_sequence
    assert reversed_input[0].cost == assignments[0].cost


def test_pairing_is_deterministic_across_repeated_calls():
    warehouse = practice_warehouse()

    fifo = {"O0001": 0, "O0002": 1}

    # Robots offered in opposite order must not change the outcome.
    forward = get_batch_strategy("nearest_astar").assign(
        warehouse,
        [("robot_001", WEST_HOME), ("robot_002", EAST_HOME)],
        [west_order(), east_order()],
        fifo,
    )

    backward = get_batch_strategy("nearest_astar").assign(
        warehouse,
        [("robot_002", EAST_HOME), ("robot_001", WEST_HOME)],
        [east_order(), west_order()],
        fifo,
    )

    assert [(a.robot_id, a.order_id) for a in forward] == [
        (a.robot_id, a.order_id) for a in backward
    ]
