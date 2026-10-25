"""Phase 7 slice 1: Random and Nearest Manhattan beside Nearest A*.

Three strategies, one sweep. What is tested here is not that each picks
some particular pair -- that is the experiment -- but the properties
that have to hold for a comparison between them to mean anything:

  * they differ by their ranking and by nothing else
  * none of them can escape the battery eligibility mask
  * each returns a legal matching, never two orders for one robot
  * the stochastic one is reproducible from its seed
  * Manhattan decides the pairing and never the route

The practice warehouse is reused from ``test_nearest_astar`` rather than
redrawn. The lane policy reads absolute grid coordinates, so a map on
arbitrary coordinates is not a smaller warehouse but an illegal one;
that fixture is a genuine one-band slice of the production map and is
asserted to obey the production lane policy.
"""

from fractions import Fraction
from math import inf

import pytest

from mrta_core.battery import BatteryConfig, BatteryDecisionContext
from task_management.allocation_strategy import (
    BATCH_STRATEGIES,
    get_batch_strategy,
)
from task_management.features import manhattan_order_cost
from test_nearest_astar import (
    EAST_HOME,
    WEST_HOME,
    FakeOrder,
    east_order,
    practice_warehouse,
    west_order,
)


SLICE_1 = ("nearest_astar", "nearest_manhattan", "random")


@pytest.fixture(scope="module")
def warehouse():
    return practice_warehouse()


@pytest.fixture
def orders():
    return [west_order(), east_order()]


@pytest.fixture
def fifo(orders):
    return {order.order_id: index for index, order in enumerate(orders)}


def pairs(assignments):
    return {(a.robot_id, a.order_id) for a in assignments}


def battery_context(soc, charger_distance_m=4.0):
    """A controlled battery context. The charger is a fixed leg away."""

    return BatteryDecisionContext(
        config=BatteryConfig(),
        soc={robot: Fraction(value) for robot, value in soc.items()},
        shelf_service_s=Fraction(2),
        dropoff_service_s=Fraction(2),
        nearest_charger=lambda cell: ("charging_001", charger_distance_m),
    )


# ----------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------


def test_all_three_strategies_are_registered():
    assert set(SLICE_1) <= set(BATCH_STRATEGIES)

    for name in SLICE_1:
        assert get_batch_strategy(name).name == name


def test_an_unknown_strategy_is_refused_by_name():
    with pytest.raises(KeyError, match="unknown batch strategy"):
        get_batch_strategy("nearest_euclidean")


# ----------------------------------------------------------------------
# The point of the slice: the policies actually disagree.
# ----------------------------------------------------------------------


def test_manhattan_and_astar_choose_different_pairings(warehouse, orders, fifo):
    """A controlled case where the naive metric picks the other pairing.

    Measured on the practice map, robot_001 at (1, 1), robot_002 at
    (9, 1)::

                          O0001 west      O0002 east
        robot_001 (1,1)   A* 25  M 14     A* 31  M 16
        robot_002 (9,1)   A* 11  M 10     A* 11  M  8

    A* sees robot_002's two options as *equal* at 11 m, so its tie-break
    gives robot_002 the older order and robot_001 the east one.
    Manhattan sees 8 < 10 and sends robot_002 east instead. That 8 is
    the illusion: in a one-way warehouse the straight line is not the
    route, and both legs really cost 11.

    Note what this case does *not* show. Greedy A* is myopic, so on this
    2x2 the Manhattan pairing happens to be cheaper in true cost
    (25 + 11 = 36 against 31 + 11 = 42). Being greedy is a property of
    the baseline, documented as such; on the full workload A* wins on
    distance, 5798 m against 5974 m.
    """

    robots = [("robot_001", (1, 1)), ("robot_002", (9, 1))]

    astar = get_batch_strategy("nearest_astar").assign(
        warehouse, robots, orders, fifo
    )
    manhattan = get_batch_strategy("nearest_manhattan").assign(
        warehouse, robots, orders, fifo
    )

    assert pairs(astar) == {("robot_001", "O0002"), ("robot_002", "O0001")}
    assert pairs(manhattan) == {("robot_001", "O0001"), ("robot_002", "O0002")}


