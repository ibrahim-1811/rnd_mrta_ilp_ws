"""Battery V1 increment B1: SOC accounting, observation only.

The ledger must be booked from the engine's own events, keep travel,
service, waiting and idle apart, agree with the engine's independent
float counters, and change nothing about what the fleet does.
"""

from dataclasses import asdict, replace
from fractions import Fraction
from pathlib import Path

import pytest

import simulation_env.fleet_engine as fleet_engine
from mrta_core.battery import BatteryConfig
from mrta_core.order_queue import OrderQueue
from mrta_core.orders import load_orders_csv
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import WarehouseMap, ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import (
    MissionPlan,
    MissionSegment,
    plan_from_cell,
)
from mrta_core.world import WorldGrid
from simulation_env.battery_ledger import (
    BatteryLedger,
    cross_check,
    fleet_summary,
)
from simulation_env.fleet import build_fleet, home_bank
from simulation_env.fleet_engine import EventType, FleetSimulationEngine
from simulation_env.grid_robot import GridRobot
from simulation_env.workload_executor import WorkloadExecutor
from task_management.allocation_strategy import get_batch_strategy


CONFIGS = Path(__file__).resolve().parents[1] / "config"

BATTERY = BatteryConfig()

# One grid edge at the scientific 1.5 m/s.
MOVE_S = Fraction(2, 3)


@pytest.fixture(scope="module")
def config():
    return load_sim_config(CONFIGS / "warehouse_sim.yaml")


@pytest.fixture(scope="module")
def warehouse(config):
    return load_warehouse_xlsx(config.map_path)


def open_floor(width: int, height: int) -> WarehouseMap:
    """Bare floor with no stations. Missions below are hand-built."""

    return WarehouseMap(
        grid=WorldGrid(
            origin_x=0.0,
            origin_y=0.0,
            resolution=1.0,
            width=width,
            height=height,
            obstacles=frozenset(),
        ),
    )


def straight_plan(path) -> MissionPlan:
    """A one-leg mission along ``path``, ending on no station."""

    path = tuple(path)
    metres = float(len(path) - 1)

    segment = MissionSegment(
        from_zone="start",
        to_zone="end",
        start_cell=path[0],
        goal_cell=path[-1],
        path=path,
        distance_m=metres,
        cost=metres,
    )

    return MissionPlan(
        zones=("start", "end"),
        segments=(segment,),
        path=path,
        total_distance_m=metres,
        total_cost=metres,
    )


def engine_for(warehouse, service_seconds=None, battery=BATTERY):
    return FleetSimulationEngine(
        warehouse=warehouse,
        resolution=1.0,
        service_seconds=service_seconds or {
            ZoneType.SHELF: 2.0,
            ZoneType.DROPOFF: 2.0,
        },
        battery=battery,
    )


def run_workload(config, warehouse, battery, orders=100):
    executor = WorkloadExecutor(
        warehouse=warehouse,
        robots=build_fleet(config, warehouse),
        queue=OrderQueue(
            load_orders_csv(config.orders_path, warehouse)[:orders]
        ),
        allocator=get_batch_strategy("nearest_astar"),
        resolution=config.resolution_m,
        speed_mps=config.speed_mps,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
        progress_interval_s=0,
        battery=battery,
    )

    executor.run()

    return executor


@pytest.fixture(scope="module")
def workload(config, warehouse):
    """One 100-order run with battery accounting on, shared."""

    return run_workload(config, warehouse, config.battery)


# ----------------------------------------------------------------------
# 1-4. The four drains, each exactly 1%.
# ----------------------------------------------------------------------


def test_twenty_metres_of_travel_is_exactly_one_percent():
    """Twenty one-cell moves, nothing else: SOC 100 -> 99 exactly."""

    engine = engine_for(open_floor(21, 1))
    engine.add_robot(GridRobot("robot_001", (0, 0)))

    engine.assign_mission(
        "robot_001",
        straight_plan([(x, 0) for x in range(21)]),
    )
    engine.run()

    snap = engine.battery.snapshot("robot_001", engine.time)

    assert engine.time == 20 * MOVE_S
    assert snap.travel_energy == 1
    assert snap.moving_s == 20 * MOVE_S
    assert snap.idle_s == 0
    assert snap.current_soc == 99


