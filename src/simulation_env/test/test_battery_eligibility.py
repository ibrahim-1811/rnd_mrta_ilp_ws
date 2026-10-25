"""Battery V1 increment B2: eligibility, CHARGE_DUE and CHARGE_NEEDED.

Eligibility is a hard feasibility mask on every robot-order pair:

    required = travel(order route) + service + travel(to nearest charger)
               + reserve
    eligible = SOC >= required

computed from decision-time information only, with the production
directed A* router. CHARGE_DUE is SOC <= 10%; CHARGE_NEEDED is SOC above
that but no eligible order among ALL pending orders. Either takes a
robot into REQUEST; since B3 that sends it to a reserved charger or
holding cell (tests/test_charger_evacuation.py).

Controlled SOCs come from ``battery.initial_soc_overrides``.
"""

from dataclasses import asdict, fields, replace
from fractions import Fraction
from pathlib import Path

import pytest

from mrta_core.battery import (
    EnergyRequirement,
    energy_requirement,
    service_energy,
    travel_energy,
)
from mrta_core.order_queue import OrderQueue
from mrta_core.orders import OrderStatus, load_orders_csv
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import best_path_to_zone, non_transit_cells
from simulation_env.charging import ResourceState
from simulation_env.fleet import build_fleet
from simulation_env.workload_executor import (
    IneligibleAssignmentError,
    WorkloadExecutor,
)
from task_management.allocation_strategy import (
    NearestAStarAllocator,
    get_batch_strategy,
)
from task_management.features import order_route_cost, route_energy


CONFIGS = Path(__file__).resolve().parent / "fixtures"

# Measured on the V2 map from robot_001's spawn cell (0, 12):
SHORT = "O0053"  # the cheapest order to reach and finish
LONG = "O0099"  # the dearest
FAR = "O0001"  # ends at (14, 29): 28 m to a charger by A*, 4 by Manhattan


@pytest.fixture(scope="module")
def config():
    return load_sim_config(CONFIGS / "v2_reference.yaml")


@pytest.fixture(scope="module")
def warehouse(config):
    return load_warehouse_xlsx(config.map_path)


@pytest.fixture(scope="module")
def all_orders(config, warehouse):
    return {o.order_id: o for o in load_orders_csv(config.orders_path, warehouse)}


def controlled(config, *, robots=None, overrides=None, reserve=None):
    """A test config: fleet size, SOC overrides, reserve. Never shipped."""

    battery = replace(
        config.battery,
        initial_soc_overrides=dict(overrides or {}),
        **({} if reserve is None else {"reserve_percent": Fraction(reserve)}),
    )

    return replace(
        config,
        robot_count=config.robot_count if robots is None else robots,
        battery=battery,
    )


def executor_for(
    config, warehouse, orders, allocator=None, events=None, window="fleet_size"
):
    # Orders carry their own lifecycle status, so every executor gets
    # fresh copies; a shared Order completed by one test would otherwise
    # silently vanish from the next test's pending queue.
    fresh = {
        order.order_id: order
        for order in load_orders_csv(config.orders_path, warehouse)
    }

    return WorkloadExecutor(
        warehouse=warehouse,
        robots=build_fleet(config, warehouse),
        queue=OrderQueue([fresh[order.order_id] for order in orders]),
        allocator=allocator or get_batch_strategy("nearest_astar"),
        resolution=config.resolution_m,
        speed_mps=config.speed_mps,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
        progress_interval_s=0,
        battery=config.battery,
        on_battery_event=None if events is None else events.append,
        window=window,
    )


def requirement_for(executor, robot_id, order):
    """Eligibility of one pair, exactly as the executor evaluates it."""

    context = executor._battery_context([robot_id])
    route = order_route_cost(
        executor.warehouse,
        executor.engine.robots[robot_id].cell,
        order.shelf_ids,
        order.dropoff_id,
        cache=executor.routes,
    )

    return route, route_energy(context, robot_id, route)


# ----------------------------------------------------------------------
# 1, 2. The boundary: equality is eligible, anything less is not.
# ----------------------------------------------------------------------