def test_manhattan_reports_the_astar_cost_not_its_own_ranking(
    warehouse, orders, fifo
):
    """The ranking key must not leak into the recorded cost.

    ``cost`` is C_static, the predicted cost of the route that will
    actually be driven, and the dataset compares it against the realized
    distance. If Manhattan wrote its own number there, every
    predicted-vs-realized comparison for this strategy would be wrong.
    """

    robots = [("robot_001", (1, 1)), ("robot_002", (9, 1))]

    assignments = get_batch_strategy("nearest_manhattan").assign(
        warehouse, robots, orders, fifo
    )

    by_pair = {(a.robot_id, a.order_id): a for a in assignments}

    # The pinned A* costs of the pairs Manhattan chose, not 14 and 8.
    assert by_pair[("robot_001", "O0001")].cost == 25.0
    assert by_pair[("robot_002", "O0002")].cost == 11.0

    # And the route carried forward is the directed A* route.
    for assignment in assignments:
        assert assignment.route.reachable
        assert assignment.route.plan is not None
        assert assignment.cost == assignment.route.distance_m


# ----------------------------------------------------------------------
# Properties every strategy must hold, or the comparison is void.
# ----------------------------------------------------------------------


@pytest.mark.parametrize("name", SLICE_1)
def test_a_strategy_returns_a_legal_matching(name, warehouse, orders, fifo):
    robots = [("robot_001", WEST_HOME), ("robot_002", EAST_HOME)]

    assignments = get_batch_strategy(name).assign(
        warehouse, robots, orders, fifo
    )

    assigned_robots = [a.robot_id for a in assignments]
    assigned_orders = [a.order_id for a in assignments]

    assert len(assigned_robots) == len(set(assigned_robots))
    assert len(assigned_orders) == len(set(assigned_orders))

    # Never an order outside the window it was offered.
    assert set(assigned_orders) <= {order.order_id for order in orders}
    assert set(assigned_robots) <= {robot for robot, _ in robots}


@pytest.mark.parametrize("name", SLICE_1)
def test_a_strategy_reports_in_fifo_order(name, warehouse, orders, fifo):
    """Whichever robot won it, the oldest order is reported first."""

    robots = [("robot_001", WEST_HOME), ("robot_002", EAST_HOME)]

    assignments = get_batch_strategy(name).assign(
        warehouse, robots, orders, fifo
    )

    positions = [fifo[a.order_id] for a in assignments]

    assert positions == sorted(positions)


@pytest.mark.parametrize("name", SLICE_1)
def test_no_strategy_can_escape_the_battery_mask(name, warehouse, orders, fifo):
    """Eligibility is infrastructure, not a thing a policy may weigh.

    At 11% SOC neither robot can finish an order and still reach a
    charger on the 10% reserve, so there is no legal pair and every
    strategy must defer -- the cheap one, the naive one and the random
    one alike.
    """

    robots = [("robot_001", WEST_HOME), ("robot_002", EAST_HOME)]

    battery = battery_context({"robot_001": 11, "robot_002": 11})

    assignments = get_batch_strategy(name).assign(
        warehouse, robots, orders, fifo, None, battery=battery
    )

    assert assignments == []


@pytest.mark.parametrize("name", SLICE_1)
def test_a_strategy_assigns_only_the_robot_that_can_afford_it(
    name, warehouse, orders, fifo
):
    """One robot full, one nearly flat: only the full one may be used."""

    robots = [("robot_001", WEST_HOME), ("robot_002", EAST_HOME)]

    battery = battery_context({"robot_001": 100, "robot_002": 11})

    assignments = get_batch_strategy(name).assign(
        warehouse, robots, orders, fifo, None, battery=battery
    )

    assert [a.robot_id for a in assignments] == ["robot_001"]

    for assignment in assignments:
        assert assignment.energy is not None
        assert assignment.energy.battery_eligible


# ----------------------------------------------------------------------
# Random: stochastic, but reproducible.
# ----------------------------------------------------------------------


def wide_case():
    """Enough robots and orders that a shuffle can actually change something."""

    orders = [
        FakeOrder("O0001", ["shelf_west"], "dropoff_west"),
        FakeOrder("O0002", ["shelf_east"], "dropoff_east"),
        FakeOrder("O0003", ["shelf_east"], "dropoff_west"),
        FakeOrder("O0004", ["shelf_west"], "dropoff_east"),
    ]

    robots = [
        ("robot_001", (1, 1)),
        ("robot_002", (9, 1)),
        ("robot_003", (5, 3)),
        ("robot_004", (12, 5)),
    ]

    return robots, orders, {o.order_id: i for i, o in enumerate(orders)}


