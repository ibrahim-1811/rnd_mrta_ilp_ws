#!/usr/bin/env python3
"""Battery V1 B4: charge to 100%, leave the charger, rejoin the fleet.

Headless demonstration on the V2 map with controlled initial SOCs. B3
proved a CHARGE_DUE robot reaches a reserved terminal; this proves the
rest of the cycle:

  single    one robot at 9% on a short workload. It charges once and
            then completes further orders -- the end-to-end proof that
            a charged robot is genuinely back in the fleet.
  four      robot_001..004 at 9%: four parallel charges, four rejoins.
  twelve    robot_001..012 at 9%: 8 charge and rejoin, 4 stay in
            holding (promotion is B5, not B4).
  recycle   robot A at 9%, robot B at 18%, one usable charger. B works
            first, then requests and occupies the same charger after
            A has charged, cleared the access edge and rejoined.
  slow      one robot at 9%, six orders, charging at 0.1%/s. Its finite
            931-second dwell must not trigger the progress watchdog.
  contested the twelve fleet on the full workload, where departures
            have to merge into aisles busy with order traffic.

Checked in every scenario:

    SOC rises only inside a charge session, at the net configured rate
    every session lands on exactly 100% (exact rational arithmetic)
    no order is dispatched to a robot between its request and its rejoin
    a slot is OCCUPIED for the whole charge, VACATING while the robot
        reverses out, and FREE only once it stands on the aisle cell
    two robots never overlap on one charger cell
    every charged robot ends back in the fleet, parked or working
    no yield, no reservation violation, no SOC below 0

Usage:
    PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management \\
        python src/simulation_env/scripts/check_b4_charging.py
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
from simulation_env.charging import ChargeState, ResourceState
from simulation_env.fleet import build_fleet
from simulation_env.workload_executor import (
    WorkloadExecutor,
    battery_event_line,
    charging_report_lines,
)
from task_management.allocation_strategy import get_batch_strategy


CONFIGS = Path(__file__).resolve().parents[1] / "config"

FULL = Fraction(100)


def low(*numbers, soc=9):
    return {f"robot_{n:03d}": Fraction(soc) for n in numbers}


SCENARIOS = {
    "single": dict(overrides=low(1), robots=1, orders=6),
    "four": dict(overrides=low(1, 2, 3, 4), orders=40),
    "twelve": dict(overrides=low(*range(1, 13))),
    "recycle": dict(
        overrides={"robot_001": Fraction(9), "robot_002": Fraction(18)},
        robots=4,
        orders=12,
        chargers=1,
    ),
    "contested": dict(overrides=low(*range(1, 13)), orders=100),
    "slow": dict(overrides=low(1), robots=1, orders=6, charge_rate=Fraction(1, 10)),
}


def run(name, overrides, robots=None, orders=None, chargers=None, charge_rate=None):

    cfg = load_sim_config(CONFIGS / "warehouse_sim.yaml")

    if robots is not None:
        cfg = replace(cfg, robot_count=robots)

    cfg = replace(
        cfg,
        battery=replace(cfg.battery, initial_soc_overrides=dict(overrides)),
    )
    if charge_rate is not None:
        cfg = replace(
            cfg, battery=replace(cfg.battery, charging_percent_per_second=charge_rate)
        )

    warehouse = load_warehouse_xlsx(cfg.map_path)

    samples = []

    def on_tick(_sim_time, _batch):

        now = ex.engine.time

        slots = {
            zone_id: (slot.state, slot.robot_id)
            for zone_id, slot in ex.chargers.slots.items()
        }

        socs = {
            robot_id: ex.battery.soc(robot_id, now) for robot_id in ex.engine.robots
        }

        cells = {robot_id: robot.cell for robot_id, robot in ex.engine.robots.items()}

        samples.append((now, socs, slots, cells))

    ex = WorkloadExecutor(
        warehouse=warehouse,
        robots=build_fleet(cfg, warehouse),
        queue=OrderQueue(load_orders_csv(cfg.orders_path, warehouse)[:orders]),
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

    if chargers is not None:
        # Controlled contention: this run has fewer usable chargers.
        ex.chargers.slots = dict(list(ex.chargers.slots.items())[:chargers])

    print(
        f"\n=== {name}: {len(ex.engine.robots)} robots, "
        f"{len(ex.queue)} orders, {len(ex.chargers.slots)} charger(s), "
        f"low {sorted(overrides)}"
    )

    ex.run()

    totals = ex.totals()

    print(
        f"  {len(ex.result.completions)}/{len(ex.queue)} orders, "
        f"{ex.result.charge_sessions} charge session(s), "
        f"{ex.result.rejoins} rejoin(s), makespan "
        f"{totals['makespan_s']:.4f} s, "
        f"{totals['reservation_violations']} violations"
    )

    for event in ex.result.battery_events:
        print(battery_event_line(event))

    for line in charging_report_lines(ex):
        print(line)

    return check(ex, samples, name=name)


def trips_of(ex):
    return list(
        {
            id(record): record for record in (*ex.charge_history, *ex.charge.values())
        }.values()
    )


def check(ex, samples, *, name=None):

    # One simulated instant can span several engine batches (a robot
    # reaches its cell on MOVE_COMPLETE; its MISSION_COMPLETE follows at
    # +0 s, and only then does the executor settle it). Keep the last
    # sample of each instant, which is the settled state.
    samples = list({sample[0]: sample for sample in samples}.values())

    problems = []

    trips = trips_of(ex)
    charged = [r for r in trips if r.kind == "charger"]

    if name == "recycle":
        a = next((r for r in charged if r.robot_id == "robot_001"), None)
        b = next((r for r in charged if r.robot_id == "robot_002"), None)
        if (
            a is None
            or b is None
            or a.zone_id != b.zone_id
            or a.rejoined_at is None
            or b.arrived_at is None
            or not a.rejoined_at < b.requested_at == b.departed_at < b.arrived_at
        ):
            problems.append("the same charger was not reused after physical clearance")
        elif not any(
            now == b.requested_at
            and slots[b.zone_id] == (ResourceState.RESERVED, b.robot_id)
            for now, _, slots, _ in samples
        ):
            problems.append(
                "second robot's actual charger reservation was not observed"
            )
        else:
            print(
                f"  reuse: {a.zone_id}, A clear t={float(a.rejoined_at):.4f}, "
                f"B reserve t={float(b.requested_at):.4f}, occupy t={float(b.arrived_at):.4f}"
            )

    if name in ("single", "slow"):
        if not charged or not any(
            d.robot_id == charged[0].robot_id
            and charged[0].rejoined_at is not None
            and d.sim_time >= float(charged[0].rejoined_at)
            for d in ex.result.dispatches
        ):
            problems.append("no actual order assignment after rejoin")
    if name == "slow" and not any(r.charge_duration_s > 600 for r in charged):
        problems.append("slow scenario did not exercise a charge longer than 600 s")

    if len(ex.result.completions) != len(ex.queue):
        problems.append("not every order completed")

    if not charged:
        problems.append("no robot charged at all")

    for record in charged:
        robot_id = record.robot_id

        # 1. Exactly full, by exact arithmetic.
        if record.charge_completed_at is None:
            problems.append(f"{robot_id} never finished charging")
            continue

        if record.arrival_soc + record.charge_gained != FULL:
            problems.append(f"{robot_id} did not reach exactly 100%")

        expected = (
            FULL - record.arrival_soc
        ) / ex.battery.config.charging_percent_per_second

        if record.charge_duration_s != expected:
            problems.append(f"{robot_id} charge duration is not exact")

        # 2. It rejoined and the slot is free behind it.
        if record.state is not ChargeState.REJOINED:
            problems.append(f"{robot_id} ended in {record.state.value}")
            continue

        if record.rejoin_soc is None or record.rejoin_soc > FULL:
            problems.append(f"{robot_id} rejoined above 100%")

        slot = ex.chargers.slots[record.zone_id]

        if slot.state is not ResourceState.FREE:
            problems.append(f"{record.zone_id} is {slot.state.value}, not FREE")

        # 3. It is off the charger cell.
        if ex.engine.robots[robot_id].cell == record.target_cell:
            problems.append(f"{robot_id} is still on its charger")

        # 4. No order while in the workflow.
        during = [
            d
            for d in ex.result.dispatches
            if d.robot_id == robot_id
            and float(record.requested_at) <= d.sim_time < float(record.rejoined_at)
        ]

        if during:
            problems.append(f"{robot_id} was given work while charging")

        clearance = [
            (slots[record.zone_id], cells[robot_id])
            for now, _, slots, cells in samples
            if now == record.rejoined_at
        ]
        if not clearance or clearance[-1] != (
            (ResourceState.FREE, None),
            record.access_cell,
        ):
            problems.append(
                f"{robot_id} slot was not freed at its physical access-cell clearance"
            )

    # 5. Slot lifecycle, sampled instant by instant.
    for record in charged:
        saw_vacating = False
        for now, _, slots, cells in samples:
            if record.arrived_at is None or now < record.arrived_at:
                continue

            state, holder = slots.get(record.zone_id, (None, None))

            if now < record.charge_completed_at:
                if (state, holder) != (ResourceState.OCCUPIED, record.robot_id):
                    problems.append(
                        f"{record.zone_id} was {state} during "
                        f"{record.robot_id}'s charge"
                    )
                    break

            elif record.rejoined_at is not None and now < record.rejoined_at:
                saw_vacating = True
                if (state, holder) != (ResourceState.VACATING, record.robot_id):
                    problems.append(
                        f"{record.zone_id} was {state} while "
                        f"{record.robot_id} reversed out"
                    )
                    break
        if not saw_vacating:
            problems.append(f"{record.robot_id} VACATING interval was not exercised")

    # 6. Never two robots on one charger cell at the same instant.
    for now, _, _, cells in samples:
        occupants = {}

        for robot_id, cell in cells.items():
            occupants.setdefault(cell, []).append(robot_id)

        for slot in ex.chargers.slots.values():
            here = occupants.get(slot.cell, [])
            if len(here) > 1:
                problems.append(f"{here} shared {slot.zone_id} at t={float(now):.4f}")

    # 7. SOC rises only inside a session, and never below zero.
    windows = {}

    for record in charged:
        windows.setdefault(record.robot_id, []).append(
            (
                record.charge_started_at,
                record.charge_completed_at
                if record.charge_completed_at is not None
                else ex.engine.time,
            )
        )

    previous = {}
    previous_at = {}

    for now, socs, _, _ in samples:
        for robot_id, soc in socs.items():
            if soc < 0:
                problems.append(f"{robot_id} fell below 0% SOC")

            was = previous.get(robot_id)

            if was is not None and soc > was:
                since = previous_at[robot_id]

                if not any(
                    start < now and end > since
                    for start, end in windows.get(robot_id, ())
                ):
                    problems.append(
                        f"{robot_id} SOC rose without charging at t={float(now):.4f}"
                    )

            previous[robot_id] = soc
            previous_at[robot_id] = now

    if ex.result.yield_events:
        problems.append(f"{ex.result.yield_events} yield events")

    if ex.totals()["reservation_violations"]:
        problems.append("reservation violations")

    # De-duplicate: one broken invariant can trip on many samples.
    seen = list(dict.fromkeys(problems))

    print("  checks: " + ("OK" if not seen else "; ".join(seen[:6])))

    return not seen


def main() -> int:

    ok = [run(name, **spec) for name, spec in SCENARIOS.items()]

    print(f"\nB4 charging and fleet rejoin: {sum(ok)}/{len(ok)} scenarios OK")

    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
