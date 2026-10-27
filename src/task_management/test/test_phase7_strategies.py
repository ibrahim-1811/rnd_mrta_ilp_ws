"""Phase 7: the five remaining conventional strategies.

Two kinds of test:

  * controlled allocation scenarios, on hand-set candidate costs, where
    each new strategy must choose differently from the strategy it is
    most likely to collapse onto -- and for the reason its definition
    gives, not by accident
  * the properties every strategy must hold on the real practice map,
    or a comparison between them is void: a legal matching, the battery
    mask, C_static carried forward, determinism

The controlled scenarios call the strategies' pairing functions on
candidates built here, so the costs are exact and the scenario says
precisely what is being tested. Nearest A*'s choice in a scenario is
its own sort key over the same candidates.
"""

from fractions import Fraction

import pytest

from mrta_core.battery import EnergyRequirement
from task_management.allocation_strategy import (
    BATCH_STRATEGIES,
    MultiCriteriaGridAwareAllocator,
    auction_pairing,
    earliest_finish_pairing,
    get_batch_strategy,
    greedy_pairing,
    hungarian_pairing,
    milp_pairing,
    multi_criteria_scores,
    MULTI_CRITERIA_WEIGHTS,
)
from task_management.features import DecisionTiming, OrderCandidate, OrderRoute
from test_nearest_astar import EAST_HOME, WEST_HOME, east_order, practice_warehouse, west_order
from test_strategies import battery_context


PHASE_7 = ("earliest_finish", "multi_criteria", "auction", "hungarian", "milp")
ALL = ("nearest_astar", "nearest_manhattan", "random", *PHASE_7)
NEEDS_TIMING = ("earliest_finish", "multi_criteria", "milp")


# -- controlled candidates ---------------------------------------------


def candidate(robot, order, cost, fifo, shelves=1, energy=None):
    route = OrderRoute(
        shelf_sequence=tuple(f"shelf_{i}" for i in range(shelves)),
        dropoff_id="dropoff",
        distance_m=cost,
        plan=object(),  # reachable; the scenarios never drive it
    )

    return OrderCandidate(
        robot_id=robot,
        order_id=order,
        fifo_position=fifo,
        cost=cost,
        route=route,
        energy=energy,
    )


def timing(robots, speed=1.5, shelf=1.0, dropoff=1.0):
    return DecisionTiming(
        speed_mps={r: speed for r in robots} if not isinstance(speed, dict) else speed,
        shelf_service_s=shelf,
        dropoff_service_s=dropoff,
    )


def fifo_of(candidates):
    return {c.order_id: c.fifo_position for c in candidates}


def nearest(candidates):
    """nearest_astar's choice: its own sort key, the shared sweep."""

    ranked = sorted(candidates, key=lambda c: (c.cost, c.fifo_position, c.robot_id))
    return pairs(greedy_pairing(ranked, fifo_of(candidates)))


def pairs(assignments):
    return {(a.robot_id, a.order_id) for a in assignments}


def energy(margin, charger_m=10.0):
    zero = Fraction(0)
    return EnergyRequirement(
        current_soc_percent=Fraction(50),
        predicted_order_distance_m=zero,
        predicted_order_travel_energy_percent=zero,
        predicted_service_energy_percent=zero,
        predicted_nearest_charger_distance_m=Fraction(charger_m),
        predicted_charger_energy_percent=zero,
        reserve_percent=Fraction(10),
        required_soc_percent=Fraction(10),
        energy_margin_percent=Fraction(margin),
        battery_eligible=True,
        nearest_charger_id="charging_001",
    )


# -- registration ------------------------------------------------------


def test_all_eight_strategies_are_registered_under_distinct_names():
    assert set(ALL) <= set(BATCH_STRATEGIES)
    assert len(set(ALL)) == 8

    for name in ALL:
        assert get_batch_strategy(name).name == name


# -- 1. Earliest Finish ------------------------------------------------