def test_random_is_reproducible_from_its_seed(warehouse):
    robots, orders, fifo = wide_case()

    first = get_batch_strategy("random", seed=42).assign(
        warehouse, robots, orders, fifo
    )
    second = get_batch_strategy("random", seed=42).assign(
        warehouse, robots, orders, fifo
    )

    assert pairs(first) == pairs(second)


def test_a_different_seed_gives_a_different_pairing(warehouse):
    """Otherwise the seed is decoration and the sweep is not random."""

    robots, orders, fifo = wide_case()

    seen = {
        frozenset(
            pairs(
                get_batch_strategy("random", seed=seed).assign(
                    warehouse, robots, orders, fifo
                )
            )
        )
        for seed in range(12)
    }

    assert len(seen) > 1


def test_reset_restores_the_random_stream(warehouse):
    """``reset()`` must genuinely reset, or a re-run is not a re-run."""

    robots, orders, fifo = wide_case()

    strategy = get_batch_strategy("random", seed=42)

    first = pairs(strategy.assign(warehouse, robots, orders, fifo))

    # Advance the stream, then rewind it.
    strategy.assign(warehouse, robots, orders, fifo)
    strategy.reset()

    assert pairs(strategy.assign(warehouse, robots, orders, fifo)) == first


def test_random_does_not_depend_on_the_order_it_is_handed(warehouse):
    """A seeded shuffle of a list built from dict iteration is not seeded.

    The strategy sorts into a canonical order before shuffling, so
    presenting the same decision with the robots and orders listed
    backwards must not change the outcome.
    """

    robots, orders, fifo = wide_case()

    forward = get_batch_strategy("random", seed=42).assign(
        warehouse, robots, orders, fifo
    )
    backward = get_batch_strategy("random", seed=42).assign(
        warehouse, robots[::-1], orders[::-1], fifo
    )

    assert pairs(forward) == pairs(backward)


# ----------------------------------------------------------------------
# The Manhattan key itself.
# ----------------------------------------------------------------------


def test_manhattan_cost_is_the_straight_line_chain(warehouse):
    """start -> shelf -> dropoff, ignoring every wall and lane rule."""

    # (1,1) -> shelf_west (7,4) -> dropoff_west (4,2)
    #   |1-7| + |1-4| = 9,  |7-4| + |4-2| = 5  ->  14
    assert manhattan_order_cost(warehouse, (1, 1), ["shelf_west"], "dropoff_west") == 14


def test_manhattan_picks_the_cheaper_shelf_permutation(warehouse):
    """Same structure as order_route_cost: it evaluates every ordering.

    Two shelves and a drop-off chosen so the two visiting orders really
    differ, (1,1) -> shelves -> dropoff_east (13,5)::

        west then east   9 + 4 + 5 = 18     <- the better one
        east then west  11 + 4 + 7 = 22

    A cost function that walked the shelves in the order given would
    return 22 half the time, and the baseline would be a different
    baseline depending on how the CSV happened to list the items.
    """

    best = 18

    assert (
        manhattan_order_cost(
            warehouse, (1, 1), ["shelf_west", "shelf_east"], "dropoff_east"
        )
        == best
    )

    # Argument order must not matter: both evaluate every permutation.
    assert (
        manhattan_order_cost(
            warehouse, (1, 1), ["shelf_east", "shelf_west"], "dropoff_east"
        )
        == best
    )


def test_manhattan_cost_is_infinite_for_an_unknown_zone(warehouse):
    """Matching order_route_cost's unreachable convention."""

    assert manhattan_order_cost(warehouse, (1, 1), ["shelf_nowhere"], "dropoff_west") == inf
    assert manhattan_order_cost(warehouse, (1, 1), ["shelf_west"], "dropoff_nowhere") == inf


def test_manhattan_underestimates_the_real_route(warehouse, orders, fifo):
    """The naive metric is optimistic, which is exactly its weakness.

    A straight line can only be shorter than a legal one-way route, so
    Manhattan never over-estimates. A strategy ranking on it is
    systematically confident about pairs the warehouse will punish.
    """

    robots = [("robot_001", (1, 1)), ("robot_002", (9, 1))]

    astar = get_batch_strategy("nearest_astar")

    for robot_id, cell in robots:
        for order in orders:
            (candidate,) = astar.score(
                warehouse, [(robot_id, cell)], [order], fifo
            )

            naive = manhattan_order_cost(
                warehouse, cell, order.shelf_ids, order.dropoff_id
            )

            assert naive <= candidate.cost
