"""Battery V1, B5: Stage Zone robots are promoted like holding robots.

When every charger and holding cell is taken, a robot asking to charge
waits on a Stage Zone (saturation). It used to stay there for the rest
of the run, even after chargers freed: at 100 robots that took 36
robots out of the fleet for good. Now it waits in the same queue as the
holding robots -- lowest SOC, earliest request, robot_id only as the
final tie-break -- and is promoted onto a FREE charger it can reach on
its present SOC, by the directed A* route from its Stage Zone cell.

Frozen policy: docs/DECISIONS.md, "Stage Zone Promotion". Nothing here
changes routing, traffic, the order schema or the allocator.
"""

from fractions import Fraction

import pytest

from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_routing import best_path_to_zone, non_transit_cells
from simulation_env.charging import ChargeState, ResourceState
from test_charger_evacuation import TWELVE, run, warehouse_type
from test_charger_evacuation import config as config
from test_charger_evacuation import warehouse as warehouse


WAITING = (ChargeState.HOLDING, ChargeState.STAGE_FALLBACK)


def trips(executor):
    """Every terminal trip of the run, current and historical, once."""

    return list(
        {
            id(record): record
            for record in (*executor.charge_history, *executor.charge.values())
        }.values()
    )


def events(executor, kind):
    return [e for e in executor.result.battery_events if e.kind == kind]


# Twelve CHARGE_DUE robots at t=0 and eight chargers. With no holding
# cell at all, four of them wait on a Stage Zone.
@pytest.fixture(scope="module")
def stage_only(config, warehouse):
    return run(config, warehouse, TWELVE, holding=0)


# Two holding cells: two robots wait in holding and two on a Stage Zone.
@pytest.fixture(scope="module")
def mixed(config, warehouse):
    return run(config, warehouse, TWELVE, holding=2)


# As mixed, but robot_001 -- which waits on a Stage Zone -- starts lower
# than everybody, so it must be first in the queue.
@pytest.fixture(scope="module")
def lowest_on_stage(config, warehouse):
    return run(config, warehouse, {**TWELVE, "robot_001": Fraction(5)}, holding=2)


# As mixed, but robot_001 has too little charge to reach any charger.
@pytest.fixture(scope="module")
def out_of_reach(config, warehouse):
    return run(config, warehouse, {**TWELVE, "robot_001": Fraction(1)}, holding=2)


@pytest.fixture(scope="module")
def scenarios(stage_only, mixed, lowest_on_stage, out_of_reach):
    return {
        "stage_only": stage_only,
        "mixed": mixed,
        "lowest_on_stage": lowest_on_stage,
        "out_of_reach": out_of_reach,
    }


# ----------------------------------------------------------------------
# Saturation, recovery, fleet rejoin.
# ----------------------------------------------------------------------


def test_every_stage_zone_robot_charges_and_rejoins(stage_only):
    """Saturation is recovered: nobody stays on the Stage Zone."""

    executor, _, _ = stage_only

    stage = [r for r in trips(executor) if r.kind == "stage"]

    assert executor.result.saturation_events == 4
    assert len(stage) == 4

    for record in stage:
        assert record.state is ChargeState.PROMOTED
        assert warehouse_type(executor, record.target_cell) is ZoneType.STAGING

        (follow,) = [
            r
            for r in trips(executor)
            if r.robot_id == record.robot_id and r.promoted_from == record.zone_id
        ]

        # The charger trip carries the original request, really charged
        # to 100% and ended back in the fleet.
        assert follow.kind == "charger"
        assert follow.promoted_from_kind == "stage"
        assert follow.requested_at == record.requested_at
        assert follow.departed_at == record.promoted_at
        assert follow.arrival_soc + follow.charge_gained == 100
        assert follow.state is ChargeState.REJOINED

    assert executor.result.promotions == 4
    assert executor.result.stage_promotions == 4
    assert executor.result.charge_sessions == 12
    assert executor.result.rejoins == 12

    # Every charger is handed back, with no promise left over.
    assert all(
        slot.state is ResourceState.FREE and slot.promised_to is None
        for slot in executor.chargers.slots.values()
    )


def test_a_promoted_robot_gives_its_stage_zone_back(stage_only):
    """The claim is released, and parking at the end stays unique."""

    executor, _, _ = stage_only

    for record in trips(executor):
        if record.kind != "stage":
            continue

        # It parks again at the end like any robot, on whatever Stage
        # Zone is then its cheapest free one -- a new claim, not the
        # fallback claim kept for ever.
        assert record.robot_id in executor.parked
        assert executor.parking.claim_of(record.robot_id) is not None

    parked_cells = [executor.engine.robots[r].cell for r in executor.parked]

    assert len(parked_cells) == len(set(parked_cells))
    assert len(executor.parked) == len(executor.engine.robots)


# ----------------------------------------------------------------------
# One queue, the frozen discipline.
# ----------------------------------------------------------------------


def test_holding_and_stage_zone_robots_wait_in_one_queue(mixed):
    executor, _, _ = mixed

    kinds = sorted(
        r.promoted_from_kind for r in trips(executor) if r.promoted_from is not None
    )

    assert kinds == ["holding", "holding", "stage", "stage"]
    assert executor.result.stage_promotions == 2


