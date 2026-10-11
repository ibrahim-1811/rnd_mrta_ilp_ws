"""Battery V1 increment B3: REQUEST robots leave the transit network.

On entering REQUEST a robot reserves, before it moves,

    1. the FREE charger with the cheapest directed A* route, else
    2. the FREE holding cell with the cheapest directed A* route, else
    3. (saturation) the nearest free Stage Zone,

and drives there by the production router. B4 charges to full, then
physically clears the charger and rejoins. B5 promotes a waiting robot
out of holding the instant a charger frees, lowest SOC first. Controlled
SOCs come from ``battery.initial_soc_overrides`` only.
"""

from dataclasses import asdict, replace
from fractions import Fraction
from pathlib import Path

import pytest

from mrta_core.order_queue import OrderQueue
from mrta_core.orders import OrderStatus, load_orders_csv
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import (
    best_path_to_zone,
    directional_blocked_edges,
    non_transit_cells,
)
from simulation_env.charging import (
    SETTLED_STATES,
    TRAVEL_STATES,
    ChargeState,
    ResourceError,
    ResourceState,
    TerminalBank,
)
from simulation_env.fleet import build_fleet
from simulation_env.grid_robot import GridRobotStatus
from simulation_env.workload_executor import WorkloadExecutor
from task_management.allocation_strategy import get_batch_strategy


CONFIGS = Path(__file__).resolve().parents[1] / "config"


def low(*numbers, soc=9):
    return {f"robot_{n:03d}": Fraction(soc) for n in numbers}


# Four CHARGE_DUE robots at t=0 (the first Gazebo scenario).
FOUR = low(1, 2, 3, 4)
# Twelve at t=0: more requesters than chargers (the second scenario).
TWELVE = low(*range(1, 13))
# Eight fill the chargers; robot_009 at 14% works, then turns
# CHARGE_NEEDED in an aisle once every charger is OCCUPIED.
LATE = {**low(*range(1, 9)), "robot_009": Fraction(14)}
# Ten fill 8 chargers + 2 holding cells (the holding bank is cut to two
# for this run only); robot_011 then finds everything taken.
SATURATED = {**low(*range(1, 11)), "robot_011": Fraction(14)}


@pytest.fixture(scope="module")
def config():
    return load_sim_config(CONFIGS / "warehouse_sim.yaml")


@pytest.fixture(scope="module")
def warehouse(config):
    return load_warehouse_xlsx(config.map_path)


@pytest.fixture(scope="module")
def terminals(warehouse):
    return non_transit_cells(warehouse)


class Monitor:
    """Samples every robot and every slot after each simulated instant."""

    def __init__(self):
        self.executor = None
        self.moves = []  # (t, robot_id, source, target)
        self.samples = []  # (t, robots, slots)
        self._cells = {}

    def attach(self, executor):
        self.executor = executor
        self._cells = {r: robot.cell for r, robot in executor.engine.robots.items()}

    def __call__(self, _sim_time, _batch):

        ex = self.executor
        now = ex.engine.time

        robots = {}

        for robot_id, robot in ex.engine.robots.items():
            if robot.cell != self._cells[robot_id]:
                self.moves.append((now, robot_id, self._cells[robot_id], robot.cell))
                self._cells[robot_id] = robot.cell

            record = ex.charge.get(robot_id)

            robots[robot_id] = (
                robot.cell,
                robot.status,
                None if record is None else record.state,
                ex.battery.soc(robot_id, now),
            )

        slots = {
            zone_id: (slot.state, slot.robot_id)
            for bank in (ex.chargers, ex.holding)
            for zone_id, slot in bank.slots.items()
        }

        self.samples.append((now, robots, slots))

    def instants(self):
        """The last sample of each simulated instant, in time order.

        One instant can take several engine batches (a robot reaches its
        cell on MOVE_COMPLETE; its MISSION_COMPLETE follows at +0 s).
        """

        last = {}

        for sample in self.samples:
            last[sample[0]] = sample

        return list(last.values())


def build(config, warehouse, overrides, *, holding=None, orders=None):

    config = replace(
        config,
        battery=replace(config.battery, initial_soc_overrides=dict(overrides)),
    )

    monitor = Monitor()
    statuses = []

    executor = WorkloadExecutor(
        warehouse=warehouse,
        robots=build_fleet(config, warehouse),
        queue=OrderQueue(load_orders_csv(config.orders_path, warehouse)[:orders]),
        allocator=get_batch_strategy("nearest_astar"),
        resolution=config.resolution_m,
        speed_mps=config.speed_mps,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
        on_tick=monitor,
        on_order_status=lambda *status: statuses.append(status),
        progress_interval_s=0,
        battery=config.battery,
    )

    if holding is not None:
        # Controlled saturation: this run's holding bank is cut short.
        executor.holding.slots = dict(list(executor.holding.slots.items())[:holding])

    monitor.attach(executor)

    return executor, monitor, statuses