def test_exact_zero_margin_is_eligible_and_executes(config, warehouse, all_orders):
    """A robot holding exactly the required SOC takes the order.

    With nothing else pending afterwards it also shows 11: finishing low
    with no work left does not make a robot CHARGE_NEEDED.
    """

    probe = executor_for(controlled(config, robots=1), warehouse, [all_orders[SHORT]])
    _, needed = requirement_for(probe, "robot_001", all_orders[SHORT])

    exact_config = controlled(
        config,
        robots=1,
        overrides={"robot_001": needed.required_soc_percent},
    )

    events = []
    executor = executor_for(exact_config, warehouse, [all_orders[SHORT]], events=events)
    executor.run()

    (dispatch,) = executor.result.dispatches

    assert dispatch.energy.energy_margin_percent == 0
    assert dispatch.energy.battery_eligible is True
    assert executor.queue.order(SHORT).status is OrderStatus.COMPLETED

    # 11. It is now too low for any order, but with no pending work that
    # is not CHARGE_NEEDED: no battery event, it simply parks. (The drive
    # home takes it from 10.3% to 9.15% -- the end-of-work case G6 will
    # govern once charging exists.)
    final = executor.battery.soc("robot_001", executor.engine.time)

    assert final < needed.required_soc_percent
    assert events == []
    assert executor.requesting == {}
    assert executor.parked == {"robot_001"}


def test_negative_margin_is_ineligible(config, warehouse, all_orders):
    """One ten-thousandth of a percent short and the pair is refused."""

    probe = executor_for(controlled(config, robots=1), warehouse, [all_orders[SHORT]])
    _, needed = requirement_for(probe, "robot_001", all_orders[SHORT])

    short_config = controlled(
        config,
        robots=1,
        overrides={"robot_001": needed.required_soc_percent - Fraction(1, 10_000)},
    )

    executor = executor_for(short_config, warehouse, [all_orders[SHORT]])
    _, requirement = requirement_for(executor, "robot_001", all_orders[SHORT])

    assert requirement.energy_margin_percent == Fraction(-1, 10_000)
    assert requirement.battery_eligible is False

    executor.dispatch()

    # Its only pending order is out of reach: CHARGE_NEEDED, not assigned.
    assert executor.result.dispatches == []
    assert executor.requesting["robot_001"].reason == "CHARGE_NEEDED"
    assert executor.queue.order(SHORT).status is OrderStatus.PENDING


# ----------------------------------------------------------------------
# 3. The mask beats geometry.
# ----------------------------------------------------------------------


def test_closer_but_ineligible_robot_is_skipped(config, warehouse, all_orders):
    order = all_orders[FAR]

    executor = executor_for(config, warehouse, [order])

    costs = sorted(
        (
            order_route_cost(
                warehouse,
                robot.cell,
                order.shelf_ids,
                order.dropoff_id,
                cache=executor.routes,
            ).distance_m,
            robot_id,
        )
        for robot_id, robot in executor.engine.robots.items()
    )

    (_, closest), (_, runner_up) = costs[0], costs[1]
    assert costs[0][0] < costs[1][0]

    context = executor._battery_context([closest, runner_up])
    _, needed = requirement_for(executor, closest, order)
    context.soc[closest] = needed.required_soc_percent - 1

    robots = [
        (robot_id, executor.engine.robots[robot_id].cell)
        for robot_id in (closest, runner_up)
    ]

    allocator = get_batch_strategy("nearest_astar")

    blind = allocator.assign(
        warehouse, robots, [order], executor.fifo_position, executor.routes
    )
    masked = allocator.assign(
        warehouse,
        robots,
        [order],
        executor.fifo_position,
        executor.routes,
        battery=context,
    )

    assert [a.robot_id for a in blind] == [closest]
    assert [a.robot_id for a in masked] == [runner_up]
    assert masked[0].energy.battery_eligible


# ----------------------------------------------------------------------
# 4, 5. The charger term is the directed-A* route, not Manhattan.
# ----------------------------------------------------------------------


