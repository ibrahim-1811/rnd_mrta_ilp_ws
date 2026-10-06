#!/usr/bin/env python3

"""Battery V1 B2: show that energy eligibility actually binds.

Controlled test only. Six robots are given low initial SOC through
``battery.initial_soc_overrides``; the other sixteen stay at 100%. The
shipped configs never do this.

    robot_001  50%    robot_002  20%    robot_006  14%
    robot_003  12%    robot_004  10%    robot_005   9%

All six start on Stage Zone cells. The full run below uses the first 40
orders, as it did in B2, when a REQUEST robot stood where it was and
one out in an aisle blocked the lane. Since B3 a REQUEST robot drives to
a reserved charger or holding cell; check_charger_evacuation.py runs
this same fleet on all 100 orders.

    PYTHONPATH=$PP $PY src/simulation_env/scripts/check_battery_eligibility.py
"""

from dataclasses import replace
from fractions import Fraction

from mrta_core.order_queue import OrderQueue
from mrta_core.orders import load_orders_csv
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.fleet import build_fleet
from simulation_env.workload_executor import (
    WorkloadExecutor,
    battery_event_line,
    eligibility_report_lines,
)
from task_management.allocation_strategy import get_batch_strategy


CONFIG = "src/simulation_env/config/warehouse_sim.yaml"

LOW = {
    "robot_001": Fraction(50),
    "robot_002": Fraction(20),
    "robot_003": Fraction(12),
    "robot_004": Fraction(10),
    "robot_005": Fraction(9),
    "robot_006": Fraction(14),
}

EXPECTED_AT_START = {
    "robot_001": "available",
    "robot_002": "available",
    "robot_003": "CHARGE_NEEDED",
    "robot_004": "CHARGE_DUE",
    "robot_005": "CHARGE_DUE",
    "robot_006": "available",
}

FULL_RUN_ORDERS = 40


def executor(config, warehouse, orders, overrides, events=None):
    config = replace(
        config,
        battery=replace(config.battery, initial_soc_overrides=overrides),
    )

    return WorkloadExecutor(
        warehouse=warehouse,
        robots=build_fleet(config, warehouse),
        queue=OrderQueue(orders),
        allocator=get_batch_strategy("nearest_astar"),
        resolution=config.resolution_m,
        speed_mps=config.speed_mps,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
        progress_interval_s=0,
        battery=config.battery,
        on_battery_event=events,
    )


def score_all(ex, robot_id):
    """Every pending order, scored for one robot right now."""

    return ex.allocator.score(
        ex.warehouse,
        [(robot_id, ex.engine.robots[robot_id].cell)],
        ex.queue.pending(),
        ex.fifo_position,
        ex.routes,
        battery=ex._battery_context([robot_id]),
    )


def show_requirement(label, r) -> None:
    print(f"  {label}")
    print(f"    route {float(r.predicted_order_distance_m):.0f} m        "
          f"-> travel   {float(r.predicted_order_travel_energy_percent):8.4f} %")
    print(f"    service                -> service  "
          f"{float(r.predicted_service_energy_percent):8.4f} %")
    print(f"    charger leg {float(r.predicted_nearest_charger_distance_m):.0f} m "
          f"({r.nearest_charger_id}) -> charger  "
          f"{float(r.predicted_charger_energy_percent):8.4f} %")
    print(f"    reserve                            "
          f"{float(r.reserve_percent):8.4f} %")
    print(f"    required               =           "
          f"{float(r.required_soc_percent):8.4f} %")
    print(f"    SOC {float(r.current_soc_percent):.4f} %  margin "
          f"{float(r.energy_margin_percent):+.4f} %  -> "
          f"{'ELIGIBLE' if r.battery_eligible else 'INELIGIBLE'}")


