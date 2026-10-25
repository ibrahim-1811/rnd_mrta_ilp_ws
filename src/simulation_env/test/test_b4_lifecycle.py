"""B4 charging, clearance, reuse and watchdog regressions."""

from dataclasses import replace
from fractions import Fraction

import pytest

from mrta_core.order_queue import OrderQueue
from mrta_core.orders import OrderStatus, load_orders_csv
from simulation_env.charging import ChargeState, ResourceState
from simulation_env.fleet_engine import DeadlockError, EventType
from simulation_env.grid_robot import GridRobot, GridRobotStatus
from test_battery_ledger import engine_for, open_floor, straight_plan
from test_charger_evacuation import build, low
from test_charger_evacuation import config as config
from test_charger_evacuation import warehouse as warehouse


def assert_clearance(executor, monitor, record):
    vacating = [
        (robots[record.robot_id], slots[record.zone_id])
        for now, robots, slots in monitor.samples
        if record.charge_completed_at <= now < record.rejoined_at
    ]
    assert vacating
    assert all(
        slot == (ResourceState.VACATING, record.robot_id) for _, slot in vacating
    )
    assert any(robot[0] == record.target_cell for robot, _ in vacating)
    # MOVE_COMPLETE clears the edge; the zero-delay MISSION_COMPLETE
    # then lets the executor release the slot. Inspect both batches.
    at_clearance = [
        (robots[record.robot_id], slots[record.zone_id])
        for now, robots, slots in monitor.samples
        if now == record.rejoined_at
    ]
    assert at_clearance
    assert all(robot[0] == record.access_cell for robot, _ in at_clearance)
    assert at_clearance[-1][1] == (ResourceState.FREE, None)


def test_slow_charge_completes_and_rejoins(config, warehouse):
    cfg = replace(
        config,
        robot_count=1,
        battery=replace(config.battery, charging_percent_per_second=Fraction(1, 10)),
    )
    ex, monitor, _ = build(cfg, warehouse, low(1), orders=6)
    ex.run()
    record = ex.charge_history[0]
    assert record.arrived_at == 28
    assert record.arrival_soc == Fraction(69, 10)
    assert record.charge_started_at == 28
    assert record.charge_duration_s == 931 > 600
    assert record.charge_completed_at == 959
    assert record.arrival_soc + record.charge_gained == 100
    assert record.rejoined_at == Fraction(2879, 3)
    assert record.rejoin_soc == Fraction(1999, 20)
    assert record.state is ChargeState.REJOINED
    assert_clearance(ex, monitor, record)
    assert ex.chargers.held_by(record.robot_id) is None
    assert not ex.requesting
    assert len(ex.result.completions) == 6
    assert len(ex.result.dispatches) == 6
    assert all(d.sim_time >= float(record.rejoined_at) for d in ex.result.dispatches)
    # These are real B2-validated assignments using the post-exit SOC.
    first = ex.result.dispatches[0]
    assert first.energy.current_soc_percent == record.rejoin_soc
    assert first.energy.battery_eligible
    assert all(o.status is OrderStatus.COMPLETED for o in ex.queue.orders)


def test_charger_is_reused_by_a_later_request(config, warehouse):
    # Pinned to the pre-S0 window this B4 scenario was approved under.
    # The test is about the *direct* reuse path, where the later
    # requester finds the charger already FREE and so departs in the
    # instant it requests. Under the wider S0 default the allocation
    # differs, robot_002 requests at t=98 while robot_001 is still
    # charging, and it is correctly routed through a holding cell and
    # promoted -- three charge records, and requested_at != departed_at
    # for the promoted trip. That is the B5 queued path, covered by
    # test_b5_queue.py, not this one.
    ex, monitor, _ = build(
        replace(config, robot_count=4),
        warehouse,
        {"robot_001": Fraction(9), "robot_002": Fraction(18)},
        orders=12,
        window="legacy",
    )
    ex.chargers.slots = dict(list(ex.chargers.slots.items())[:1])
    ex.run()
    a, b = ex.charge_history
    assert (a.robot_id, b.robot_id) == ("robot_001", "robot_002")
    assert a.zone_id == b.zone_id
    assert a.rejoined_at < b.requested_at == b.departed_at < b.arrived_at
    assert_clearance(ex, monitor, a)
    assert_clearance(ex, monitor, b)
    assert any(
        a.rejoined_at <= now < b.requested_at
        and slots[a.zone_id] == (ResourceState.FREE, None)
        for now, _, slots in monitor.samples
    )
    assert any(
        now == b.requested_at
        and slots[b.zone_id] == (ResourceState.RESERVED, b.robot_id)
        for now, _, slots in monitor.samples
    )
    assert any(
        now == b.arrived_at and slots[b.zone_id] == (ResourceState.OCCUPIED, b.robot_id)
        for now, _, slots in monitor.samples
    )
    assert len(ex.result.completions) == 12