def test_each_promotion_takes_the_lowest_soc_waiting_robot(mixed, lowest_on_stage):
    """Lowest SOC first, over holding and Stage Zone robots together."""

    for executor, monitor, _ in (mixed, lowest_on_stage):
        by_instant = {sample[0]: sample for sample in monitor.samples}
        promotions = [r for r in trips(executor) if r.promoted_at is not None]

        assert len(promotions) == 4

        for record in promotions:
            # The sampled instant just before the grant, so the
            # comparison uses the SOCs the executor itself saw.
            before = max(now for now in by_instant if now < record.promoted_at)

            _, robots, _ = by_instant[before]

            waiting = {
                robot_id: soc
                for robot_id, (_, _, state, soc) in robots.items()
                if state in WAITING
            }

            assert record.robot_id in waiting
            assert waiting[record.robot_id] == min(waiting.values())


def test_a_lower_soc_stage_zone_robot_goes_before_holding_robots(lowest_on_stage):
    executor, _, _ = lowest_on_stage

    first = events(executor, "PROMOTE")[0]

    assert first.robot_id == "robot_001"
    assert first.detail.startswith("stage ")


# ----------------------------------------------------------------------
# Directed A*, battery reachability, terminal safety.
# ----------------------------------------------------------------------


def test_a_stage_zone_robot_drives_the_directed_astar_route(scenarios, warehouse):
    blocked = non_transit_cells(warehouse)
    checked = 0

    for executor, _, _ in scenarios.values():
        stage = {r.zone_id: r for r in trips(executor) if r.kind == "stage"}

        for record in trips(executor):
            if record.promoted_from_kind != "stage":
                continue

            start = stage[record.promoted_from].target_cell
            best = best_path_to_zone(warehouse, start, record.zone_id, blocked)

            # From the Stage Zone cell, by the production router, and
            # through no terminal other than the charger it ends on.
            assert record.path[0] == start
            assert record.path[-1] == record.target_cell
            assert record.route_cost_m == best.total_cost
            assert not set(record.path[1:-1]) & blocked

            checked += 1

    assert checked


def test_a_charger_out_of_reach_is_never_offered(out_of_reach):
    """Too little charge for any free charger: keep waiting, never strand."""

    executor, _, _ = out_of_reach

    record = executor.charge["robot_001"]

    assert record.kind == "stage"
    assert record.state is ChargeState.STAGE_FALLBACK
    assert record.charger_out_of_reach_at is not None

    # Reported once, not once per tick.
    assert [e.robot_id for e in events(executor, "OUT_OF_REACH")] == ["robot_001"]

    assert not [
        r for r in trips(executor) if r.robot_id == "robot_001" and r.kind == "charger"
    ]

    # The queue moved on to the robots that could go.
    assert executor.result.promotions == 3


def test_no_promoted_robot_arrives_flat(scenarios):
    for executor, _, _ in scenarios.values():
        for record in trips(executor):
            if record.promoted_from is not None:
                assert record.arrival_soc >= 0, record.robot_id


def test_no_waiting_robot_is_left_beside_a_free_charger_it_can_reach(scenarios):
    """Liveness: (FREE charger, waiting robot) never survives a tick."""

    for name, (executor, monitor, _) in scenarios.items():
        unreachable = {
            r.robot_id for r in trips(executor) if r.charger_out_of_reach_at is not None
        }

        for now, robots, slots in monitor.samples:
            waiting = [
                robot_id
                for robot_id, (_, _, state, _) in robots.items()
                if state in WAITING and robot_id not in unreachable
            ]

            free = [
                zone_id
                for zone_id in executor.chargers.slots
                if slots[zone_id][0] is ResourceState.FREE
            ]

            assert not (waiting and free), (name, now, waiting, free)


def test_charger_reservations_stay_exclusive(scenarios):
    """One robot per charger and one charger per robot, at every instant."""

    for name, (executor, monitor, _) in scenarios.items():
        charger_cells = {slot.cell: z for z, slot in executor.chargers.slots.items()}

        for now, robots, slots in monitor.samples:
            holders = [
                slots[zone_id][1]
                for zone_id in executor.chargers.slots
                if slots[zone_id][1] is not None
            ]

            assert len(holders) == len(set(holders)), (name, now, holders)

            for robot_id, (cell, *_) in robots.items():
                if cell in charger_cells:
                    assert slots[charger_cells[cell]][1] == robot_id, (name, now)


def test_traffic_stays_safe(scenarios):
    """No shared cell and no head-on swap, Stage Zone exits included."""

    for name, (executor, _, _) in scenarios.items():
        assert executor.engine.reservations.audit() == [], name


def test_stage_zone_promotion_is_deterministic(config, warehouse, mixed):
    again, _, _ = run(config, warehouse, TWELVE, holding=2)

    def trace(executor):
        return [
            (e.sim_time, e.robot_id, e.kind, e.detail)
            for e in executor.result.battery_events
        ]

    assert trace(again) == trace(mixed[0])