def main() -> int:

    config = load_sim_config(CONFIG)
    warehouse = load_warehouse_xlsx(config.map_path)

    failures = []

    def check(ok, message):
        if not ok:
            failures.append(message)

    # --------------------------------------------------------------
    # 1. The formula, on one real pair.
    # --------------------------------------------------------------

    print("=" * 74)
    print("1. ELIGIBILITY FORMULA, ONE REAL PAIR: robot_001 -> O0001 at t=0")
    print("=" * 74)

    ex = executor(config, warehouse,
                  load_orders_csv(config.orders_path, warehouse), {})
    full = {c.order_id: c for c in score_all(ex, "robot_001")}
    show_requirement("robot_001 at 100 %", full["O0001"].energy)

    ex = executor(config, warehouse,
                  load_orders_csv(config.orders_path, warehouse),
                  {"robot_001": Fraction(14)})
    low = {c.order_id: c for c in score_all(ex, "robot_001")}
    show_requirement("the same pair at 14 %", low["O0001"].energy)

    check(full["O0001"].energy.battery_eligible, "O0001 should fit at 100%")
    check(not low["O0001"].energy.battery_eligible, "O0001 should not fit at 14%")

    # --------------------------------------------------------------
    # 2. The first decision, every low robot on its Stage Zone.
    # --------------------------------------------------------------

    print()
    print("=" * 74)
    print("2. FIRST DECISION, t=0, 100 PENDING ORDERS")
    print("=" * 74)

    events = []
    ex = executor(config, warehouse,
                  load_orders_csv(config.orders_path, warehouse), LOW,
                  events.append)

    scored = {robot_id: score_all(ex, robot_id) for robot_id in LOW}

    ex.dispatch()

    assigned = {d.robot_id: d for d in ex.result.dispatches}

    print("  robot       SOC   required over 100 orders   eligible  "
          "state at t=0")

    for robot_id in LOW:

        required = [c.energy.required_soc_percent for c in scored[robot_id]]
        fits = sum(1 for c in scored[robot_id] if c.eligible)

        if robot_id in ex.requesting:
            state = ex.requesting[robot_id].reason
        else:
            state = "available"

        print(f"  {robot_id}  {float(LOW[robot_id]):5.1f}   "
              f"{float(min(required)):7.4f} .. {float(max(required)):7.4f} %"
              f"      {fits:3d}/100  {state}"
              + (f", assigned {assigned[robot_id].order_id}"
                 if robot_id in assigned else ""))

        check(state == EXPECTED_AT_START[robot_id],
              f"{robot_id}: expected {EXPECTED_AT_START[robot_id]}, got {state}")

    # --------------------------------------------------------------
    # 3. Short orders only: robot_006 at 14%.
    # --------------------------------------------------------------

    fits = [c.energy for c in scored["robot_006"] if c.eligible]
    refused = [c.energy for c in scored["robot_006"] if not c.eligible]

    print()
    print("=" * 74)
    print("3. robot_006 AT 14 %: ELIGIBLE FOR SHORT ORDERS ONLY")
    print("=" * 74)
    print(f"  eligible  {len(fits):3d} orders, route "
          f"{float(min(e.predicted_order_distance_m for e in fits)):.0f}.."
          f"{float(max(e.predicted_order_distance_m for e in fits)):.0f} m, "
          f"required up to "
          f"{float(max(e.required_soc_percent for e in fits)):.4f} %")
    print(f"  refused   {len(refused):3d} orders, route "
          f"{float(min(e.predicted_order_distance_m for e in refused)):.0f}.."
          f"{float(max(e.predicted_order_distance_m for e in refused)):.0f} m, "
          f"required from "
          f"{float(min(e.required_soc_percent for e in refused)):.4f} %")

    check(max(e.required_soc_percent for e in fits) <= 14,
          "an eligible order needs more than 14%")
    check(min(e.required_soc_percent for e in refused) > 14,
          "a refused order needs 14% or less")

    # --------------------------------------------------------------
    # 4. A whole run, 40 orders.
    # --------------------------------------------------------------

    print()
    print("=" * 74)
    print(f"4. FULL RUN, FIRST {FULL_RUN_ORDERS} ORDERS, SAME LOW FLEET")
    print("=" * 74)

    ex = executor(
        config, warehouse,
        load_orders_csv(config.orders_path, warehouse)[:FULL_RUN_ORDERS],
        LOW,
        lambda event: print(battery_event_line(event)),
    )

    try:
        ex.run()
    except RuntimeError as error:
        failures.append(f"full run did not finish: {error}")

    totals = ex.totals()

    print()
    print(f"  completed        : {len(ex.result.completions)}/{FULL_RUN_ORDERS}")
    print(f"  parked           : {len(ex.parked)}   "
          f"in charge request: {sorted(ex.requesting)}")
    print(f"  makespan         : {totals['makespan_s']:.4f} s   "
          f"violations {totals['reservation_violations']}")

    for robot_id in LOW:
        taken = [d for d in ex.result.dispatches if d.robot_id == robot_id]
        margins = [float(d.energy.energy_margin_percent) for d in taken]
        final = ex.battery.soc(robot_id, ex.engine.time)
        print(f"  {robot_id}  start {float(LOW[robot_id]):5.1f} %  "
              f"orders {len(taken):2d}  lowest margin "
              f"{min(margins) if margins else float('nan'):+8.4f} %  "
              f"final {float(final):8.4f} %")

    print()
    for line in eligibility_report_lines(ex):
        print(line)

    check(len(ex.result.completions) == FULL_RUN_ORDERS, "not every order done")
    check(totals["reservation_violations"] == 0, "reservation violations")
    check(
        all(d.energy.battery_eligible for d in ex.result.dispatches),
        "an ineligible pair was dispatched",
    )
    check(
        not any(d.robot_id in ("robot_003", "robot_004", "robot_005")
                for d in ex.result.dispatches),
        "a CHARGE_DUE / CHARGE_NEEDED robot was given work",
    )
    # B3: a requester ends on the non-transit terminal it reserved.
    for robot_id in ex.requesting:
        check(
            ex.engine.robots[robot_id].cell == ex.charge[robot_id].target_cell,
            f"{robot_id} is in charge request off its reserved terminal",
        )

    print()

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print("status : OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