def run(config, warehouse, overrides, **kwargs):

    executor, monitor, statuses = build(config, warehouse, overrides, **kwargs)
    executor.run()

    return executor, monitor, statuses


@pytest.fixture(scope="module")
def four(config, warehouse):
    return run(config, warehouse, FOUR)


@pytest.fixture(scope="module")
def twelve(config, warehouse):
    return run(config, warehouse, TWELVE)


@pytest.fixture(scope="module")
def late(config, warehouse):
    return run(config, warehouse, LATE)


@pytest.fixture(scope="module")
def saturated(config, warehouse):
    return run(config, warehouse, SATURATED, holding=2)


@pytest.fixture(scope="module")
def scenarios(four, twelve, late, saturated):
    return {"four": four, "twelve": twelve, "late": late, "saturated": saturated}


def astar_costs(warehouse, cell, zone_ids):
    blocked = non_transit_cells(warehouse)
    return {
        zone_id: best_path_to_zone(warehouse, cell, zone_id, blocked).total_cost
        for zone_id in zone_ids
    }


def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def trip_end(record):
    """When one terminal trip stopped owning its robot, or None.

    A charger trip ends when the robot rejoins the fleet; a holding
    trip ends when the robot is promoted and clears the cell (B5); a
    preempted trip that found no eligible work ends when the robot
    clears the charger and its holding trip takes over. Consecutive
    trips are contiguous, so the union of these windows is the whole
    time the robot was out of the fleet.
    """

    if record.rejoined_at is not None:
        return record.rejoined_at

    if record.left_holding_at is not None:
        return record.left_holding_at

    if record.state is ChargeState.PREEMPTED:
        return record.left_charger_at

    return None


# ----------------------------------------------------------------------
# 1, 2. Selection is by directed A* cost; Manhattan decides nothing.
# ----------------------------------------------------------------------


def test_nearest_charger_is_selected_by_directed_astar(config, warehouse):
    executor, _, _ = build(config, warehouse, low(1))
    start = executor.engine.robots["robot_001"].cell

    executor.dispatch()

    costs = astar_costs(warehouse, start, executor.chargers.slots)
    expected = min(costs, key=lambda zone_id: (costs[zone_id], zone_id))

    record = executor.charge["robot_001"]

    assert record.kind == "charger"
    assert record.zone_id == expected
    assert record.route_cost_m == costs[expected]
    assert record.state is ChargeState.TO_CHARGER


def test_manhattan_can_disagree_and_does_not_decide(warehouse, terminals):
    bank = TerminalBank(warehouse, ZoneType.CHARGING, "charger")
    cells = {zone_id: slot.cell for zone_id, slot in bank.slots.items()}

    grid = warehouse.grid
    transit = sorted(
        (x, y)
        for x in range(grid.width)
        for y in range(grid.height)
        if (x, y) not in grid.obstacles and (x, y) not in terminals
    )

    for cell in transit:
        costs = astar_costs(warehouse, cell, cells)
        by_astar = min(costs, key=lambda z: (costs[z], z))
        by_manhattan = min(cells, key=lambda z: (manhattan(cell, cells[z]), z))

        # Manhattan strictly prefers another charger, not a mere tie.
        if manhattan(cell, cells[by_manhattan]) < manhattan(cell, cells[by_astar]):
            break
    else:
        pytest.fail("no cell where Manhattan and A* disagree")

    # A real disagreement: Manhattan's pick is strictly dearer by road.
    assert costs[by_manhattan] > costs[by_astar]
    assert manhattan(cell, cells[by_manhattan]) < manhattan(cell, cells[by_astar])

    assert bank.assign({"robot_x": cell}, now=Fraction(0)) == {
        "robot_x": (by_astar, costs[by_astar])
    }


# ----------------------------------------------------------------------
# 3, 4. Reservations are unique.
# ----------------------------------------------------------------------


