"""Battery V1 increment B5: queue promotion and charger preemption.

A holding cell is a waiting room, not a destination. When a charger
becomes FREE the waiting robots are ordered by lowest SOC, then earliest
charge request, then ``robot_id`` purely as an engineering tie-break,
and the best of them is promoted onto it. When every charger is occupied
and a CHARGE_DUE robot needs one, the charging robot with the highest
SOC gives it up -- but only if its SOC is strictly above the
requester's, and the charger is then held for that requester until it
can physically enter.

Frozen policy: docs/DECISIONS.md, "Charger Saturation and Preemption",
"Charger Queue Discipline", "Preempted Robot". Nothing here changes
routing, traffic, the order schema or the allocator.
"""

from dataclasses import asdict, replace
from fractions import Fraction

import pytest

from simulation_env.charging import (
    ChargeState,
    ResourceError,
    ResourceState,
    TerminalBank,
)
from mrta_core.warehouse_map import ZoneType
from test_battery_eligibility import FAR, SHORT, controlled, executor_for
from test_charger_evacuation import build, config as config
from test_charger_evacuation import late as late
from test_charger_evacuation import twelve as twelve
from test_charger_evacuation import warehouse as warehouse
from test_charger_evacuation import TWELVE, run


def trips(executor):
    """Every terminal trip of the run, current and historical, once."""

    return list(
        {
            id(record): record
            for record in (*executor.charge_history, *executor.charge.values())
        }.values()
    )


# ----------------------------------------------------------------------
# 1. The acceptance scenario: 12 requesters, 8 chargers, 14 holding.
# ----------------------------------------------------------------------


def test_every_waiting_robot_eventually_reaches_a_charger(twelve):
    """The four robots that found no charger do not stay in holding."""

    executor, _, _ = twelve

    charger_trips = [r for r in trips(executor) if r.kind == "charger"]
    holding_trips = [r for r in trips(executor) if r.kind == "holding"]

    assert len(holding_trips) == 4
    assert len(charger_trips) == 12

    # Each of the four waiters charged, on a charger it was promoted to.
    promoted = {r.robot_id: r for r in charger_trips if r.promoted_from is not None}

    assert len(promoted) == 4
    assert {r.robot_id for r in holding_trips} == set(promoted)

    for record in promoted.values():
        assert record.charge_started_at is not None
        assert record.arrival_soc + record.charge_gained == 100
        assert record.state is ChargeState.REJOINED

    assert executor.result.promotions == 4
    assert executor.result.charge_sessions == 12
    assert executor.result.rejoins == 12

    # Every slot is handed back at the end of the run.
    for bank in (executor.chargers, executor.holding):
        assert all(
            slot.state is ResourceState.FREE and slot.promised_to is None
            for slot in bank.slots.values()
        )


def test_charger_occupancy_never_exceeds_the_bank(twelve):
    """Eight chargers, one robot each, at every sampled instant."""

    executor, monitor, _ = twelve

    charger_cells = {slot.cell: zone for zone, slot in executor.chargers.slots.items()}

    assert len(charger_cells) == 8

    for now, robots, slots in monitor.samples:
        held = [
            zone_id
            for zone_id in executor.chargers.slots
            if slots[zone_id][0] is not ResourceState.FREE
        ]

        assert len(held) <= 8, (now, held)

        # One robot per charger cell, and only its own holder on it.
        on_bank = [
            (robot_id, cell) for robot_id, (cell, *_) in robots.items()
            if cell in charger_cells
        ]

        assert len({cell for _, cell in on_bank}) == len(on_bank), (now, on_bank)

        for robot_id, cell in on_bank:
            zone_id = charger_cells[cell]

            assert slots[zone_id][1] == robot_id, (now, robot_id, zone_id)


def test_no_robot_ever_holds_two_charger_reservations(twelve, late):
    """One robot, one charger, one holding cell, at every instant."""

    for executor, monitor, _ in (twelve, late):
        for now, _, slots in monitor.samples:
            for bank in (executor.chargers, executor.holding):
                holders = [
                    slots[zone_id][1]
                    for zone_id in bank.slots
                    if slots[zone_id][1] is not None
                ]

                assert len(holders) == len(set(holders)), (now, bank.kind, holders)


