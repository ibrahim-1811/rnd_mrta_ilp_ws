"""S0: the allocation candidate window and the FIFO liveness rule.

Two separate defects live in ``min(available_robots, pending)``:

1. *No choice.* The window is sized by the number of free robots, and
   under continuous dispatch robots come free one at a time, so 62 of
   71 allocator calls on the 100-order workload are 1 robot x 1 order.
   Every strategy returns the same assignment on a 1x1 decision, so no
   strategy comparison is defensible. Fixed by W = fleet_size.

2. *No liveness.* The window is a strict FIFO prefix, so a head order
   that no free robot is battery-eligible for hides the feasible orders
   behind it. The allocator returns nothing, recovery parks the robot
   because it owes no work, and the run stalls with orders pending.
   Fixed by extending the window down the FIFO sequence when the prefix
   offers no assignable pair at all.

See docs/DECISIONS.md, "Allocation Candidate Window" and "FIFO Liveness
Rule", and docs/ARCHITECTURE.md section 12, P0-1.

Both are infrastructure: the window is identical for every strategy and
no strategy ever chooses to skip an order.
"""

from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from mrta_core.order_queue import OrderQueue
from mrta_core.orders import OrderStatus, load_orders_csv
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.fleet import build_fleet
from simulation_env.workload_executor import WorkloadExecutor
from task_management.allocation_strategy import get_batch_strategy


CONFIGS = Path(__file__).resolve().parent / "fixtures"

# Measured on the V2 map from robot_001's spawn cell (0, 12), with the
# shipped 10% reserve and 10% charge-due threshold:
CHEAP = "O0053"  # 42 m route, requires 12.4667 % SOC
DEAR = "O0099"  # 108 m route, requires 16.8833 % SOC

# Inside the 4.4167 % band where CHEAP is eligible and DEAR is not, and
# clear of CHARGE_DUE so the robot is offered work rather than gated out
# into the charging workflow.
BLOCKED_SOC = Fraction(14)


@pytest.fixture(scope="module")
def config():
    return load_sim_config(CONFIGS / "v2_reference.yaml")


@pytest.fixture(scope="module")
def warehouse(config):
    return load_warehouse_xlsx(config.map_path)


def one_robot(config, soc):
    """A single-robot fleet at a controlled SOC. Never shipped."""

    battery = replace(config.battery, initial_soc_overrides={"robot_001": soc})

    return replace(config, robot_count=1, battery=battery)


def executor_for(config, warehouse, order_ids, window="fleet_size"):
    """An executor whose queue is exactly ``order_ids``, in that order.

    The FIFO sequence is the CSV sequence, so passing the ids in a
    chosen order is how a controlled head-of-queue case is built.
    Orders carry their own lifecycle status, so each executor gets fresh
    copies loaded from disk.
    """

    fresh = {
        order.order_id: order
        for order in load_orders_csv(config.orders_path, warehouse)
    }

    queue = OrderQueue([fresh[order_id] for order_id in order_ids])

    executor = WorkloadExecutor(
        warehouse=warehouse,
        robots=build_fleet(config, warehouse),
        queue=queue,
        allocator=get_batch_strategy("nearest_astar"),
        resolution=config.resolution_m,
        speed_mps=config.speed_mps,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
        progress_interval_s=0,
        battery=config.battery,
        window=window,
    )

    return executor, queue


def completed(queue):
    """Completed order ids, in the order they actually completed."""

    done = [
        order for order in queue.orders if order.status is OrderStatus.COMPLETED
    ]

    return [order.order_id for order in sorted(done, key=lambda o: o.completion_time)]


# ----------------------------------------------------------------------
# The liveness defect, and the control that proves the workload is
# feasible and only the FIFO sequence differs.
# ----------------------------------------------------------------------


