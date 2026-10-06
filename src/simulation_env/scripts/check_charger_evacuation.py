#!/usr/bin/env python3
"""Battery V1 B3/B4: REQUEST robots leave the transit network, charge
and rejoin the fleet.

Headless demonstration on the V2 map with controlled initial SOCs:

  four      robot_001..004 at 9%: four distinct chargers
  twelve    robot_001..012 at 9%: 8 chargers, 4 holding cells
  late      8 at 9% fill the chargers; robot_009 (14%) later turns
            CHARGE_NEEDED in an aisle and goes to a holding cell
  saturated 10 at 9%, holding bank cut to 2 cells for this run only;
            robot_011 (14%) finds everything taken -> Stage Zone
  b2_block  the B2 controlled fleet (50/20/12/10/9/14 %) on all 100
            orders, which deadlocked at t=391.3333 in B2 because a
            REQUEST robot held its aisle cell

For every scenario it checks: every requester reaches its reserved
terminal; a charger robot charges to exactly 100%, reverses off and
rejoins, and its slot is FREE again; a holding robot stays put (B5);
a charger is only ever entered or left by the robot holding it at the
time; no robot moves terminal -> terminal; no yield; no reservation
violation; SOC rises only inside a charge session. Exits non-zero on
any failure.

Usage:
    PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management \\
        python src/simulation_env/scripts/check_charger_evacuation.py
"""

import sys
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

from mrta_core.order_queue import OrderQueue
from mrta_core.orders import load_orders_csv
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import non_transit_cells
from simulation_env.charging import (
    SETTLED_STATES,
    ChargeState,
    ResourceState,
)
from simulation_env.fleet import build_fleet
from simulation_env.workload_executor import (
    WorkloadExecutor,
    battery_event_line,
    charging_report_lines,
)
from task_management.allocation_strategy import get_batch_strategy


CONFIGS = Path(__file__).resolve().parents[1] / "config"


def low(*numbers, soc=9):
    return {f"robot_{n:03d}": Fraction(soc) for n in numbers}


SCENARIOS = {
    "four": dict(config="warehouse_sim_b3_four_low.yaml"),
    "twelve": dict(config="warehouse_sim_b3_twelve_low.yaml"),
    "late": dict(overrides={**low(*range(1, 9)), "robot_009": Fraction(14)}),
    "saturated": dict(
        overrides={**low(*range(1, 11)), "robot_011": Fraction(14)},
        holding=2,
    ),
    "b2_block": dict(
        overrides={
            "robot_001": Fraction(50),
            "robot_002": Fraction(20),
            "robot_003": Fraction(12),
            "robot_004": Fraction(10),
            "robot_005": Fraction(9),
            "robot_006": Fraction(14),
        }
    ),
}


def run(name, config="warehouse_sim.yaml", overrides=None, holding=None):

    cfg = load_sim_config(CONFIGS / config)

    if overrides is not None:
        cfg = replace(
            cfg,
            battery=replace(cfg.battery, initial_soc_overrides=overrides),
        )

    warehouse = load_warehouse_xlsx(cfg.map_path)

    moves = []
    soc_rose = []

    def on_tick(_sim_time, _batch):

        now = ex.engine.time

        for robot_id, robot in ex.engine.robots.items():
            if robot.cell != cells[robot_id]:
                moves.append((now, robot_id, cells[robot_id], robot.cell))
                cells[robot_id] = robot.cell

            soc = ex.battery.soc(robot_id, now)

            if soc > last_soc[robot_id]:
                soc_rose.append((robot_id, last_time[robot_id], now))

            last_soc[robot_id] = soc
            last_time[robot_id] = now

    ex = WorkloadExecutor(
        warehouse=warehouse,
        robots=build_fleet(cfg, warehouse),
        queue=OrderQueue(load_orders_csv(cfg.orders_path, warehouse)),
        allocator=get_batch_strategy("nearest_astar"),
        resolution=cfg.resolution_m,
        speed_mps=cfg.speed_mps,
        service_seconds={
            ZoneType.SHELF: cfg.shelf_pick_s,
            ZoneType.DROPOFF: cfg.dropoff_s,
        },
        on_tick=on_tick,
        progress_interval_s=0,
        battery=cfg.battery,
    )

    if holding is not None:
        ex.holding.slots = dict(list(ex.holding.slots.items())[:holding])

    cells = {r: robot.cell for r, robot in ex.engine.robots.items()}
    last_soc = {r: ex.battery.soc(r, Fraction(0)) for r in ex.engine.robots}
    last_time = {r: Fraction(0) for r in ex.engine.robots}

    print(
        f"\n=== {name}: {config}"
        + (f", holding cut to {holding}" if holding else "")
        + (f", SOC overrides {sorted(overrides)}" if overrides else "")
    )

    ex.run()
    totals = ex.totals()

    print(
        f"  {len(ex.result.completions)}/{len(ex.queue)} orders, "
        f"{len(ex.parked)} parked, {len(ex.settled_charging())} on "
        f"charging-workflow terminals, makespan "
        f"{totals['makespan_s']:.4f} s, "
        f"{totals['reservation_violations']} violations"
    )

    for event in ex.result.battery_events:
        print(battery_event_line(event))

    for line in charging_report_lines(ex):
        print(line)

    print("  routes (request cell -> ... -> terminal):")

    for robot_id in sorted(ex.charge):
        path = ex.charge[robot_id].path
        print(
            f"    {robot_id} {len(path) - 1:2d} moves: "
            + (" ".join(map(str, path)) if path else "(already there)")
        )

    return check(ex, moves, soc_rose)