def test_repeated_sessions_do_not_leave_stale_state(config, warehouse):
    cfg = replace(
        config,
        robot_count=1,
        battery=replace(config.battery, travel_percent_per_20m=Fraction(5)),
    )
    ex, monitor, _ = build(cfg, warehouse, low(1, soc=15), orders=6)
    # Identical real orders keep every FIFO choice energy-feasible after
    # a full charge; higher configured drain forces a second session.
    order = load_orders_csv(config.orders_path, warehouse)[0]
    ex.queue = OrderQueue([replace(order, order_id=f"repeat_{i}") for i in range(6)])
    ex.fifo_position = {o.order_id: i for i, o in enumerate(ex.queue.orders)}
    ex.run()
    first, second = ex.charge_history
    assert first is not second
    assert first.robot_id == second.robot_id == "robot_001"
    assert ex.charge["robot_001"] is second
    assert first.rejoined_at < second.requested_at < second.rejoined_at
    between = [
        d
        for d in ex.result.dispatches
        if float(first.rejoined_at) <= d.sim_time < float(second.requested_at)
    ]
    after = [d for d in ex.result.dispatches if d.sim_time >= float(second.rejoined_at)]
    assert between and after
    for record in (first, second):
        assert record.arrival_soc + record.charge_gained == 100
        assert_clearance(ex, monitor, record)
        assert not any(
            float(record.requested_at) <= d.sim_time < float(record.rejoined_at)
            for d in ex.result.dispatches
        )
    assert not ex.requesting and not ex.engine._charging
    assert ex.chargers.held_by("robot_001") is None
    assert all(s.state is ResourceState.FREE for s in ex.chargers.slots.values())
    assert ex.result.charge_sessions == ex.result.rejoins == 2
    assert len(ex.result.completions) == 6


def test_rejoin_rejects_a_robot_still_on_the_charger(config, warehouse):
    ex, _, _ = build(replace(config, robot_count=1), warehouse, low(1), orders=6)
    ex.dispatch()
    while ex.charge["robot_001"].state is not ChargeState.LEAVING_CHARGER:
        ex.engine.step_once()
    record = ex.charge["robot_001"]
    assert ex.engine.robots[record.robot_id].cell == record.target_cell
    with pytest.raises(RuntimeError, match="clear"):
        ex._rejoin(record.robot_id)
    assert ex.chargers.slots[record.zone_id].state is ResourceState.VACATING
    assert record.robot_id in ex.requesting


@pytest.mark.parametrize("charging", [False, True])
def test_watchdog_detects_stalled_traffic_with_future_events(charging):
    engine = engine_for(open_floor(3, 1))
    engine.add_robot(GridRobot("blocked", (0, 0)))
    engine.add_robot(GridRobot("obstacle", (1, 0)))
    if charging:
        engine.add_robot(GridRobot("charger", (2, 0)))
        engine.begin_charge("charger", Fraction(1000))
    engine.assign_mission("blocked", straight_plan([(0, 0), (1, 0)]))
    assert engine.robots["blocked"].status is GridRobotStatus.WAITING
    # Irrelevant finite events must not exempt an impossible move.
    for time in (100, 200, 300, 400, 500, 600, 601):
        engine._schedule("obstacle", EventType.WAIT_COMPLETE, Fraction(time))
    with pytest.raises(DeadlockError, match="NO-PROGRESS"):
        engine.run()
    assert engine.time == 601
    if charging:
        assert engine.robots["charger"].status is GridRobotStatus.CHARGING


def test_zero_time_event_livelock_still_hits_the_event_budget():
    engine = engine_for(open_floor(1, 1))
    engine.add_robot(GridRobot("robot", (0, 0)))
    engine.begin_charge("robot", Fraction(1000))
    for _ in range(20):
        engine._schedule("robot", EventType.WAIT_COMPLETE, Fraction(0))
    with pytest.raises(RuntimeError, match="event budget exceeded"):
        engine.run(max_events=10)


def test_missing_charge_completion_keeps_watchdog_active():
    engine = engine_for(open_floor(1, 1))
    engine.add_robot(GridRobot("robot", (0, 0)))
    engine.begin_charge("robot", Fraction(1000))
    engine._queue.clear()  # Corrupt the schedule, leaving CHARGING status.
    engine._schedule("robot", EventType.WAIT_COMPLETE, Fraction(601))
    with pytest.raises(DeadlockError, match="NO-PROGRESS"):
        engine.run()


def test_charge_without_any_completion_event_is_not_silently_finished():
    engine = engine_for(open_floor(1, 1))
    engine.add_robot(GridRobot("robot", (0, 0)))
    engine.begin_charge("robot", Fraction(1000))
    engine._queue.clear()
    with pytest.raises(DeadlockError, match="charge.*completion"):
        engine.run()


def test_charge_pause_preserves_accumulated_no_progress_time():
    engine = engine_for(open_floor(1, 1))
    engine.add_robot(GridRobot("robot", (0, 0)))

    def tick(_time, batch):
        if engine.time == 500:
            engine.begin_charge("robot", Fraction(1000))
        elif any(e.kind is EventType.CHARGE_COMPLETE for e in batch):
            engine._schedule("robot", EventType.WAIT_COMPLETE, Fraction(101))

    engine.on_tick = tick
    engine._schedule("robot", EventType.WAIT_COMPLETE, Fraction(500))
    with pytest.raises(DeadlockError, match="NO-PROGRESS"):
        engine.run()
    # 500 before + 101 after = 601 non-charging seconds. A reset at
    # charge completion would wrongly forgive the earlier stalled time.
    assert engine.time == 1601