def test_simultaneous_requests_reserve_distinct_chargers(config, warehouse):
    executor, _, _ = build(config, warehouse, FOUR)
    executor.dispatch()

    held = {r: executor.chargers.held_by(r) for r in FOUR}

    assert None not in held.values()
    assert len(set(held.values())) == 4

    for robot_id, zone_id in held.items():
        slot = executor.chargers.slots[zone_id]
        assert (slot.state, slot.robot_id) == (ResourceState.RESERVED, robot_id)
        assert slot.reserved_at == 0
        assert executor.charge[robot_id].departed_at == 0


def test_reserved_charger_cannot_be_selected_twice(warehouse):
    bank = TerminalBank(warehouse, ZoneType.CHARGING, "charger")
    cell = (3, 4)

    first, _ = bank.assign({"robot_a": cell}, now=Fraction(0))["robot_a"]

    assert first not in bank.free()

    second, _ = bank.assign({"robot_b": cell}, now=Fraction(0))["robot_b"]

    assert second != first

    with pytest.raises(ResourceError):
        bank.reserve(first, "robot_c", Fraction(1))

    with pytest.raises(ResourceError):
        bank.reserve(bank.free()[0], "robot_a", Fraction(1))


# ----------------------------------------------------------------------
# 5, 6, 7. The robot drives the costed route onto its reserved charger.
# ----------------------------------------------------------------------


def test_robot_physically_reaches_the_reserved_charger(four):
    executor, monitor, _ = four

    for robot_id in FOUR:
        record = executor.charge[robot_id]
        slot = executor.chargers.slots[record.zone_id]

        # It physically reached and occupied the charger. After B4 it
        # then charged and reversed off, so the arrival is asserted from
        # the record and the sampled history, not from the end state.
        assert record.arrived_at is not None
        assert warehouse_type(executor, slot.cell) is ZoneType.CHARGING

        on_charger = [
            now
            for now, robots, _ in monitor.samples
            if robots[robot_id][0] == slot.cell
        ]

        assert on_charger, robot_id
        assert min(on_charger) == record.arrived_at

        # The drive in is still exactly the costed route.
        arrival = [
            target
            for now, r, _, target in monitor.moves
            if r == robot_id and now <= record.arrived_at
        ]

        assert tuple([record.request_cell] + arrival) == record.path


def warehouse_type(executor, cell):
    return executor.warehouse.zone_type_at(cell)


def test_no_terminal_is_used_as_intermediate_transit(scenarios, warehouse, terminals):
    blocked_edges = directional_blocked_edges(warehouse)

    for executor, monitor, _ in scenarios.values():
        for record in executor.charge.values():
            assert record.path[-1] == record.target_cell
            assert not set(record.path[1:-1]) & terminals

            for edge in zip(record.path, record.path[1:]):
                assert edge not in blocked_edges

        for _, robot_id, source, target in monitor.moves:
            assert manhattan(source, target) == 1
            assert not (source in terminals and target in terminals), robot_id


def test_charger_arrival_makes_the_resource_occupied(four):
    executor, monitor, _ = four

    for robot_id in FOUR:
        record = executor.charge[robot_id]

        states = [(now, slots[record.zone_id]) for now, _, slots in monitor.instants()]

        before = {s for now, s in states if now < record.arrived_at}

        assert before == {(ResourceState.RESERVED, robot_id)}

        # OCCUPIED from the arrival instant until the robot is full.
        during = {
            s
            for now, s in states
            if record.arrived_at <= now < record.charge_completed_at
        }

        assert during == {(ResourceState.OCCUPIED, robot_id)}

        # Then VACATING while it reverses out, and FREE once clear --
        # never OCCUPIED by anybody else in between (B4).
        after = [(now, s) for now, s in states if now >= record.charge_completed_at]

        assert after
        assert all(
            s
            in (
                (ResourceState.VACATING, robot_id),
                (ResourceState.FREE, None),
            )
            for _, s in after
        ), after


# ----------------------------------------------------------------------
# 8, 9. More requesters than chargers go to holding, uniquely.
# ----------------------------------------------------------------------


def test_all_chargers_occupied_sends_the_robot_to_holding(late, terminals):
    executor, monitor, _ = late

    # The holding trip, as it was taken: every charger was OCCUPIED at
    # the instant of the request. In B5 the trip then ends in promotion,
    # so the robot's *current* record is the charger trip that followed.
    (record,) = [
        r
        for r in (*executor.charge_history, *executor.charge.values())
        if r.robot_id == "robot_009" and r.kind == "holding"
    ]

    assert record.reason == "CHARGE_NEEDED"
    assert record.request_cell not in terminals  # asked from an aisle
    assert record.state in (ChargeState.HOLDING, ChargeState.PROMOTED)

    (slots,) = [s for now, _, s in monitor.instants() if now == record.requested_at]

    for zone_id in executor.chargers.slots:
        assert slots[zone_id][0] is ResourceState.OCCUPIED