def test_charger_distance_is_directed_astar(config, warehouse, all_orders):
    executor = executor_for(config, warehouse, [all_orders[FAR]])
    route, requirement = requirement_for(executor, "robot_001", all_orders[FAR])

    end = route.plan.path[-1]

    # The charger leg starts on the aisle cell after the drop-off
    # retreat, never inside the drop-off itself.
    assert end == route.plan.segments[-1].exit_cell
    assert end not in non_transit_cells(warehouse)

    chargers = [z.zone_id for z in warehouse.zones_of_type(ZoneType.CHARGING)]
    by_astar = min(
        best_path_to_zone(
            warehouse, end, zone_id, non_transit_cells(warehouse)
        ).distance
        for zone_id in chargers
    )

    assert requirement.predicted_nearest_charger_distance_m == by_astar
    assert requirement.predicted_charger_energy_percent == Fraction(int(by_astar), 20)
    assert (end, by_astar) == ((14, 29), 28.0)


def test_manhattan_cannot_replace_the_charger_route(config, warehouse, all_orders):
    """Between the two estimates, the directed route decides: refused."""

    executor = executor_for(config, warehouse, [all_orders[FAR]])
    route, astar = requirement_for(executor, "robot_001", all_orders[FAR])

    end = route.plan.path[-1]
    manhattan = min(
        abs(end[0] - cell[0]) + abs(end[1] - cell[1])
        for zone in warehouse.zones_of_type(ZoneType.CHARGING)
        for cell in zone.cells
    )

    assert manhattan == 4
    assert astar.predicted_nearest_charger_distance_m == 28

    with_manhattan = energy_requirement(
        executor.battery.config,
        current_soc=0,
        order_distance_m=astar.predicted_order_distance_m,
        service_s=4 * 2,
        charger_distance_m=manhattan,
        charger_id="manhattan",
    )

    # A SOC that Manhattan would call enough, but the real route does not.
    soc = with_manhattan.required_soc_percent + Fraction(1, 2)

    assert soc < astar.required_soc_percent

    context = executor._battery_context(["robot_001"])
    context.soc["robot_001"] = soc

    decided = route_energy(context, "robot_001", route)

    assert decided.predicted_nearest_charger_distance_m == 28
    assert decided.battery_eligible is False


# ----------------------------------------------------------------------
# 6, 7. Waiting: never predicted, always realized.
# ----------------------------------------------------------------------


def test_predicted_waiting_is_not_a_term(config, warehouse, all_orders):
    field_names = {f.name for f in fields(EnergyRequirement)}

    assert not any(
        word in name
        for name in field_names
        for word in ("wait", "congestion", "actual", "realized")
    )

    executor = executor_for(config, warehouse, [all_orders[FAR]])
    route, r = requirement_for(executor, "robot_001", all_orders[FAR])

    config_ = executor.battery.config

    # Exactly four terms, each from static route geometry or the
    # configured service times: nothing else.
    assert r.required_soc_percent == (
        travel_energy(config_, route.distance_m)
        + service_energy(config_, 2 * len(route.shelf_sequence) + 2)
        + travel_energy(config_, r.predicted_nearest_charger_distance_m)
        + config_.reserve_percent
    )
    assert r.predicted_order_distance_m == route.plan.total_distance_m


def test_realized_waiting_still_drains_soc(config, warehouse):
    executor = executor_for(
        config, warehouse, load_orders_csv(config.orders_path, warehouse)
    )
    executor.run()

    now = executor.engine.time
    waited = [
        executor.battery.snapshot(robot_id, now)
        for robot_id, robot in executor.engine.robots.items()
        if robot.waiting_time_s > 0
    ]

    assert waited
    for snap in waited:
        assert snap.waiting_energy == snap.waiting_s / 300 > 0
        assert snap.current_soc == (
            snap.initial_soc
            - snap.travel_energy
            - snap.service_energy
            - snap.waiting_energy
            - snap.idle_energy
        )


# ----------------------------------------------------------------------
# 8. Busy robots finish the whole order first.
# ----------------------------------------------------------------------