def test_finish_time_is_travel_plus_service():
    c = candidate("r1", "o1", 15.0, 0, shelves=3)
    t = timing(["r1"], speed=1.5, shelf=2.0, dropoff=4.0)

    assert t.travel_s(c) == pytest.approx(10.0)
    assert t.service_s(c) == pytest.approx(3 * 2.0 + 4.0)
    assert t.finish_s(c) == pytest.approx(20.0)


def test_earliest_finish_prefers_the_order_with_fewer_services():
    """10.0 m with 3 shelves finishes later than 10.5 m with 1 shelf.

        o1  10.0 m / 1.5 + 3 x 1 + 1 = 10.67 s   nearest_astar's pick
        o2  10.5 m / 1.5 + 1 x 1 + 1 =  9.00 s   earliest_finish's pick
    """

    cs = [candidate("r1", "o1", 10.0, 0, shelves=3), candidate("r1", "o2", 10.5, 1)]

    assert nearest(cs) == {("r1", "o1")}
    assert pairs(earliest_finish_pairing(cs, timing(["r1"]), fifo_of(cs))) == {("r1", "o2")}


def test_earliest_finish_prefers_the_faster_robot():
    """Same order: the shorter route on the slower robot finishes later."""

    cs = [candidate("slow", "o1", 10.0, 0), candidate("fast", "o1", 12.0, 0)]
    t = timing(None, speed={"slow": 1.0, "fast": 1.5})

    assert nearest(cs) == {("slow", "o1")}
    assert pairs(earliest_finish_pairing(cs, t, fifo_of(cs))) == {("fast", "o1")}


def test_earliest_finish_never_uses_waiting():
    """The estimate is a function of route, speed and services only."""

    c = candidate("r1", "o1", 9.0, 0, shelves=2)
    t = timing(["r1"])

    assert set(DecisionTiming.__dataclass_fields__) == {
        "speed_mps",
        "shelf_service_s",
        "dropoff_service_s",
    }
    assert t.finish_s(c) == pytest.approx(9.0 / 1.5 + 2 + 1)


# -- 2. Multi-Criteria Grid-Aware ---------------------------------------


def test_multi_criteria_pulls_an_older_order_forward():
    """Earliest finish takes the 29 m new order; the FIFO criterion takes
    the 30 m old one, because 1 m is small within this decision's range.
    """

    cs = [
        candidate("r1", "old", 30.0, 0),
        candidate("r1", "mid", 60.0, 5),
        candidate("r1", "new", 29.0, 10),
    ]
    t = timing(["r1"])
    strategy_pick = sorted(
        zip(multi_criteria_scores(cs, t, MULTI_CRITERIA_WEIGHTS), cs),
        key=lambda pair: pair[0],
    )[0][1]

    assert pairs(earliest_finish_pairing(cs, t, fifo_of(cs))) == {("r1", "new")}
    assert (strategy_pick.robot_id, strategy_pick.order_id) == ("r1", "old")


def test_multi_criteria_prefers_battery_headroom_at_equal_cost():
    cs = [
        candidate("r1", "o1", 20.0, 0, energy=energy(margin=5)),
        candidate("r2", "o1", 20.0, 0, energy=energy(margin=50)),
    ]
    t = timing(["r1", "r2"])
    scores = multi_criteria_scores(cs, t, MULTI_CRITERIA_WEIGHTS)

    # Earliest finish ties and falls to robot_id; multi-criteria does not.
    assert pairs(earliest_finish_pairing(cs, t, fifo_of(cs))) == {("r1", "o1")}
    assert scores[1] < scores[0]


def test_multi_criteria_rescales_weights_without_battery():
    cs = [candidate("r1", "o1", 10.0, 0), candidate("r1", "o2", 20.0, 1)]
    scores = multi_criteria_scores(cs, timing(["r1"]), MULTI_CRITERIA_WEIGHTS)

    # o2 is worst on both remaining criteria: the full weight total.
    assert scores[1] == pytest.approx(sum(MULTI_CRITERIA_WEIGHTS.values()))
    assert scores[0] == 0.0