def test_holding_cells_receive_unique_reservations(twelve):
    executor, _, _ = twelve

    # Eight robots took the eight chargers. In B4 a charger is released
    # again once its robot is full, so this counts the trips, not the
    # slots still occupied at the end of the run.
    trips = {
        id(record): record
        for record in (*executor.charge_history, *executor.charge.values())
    }.values()

    # Eight chargers, four holding cells. In B5 each of those four
    # robots is later promoted onto a freed charger, so a robot can hold
    # more than one charger trip over the run; the holding cells stay
    # one trip each.
    charged = [r.zone_id for r in trips if r.kind == "charger"]
    holders = [r.zone_id for r in trips if r.kind == "holding"]

    assert len(charged) == 12
    assert len(holders) == 4 == len(set(holders))

    for zone_id in holders:
        slot = executor.holding.slots[zone_id]

        # Promoted out, or still waiting -- never handed to a second
        # robot while its own holder was on it.
        assert slot.state in (ResourceState.FREE, ResourceState.OCCUPIED)

        if slot.robot_id is not None:
            assert executor.charge[slot.robot_id].zone_id == zone_id

    occupied = [
        zone_id
        for zone_id in holders
        if executor.holding.slots[zone_id].state is ResourceState.OCCUPIED
    ]

    for zone_id in occupied:
        with pytest.raises(ResourceError):
            executor.holding.reserve(zone_id, "robot_020", Fraction(1))


# ----------------------------------------------------------------------
# 10, 11, 12. No aisle waiting; no swap on an access edge.
# ----------------------------------------------------------------------


def test_no_request_robot_remains_stopped_on_a_transit_cell(scenarios, terminals):
    for executor, monitor, _ in scenarios.values():
        for now, robots, _ in monitor.samples:
            for robot_id, (cell, status, state, _) in robots.items():
                if state is None:
                    continue

                # Never an unlaunched REQUEST at the end of an instant.
                assert state is not ChargeState.REQUEST, (robot_id, now)

                if state in SETTLED_STATES or state is ChargeState.CHARGING:
                    assert cell in terminals, (robot_id, now)

                if state in TRAVEL_STATES:
                    assert status is not GridRobotStatus.IDLE, (robot_id, now)

        for record in executor.charge.values():
            if record.promoted_from is None:
                # A requester departs at the instant it requests.
                assert record.departed_at == record.requested_at
            else:
                # A promoted robot was never in an aisle to begin with:
                # it departs the instant it is clear of its holding cell
                # (B5), which is later than the original request.
                assert record.departed_at > record.requested_at

            # Settled on a terminal, or back in the fleet having charged
            # and cleared the access edge (B4).
            assert (
                record.state in SETTLED_STATES or record.state is ChargeState.REJOINED
            )


def test_terminal_access_cannot_create_an_opposite_edge_swap(scenarios, warehouse):
    # The slot rule: nobody else may reserve while it is RESERVED,
    # OCCUPIED or VACATING, and VACATING frees only once cleared.
    bank = TerminalBank(warehouse, ZoneType.CHARGE_QUEUE, "holding")
    zone_id = next(iter(bank.slots))

    bank.reserve(zone_id, "robot_a", Fraction(0))
    bank.occupy(zone_id, "robot_a", Fraction(1), bank.slots[zone_id].cell)
    bank.vacate(zone_id, "robot_a")

    for state in (ResourceState.VACATING,):
        assert bank.slots[zone_id].state is state
        assert zone_id not in bank.free()
        with pytest.raises(ResourceError):
            bank.reserve(zone_id, "robot_b", Fraction(2))

    bank.cleared(zone_id, "robot_a")
    assert zone_id in bank.free()

    # Execution: a charger / holding cell is entered only by its
    # holder, and left only by that same holder after it is full (B4),
    # so the access edge never carries two opposing requests.
    for executor, monitor, _ in scenarios.values():
        cells = {
            slot.cell: slot
            for b in (executor.chargers, executor.holding)
            for slot in b.slots.values()
        }

        entries = [(now, r, t) for now, r, _, t in monitor.moves if t in cells]
        exits = [(now, r, src) for now, r, src, _ in monitor.moves if src in cells]

        # A terminal is left only by the robot that held it -- off a
        # charger when it is full (B4) or preempted (B5), and off a
        # holding cell when that robot is promoted (B5). Nobody else can
        # be on the access edge, because the slot is not FREE until the
        # leaver stands in the aisle.
        history = {
            (record.robot_id, record.target_cell)
            for record in (
                *executor.charge_history,
                *executor.charge.values(),
            )
        }

        for _, robot_id, cell in exits:
            assert (robot_id, cell) in history, (robot_id, cell)

        # Each terminal is entered at most once per occupancy, by its
        # own holder, and an entry never coincides with an exit.
        for now, robot_id, cell in entries:
            assert (robot_id, cell) in history, (robot_id, cell)
            assert not any(other == now and src == cell for other, _, src in exits), (
                robot_id,
                cell,
                now,
            )

        assert executor.result.yield_events == 0
        assert len(executor.engine.reservations.audit()) == 0


