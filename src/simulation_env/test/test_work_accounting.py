"""Order work and end-of-work recovery are measured apart (B6a).

docs/DECISIONS.md, "End of Workload": parking and charging after the
final order must not change ``order_makespan``, and must not be booked
to a strategy as if it were order work. WorkAccounting puts every robot
in exactly one phase at every instant -- support, productive or
recovery -- and run_metrics reports each phase's share of the run.

What has to hold, and is checked here on real runs with the battery on:

    partition     support + productive + recovery is each counter's run
                  total, per robot and over the fleet
    one number    the productive totals are the sum of the outcome rows
    makespan      order_makespan is the final drop-off; recovery only
                  lengthens fleet_recovery_time
"""

from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path

import pytest

from mrta_core.order_queue import OrderQueue
from mrta_core.orders import load_orders_csv
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env import workload_executor
from simulation_env.battery_ledger import fleet_summary
from simulation_env.dataset import RunRecorder
from simulation_env.fleet import build_fleet
from simulation_env.run_metrics import METRICS_SCHEMA_VERSION, run_metrics
from simulation_env.work_accounting import (
    PRODUCTIVE,
    RECOVERY,
    ROBOT_COUNTERS,
    SUPPORT,
    WorkAccounting,
)
from task_management.allocation_strategy import get_batch_strategy


CONFIGS = Path(__file__).resolve().parent / "fixtures"

# Four robots start CHARGE_DUE and charge at t=0.
FOUR_LOW = {f"robot_{n:03d}": Fraction(9) for n in (1, 2, 3, 4)}

# Robot counters and the two the battery ledger adds.
COUNTERS = (*ROBOT_COUNTERS, "energy_percent", "charging_s")

# The outcome-row field each productive counter must add up to.
OUTCOME_FIELDS = {
    "distance_m": "realized_distance_m",
    "travel_time_s": "realized_travel_time_s",
    "waiting_time_s": "realized_waiting_s",
    "service_time_s": "realized_service_s",
    "energy_percent": "energy_consumed_percent",
}


@dataclass
class PerRobotAccounting(WorkAccounting):
    """WorkAccounting that also keeps each robot's own phase totals.

    The production meter only keeps fleet totals; this spy adds the
    per-robot view and the transition log, so the partition can be
    checked robot by robot. It changes nothing the executor reads.
    """

    registered: dict = field(default_factory=dict)
    by_robot: dict = field(default_factory=dict)
    transitions: list = field(default_factory=list)

    def register(self, robot_id, values):
        super().register(robot_id, values)
        self.registered[robot_id] = values

    def _enter(self, robot_id, phase, values, now):
        left = self.phase[robot_id]
        spent = super()._enter(robot_id, phase, values, now)

        bucket = self.by_robot.setdefault(robot_id, {}).setdefault(left, {})

        for name, value in spent.items():
            bucket[name] = bucket.get(name, 0) + value

        self.transitions.append((now, robot_id, left, phase))

        return spent


@pytest.fixture(scope="module")
def config():
    return load_sim_config(CONFIGS / "v2_reference.yaml")


@pytest.fixture(scope="module")
def warehouse(config):
    return load_warehouse_xlsx(config.map_path)


def run(config, warehouse, *, orders, overrides):
    """One recorded run of the reference workload, with the spy meter."""

    config = replace(
        config,
        battery=replace(config.battery, initial_soc_overrides=dict(overrides)),
    )

    recorder = RunRecorder(run_id="test")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(workload_executor, "WorkAccounting", PerRobotAccounting)

        executor = workload_executor.WorkloadExecutor(
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
            progress_interval_s=0,
            battery=config.battery,
            window=config.allocation_window,
            dispatch_quantum_s=config.dispatch_quantum_s,
            recorder=recorder,
            strategy_name="nearest_astar",
        )

    executor.run()

    return executor, recorder


@pytest.fixture(scope="module")
def busy(config, warehouse):
    """100 orders; the four low robots charge while orders are owed."""

    return run(config, warehouse, orders=None, overrides=FOUR_LOW)


@pytest.fixture(scope="module")
def tail(config, warehouse):
    """5 orders, all allocated at t=0: the four low robots charge in recovery."""

    return run(config, warehouse, orders=5, overrides=FOUR_LOW)


@pytest.fixture(params=["busy", "tail"])
def scenario(request):
    return request.getfixturevalue(request.param)