def test_busy_robot_crossing_ten_percent_finishes_its_order(
    config, warehouse, all_orders
):
    """SOC falls through 10% mid-order: flag it, finish, then REQUEST.

    With the frozen 10% reserve, eligibility guarantees a robot ends
    every order above 10% plus its charger leg, so crossing while busy
    needs realized waiting beyond that margin. To make the crossing
    deterministic this controlled config sets the reserve to 0.
    """

    busy = controlled(
        config,
        robots=1,
        overrides={"robot_001": Fraction(23, 2)},  # 11.5%
        reserve=0,
    )

    events = []
    executor = executor_for(
        busy, warehouse, [all_orders[FAR], all_orders[SHORT]], events=events
    )

    kinds = []
    engine_on_event = executor.engine.on_event

    def trace(event):
        kinds.append((event.sim_time, event.kind.value))
        engine_on_event(event)

    executor.engine.on_event = trace

    executor.dispatch()
    executor.engine.run()

    # B3: the request is followed at once by a charger reservation, and
    # the robot then drives onto that charger. B4 then adds
    # CHARGE_START, CHARGE_FULL and REJOIN for the same robot.
    due, request, reserve, arrive = events[:4]

    assert [e.kind for e in events[4:]] == [
        "CHARGE_START",
        "CHARGE_FULL",
        "REJOIN",
    ]

    assert (reserve.kind, arrive.kind) == ("RESERVE", "ARRIVE")
    assert reserve.sim_time == request.sim_time
    assert due.kind == "CHARGE_DUE"
    assert due.detail.startswith(f"while busy on {FAR}")
    assert request.kind == "REQUEST" and request.reason == "CHARGE_DUE"
    assert due.soc_percent <= 10

    # Flagged mid-order, requested only after every service and the
    # retreat out of the drop-off.
    services = [t for t, kind in kinds if kind == "SERVICE_COMPLETE"]
    retreats = [t for t, kind in kinds if kind == "RETREAT_COMPLETE"]
    finished = [t for t, kind in kinds if kind == "MISSION_COMPLETE"]

    # Up to the charge request: every service of the FAR order and
    # nothing else. (B4 lets the robot rejoin afterwards and take the
    # SHORT order, which adds services later in the run.)
    asked = float(request.sim_time)

    before = [t for t in services if t <= asked]

    assert len(before) == len(all_orders[FAR].shelf_ids) + 1
    assert float(due.sim_time) < before[-1]
    # finished[0] is the order mission; finished[1] the charger trip.
    retreats_before = [t for t in retreats if t <= asked]

    assert asked == retreats_before[-1] == finished[0]

    assert executor.queue.order(FAR).status is OrderStatus.COMPLETED

    # The SHORT order was NOT taken while the robot was charging: its
    # dispatch, if any, comes only after the rejoin (B4).
    record = executor.charge["robot_001"]

    assert record.rejoined_at is not None

    # The charge itself lands on exactly 100%.
    assert record.arrival_soc + record.charge_gained == 100

    # Reversing off the charger is one cell of real driving, so it
    # rejoins exactly one move's travel energy below full.
    step = travel_energy(executor.battery.config, executor.warehouse.grid.resolution)

    assert record.rejoin_soc == 100 - step

    shorts = [d for d in executor.result.dispatches if d.order_id == SHORT]

    assert shorts
    assert all(d.sim_time >= float(record.rejoined_at) for d in shorts)
    assert executor.queue.order(SHORT).status is OrderStatus.COMPLETED

    # Having charged, it is back in the fleet rather than parked on the
    # charger, and it cleared the slot behind it.
    assert "robot_001" not in executor.requesting
    assert executor.chargers.slots[record.zone_id].state is (ResourceState.FREE)


# ----------------------------------------------------------------------
# 9, 10. CHARGE_NEEDED is judged against every pending order.
# ----------------------------------------------------------------------