def test_resource_remains_unavailable_while_occupied(late):
    executor, monitor, _ = late

    # Once occupied, a charger keeps its robot for the whole charge and
    # is handed to nobody else while that robot reverses out (B4).
    records = [
        record
        for record in (*executor.charge_history, *executor.charge.values())
        if record.kind == "charger"
    ]

    assert records

    for record in records:
        for now, _, slots in monitor.instants():
            if not record.arrived_at <= now:
                continue

            state, holder = slots[record.zone_id]

            if now < record.charge_completed_at:
                assert (state, holder) == (
                    ResourceState.OCCUPIED,
                    record.robot_id,
                )

            elif record.rejoined_at is not None and now < record.rejoined_at:
                assert (state, holder) == (
                    ResourceState.VACATING,
                    record.robot_id,
                )


# ----------------------------------------------------------------------
# 13. Saturation.
# ----------------------------------------------------------------------


def test_saturation_falls_back_to_a_safe_stage_zone(saturated, terminals):
    executor, monitor, _ = saturated

    record = executor.charge["robot_011"]

    assert executor.result.saturation_events == 1
    assert record.request_cell not in terminals
    assert (record.kind, record.state) == ("stage", ChargeState.STAGE_FALLBACK)
    assert warehouse_type(executor, record.target_cell) is ZoneType.STAGING
    assert executor.parking.claim_of("robot_011") == record.zone_id

    events = [e for e in executor.result.battery_events if e.robot_id == "robot_011"]
    assert [e.kind for e in events] == ["REQUEST", "SATURATION", "RESERVE", "ARRIVE"]

    # The fallback cell is never handed to a robot parking at the end.
    parked_cells = {executor.engine.robots[r].cell for r in executor.parked}
    assert record.target_cell not in parked_cells
    assert len(parked_cells) == len(executor.parked)


# ----------------------------------------------------------------------
# 14, 15. Ownership untouched; deterministic.
# ----------------------------------------------------------------------


def test_charging_scheduler_never_modifies_order_ownership(scenarios):
    for executor, _, statuses in scenarios.values():
        owner = {}

        for order_id, status, _, robot_id in statuses:
            if status is OrderStatus.ASSIGNED:
                assert order_id not in owner
                owner[order_id] = robot_id
            else:
                assert owner[order_id] == robot_id

        assert len(owner) == len(executor.queue)
        assert all(o.status is OrderStatus.COMPLETED for o in executor.queue.orders)

        # A robot in the charging workflow takes no new order while it
        # is in it. After B4 it may be given work again once it has
        # charged and rejoined, so the window that must stay empty ends
        # when the robot leaves the workflow -- not at the end of the
        # run, and in B5 not necessarily on the trip that started it:
        # a holding trip ends by handing the robot to a charger trip,
        # which carries the original request time and closes the window.
        for record in {
            id(r): r for r in (*executor.charge_history, *executor.charge.values())
        }.values():
            end = trip_end(record)

            during = [
                d
                for d in executor.result.dispatches
                if d.robot_id == record.robot_id
                and d.sim_time >= float(record.requested_at)
                and (end is None or d.sim_time < float(end))
            ]

            assert during == []


def test_behaviour_is_deterministic(config, warehouse, late):
    again, _, _ = run(config, warehouse, LATE)
    executor = late[0]

    assert {r: asdict(c) for r, c in again.charge.items()} == {
        r: asdict(c) for r, c in executor.charge.items()
    }
    assert again.result.battery_events == executor.result.battery_events
    assert again.result.dispatches == executor.result.dispatches