def metrics(executor):
    return run_metrics(executor, strategy="nearest_astar", seed=42, wall_s=1.0)


def final_counters(executor):
    return {robot_id: executor._counters(robot_id) for robot_id in executor.engine.robots}


# ----------------------------------------------------------------------
# WorkAccounting on its own.
# ----------------------------------------------------------------------


def at(distance):
    return {"distance_m": distance}


def test_a_robot_starts_in_support():
    meter = WorkAccounting()
    meter.register("r1", at(0))

    assert meter.phase["r1"] == SUPPORT
    assert meter.split({"r1": at(4)})[SUPPORT]["distance_m"] == 4


def test_an_order_mission_is_productive_and_returns_what_it_spent():
    meter = WorkAccounting()
    meter.register("r1", at(0))

    meter.launched("r1", at(3), now=1)
    spent = meter.retired("r1", at(10), now=5)

    assert spent == at(7)
    assert meter.phase["r1"] == SUPPORT  # orders are still owed
    assert meter.totals == {SUPPORT: at(3), PRODUCTIVE: at(7)}


def test_an_idle_robot_enters_recovery_at_exhaustion():
    meter = WorkAccounting()
    meter.register("r1", at(0))

    meter.exhaust(now=8, idle={"r1": at(2)})

    assert meter.exhausted_at == 8
    assert meter.phase["r1"] == RECOVERY
    assert meter.recovery_from == {"r1": 8}
    assert meter.split({"r1": at(9)}) == {
        PRODUCTIVE: at(0),
        SUPPORT: at(2),
        RECOVERY: at(7),
    }


def test_a_robot_mid_order_at_exhaustion_enters_recovery_when_it_retires():
    meter = WorkAccounting()
    meter.register("busy", at(0))
    meter.register("idle", at(0))

    meter.launched("busy", at(1), now=1)
    # Only the idle robot is handed over; the busy one still owes.
    meter.exhaust(now=4, idle={"idle": at(0)})

    assert meter.phase["busy"] == PRODUCTIVE
    assert "busy" not in meter.recovery_from

    spent = meter.retired("busy", at(11), now=9)

    assert spent == at(10)
    assert meter.phase["busy"] == RECOVERY
    assert meter.recovery_from == {"idle": 4, "busy": 9}


def test_split_closes_open_segments_without_advancing_the_meter():
    meter = WorkAccounting()
    meter.register("r1", at(0))
    meter.launched("r1", at(2), now=1)

    first = meter.split({"r1": at(6)})
    second = meter.split({"r1": at(6)})

    assert first == second == {PRODUCTIVE: at(4), SUPPORT: at(2), RECOVERY: at(0)}
    assert meter.phase["r1"] == PRODUCTIVE
    assert meter.totals == {SUPPORT: at(2)}


# ----------------------------------------------------------------------
# Real runs: the phases partition the run.
# ----------------------------------------------------------------------


def test_phases_partition_every_counter_over_the_fleet(scenario):
    executor, _ = scenario
    phases = executor.phase_totals()
    totals = executor.totals()
    fleet = fleet_summary(executor.engine.battery.snapshots(executor.engine.time))

    run_total = {
        **{name: totals[name] for name in ROBOT_COUNTERS},
        "energy_percent": fleet["total_energy_consumed"],
        "charging_s": fleet["total_charging_s"],
    }

    for name in COUNTERS:
        assert sum(phases[phase][name] for phase in phases) == pytest.approx(
            run_total[name]
        ), name


def test_phases_partition_every_counter_per_robot(scenario):
    executor, _ = scenario
    meter = executor.accounting
    final = final_counters(executor)

    for robot_id, values in final.items():
        # Every counter starts at zero, so the partition is the robot's
        # own run total.
        assert all(v == 0 for v in meter.registered[robot_id].values())

        closed = meter.by_robot.get(robot_id, {})

        for name in COUNTERS:
            booked = sum(segment.get(name, 0) for segment in closed.values())
            still_open = values[name] - meter._mark[robot_id][name]

            assert booked + still_open == pytest.approx(values[name]), (robot_id, name)


def test_phase_totals_is_read_only(scenario):
    executor, _ = scenario

    assert executor.phase_totals() == executor.phase_totals()