def test_a_feasible_workload_completes_when_the_fifo_head_is_ineligible(
    config, warehouse
):
    """The order behind an ineligible head must not be hidden.

    robot_001 at 14% can afford CHEAP but not DEAR. With DEAR at the
    head of a strict prefix window the allocator sees only DEAR, returns
    nothing, and the run stalls at t=0 with both orders pending -- even
    though the fleet can finish both: take CHEAP, charge, take DEAR.

    This is the stall that the FIFO liveness rule exists to remove.
    """

    executor, queue = executor_for(
        one_robot(config, BLOCKED_SOC), warehouse, [DEAR, CHEAP]
    )

    executor.run()

    # CHEAP first: DEAR is unaffordable until the robot has charged.
    assert completed(queue) == [CHEAP, DEAR]

    # And it completed because the window was extended past the head,
    # not because the head turned out to be affordable after all.
    assert executor.result.window_extensions > 0


def test_the_same_workload_completes_when_the_cheap_order_leads(config, warehouse):
    """The control: same robot, same SOC, same orders, order swapped.

    This is what makes the case above a defect rather than an infeasible
    workload. Nothing about the fleet or the energy changes -- only the
    CSV sequence -- and the run completes.
    """

    executor, queue = executor_for(
        one_robot(config, BLOCKED_SOC), warehouse, [CHEAP, DEAR]
    )

    executor.run()

    assert completed(queue) == [CHEAP, DEAR]
    assert executor.result.deferrals == 0


def test_the_legacy_window_still_stalls_on_the_same_workload(config, warehouse):
    """The defect, kept reproducible under the pre-S0 rule.

    ``window="legacy"`` restores ``min(available_robots, pending)`` with
    no liveness extension, which is what the frozen pre-S0 baselines
    were measured under. The stall is therefore expected here, and this
    test is what proves the candidate window is the thing that fixed it
    rather than some unrelated change in routing or battery behaviour.
    """

    executor, queue = executor_for(
        one_robot(config, BLOCKED_SOC), warehouse, [DEAR, CHEAP], window="legacy"
    )

    with pytest.raises(RuntimeError, match="workload stalled"):
        executor.run()

    assert completed(queue) == []
    assert executor.result.window_extensions == 0


# ----------------------------------------------------------------------
# The choice defect: a window wide enough to decide in.
# ----------------------------------------------------------------------


def test_a_lone_free_robot_is_offered_the_whole_window(config, warehouse):
    """W = fleet_size, not the number of robots that happen to be free.

    This is the whole point of S0. One robot free out of a fleet of 22
    is the common case under continuous dispatch, and under the legacy
    rule that robot was offered exactly one order -- so Random,
    Hungarian and MILP all returned the same pair and no strategy
    comparison meant anything.

    Note that ``fleet_size`` is the *configured* fleet, so a one-robot
    fleet legitimately has a window of one. The width follows the fleet,
    not the free pool.
    """

    order_ids = [f"O{n:04d}" for n in range(1, 13)]

    executor, queue = executor_for(config, warehouse, order_ids)

    window = executor._candidate_window(["robot_001"], queue.pending())

    assert len(window) == len(order_ids)


def test_the_legacy_window_gives_a_lone_robot_no_choice(config, warehouse):
    """The same fleet and queue under the pre-S0 rule: one order."""

    order_ids = [f"O{n:04d}" for n in range(1, 13)]

    executor, queue = executor_for(config, warehouse, order_ids, window="legacy")

    window = executor._candidate_window(["robot_001"], queue.pending())

    assert len(window) == 1


def full_queue(config, warehouse):
    return OrderQueue(load_orders_csv(config.orders_path, warehouse))


def run_full(config, warehouse, window):
    """The shipped 100-order workload, 22 robots, one window mode."""

    queue = full_queue(config, warehouse)

    executor = WorkloadExecutor(
        warehouse=warehouse,
        robots=build_fleet(config, warehouse),
        queue=queue,
        allocator=get_batch_strategy("nearest_astar"),
        resolution=config.resolution_m,
        speed_mps=config.speed_mps,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
        progress_interval_s=0,
        battery=config.battery,
        window=window,
    )

    executor.run()

    return executor, queue