def test_sixty_seconds_of_service_is_exactly_one_percent(warehouse):
    """Two 60 s services on a real pick-and-drop mission: exactly 2%."""

    start = min(home_bank(warehouse)[0].cells)

    engine = engine_for(
        warehouse,
        service_seconds={ZoneType.SHELF: 60, ZoneType.DROPOFF: 60},
    )
    engine.add_robot(GridRobot("robot_001", start))

    engine.assign_mission(
        "robot_001",
        plan_from_cell(warehouse, start, ["shelf_001", "dropoff_001"]),
    )
    engine.run()

    snap = engine.battery.snapshot("robot_001", engine.time)

    assert snap.service_s == 120
    assert snap.service_energy == 2
    assert snap.waiting_s == 0


def test_three_hundred_seconds_of_waiting_is_exactly_one_percent():
    ledger = BatteryLedger(BATTERY, Fraction(1))
    ledger.register("robot_001", Fraction(0))

    ledger.book_wait("robot_001", Fraction(300))

    snap = ledger.snapshot("robot_001", Fraction(300))

    assert snap.waiting_energy == 1
    assert snap.idle_s == 0
    assert snap.idle_energy == 0
    assert snap.current_soc == 99


def test_three_hundred_seconds_of_idle_is_exactly_one_percent():
    ledger = BatteryLedger(BATTERY, Fraction(1))
    ledger.register("robot_001", Fraction(0))

    snap = ledger.snapshot("robot_001", Fraction(300))

    assert snap.idle_s == 300
    assert snap.idle_energy == 1
    assert snap.waiting_energy == 0
    assert snap.current_soc == 99


def test_a_robot_that_never_moves_is_idle_for_the_whole_run(warehouse):
    """Idle is the engine clock's remainder, not a counter of its own."""

    bank = home_bank(warehouse)
    worker_start = min(bank[0].cells)

    engine = engine_for(warehouse)
    engine.add_robot(GridRobot("robot_001", worker_start))
    engine.add_robot(GridRobot("robot_002", min(bank[5].cells)))

    engine.assign_mission(
        "robot_001",
        plan_from_cell(warehouse, worker_start, ["shelf_001"]),
    )
    engine.run()

    still = engine.battery.snapshot("robot_002", engine.time)

    assert engine.time > 0
    assert still.idle_s == engine.time
    assert still.idle_energy == engine.time / 300
    assert still.travel_energy == still.service_energy == 0


# ----------------------------------------------------------------------
# 5, 7. A whole mission against a hand calculation.
# ----------------------------------------------------------------------


def test_combined_accounting_matches_hand_calculation(warehouse):
    """Staging -> shelf_001 -> dropoff_001, then 150 s standing still.

    One robot, so it never waits; it moves or serves continuously, so
    until the mission ends it is never idle either:

        T   = D * 2/3 s + 2 s pick + 2 s drop
        SOC = 100 - D/20 - 4/60                 at T
        SOC = 100 - D/20 - 4/60 - 150/300       at T + 150 s
    """

    start = min(home_bank(warehouse)[0].cells)

    plan = plan_from_cell(warehouse, start, ["shelf_001", "dropoff_001"])
    distance = Fraction(int(plan.total_distance_m))

    engine = engine_for(warehouse)
    engine.add_robot(GridRobot("robot_001", start))
    engine.assign_mission("robot_001", plan)
    engine.run()

    assert engine.time == distance * MOVE_S + 4

    at_end = engine.battery.snapshot("robot_001", engine.time)

    assert at_end.travel_energy == distance / 20
    assert at_end.service_energy == Fraction(4, 60)
    assert at_end.waiting_energy == 0
    assert at_end.idle_energy == 0
    assert at_end.current_soc == 100 - distance / 20 - Fraction(4, 60)

    later = engine.battery.snapshot("robot_001", engine.time + 150)

    assert later.idle_s == 150
    assert later.current_soc == (
        100 - distance / 20 - Fraction(4, 60) - Fraction(1, 2)
    )

    # 7. Service time is booked as service, never as waiting or idle.
    assert at_end.service_s == 4
    assert at_end.waiting_s == 0
    assert at_end.idle_s == 0


