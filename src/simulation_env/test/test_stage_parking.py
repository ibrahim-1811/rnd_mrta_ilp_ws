"""Final parking: the Stage Zone bank is a shared fleet resource.

These pin the policy, not an implementation detail: a robot's spawn
cell buys it nothing, the cell it ends on is the cheapest *free* one by
directed A*, and two robots can never be sent to the same cell.

The real V2 map is used deliberately. The whole point of the policy is
that the legal one-way route, not the straight-line distance, decides
which bank cell is nearest, and only a real warehouse shows that.
"""

from pathlib import Path

import pytest

from mrta_core.order_queue import OrderQueue
from mrta_core.orders import load_orders_csv
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.fleet import build_fleet, home_stations
from simulation_env.stage_parking import StageParking
from simulation_env.workload_executor import WorkloadExecutor
from task_management.allocation_strategy import get_batch_strategy


CONFIG = Path(__file__).resolve().parent / "fixtures" / "v2_reference.yaml"


# A cell where the two measures disagree loudly. staging_001 is five
# cells away as the crow flies but 27 by legal route; staging_010 is
# fourteen away yet costs only 18, because the aisles are one-way.
DISAGREEING_CELL = (2, 9)

MANHATTAN_NEAREST = "staging_001"
ASTAR_NEAREST = "staging_010"


def manhattan(a, b) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


@pytest.fixture(scope="module")
def config():
    return load_sim_config(CONFIG)


@pytest.fixture(scope="module")
def warehouse(config):
    return load_warehouse_xlsx(config.map_path)


@pytest.fixture(scope="module")
def workload(config, warehouse):
    """One 100-order run of the real fleet, shared by the slow tests."""

    robots = build_fleet(config, warehouse)

    orders = load_orders_csv(config.orders_path, warehouse)[:100]

    executor = WorkloadExecutor(
        warehouse=warehouse,
        robots=robots,
        queue=OrderQueue(orders),
        allocator=get_batch_strategy("nearest_astar"),
        resolution=config.resolution_m,
        speed_mps=config.speed_mps,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
        progress_interval_s=0,
    )

    executor.run()

    return executor


def ranked_zones(parking, cell):
    """Every bank cell from ``cell``, cheapest legal route first."""

    costs = {zone_id: parking.route_cost(cell, zone_id) for zone_id in parking.cell_of}

    return sorted(
        (zone_id for zone_id, cost in costs.items() if cost is not None),
        key=lambda zone_id: (costs[zone_id], zone_id),
    ), costs


# ----------------------------------------------------------------------
# Selection
# ----------------------------------------------------------------------


def test_nearest_free_stage_is_chosen_by_directed_astar(warehouse):
    """Directed route cost decides, not Manhattan distance."""

    parking = StageParking(warehouse)

    order, costs = ranked_zones(parking, DISAGREEING_CELL)

    # The premise: the two measures really do disagree here.
    closest_by_line = min(
        sorted(parking.cell_of),
        key=lambda z: (
            manhattan(DISAGREEING_CELL, parking.cell_of[z]),
            z,
        ),
    )

    assert closest_by_line == MANHATTAN_NEAREST
    assert order[0] == ASTAR_NEAREST
    assert costs[ASTAR_NEAREST] < costs[MANHATTAN_NEAREST]

    claims = parking.assign({"robot_001": DISAGREEING_CELL})

    assert claims["robot_001"] == ASTAR_NEAREST

    # Had it used straight-line distance it would have gone here.
    assert claims["robot_001"] != MANHATTAN_NEAREST


def test_occupied_stage_forces_the_next_cheapest(warehouse):
    """A claimed bank cell is skipped, and only for the next cheapest."""

    parking = StageParking(warehouse)

    order, _ = ranked_zones(parking, DISAGREEING_CELL)

    # Somebody already holds the cheapest one.
    parking.assign({"robot_holder": parking.cell_of[order[0]]})

    assert parking.holder_of(order[0]) == "robot_holder"

    claims = parking.assign({"robot_002": DISAGREEING_CELL})

    assert claims["robot_002"] == order[1]

    # And a cell merely occupied, with no claim on it, is also off
    # limits: a robot standing there is a wall.
    fresh = StageParking(warehouse)

    claims = fresh.assign(
        {"robot_003": DISAGREEING_CELL},
        blocked_cells=frozenset({fresh.cell_of[order[0]]}),
    )

    assert claims["robot_003"] == order[1]