def test_multi_criteria_refuses_unknown_weights():
    with pytest.raises(ValueError, match="unknown multi_criteria weight"):
        MultiCriteriaGridAwareAllocator(weights={"congestion": 1.0})


# -- 3. Auction ------------------------------------------------------------


def test_auction_gives_the_oldest_order_to_its_cheapest_robot():
    """One robot, two orders: nearest takes the cheap young order, the
    FIFO auction sells the old one first."""

    cs = [candidate("r1", "old", 20.0, 0), candidate("r1", "young", 5.0, 1)]

    assert nearest(cs) == {("r1", "young")}
    assert pairs(auction_pairing(cs, fifo_of(cs))) == {("r1", "old")}


def test_auction_lets_the_oldest_order_take_a_contested_robot():
    """r1 is cheapest for both; the old order wins it at auction."""

    cs = [
        candidate("r1", "old", 8.0, 0),
        candidate("r2", "old", 9.0, 0),
        candidate("r1", "young", 2.0, 1),
        candidate("r2", "young", 30.0, 1),
    ]

    assert nearest(cs) == {("r1", "young"), ("r2", "old")}
    assert pairs(auction_pairing(cs, fifo_of(cs))) == {("r1", "old"), ("r2", "young")}


# -- 4. Hungarian ----------------------------------------------------------


def test_hungarian_minimises_the_total_where_greedy_does_not():
    """Greedy locks r1-o1 at 1 m and pays 10 m for r2-o2 (11 m total);
    the optimum crosses over for 2 + 2 = 4 m."""

    cs = [
        candidate("r1", "o1", 1.0, 0),
        candidate("r1", "o2", 2.0, 1),
        candidate("r2", "o1", 2.0, 0),
        candidate("r2", "o2", 10.0, 1),
    ]
    chosen = hungarian_pairing(cs, fifo_of(cs))

    assert nearest(cs) == {("r1", "o1"), ("r2", "o2")}
    assert pairs(chosen) == {("r1", "o2"), ("r2", "o1")}
    assert sum(a.cost for a in chosen) == 4.0


def test_hungarian_pairs_as_many_robots_as_the_mask_allows():
    """r2 can only take o1. Greedy gives o1 to r1 and strands r2."""

    cs = [
        candidate("r1", "o1", 1.0, 0),
        candidate("r1", "o2", 2.0, 1),
        candidate("r2", "o1", 3.0, 0),
    ]

    assert nearest(cs) == {("r1", "o1")}
    assert pairs(hungarian_pairing(cs, fifo_of(cs))) == {("r1", "o2"), ("r2", "o1")}


# -- 5. MILP ----------------------------------------------------------------


def unit_timing(robots):
    """finish = cost: speed 1 m/s, no service time."""

    return timing(robots, speed=1.0, shelf=0.0, dropoff=0.0)


def test_milp_minimises_the_latest_finish_not_the_sum():
    """Hungarian takes 1 + 7 = 8 (latest 7); MILP takes 6 + 6 = 12
    (latest 6). Different objectives, different answers."""

    cs = [
        candidate("r1", "o1", 1.0, 0),
        candidate("r1", "o2", 6.0, 1),
        candidate("r2", "o1", 6.0, 0),
        candidate("r2", "o2", 7.0, 1),
    ]

    assert pairs(hungarian_pairing(cs, fifo_of(cs))) == {("r1", "o1"), ("r2", "o2")}
    assert pairs(milp_pairing(cs, unit_timing(["r1", "r2"]), fifo_of(cs))) == {
        ("r1", "o2"),
        ("r2", "o1"),
    }


def test_milp_breaks_an_equal_makespan_on_total_finish():
    """Both matchings end at 5; the second totals 7 instead of 10."""

    cs = [
        candidate("r1", "o1", 5.0, 0),
        candidate("r1", "o2", 5.0, 1),
        candidate("r2", "o1", 2.0, 0),
        candidate("r2", "o2", 5.0, 1),
    ]

    assert pairs(milp_pairing(cs, unit_timing(["r1", "r2"]), fifo_of(cs))) == {
        ("r1", "o2"),
        ("r2", "o1"),
    }