def test_service_and_waiting_match_the_engine_counters(workload):
    """Across a real run, every category agrees with GridRobot's own."""

    ledger = workload.engine.battery
    now = workload.engine.time

    for robot_id, robot in workload.engine.robots.items():

        snap = ledger.snapshot(robot_id, now)

        assert float(snap.service_s) == pytest.approx(robot.service_time_s)
        assert float(snap.waiting_s) == pytest.approx(robot.waiting_time_s)
        assert float(snap.moving_s) == pytest.approx(robot.travel_time_s)
        assert snap.travel_energy == Fraction(int(robot.distance_m), 20)
        assert snap.idle_s >= 0

        assert snap.moving_s + snap.service_s + snap.waiting_s + (
            snap.idle_s
        ) == now


# ----------------------------------------------------------------------
# 6. Yield side step and rejoin are travel; the bay wait is waiting.
# ----------------------------------------------------------------------


def test_yield_and_rejoin_moves_are_booked_as_travel(monkeypatch):
    """Drive the engine's real yield -> wait -> rejoin sequence.

    The production map cannot produce this any more -- its core is
    one-way, so no two robots are ever planned head-on -- but the
    engine still carries the code. Two robots are sent head-on across
    open floor. robot_002 is on a parking run, so the busy robot_001
    has right of way both at the conflict and when the corridor
    reopens. Only the choice of side bay is fixed here; everything else
    is the engine's own path.
    """

    monkeypatch.setattr(
        fleet_engine,
        "select_local_yield_cell",
        lambda **_: (2, 2),
    )

    kinds = []

    engine = engine_for(open_floor(5, 3))
    engine.on_event = lambda event: kinds.append(
        (event.robot_id, event.kind)
    )

    engine.add_robot(GridRobot("robot_001", (1, 1)))
    engine.add_robot(GridRobot("robot_002", (2, 1)))

    engine.assign_missions_batch(
        {
            "robot_001": straight_plan([(1, 1), (2, 1), (3, 1)]),
            "robot_002": straight_plan([(2, 1), (1, 1), (0, 1)]),
        },
        parking={"robot_002"},
    )
    engine.run()

    assert ("robot_002", EventType.YIELD_COMPLETE) in kinds
    assert ("robot_002", EventType.REJOIN_COMPLETE) in kinds

    winner = engine.robots["robot_001"]
    yielder = engine.robots["robot_002"]

    assert winner.cell == (3, 1)
    assert yielder.cell == (0, 1)

    snap = {
        robot_id: engine.battery.snapshot(robot_id, engine.time)
        for robot_id in engine.robots
    }

    # Side step and rejoin are two real moves on top of the two planned.
    assert yielder.distance_m == 4.0
    assert snap["robot_002"].travel_energy == Fraction(4, 20)
    assert snap["robot_001"].travel_energy == Fraction(2, 20)

    # Standing in the bay, and the winner's hold at the conflict, are
    # both traffic waiting, booked as waiting and not as idle.
    assert snap["robot_002"].waiting_s > 0
    assert snap["robot_001"].waiting_s > 0

    for robot_id, robot in engine.robots.items():

        assert float(snap[robot_id].waiting_s) == pytest.approx(
            robot.waiting_time_s
        )
        assert cross_check(snap[robot_id], robot, BATTERY) < 1e-12


# ----------------------------------------------------------------------
# The invariant, on a real workload.
# ----------------------------------------------------------------------