def test_every_robot_ends_in_recovery_and_never_leaves_it(scenario):
    executor, _ = scenario
    meter = executor.accounting

    assert meter.exhausted_at is not None
    assert set(meter.phase.values()) == {RECOVERY}

    for now, robot_id, left, entered in meter.transitions:
        assert left != RECOVERY, robot_id

        # A retirement in the exhaustion instant itself is booked before
        # the dispatch that exhausts the workload, so <= rather than <.
        if entered == SUPPORT:
            assert now <= meter.exhausted_at, robot_id

        if entered == RECOVERY:
            assert now >= meter.exhausted_at, robot_id

    assert all(t >= meter.exhausted_at for t in meter.recovery_from.values())


def test_productive_totals_are_the_outcome_rows(scenario):
    """One calculation: the run's order work is its outcome rows."""

    executor, recorder = scenario
    productive = executor.phase_totals()[PRODUCTIVE]

    assert len(recorder.outcomes) == len(executor.result.completions)

    for name, column in OUTCOME_FIELDS.items():
        assert productive[name] == pytest.approx(
            sum(row[column] for row in recorder.outcomes)
        ), name

    # No robot charges mid-order.
    assert productive["charging_s"] == 0


# ----------------------------------------------------------------------
# Real runs: the metrics.
# ----------------------------------------------------------------------


def test_order_makespan_is_the_final_drop_off(scenario):
    executor, recorder = scenario
    m = metrics(executor)

    final_drop_off = max(c.sim_time for c in executor.result.completions)

    assert m["metrics_schema_version"] == METRICS_SCHEMA_VERSION == 2
    assert m["order_makespan_s"] == final_drop_off
    assert m["order_makespan_s"] == max(row["completed_at"] for row in recorder.outcomes)
    assert m["orders_exhausted_at_s"] <= m["order_makespan_s"]
    assert m["order_makespan_s"] <= m["fleet_recovery_time_s"]
    assert m["recovery_tail_s"] == pytest.approx(
        m["fleet_recovery_time_s"] - m["order_makespan_s"]
    )
    assert m["order_throughput_per_h"] == pytest.approx(
        m["orders_completed"] / m["order_makespan_s"] * 3600
    )


def test_version_1_fields_keep_their_values(scenario):
    executor, _ = scenario
    m = metrics(executor)
    totals = executor.totals()

    assert m["makespan_s"] == m["fleet_recovery_time_s"] == float(totals["makespan_s"])
    assert m["total_distance_m"] == float(totals["distance_m"])
    assert m["total_waiting_s"] == float(totals["waiting_time_s"])
    assert m["distance_per_order_m"] == pytest.approx(
        m["total_distance_m"] / m["orders_completed"]
    )


def test_metric_phase_fields_add_up_to_the_totals(scenario):
    executor, _ = scenario
    m = metrics(executor)

    for total, stem in (
        ("total_distance_m", "distance_m"),
        ("total_waiting_s", "waiting_s"),
        ("energy_consumed_percent", "energy_percent"),
    ):
        parts = sum(m[f"{phase}_{stem}"] for phase in (PRODUCTIVE, SUPPORT, RECOVERY))

        assert parts == pytest.approx(m[total]), total


def test_charging_while_orders_are_owed_is_support(busy):
    executor, _ = busy
    m = metrics(executor)
    phases = executor.phase_totals()

    assert m["charge_sessions"] == 4
    assert phases[SUPPORT]["charging_s"] == pytest.approx(m["charging_time_s"])
    assert m["recovery_charging_s"] == 0
    # Every phase did something in this run.
    assert all(m[f"{phase}_distance_m"] > 0 for phase in (PRODUCTIVE, SUPPORT, RECOVERY))


def test_charging_after_the_last_order_is_recovery_not_order_work(tail):
    executor, _ = tail
    m = metrics(executor)

    final_drop_off = max(c.sim_time for c in executor.result.completions)

    assert m["orders_completed"] == 5
    assert m["charge_sessions"] == 4
    assert m["recovery_charging_s"] > 0
    assert m["recovery_charging_s"] == pytest.approx(m["charging_time_s"])
    assert m["recovery_distance_m"] > 0
    # The charging ran past the last drop-off; order_makespan did not.
    assert m["order_makespan_s"] == final_drop_off < m["fleet_recovery_time_s"]
    assert m["recovery_tail_s"] > 0