def test_milp_pairs_as_many_robots_as_the_mask_allows():
    cs = [
        candidate("r1", "o1", 1.0, 0),
        candidate("r1", "o2", 2.0, 1),
        candidate("r2", "o1", 3.0, 0),
    ]

    assert pairs(milp_pairing(cs, unit_timing(["r1", "r2"]), fifo_of(cs))) == {
        ("r1", "o2"),
        ("r2", "o1"),
    }


def test_optimisers_return_nothing_for_no_candidates():
    assert hungarian_pairing([], {}) == []
    assert milp_pairing([], unit_timing([]), {}) == []


# -- properties on the practice map, all eight strategies -----------------


@pytest.fixture(scope="module")
def warehouse():
    return practice_warehouse()


@pytest.fixture
def orders():
    return [west_order(), east_order()]


@pytest.fixture
def fifo(orders):
    return {order.order_id: index for index, order in enumerate(orders)}


ROBOTS = [("robot_001", WEST_HOME), ("robot_002", EAST_HOME)]
TIMING = timing(["robot_001", "robot_002"])


@pytest.mark.parametrize("name", ALL)
def test_every_strategy_returns_a_legal_matching_in_fifo_order(name, warehouse, orders, fifo):
    assignments = get_batch_strategy(name).assign(
        warehouse, ROBOTS, orders, fifo, timing=TIMING
    )

    robots = [a.robot_id for a in assignments]
    ids = [a.order_id for a in assignments]

    assert assignments
    assert len(robots) == len(set(robots))
    assert len(ids) == len(set(ids))
    assert set(ids) <= {o.order_id for o in orders}
    assert [fifo[i] for i in ids] == sorted(fifo[i] for i in ids)


@pytest.mark.parametrize("name", ALL)
def test_every_strategy_carries_c_static_and_the_astar_route(name, warehouse, orders, fifo):
    for a in get_batch_strategy(name).assign(warehouse, ROBOTS, orders, fifo, timing=TIMING):
        assert a.route.reachable
        assert a.cost == a.route.distance_m


@pytest.mark.parametrize("name", ALL)
def test_no_strategy_can_escape_the_battery_mask(name, warehouse, orders, fifo):
    battery = battery_context({"robot_001": 11, "robot_002": 11})

    assert (
        get_batch_strategy(name).assign(
            warehouse, ROBOTS, orders, fifo, None, battery=battery, timing=TIMING
        )
        == []
    )


@pytest.mark.parametrize("name", ALL)
def test_every_strategy_uses_only_the_robot_that_can_afford_it(name, warehouse, orders, fifo):
    battery = battery_context({"robot_001": 100, "robot_002": 11})

    assignments = get_batch_strategy(name).assign(
        warehouse, ROBOTS, orders, fifo, None, battery=battery, timing=TIMING
    )

    assert [a.robot_id for a in assignments] == ["robot_001"]
    assert all(a.energy.battery_eligible for a in assignments)


@pytest.mark.parametrize("name", PHASE_7)
def test_new_strategies_are_deterministic(name, warehouse, orders, fifo):
    first = get_batch_strategy(name).assign(warehouse, ROBOTS, orders, fifo, timing=TIMING)
    again = get_batch_strategy(name).assign(
        warehouse, list(reversed(ROBOTS)), list(reversed(orders)), fifo, timing=TIMING
    )

    assert pairs(first) == pairs(again)


@pytest.mark.parametrize("name", NEEDS_TIMING)
def test_time_estimating_strategies_refuse_to_run_without_timing(name, warehouse, orders, fifo):
    with pytest.raises(ValueError, match="needs the decision timing"):
        get_batch_strategy(name).assign(warehouse, ROBOTS, orders, fifo)