def test_final_soc_identity_holds_for_every_robot(workload):
    ledger = workload.engine.battery
    now = workload.engine.time

    for robot_id, robot in workload.engine.robots.items():

        snap = ledger.snapshot(robot_id, now)

        assert snap.current_soc == (
            snap.initial_soc
            - snap.travel_energy
            - snap.service_energy
            - snap.waiting_energy
            - snap.idle_energy
        )

        # No charging exists, so SOC never rose: the minimum is now.
        assert snap.minimum_soc == snap.current_soc

        # And an independent rebuild from the engine's float counters.
        assert cross_check(snap, robot, ledger.config) < 1e-9

    summary = fleet_summary(ledger.snapshots(now))

    assert summary["below_zero"] == []
    assert summary["min_final_soc"] <= summary["mean_final_soc"]
    assert summary["mean_final_soc"] <= summary["max_final_soc"]


# ----------------------------------------------------------------------
# 8. Determinism.
# ----------------------------------------------------------------------


def test_repeated_runs_give_identical_soc(config, warehouse, workload):
    again = run_workload(config, warehouse, config.battery)

    first = workload.engine.battery.snapshots(workload.engine.time)
    second = again.engine.battery.snapshots(again.engine.time)

    assert first == second


# ----------------------------------------------------------------------
# 9. Per-robot initial SOC override, for controlled tests.
# ----------------------------------------------------------------------


def test_initial_soc_override(config, warehouse):

    overridden = replace(
        config,
        battery=replace(
            config.battery,
            initial_soc_overrides={"robot_003": Fraction(25, 2)},
        ),
    )

    robots = build_fleet(overridden, warehouse)

    engine = engine_for(warehouse, battery=overridden.battery)

    for robot in robots:
        engine.add_robot(robot)

    assert engine.battery.soc("robot_003", engine.time) == Fraction(25, 2)
    assert engine.battery.soc("robot_004", engine.time) == 100


def test_override_for_a_robot_outside_the_fleet_is_refused(config, warehouse):

    stray = replace(
        config,
        battery=replace(
            config.battery,
            initial_soc_overrides={"robot_099": Fraction(50)},
        ),
    )

    with pytest.raises(ValueError, match="robot_099"):
        build_fleet(stray, warehouse)


@pytest.mark.parametrize("name", [
    "warehouse_sim.yaml",
    "warehouse_sim_10.yaml",
    "warehouse_sim_500.yaml",
])
def test_scientific_configs_carry_no_overrides(name):
    battery = load_sim_config(CONFIGS / name).battery

    assert battery == BatteryConfig()


# ----------------------------------------------------------------------
# 10. Observation only.
# ----------------------------------------------------------------------


def test_battery_accounting_changes_no_execution_result(
    config, warehouse, workload
):
    """Battery on or off, the 100-order fleet does exactly the same thing.

    Since B2 battery can constrain allocation. On the standard workload
    no robot ever gets near the limit, so the mask never binds and
    every decision is the one the battery-blind run makes. The battery
    run's dispatch records additionally carry decision-time energy
    features, which are compared away here.
    """

    off = run_workload(
        config,
        warehouse,
        replace(config.battery, enabled=False),
    )

    assert off.engine.battery is None
    assert workload.engine.battery is not None

    def decision(dispatch):
        record = asdict(dispatch)
        record.pop("energy")
        return record

    assert [decision(d) for d in workload.result.dispatches] == [
        decision(d) for d in off.result.dispatches
    ]
    assert workload.result.battery_events == []
    assert [asdict(c) for c in workload.result.completions] == [
        asdict(c) for c in off.result.completions
    ]
    assert workload.totals() == off.totals()
    assert workload.parking.claims == off.parking.claims
    assert workload.result.returns == off.result.returns

    for robot_id, robot in workload.engine.robots.items():
        other = off.engine.robots[robot_id]

        assert (robot.cell, robot.distance_m, robot.travel_time_s,
                robot.waiting_time_s, robot.service_time_s) == (
            other.cell, other.distance_m, other.travel_time_s,
            other.waiting_time_s, other.service_time_s,
        )