def check(ex, moves, soc_rose):

    terminals = non_transit_cells(ex.warehouse)

    slots = {
        slot.cell: slot
        for bank in (ex.chargers, ex.holding)
        for slot in bank.slots.values()
    }

    trips = {
        id(record): record for record in (*ex.charge_history, *ex.charge.values())
    }.values()

    # Who legitimately held each terminal cell, and when.
    tenancy = {}

    for record in trips:
        tenancy.setdefault(record.target_cell, []).append(record)

    problems = []

    if len(ex.result.completions) != len(ex.queue):
        problems.append("not every order completed")

    full = Fraction(100)

    for record in trips:
        robot_id = record.robot_id

        if record.kind == "charger":
            # B4: charged to exactly 100%, reversed off, slot released.
            if record.state is not ChargeState.REJOINED:
                problems.append(f"{robot_id} ended in {record.state.value}")
                continue

            if record.arrival_soc + record.charge_gained != full:
                problems.append(f"{robot_id} did not charge to exactly 100%")

            expected = (
                full - record.arrival_soc
            ) / ex.battery.config.charging_percent_per_second

            if record.charge_duration_s != expected:
                problems.append(f"{robot_id} charge duration is not exact")

            if ex.chargers.slots[record.zone_id].state is not ResourceState.FREE:
                problems.append(f"{record.zone_id} was not released")

            if ex.engine.robots[robot_id].cell == record.target_cell:
                problems.append(f"{robot_id} never left its charger")

        else:
            # Holding and Stage Zone fallbacks stay put: promotion is B5.
            if record.state not in SETTLED_STATES:
                problems.append(f"{robot_id} ended in {record.state.value}")

            if ex.engine.robots[robot_id].cell != record.target_cell:
                problems.append(f"{robot_id} is not on its terminal")

            if record.charge_started_at is not None:
                problems.append(f"{robot_id} charged in a {record.kind} cell")

        if record.departed_at != record.requested_at:
            problems.append(f"{robot_id} waited before leaving")

        if set(record.path[1:-1]) & terminals:
            problems.append(f"{robot_id} routed through a terminal")

    def held_by(cell, robot_id, when):
        """Was this robot the legitimate holder of ``cell`` at ``when``?"""

        for record in tenancy.get(cell, ()):
            if record.robot_id != robot_id:
                continue

            start = record.departed_at
            end = record.rejoined_at if record.rejoined_at is not None else None

            if start <= when and (end is None or when <= end):
                return True

        return False

    for when, robot_id, source, target in moves:
        if source in terminals and target in terminals:
            problems.append(f"{robot_id} moved terminal->terminal")

        if source in slots and not held_by(source, robot_id, when):
            problems.append(
                f"{robot_id} left {slots[source].zone_id} without holding it"
            )

        if target in slots and not held_by(target, robot_id, when):
            problems.append(
                f"{robot_id} entered {slots[target].zone_id} without holding it"
            )

    # Two robots must never be on one terminal cell in the same instant.
    for cell, records in tenancy.items():
        spans = sorted(
            (r.arrived_at, r.rejoined_at, r.robot_id)
            for r in records
            if r.arrived_at is not None
        )

        for (_, end, first), (start, _, second) in zip(spans, spans[1:]):
            if end is None or start < end:
                problems.append(f"{first} and {second} overlapped on {cell}")

    if ex.result.yield_events:
        problems.append(f"{ex.result.yield_events} yield events")

    if ex.totals()["reservation_violations"]:
        problems.append("reservation violations")

    # SOC may rise only across a charge session.
    windows = {}

    for record in trips:
        if record.charge_started_at is not None:
            windows.setdefault(record.robot_id, []).append(
                (
                    record.charge_started_at,
                    record.charge_completed_at
                    if record.charge_completed_at is not None
                    else ex.engine.time,
                )
            )

    for robot_id, since, now in soc_rose:
        if not any(
            start < now and end > since for start, end in windows.get(robot_id, ())
        ):
            problems.append(
                f"{robot_id} SOC rose without charging at t={float(now):.4f}"
            )

    print("  checks: " + ("OK" if not problems else "; ".join(problems)))

    return not problems


def main() -> int:

    ok = [run(name, **spec) for name, spec in SCENARIOS.items()]

    print(f"\nB3/B4 charger evacuation and charging: {sum(ok)}/{len(ok)} scenarios OK")

    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