def test_no_waiting_robot_is_left_beside_a_free_charger(twelve, late):
    """Liveness: the pair (FREE charger, waiting robot) never survives a tick.

    Promotion is one idempotent pass per dispatch, so it fires both when
    a charger is released and when a waiting robot arrives in holding.
    The monitor samples after the executor's dispatch, so this is the
    state the next instant would start from.
    """

    for executor, monitor, _ in (twelve, late):
        for now, robots, slots in monitor.samples:
            waiting = [
                robot_id
                for robot_id, (_, _, state, _) in robots.items()
                if state is ChargeState.HOLDING
            ]

            if not waiting:
                continue

            free = [
                zone_id
                for zone_id in executor.chargers.slots
                if slots[zone_id][0] is ResourceState.FREE
            ]

            assert free == [], (now, waiting, free)


def test_promotion_happens_in_the_instant_the_charger_frees(twelve):
    """Simultaneous release and promotion: no idle gap in between."""

    executor, _, _ = twelve

    promotions = [r for r in trips(executor) if r.promoted_at is not None]

    assert promotions

    released = sorted(
        record.rejoined_at
        for record in trips(executor)
        if record.kind == "charger" and record.rejoined_at is not None
    )

    for record in promotions:
        # Either a charger was handed back at this very instant, or the
        # robot had only just arrived in holding next to a free one.
        assert record.promoted_at in released or record.promoted_at == record.arrived_at


def test_promotion_does_nothing_without_a_waiting_robot(config, warehouse):
    """No holding robot: the pass is a no-op and changes no slot."""

    executor, _, _ = build(config, warehouse, {}, orders=4)

    executor.dispatch()

    before = {
        zone_id: (slot.state, slot.robot_id, slot.promised_to)
        for zone_id, slot in executor.chargers.slots.items()
    }

    assert executor._promote() == 0
    assert executor.result.promotions == 0
    assert {
        zone_id: (slot.state, slot.robot_id, slot.promised_to)
        for zone_id, slot in executor.chargers.slots.items()
    } == before


# ----------------------------------------------------------------------
# 2. Preemption. A CHARGE_DUE robot, every charger occupied.
# ----------------------------------------------------------------------


def preempting(config, warehouse, *, rate=None):
    """One charger, one robot on it, one robot that turns CHARGE_DUE later.

    robot_001 is CHARGE_DUE at t=0 and takes the only charger.
    robot_002 is eligible for the FAR order, crosses 10% while running
    it, and requests at t=56 -- while robot_001 is mid-charge. With the
    frozen 10% reserve a robot always ends an order above 10% plus its
    charger leg, so this controlled config sets the reserve to 0 to make
    the crossing deterministic, exactly as the B2 tests do.

    ``rate`` sets how much the victim has gained by then: the default
    1%/s leaves it fit for work, and a tenth of that leaves it below the
    CHARGE_DUE threshold, which is the other branch of the frozen
    "Preempted Robot" rule.
    """

    cfg = controlled(
        config,
        robots=2,
        overrides={"robot_001": Fraction(9), "robot_002": Fraction(23, 2)},
        reserve=0,
    )

    if rate is not None:
        cfg = replace(
            cfg,
            battery=replace(cfg.battery, charging_percent_per_second=rate),
        )

    events = []

    orders = [FAR, SHORT]

    executor = executor_for(
        cfg,
        warehouse,
        [o for o in _orders(config, warehouse, orders)],
        events=events,
    )

    # Controlled saturation: one charger, so the second requester cannot
    # simply take another.
    executor.chargers.slots = dict(list(executor.chargers.slots.items())[:1])

    executor.run()

    return executor, events


def _orders(config, warehouse, order_ids):
    from mrta_core.orders import load_orders_csv

    by_id = {o.order_id: o for o in load_orders_csv(config.orders_path, warehouse)}

    return [by_id[order_id] for order_id in order_ids]


@pytest.fixture(scope="module")
def preempted_rejoins(config, warehouse):
    return preempting(config, warehouse)


@pytest.fixture(scope="module")
def preempted_waits(config, warehouse):
    return preempting(config, warehouse, rate=Fraction(1, 10))


def test_charge_due_requester_preempts_the_highest_soc_charger(preempted_rejoins):
    executor, events = preempted_rejoins

    (victim,) = [r for r in trips(executor) if r.preempted_at is not None]

    assert victim.robot_id == "robot_001"
    assert victim.preempted_by == "robot_002"
    assert victim.state in (ChargeState.REJOINED, ChargeState.PREEMPTED)
    assert executor.result.preemptions == 1

    (event,) = [e for e in events if e.kind == "PREEMPTED"]

    assert event.sim_time == victim.preempted_at

    # The requester really was CHARGE_DUE, and really was below the
    # robot it displaced.
    requester = executor.charge["robot_002"]

    assert requester.reason == "CHARGE_DUE"
    assert event.soc_percent > requester.request_soc
    assert requester.requested_at == victim.preempted_at

    # Recorded, never branched on.
    assert victim.victim_soc_ge_80 is (event.soc_percent >= 80)