# ----------------------------------------------------------------------
# SOC rises only while charging (B4). Before B4 it never rose at all;
# the invariant is now narrower, not gone.
# ----------------------------------------------------------------------


def test_soc_rises_only_while_charging(scenarios):
    """Outside a charge session SOC still only ever falls."""

    for executor, monitor, _ in scenarios.values():
        # When each robot was charging, by sampled instant.
        windows = {}

        for record in (
            *executor.charge_history,
            *executor.charge.values(),
        ):
            if record.charge_started_at is None:
                continue

            windows.setdefault(record.robot_id, []).append(
                (
                    float(record.charge_started_at),
                    float(
                        record.charge_completed_at
                        if record.charge_completed_at is not None
                        else executor.engine.time
                    ),
                )
            )

        last = {}
        last_at = {}

        for sim_time, robots, _ in monitor.samples:
            for robot_id, (_, _, _, soc) in robots.items():
                previous = last.get(robot_id)

                if previous is not None and soc > previous:
                    # A rise is legal only across a charge session.
                    since = last_at[robot_id]

                    assert any(
                        start < sim_time and end > since
                        for start, end in windows.get(robot_id, ())
                    ), (
                        f"{robot_id} SOC rose from {float(previous)} to "
                        f"{float(soc)} between t={since} and "
                        f"t={sim_time} without charging"
                    )

                last[robot_id] = soc
                last_at[robot_id] = sim_time


def test_a_charged_robot_reaches_exactly_full(scenarios):
    """Every completed session ends at exactly 100%, never above."""

    full = Fraction(100)

    for executor, _, _ in scenarios.values():
        for record in (
            *executor.charge_history,
            *executor.charge.values(),
        ):
            if record.charge_completed_at is None:
                continue

            assert (
                record.charge_duration_s
                == (full - record.arrival_soc)
                / executor.battery.config.charging_percent_per_second
            )

            reached = record.arrival_soc + record.charge_gained

            assert reached == full


def test_a_holding_robot_is_promoted_onto_a_freed_charger(scenarios):
    """Queue promotion (B5), across every controlled scenario.

    A holding cell is a waiting room, not a destination: every holding
    trip either ends in promotion onto a charger, or the robot is still
    waiting because every charger stayed taken to the end of the run. A
    holding trip never charges in place.
    """

    promoted = 0

    for executor, _, _ in scenarios.values():
        trips = {
            id(record): record
            for record in (*executor.charge_history, *executor.charge.values())
        }.values()

        for record in trips:
            if record.kind != "holding":
                continue

            assert record.charge_started_at is None
            assert record.state in (ChargeState.HOLDING, ChargeState.PROMOTED)

            if record.state is ChargeState.PROMOTED:
                promoted += 1

                assert record.promoted_at is not None
                assert record.left_holding_at >= record.promoted_at

                # The charger trip that took over carries the original
                # request, and really charged.
                (follow,) = [
                    r
                    for r in trips
                    if r.robot_id == record.robot_id
                    and r.promoted_from == record.zone_id
                ]

                assert follow.kind == "charger"
                assert follow.requested_at == record.requested_at
                assert follow.departed_at == record.left_holding_at
                assert follow.charge_started_at is not None

        # Nothing is left waiting next to an empty charger.
        waiting = [
            r for r in executor.charge.values() if r.state is ChargeState.HOLDING
        ]

        if waiting:
            assert executor.chargers.free() == []

    assert promoted, "no scenario exercised promotion"


def test_promotion_takes_the_lowest_soc_waiting_robot(twelve):
    """The frozen queue discipline: lowest SOC, earliest request, id.

    Twelve robots request at t=0; eight take the chargers and four wait
    in holding. Each promotion must go to the lowest-SOC robot waiting
    at that instant.
    """

    executor, monitor, _ = twelve

    promotions = [
        record
        for record in (*executor.charge_history, *executor.charge.values())
        if record.promoted_at is not None
    ]

    assert len(promotions) == 4

    by_instant = {sample[0]: sample for sample in monitor.samples}

    for record in promotions:
        # The sampled instant just before the grant, so the comparison
        # uses the SOCs the executor itself saw.
        before = max(now for now in by_instant if now < record.promoted_at)

        _, robots, _ = by_instant[before]

        waiting = {
            robot_id: soc
            for robot_id, (_, _, state, soc) in robots.items()
            if state is ChargeState.HOLDING
        }

        assert record.robot_id in waiting

        best = min(waiting, key=lambda robot_id: (waiting[robot_id], robot_id))

        assert waiting[record.robot_id] == waiting[best]