def test_charge_needed_scans_all_pending_orders(config, warehouse, all_orders):
    """CHARGE_NEEDED is judged over every pending order, not the batch.

    robot_001 can afford SHORT but not LONG, and LONG sits at the head
    of the queue. It must therefore stay available: an order it can take
    exists, so it is not CHARGE_NEEDED.

    Retargeted for S0. This test used to assert that the robot then got
    *nothing* -- ``dispatches == []`` -- because the batch was the FIFO
    head alone and its only pair was refused. That standstill is the
    stall the FIFO liveness rule removes, so the assertion is now the
    opposite: the window is extended past the unaffordable head and the
    robot is given the order it can actually run. The B2 behaviour under
    test, that the CHARGE_NEEDED scan covers all pending orders rather
    than the batch, is unchanged.

    See docs/DECISIONS.md, "FIFO Liveness Rule", and
    test_s0_window.py for the stall itself.
    """

    probe = executor_for(controlled(config, robots=1), warehouse, [all_orders[LONG]])
    _, long_needed = requirement_for(probe, "robot_001", all_orders[LONG])
    _, short_needed = requirement_for(probe, "robot_001", all_orders[SHORT])

    soc = (long_needed.required_soc_percent + short_needed.required_soc_percent) / 2
    between = controlled(config, robots=1, overrides={"robot_001": soc})

    both = executor_for(between, warehouse, [all_orders[LONG], all_orders[SHORT]])
    both.dispatch()

    # The head is still the head: liveness widens the window, it never
    # reorders the queue.
    assert both.queue.eligible(1)[0].order_id == LONG
    assert "robot_001" not in both.requesting

    # Four pair scans, none of them an allocation: two by the
    # CHARGE_NEEDED gate walking the pending queue, then two by the
    # liveness probe -- the head, which fails, and the order behind it,
    # which succeeds and closes the window.
    assert both.result.availability_pairs == 4
    assert both.result.window_extensions == 1

    # LONG was offered and refused by the mask; SHORT was dispatched.
    assert both.result.ineligible_pairs == 1
    assert [(d.robot_id, d.order_id) for d in both.result.dispatches] == [
        ("robot_001", SHORT)
    ]


def test_one_eligible_order_prevents_charge_needed(config, warehouse, all_orders):
    probe = executor_for(controlled(config, robots=1), warehouse, [all_orders[LONG]])
    _, long_needed = requirement_for(probe, "robot_001", all_orders[LONG])
    _, short_needed = requirement_for(probe, "robot_001", all_orders[SHORT])

    soc = (long_needed.required_soc_percent + short_needed.required_soc_percent) / 2
    between = controlled(config, robots=1, overrides={"robot_001": soc})

    only_long = executor_for(between, warehouse, [all_orders[LONG]])
    only_long.dispatch()

    assert only_long.requesting["robot_001"].reason == "CHARGE_NEEDED"

    with_short = executor_for(between, warehouse, [all_orders[SHORT], all_orders[LONG]])
    with_short.dispatch()

    assert "robot_001" not in with_short.requesting
    assert [d.order_id for d in with_short.result.dispatches] == [SHORT]


# ----------------------------------------------------------------------
# 12. An allocator that ignores the mask is stopped, not corrected.
# ----------------------------------------------------------------------


class _MaskBlindAllocator(NearestAStarAllocator):
    """Scores with battery features but assigns on reachability alone.

    It takes the *dearest* reachable pair, which is the shape a
    mask-unaware allocator fails in: with a wide candidate window
    (S0) the cheapest pair is usually affordable anyway, so a
    cheapest-first blind allocator would accidentally behave itself
    and never exercise the executor's refusal.
    """

    name = "mask_blind"

    def assign(
        self, warehouse, robots, orders, fifo_position, cache=None, battery=None
    ):
        candidates = sorted(
            (
                c
                for c in self.score(
                    warehouse, robots, orders, fifo_position, cache, battery
                )
                if c.reachable
            ),
            key=lambda c: (-c.cost, c.fifo_position, c.robot_id),
        )
        from task_management.allocation_strategy import OrderAssignment

        best = candidates[0]
        return [
            OrderAssignment(
                best.robot_id, best.order_id, best.cost, best.route, best.energy
            )
        ]