def _brute_force_optimum(cs, t):
    """(latest finish, total finish) of the best maximum matching."""

    from itertools import combinations

    best_size, best = 0, None

    for size in range(len(cs), 0, -1):
        for subset in combinations(cs, size):
            if len({c.robot_id for c in subset}) < size:
                continue
            if len({c.order_id for c in subset}) < size:
                continue

            finishes = [t.finish_s(c) for c in subset]
            value = (max(finishes), sum(finishes))

            if best is None or value < best:
                best = value

        if best is not None:
            best_size = size
            break

    return best_size, best


@pytest.mark.parametrize("seed", range(25))
def test_milp_matches_brute_force_on_random_decisions(seed):
    """The aggregated formulation and the pruning are exact."""

    from random import Random

    rng = Random(seed)
    robots = [f"r{i}" for i in range(rng.randint(1, 4))]
    orders = [f"o{j}" for j in range(rng.randint(1, 4))]
    cs = [
        candidate(r, o, float(rng.randint(5, 60)), j, shelves=rng.randint(1, 3))
        for r in robots
        for j, o in enumerate(orders)
        if rng.random() < 0.8  # some pairs ineligible
    ]
    t = timing(robots)

    size, best = _brute_force_optimum(cs, t)
    chosen = milp_pairing(cs, t, fifo_of(cs))
    finishes = [t.finish_s(next(c for c in cs if (c.robot_id, c.order_id) == (a.robot_id, a.order_id))) for a in chosen]

    assert len(chosen) == size

    if size:
        assert max(finishes) == pytest.approx(best[0])
        assert sum(finishes) == pytest.approx(best[1])


# -- multi_criteria: directions, normalisation, zero range ----------------


def test_multi_criteria_prefers_the_earlier_finish():
    cs = [candidate("r1", "o1", 30.0, 0), candidate("r2", "o1", 15.0, 0)]
    scores = multi_criteria_scores(cs, timing(["r1", "r2"]), MULTI_CRITERIA_WEIGHTS)

    assert scores[1] < scores[0]


def test_multi_criteria_prefers_ending_nearer_a_charger():
    cs = [
        candidate("r1", "o1", 20.0, 0, energy=energy(margin=30, charger_m=40.0)),
        candidate("r2", "o1", 20.0, 0, energy=energy(margin=30, charger_m=5.0)),
    ]
    scores = multi_criteria_scores(cs, timing(["r1", "r2"]), MULTI_CRITERIA_WEIGHTS)

    assert scores[1] < scores[0]
    assert scores[0] == pytest.approx(MULTI_CRITERIA_WEIGHTS["charger_distance"])


def test_multi_criteria_zero_range_contributes_nothing():
    """Every criterion equal across the decision: every score is 0, and
    the pair falls to the shared tie-break (finish, C_static, FIFO, id)."""

    cs = [
        candidate("r2", "o1", 20.0, 0, energy=energy(margin=30)),
        candidate("r1", "o1", 20.0, 0, energy=energy(margin=30)),
    ]
    t = timing(["r1", "r2"])

    assert multi_criteria_scores(cs, t, MULTI_CRITERIA_WEIGHTS) == [0.0, 0.0]
    assert multi_criteria_scores(cs[:1], t, MULTI_CRITERIA_WEIGHTS) == [0.0]


def test_multi_criteria_scores_stay_within_the_weight_total():
    from random import Random

    rng = Random(3)
    cs = [
        candidate(
            f"r{i}",
            f"o{j}",
            float(rng.randint(5, 90)),
            j,
            shelves=rng.randint(1, 3),
            energy=energy(margin=rng.randint(1, 80), charger_m=rng.randint(1, 50)),
        )
        for i in range(4)
        for j in range(4)
    ]
    scores = multi_criteria_scores(cs, timing([f"r{i}" for i in range(4)]), MULTI_CRITERIA_WEIGHTS)

    assert all(0.0 <= s <= sum(MULTI_CRITERIA_WEIGHTS.values()) + 1e-12 for s in scores)
    # Some pair is best on every criterion it can be, so the minimum is
    # low and the spread is real.
    assert min(scores) < max(scores)