def forced(result):
    """Decisions with exactly one robot and one order on the table."""

    return sum(
        count
        for shape, count in result.decision_shapes.items()
        if shape[0] * shape[1] == 1
    )


def test_the_legacy_window_leaves_almost_every_decision_forced(config, warehouse):
    """P0-1, pinned: 62 of 71 decisions on the 100-order workload.

    This is the measurement the candidate window exists to fix, and the
    number quoted in docs/ARCHITECTURE.md section 12. A forced decision
    is one every strategy answers identically, so under this rule 87% of
    the workload could not tell two strategies apart.
    """

    executor, _ = run_full(config, warehouse, "legacy")

    assert executor.result.decisions == 71
    assert forced(executor.result) == 62
    assert executor.result.choice_bearing == 9


def test_the_fleet_size_window_leaves_almost_no_decision_forced(config, warehouse):
    """The same workload with W = fleet_size: 1 forced decision of 64.

    The one that remains is the tail of the queue, where fewer than two
    orders are left to choose between. Nothing else about the run
    changes: same routing, same traffic, same battery.
    """

    executor, _ = run_full(config, warehouse, "fleet_size")

    assert executor.result.decisions == 64
    assert forced(executor.result) == 1
    assert executor.result.choice_bearing == 63


def test_the_legacy_window_reproduces_the_pre_s0_baseline(config, warehouse):
    """The frozen pre-S0 100-order baseline, bit-identical.

    Measured 2026-09-13 and unchanged through Battery V1 B0-B5. Keeping
    it reproducible is why ``legacy`` exists: it is the anchor proving
    that S0 changed the allocation window and nothing else.

    See docs/DECISIONS.md, "Baseline Re-Freeze Policy".
    """

    executor, queue = run_full(config, warehouse, "legacy")

    totals = executor.totals()

    assert len(completed(queue)) == 100
    assert totals["makespan_s"] == 282.0
    assert totals["distance_m"] == 6430.0
    assert totals["waiting_time_s"] == pytest.approx(264.6667, abs=1e-4)
    assert totals["reservation_violations"] == 0


def test_the_window_is_always_a_fifo_prefix(config, warehouse):
    """Widening offers more orders; it never reorders the queue."""

    order_ids = [f"O{n:04d}" for n in range(1, 13)]

    executor, queue = executor_for(
        replace(config, robot_count=4), warehouse, order_ids
    )

    free = sorted(executor.engine.robots)
    pending = queue.pending()

    window = executor._candidate_window(free, pending)

    assert window == pending[: len(window)]
    assert len(window) == 4


# ----------------------------------------------------------------------
# S0b: the dispatch quantum. The window fixes the order side of a
# decision; this fixes the robot side.
# ----------------------------------------------------------------------


def run_full_quantum(config, warehouse, quantum):
    queue = full_queue(config, warehouse)

    executor = WorkloadExecutor(
        warehouse=warehouse,
        robots=build_fleet(config, warehouse),
        queue=queue,
        allocator=get_batch_strategy("nearest_astar"),
        resolution=config.resolution_m,
        speed_mps=config.speed_mps,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
        progress_interval_s=0,
        battery=config.battery,
        window="fleet_size",
        dispatch_quantum_s=quantum,
    )

    executor.run()

    return executor, queue


def robots_per_decision(result):
    return [robots for _, robots, _ in result.decision_log]


def test_a_zero_quantum_is_exactly_continuous_dispatch(config, warehouse):
    """The default must not change anything. 0 means 0."""

    executor, queue = run_full_quantum(config, warehouse, 0.0)

    totals = executor.totals()

    assert executor.result.quantum_holds == 0
    assert len(completed(queue)) == 100
    assert totals["makespan_s"] == 268.6667 or totals["makespan_s"] == pytest.approx(
        268.6667, abs=1e-4
    )
    assert totals["distance_m"] == 5798.0