def test_ineligible_assignment_raises(config, warehouse, all_orders):
    probe = executor_for(controlled(config, robots=1), warehouse, [all_orders[LONG]])
    _, long_needed = requirement_for(probe, "robot_001", all_orders[LONG])
    _, short_needed = requirement_for(probe, "robot_001", all_orders[SHORT])

    soc = (long_needed.required_soc_percent + short_needed.required_soc_percent) / 2
    between = controlled(config, robots=1, overrides={"robot_001": soc})

    executor = executor_for(
        between,
        warehouse,
        [all_orders[LONG], all_orders[SHORT]],
        allocator=_MaskBlindAllocator(),
    )

    with pytest.raises(IneligibleAssignmentError, match=f"robot_001 -> {LONG}"):
        executor.dispatch()

    # Refused before anything changed: no order moved, no robot left.
    assert executor.queue.order(LONG).status is OrderStatus.PENDING
    assert executor.active == {} and executor.deferred == {}


# ----------------------------------------------------------------------
# 13. Determinism.
# ----------------------------------------------------------------------


def test_repeated_runs_give_identical_eligibility(config, warehouse):
    runs = []

    for _ in range(2):
        executor = executor_for(
            config, warehouse, load_orders_csv(config.orders_path, warehouse)
        )
        executor.run()
        runs.append(executor)

    first, second = runs

    assert [asdict(d) for d in first.result.dispatches] == [
        asdict(d) for d in second.result.dispatches
    ]
    assert all(d.energy is not None for d in first.result.dispatches)
    assert first.result.battery_events == second.result.battery_events
    assert (
        first.result.candidate_pairs,
        first.result.ineligible_pairs,
        first.result.min_assigned_margin,
    ) == (
        second.result.candidate_pairs,
        second.result.ineligible_pairs,
        second.result.min_assigned_margin,
    )


# ----------------------------------------------------------------------
# Controlled low-SOC fleet: 50 / 20 / 14 / 12 / 10 / 9 %.
# ----------------------------------------------------------------------


LOW_FLEET = {
    "robot_001": Fraction(50),
    "robot_002": Fraction(20),
    "robot_003": Fraction(12),
    "robot_004": Fraction(10),
    "robot_005": Fraction(9),
    "robot_006": Fraction(14),
}


@pytest.fixture(scope="module")
def low_fleet_first_decision(config, warehouse):
    """The t=0 decision with every low robot still on its Stage Zone."""

    events = []
    executor = executor_for(
        controlled(config, overrides=LOW_FLEET),
        warehouse,
        load_orders_csv(config.orders_path, warehouse),
        events=events,
    )
    executor.dispatch()

    return executor, events


def test_low_fleet_classification(low_fleet_first_decision):
    executor, _ = low_fleet_first_decision

    state = {
        robot_id: executor.requesting[robot_id].reason
        for robot_id in executor.requesting
    }

    assert state == {
        "robot_003": "CHARGE_NEEDED",  # 12%: not due, but nothing fits
        "robot_004": "CHARGE_DUE",  # exactly 10%
        "robot_005": "CHARGE_DUE",  # 9%
    }

    assigned = {d.robot_id for d in executor.result.dispatches}

    assert {"robot_001", "robot_002"} <= assigned
    assert not assigned & set(state)

    # B3: each requester has reserved a terminal before it moves.
    for robot_id in state:
        assert executor.charge[robot_id].zone_id is not None


def test_low_robot_is_eligible_only_for_short_orders(low_fleet_first_decision):
    """robot_006 at 14%: some orders fit, the longer ones do not."""

    executor, _ = low_fleet_first_decision

    context = executor._battery_context(["robot_006"])
    cell = executor.engine.robots["robot_006"].cell

    scored = executor.allocator.score(
        executor.warehouse,
        [("robot_006", cell)],
        executor.queue.orders,
        executor.fifo_position,
        executor.routes,
        battery=context,
    )

    fits = [c.energy for c in scored if c.eligible]
    refused = [c.energy for c in scored if not c.eligible]

    assert fits and refused
    assert max(e.required_soc_percent for e in fits) <= 14
    assert min(e.required_soc_percent for e in refused) > 14
    assert max(e.predicted_order_distance_m for e in fits) < max(
        e.predicted_order_distance_m for e in refused
    )
    assert "robot_006" not in executor.requesting