def test_two_returning_robots_cannot_claim_the_same_stage(warehouse):
    """One batch, one cheapest cell, two robots: they must split."""

    parking = StageParking(warehouse)

    order, _ = ranked_zones(parking, DISAGREEING_CELL)

    # Both want the same cell: (2,10) ranks the bank identically.
    claims = parking.assign(
        {
            "robot_001": DISAGREEING_CELL,
            "robot_002": (2, 10),
        }
    )

    assert set(claims) == {"robot_001", "robot_002"}
    assert claims["robot_001"] != claims["robot_002"]

    # The cheapest cell goes to whichever robot is cheaper to it, and
    # the other takes the next cheapest. Nobody is left without one.
    assert set(claims.values()) == set(order[:2])

    # robot_002 is one cell closer, so it wins the cheapest -- on cost,
    # not on being robot_002.
    assert claims["robot_002"] == order[0]
    assert claims["robot_001"] == order[1]


def test_claims_are_exclusive_across_separate_batches(warehouse):
    """A reservation survives, so a later batch cannot take it."""

    parking = StageParking(warehouse)

    first = parking.assign({"robot_001": DISAGREEING_CELL})
    second = parking.assign({"robot_002": DISAGREEING_CELL})

    assert first["robot_001"] != second["robot_002"]

    # Asking again for a robot that already holds one changes nothing.
    again = parking.assign({"robot_001": DISAGREEING_CELL})

    assert again["robot_001"] == first["robot_001"]


def test_released_claim_returns_to_the_pool(warehouse):
    """A return mission that never planned must not hold the cell."""

    parking = StageParking(warehouse)

    claimed = parking.assign({"robot_001": DISAGREEING_CELL})["robot_001"]

    parking.release("robot_001")

    assert parking.claim_of("robot_001") is None
    assert parking.holder_of(claimed) is None

    assert parking.assign({"robot_002": DISAGREEING_CELL})["robot_002"] == (claimed)


def test_robot_id_only_breaks_an_exact_tie(warehouse):
    """Identity carries no parking priority.

    Two robots standing on the same cell are indistinguishable on every
    meaningful value, so the tie has to fall to the id -- and that is
    the only situation in which it may.
    """

    parking = StageParking(warehouse)

    order, _ = ranked_zones(parking, DISAGREEING_CELL)

    claims = parking.assign(
        {
            "robot_009": DISAGREEING_CELL,
            "robot_002": DISAGREEING_CELL,
        }
    )

    assert claims["robot_002"] == order[0]
    assert claims["robot_009"] == order[1]


# ----------------------------------------------------------------------
# End to end, on the real fleet
# ----------------------------------------------------------------------


def test_robot_does_not_necessarily_return_to_its_spawn_stage(
    config, warehouse, workload
):
    """Spawn position confers no title to a bank cell."""

    spawn = home_stations(config, warehouse)

    claims = workload.parking.claims

    moved = [
        robot_id for robot_id, zone_id in claims.items() if zone_id != spawn[robot_id]
    ]

    assert moved, (
        "every robot parked back on its spawn cell; the bank is not "
        "behaving as a shared resource"
    )


def test_every_robot_ends_on_a_unique_stage_zone(config, workload):
    """22 robots, 22 bank cells, no double occupancy."""

    claims = workload.parking.claims

    assert len(claims) == config.robot_count
    assert len(set(claims.values())) == config.robot_count

    # And each is physically standing on the cell it claimed.
    for robot_id, robot in workload.engine.robots.items():
        assert robot.cell == workload.parking.cell_for(robot_id)

    assert len(workload.parked) == config.robot_count


def test_parking_never_touches_order_ownership(workload):
    """Every order still completed, and none changed hands to park."""

    assert not workload.queue.pending()
    assert not workload.active
    assert not workload.deferred

    completions = {
        record.order_id: record.robot_id for record in workload.result.completions
    }

    dispatches = {
        record.order_id: record.robot_id for record in workload.result.dispatches
    }

    assert completions == dispatches