def test_a_preempted_session_books_only_the_time_it_charged(preempted_rejoins):
    """SOC bookkeeping: the interval charged, not the one scheduled."""

    executor, _ = preempted_rejoins

    (victim,) = [r for r in trips(executor) if r.preempted_at is not None]

    rate = executor.battery.config.charging_percent_per_second

    assert victim.charge_elapsed_s == victim.preempted_at - victim.charge_started_at
    assert victim.charge_elapsed_s < victim.charge_duration_s
    assert victim.charge_gained == victim.charge_elapsed_s * rate
    assert victim.arrival_soc + victim.charge_gained < 100
    assert victim.charge_completed_at is None

    # No stale session and no orphaned completion event.
    assert not executor.engine._charging
    assert executor.chargers.held_by(victim.robot_id) != victim.zone_id


def test_the_preempted_charger_is_held_for_the_requester(preempted_waits):
    """The freed charger goes to the robot it was taken for.

    The victim stands one cell from the charger it gave up with the
    lowest SOC in the bank, so without the promise the queue discipline
    would hand it straight back and the preemption would achieve
    nothing.
    """

    executor, events = preempted_waits

    (victim,) = [r for r in trips(executor) if r.preempted_at is not None]

    # The requester's two trips: the holding cell it was sent to, and
    # the charger it was then promoted onto.
    (waited,) = [
        r for r in trips(executor) if r.robot_id == "robot_002" and r.kind == "holding"
    ]
    (promoted,) = [
        r
        for r in trips(executor)
        if r.robot_id == "robot_002" and r.promoted_from is not None
    ]

    assert promoted.zone_id == victim.zone_id
    assert waited.promoted_at > victim.preempted_at

    # The victim went to holding and waited for a later release; it did
    # not re-enter the charger it had just given up.
    (victim_wait,) = [
        r
        for r in trips(executor)
        if r.robot_id == victim.robot_id and r.kind == "holding"
    ]

    # Its holding trip starts from the aisle cell it reversed into, at
    # the instant it cleared the access edge -- not from the charger.
    assert victim_wait.requested_at > victim.left_charger_at
    assert victim_wait.request_cell == victim.access_cell
    assert victim_wait.promoted_at >= promoted.rejoined_at


def test_a_preempted_robot_below_the_threshold_waits_in_holding(preempted_waits):
    """Frozen rule: rejoin only if energy-eligible, else holding."""

    executor, events = preempted_waits

    (victim,) = [r for r in trips(executor) if r.preempted_at is not None]

    due = executor.battery.config.charge_due_percent

    (event,) = [e for e in events if e.kind == "PREEMPT_WAIT"]

    assert victim.preempt_rejoined is False
    assert event.soc_percent <= due
    assert executor.result.preempt_to_holding == 1
    assert executor.result.preempt_rejoins == 0

    # It still charged in the end, by promotion, and reached 100%.
    (charged,) = [
        r
        for r in trips(executor)
        if r.robot_id == victim.robot_id and r.promoted_from is not None
    ]

    assert charged.arrival_soc + charged.charge_gained == 100


def test_a_preempted_robot_rejoins_when_it_has_energy_for_work(preempted_rejoins):
    executor, events = preempted_rejoins

    (victim,) = [r for r in trips(executor) if r.preempted_at is not None]

    due = executor.battery.config.charge_due_percent

    assert victim.preempt_rejoined is True
    assert victim.rejoin_soc > due
    assert executor.result.preempt_rejoins == 1
    assert executor.result.preempt_to_holding == 0
    assert not [e for e in events if e.kind == "PREEMPT_WAIT"]

    # Back in the fleet, and it took work again.
    assert victim.robot_id not in executor.requesting
    assert any(
        d.robot_id == victim.robot_id and d.sim_time >= float(victim.rejoined_at)
        for d in executor.result.dispatches
    )