def test_the_quantum_batches_robots_into_one_decision(config, warehouse):
    """Mean robots per decision rises, which is the whole point.

    Continuous dispatch averages 1.56 robots per decision on this
    workload; a 3 s quantum averages 3.03 and makes 25 of 33 decisions
    carry a choice on the robot side. Without that, Hungarian and MILP
    are argmin and cannot be shown distinct from greedy nearest.
    """

    continuous, _ = run_full_quantum(config, warehouse, 0.0)
    batched, _ = run_full_quantum(config, warehouse, 3.0)

    lone = robots_per_decision(continuous.result)
    many = robots_per_decision(batched.result)

    assert sum(many) / len(many) > 1.5 * (sum(lone) / len(lone))
    assert sum(many) / len(many) > 2.5
    assert batched.result.decisions < continuous.result.decisions
    assert batched.result.quantum_holds > 0

    # Most decisions now offer a real choice of robot, not just of order.
    assert sum(1 for m in many if m > 1) > len(many) // 2


def test_the_quantum_never_drops_or_duplicates_work(config, warehouse):
    """Holding a robot is a delay, never a change of ownership."""

    for quantum in (0.0, 1.0, 3.0, 10.0):
        executor, queue = run_full_quantum(config, warehouse, quantum)

        done = completed(queue)

        assert len(done) == 100
        assert len(set(done)) == 100
        assert executor.totals()["reservation_violations"] == 0
        assert len(executor.parked) == 22


def test_no_robot_is_held_longer_than_one_quantum(config, warehouse):
    """The liveness bound, checked where it is decided.

    The window opens on the robot that has been free longest. Measured
    from the latest arrival instead, a steady trickle of freed robots
    could postpone a decision for the whole run; measured from the
    oldest, every robot is dispatched within one quantum of becoming
    free however busy the fleet is.

    Checked directly on the rule rather than inferred from a run: gaps
    between decisions in a finished run legitimately exceed the quantum
    whenever nothing was free to dispatch.
    """

    quantum = Fraction(3)

    executor, queue = executor_for(
        config, warehouse, [f"O{n:04d}" for n in range(1, 13)]
    )
    executor.dispatch_quantum = quantum

    # One robot free, another in flight so more could come free.
    executor.active = {"robot_002": "O0001"}

    free = ["robot_001"]
    pending = queue.pending()

    # Just became free: hold, and arm the clock for the instant the
    # window closes.
    assert executor._quantum_hold(free, pending) is True
    assert executor._quantum_armed == executor.engine.time + quantum

    # A full quantum later the window is shut, whatever else is going on.
    executor._free_since["robot_001"] = executor.engine.time - quantum

    assert executor._quantum_hold(free, pending) is False


def test_nothing_is_held_when_holding_cannot_pay(config, warehouse):
    """The three refusals, each of which would otherwise waste a quantum."""

    executor, queue = executor_for(
        config, warehouse, [f"O{n:04d}" for n in range(1, 13)]
    )
    executor.dispatch_quantum = Fraction(3)

    pending = queue.pending()

    # No robot in flight: nothing can come free, so waiting buys only
    # idle drain.
    executor.active = {}
    assert executor._quantum_hold(["robot_001"], pending) is False

    # More robots in hand than orders left: a wider batch cannot help.
    executor.active = {"robot_002": "O0001"}
    executor._free_since.clear()
    assert executor._quantum_hold(sorted(executor.engine.robots), pending) is False

    # No quantum configured at all.
    executor.dispatch_quantum = Fraction(0)
    executor._free_since.clear()
    assert executor._quantum_hold(["robot_001"], pending) is False


def test_the_quantum_is_deterministic(config, warehouse):
    """Same quantum, same run, identical output."""

    first, _ = run_full_quantum(config, warehouse, 3.0)
    second, _ = run_full_quantum(config, warehouse, 3.0)

    assert first.totals() == second.totals()
    assert first.result.decision_log == second.result.decision_log


def test_a_negative_quantum_is_rejected(config, warehouse):
    with pytest.raises(ValueError, match=">= 0"):
        run_full_quantum(config, warehouse, -1.0)