def test_a_lower_soc_charging_robot_is_never_replaced(config, warehouse):
    """Only a strictly higher-SOC victim may be displaced.

    Exercised at the policy seam with the real mid-charge fleet state,
    so the SOCs compared are the executor's own.
    """

    executor, _, _ = build(config, warehouse, TWELVE, orders=12)

    executor.dispatch()

    while not [
        r for r in executor.charge.values() if r.state is ChargeState.CHARGING
    ]:
        executor.engine.step_once()

    charging = {
        robot_id: executor.battery.soc(robot_id, executor.engine.time)
        for robot_id, record in executor.charge.items()
        if record.state is ChargeState.CHARGING
    }

    highest = max(charging.values())

    # A requester with more charge than anybody on a charger.
    assert executor._select_victim(highest) is None
    assert executor._select_victim(highest + 1) is None

    # One with less takes the maximum, and ties break on robot_id only.
    soc, victim = executor._select_victim(highest - 1)

    assert soc == highest
    assert victim == min(r for r, s in charging.items() if s == highest)


def test_a_charge_needed_robot_never_preempts(late):
    """Saturation without preemption: only CHARGE_DUE may displace."""

    executor, _, _ = late

    (record,) = [
        r
        for r in trips(executor)
        if r.robot_id == "robot_009" and r.kind == "holding"
    ]

    assert record.reason == "CHARGE_NEEDED"
    assert executor.result.preemptions == 0
    assert not [r for r in trips(executor) if r.preempted_at is not None]

    # It waited for a charger instead, and got one by promotion.
    assert record.state is ChargeState.PROMOTED


# ----------------------------------------------------------------------
# 3. Determinism, and the slot rules the whole increment rests on.
# ----------------------------------------------------------------------


def test_b5_behaviour_is_deterministic(config, warehouse, twelve):
    again, _, _ = run(config, warehouse, TWELVE)
    executor = twelve[0]

    assert {r: asdict(c) for r, c in again.charge.items()} == {
        r: asdict(c) for r, c in executor.charge.items()
    }
    assert [asdict(r) for r in again.charge_history] == [
        asdict(r) for r in executor.charge_history
    ]
    assert again.result.battery_events == executor.result.battery_events
    assert again.result.dispatches == executor.result.dispatches
    assert (again.result.promotions, again.result.preemptions) == (
        executor.result.promotions,
        executor.result.preemptions,
    )


def test_a_promised_slot_is_not_on_offer(warehouse):
    bank = TerminalBank(warehouse, ZoneType.CHARGING, "charger")
    zone_id = next(iter(bank.slots))

    bank.reserve(zone_id, "robot_a", Fraction(0))
    bank.occupy(zone_id, "robot_a", Fraction(1), bank.slots[zone_id].cell)
    bank.vacate(zone_id, "robot_a")
    bank.promise(zone_id, "robot_b", Fraction(2))

    assert bank.promised("robot_b") == zone_id

    # Still not free while the holder leaves, and not free afterwards
    # either: it belongs to robot_b now.
    assert zone_id not in bank.free()

    bank.cleared(zone_id, "robot_a")

    assert bank.slots[zone_id].state is ResourceState.FREE
    assert zone_id not in bank.free()

    with pytest.raises(ResourceError, match="promised"):
        bank.reserve(zone_id, "robot_c", Fraction(3))

    bank.reserve(zone_id, "robot_b", Fraction(4))

    assert bank.slots[zone_id].promised_to is None
    assert bank.held_by("robot_b") == zone_id


def test_a_slot_cannot_be_promised_twice_or_to_its_own_holder(warehouse):
    bank = TerminalBank(warehouse, ZoneType.CHARGING, "charger")
    zone_id = next(iter(bank.slots))

    bank.reserve(zone_id, "robot_a", Fraction(0))
    bank.occupy(zone_id, "robot_a", Fraction(1), bank.slots[zone_id].cell)

    with pytest.raises(ResourceError, match="holding now"):
        bank.promise(zone_id, "robot_a", Fraction(2))

    bank.promise(zone_id, "robot_b", Fraction(2))

    with pytest.raises(ResourceError, match="already promised"):
        bank.promise(zone_id, "robot_c", Fraction(3))

    bank.cancel_promise(zone_id)

    assert bank.slots[zone_id].promised_to is None

    bank.promise(zone_id, "robot_c", Fraction(4))

    assert bank.promised("robot_c") == zone_id


def test_an_uncollectable_promise_is_released(preempted_waits):
    """No charger is left standing empty for a robot that cannot come.

    Every promise in these runs is collected, so the invariant to check
    is the end state: nothing outstanding once the run is over.
    """

    executor, _ = preempted_waits

    assert all(
        slot.promised_to is None for slot in executor.chargers.slots.values()
    )

    executor.chargers.promise(
        next(iter(executor.chargers.slots)), "robot_404", Fraction(0)
    )

    executor._expire_promises()

    assert all(
        slot.promised_to is None for slot in executor.chargers.slots.values()
    )
