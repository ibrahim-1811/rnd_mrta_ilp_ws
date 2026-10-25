#!/usr/bin/env python3
"""Battery V1 B5: holding promotion and charger preemption.

Headless demonstration on the V2 map with controlled initial SOCs. B4
proved a robot charges to 100%, leaves and rejoins; this proves the two
policies that decide *who* gets a charger:

  twelve    robot_001..012 at 9%: 8 take the chargers, 4 wait in
            holding, and all 4 are promoted onto chargers as they free
            up. The acceptance scenario: 12 robots, 8 chargers, 14
            holding cells.
  late      8 at 9% fill the chargers; robot_009 (14%) later turns
            CHARGE_NEEDED with every charger OCCUPIED. A CHARGE_NEEDED
            robot never preempts -- it waits and is promoted.
  preempt   robot_001 at 9% takes the only usable charger; robot_002
            (11.5%, reserve 0) crosses 10% while running an order and
            requests mid-charge. It preempts, and the victim -- fit for
            work on its partial charge -- rejoins the fleet.
  preempt_wait
            the same at 0.1%/s, so the victim is still below the
            CHARGE_DUE threshold when it is cut short. It is not fit for
            work, so it waits in a holding cell instead, and the charger
            it gave up goes to the robot that displaced it.
  contested the twelve fleet on the full 100-order workload, where
            promotions have to merge into aisles busy with order work.

Checked in every scenario:

    charger occupancy never exceeds the bank, one robot per cell
    no robot ever holds two charger or two holding reservations
    no robot waits in holding while a charger stands free and unpromised
    every promotion goes to the lowest-SOC waiting robot of that instant
    a promoted robot leaves its holding cell only after being promoted,
        and the cell is released only once it stands on the aisle cell
    only a CHARGE_DUE robot preempts, only the highest-SOC charging
        robot is displaced, and only if its SOC is strictly higher
    a preempted session books exactly the time it charged
    a preempted robot rejoins only if it is fit for work, else it waits
    the charger freed by a preemption is held for the requester
    no yield, no reservation violation, no SOC below 0
    every order still completes

Usage:
    PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management \\
        python src/simulation_env/scripts/check_b5_queue.py
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


CONFIGS = Path(__file__).resolve().parents[1] / "test" / "fixtures"

FULL = Fraction(100)


def low(*numbers, soc=9):
    return {f"robot_{n:03d}": Fraction(soc) for n in numbers}


# robot_002 is eligible for the first order at 11.5%, crosses 10% while
# running it, and requests while robot_001 is still charging. The frozen
# 10% reserve guarantees a robot ends an order above 10% plus its
# charger leg, so the controlled reserve of 0 is what makes the crossing
# deterministic -- exactly as in the B2 checks.
PREEMPT = {"robot_001": Fraction(9), "robot_002": Fraction(23, 2)}


SCENARIOS = {
    "twelve": dict(overrides=low(*range(1, 13)), orders=40),
    "late": dict(overrides={**low(*range(1, 9)), "robot_009": Fraction(14)}, orders=40),
    "preempt": dict(overrides=PREEMPT, robots=2, orders=2, chargers=1, reserve=0),
    "preempt_wait": dict(
        overrides=PREEMPT,
        robots=2,
        orders=2,
        chargers=1,
        reserve=0,
        charge_rate=Fraction(1, 10),
    ),
    "contested": dict(overrides=low(*range(1, 13)), orders=100),
}


def run(
    name,
    overrides,
    robots=None,
    orders=None,
    chargers=None,
    charge_rate=None,
    reserve=None,
):

    cfg = load_sim_config(CONFIGS / "v2_reference.yaml")

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

    if reserve is not None:
        cfg = replace(
            cfg, battery=replace(cfg.battery, reserve_percent=Fraction(reserve))
        )

    warehouse = load_warehouse_xlsx(cfg.map_path)

    samples = []

    def on_tick(_sim_time, _batch):

        now = ex.engine.time

        slots = {
            zone_id: (slot.state, slot.robot_id, slot.promised_to)
            for bank in (ex.chargers, ex.holding)
            for zone_id, slot in bank.slots.items()
        }

        robot_state = {
            robot_id: (
                robot.cell,
                ex.battery.soc(robot_id, now),
                None if robot_id not in ex.charge else ex.charge[robot_id].state,
            )
            for robot_id, robot in ex.engine.robots.items()
        }

        samples.append((now, robot_state, slots))

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
        f"{len(ex.holding.slots)} holding, low {sorted(overrides)}"
    )

    ex.run()

    totals = ex.totals()

    print(
        f"  {len(ex.result.completions)}/{len(ex.queue)} orders, "
        f"{ex.result.charge_sessions} charge session(s), "
        f"{ex.result.promotions} promotion(s), "
        f"{ex.result.preemptions} preemption(s) "
        f"({ex.result.preempt_rejoins} rejoined, "
        f"{ex.result.preempt_to_holding} to holding), makespan "
        f"{totals['makespan_s']:.4f} s, "
        f"{totals['reservation_violations']} violations"
    )

    for event in ex.result.battery_events:
        print(battery_event_line(event))

    for line in charging_report_lines(ex):
        print(line)

    return check(name, ex, samples)


def trips(ex):
    """Every terminal trip of the run, current and historical, once."""

    return list(
        {
            id(record): record
            for record in (*ex.charge_history, *ex.charge.values())
        }.values()
    )


def check(name, ex, samples):

    problems = []

    every = trips(ex)

    promotions = [r for r in every if r.promoted_at is not None]
    preemptions = [r for r in every if r.preempted_at is not None]

    charger_cells = {slot.cell: zone for zone, slot in ex.chargers.slots.items()}

    if len(ex.result.completions) != len(ex.queue):
        problems.append("not every order completed")

    # 1. The scenario really exercised what it is for.
    if name in ("twelve", "late", "contested") and not promotions:
        problems.append("no promotion happened")

    if name.startswith("preempt") and not preemptions:
        problems.append("no preemption happened")

    if name == "late" and preemptions:
        problems.append("a CHARGE_NEEDED robot preempted")

    if name == "preempt" and ex.result.preempt_rejoins != 1:
        problems.append("the preempted robot did not rejoin the fleet")

    if name == "preempt_wait" and ex.result.preempt_to_holding != 1:
        problems.append("the preempted robot did not wait in holding")

    # 2. Promotion: the lowest-SOC waiting robot of that instant, and a
    #    holding cell left only because of a promotion.
    by_instant = {}

    for sample in samples:
        by_instant.setdefault(sample[0], []).append(sample)

    for record in promotions:
        if record.kind != "holding":
            problems.append(f"{record.robot_id} was promoted out of {record.kind}")
            continue

        if record.left_holding_at is None or record.left_holding_at < record.promoted_at:
            problems.append(f"{record.robot_id} left holding before it was promoted")

        if ex.holding.slots[record.zone_id].robot_id == record.robot_id:
            problems.append(f"{record.zone_id} was never released")

        at = record.promoted_at

        if at not in by_instant:
            continue

        # The state the grant was made from, reconstructed at the
        # instant it happened: the robots still in holding, plus the
        # ones promoted out of it in this same instant. A robot that
        # arrives in holding next to a free charger is promoted in that
        # same tick, so "the instant before" has no waiting set at all.
        _, robot_state, _ = by_instant[at][-1]

        granted = {r.robot_id for r in promotions if r.promoted_at == at}

        waiting = {
            robot_id: soc
            for robot_id, (_, soc, state) in robot_state.items()
            if state is ChargeState.HOLDING or robot_id in granted
        }

        if record.robot_id not in waiting:
            problems.append(f"{record.robot_id} was promoted without waiting")

        elif waiting[record.robot_id] != min(waiting.values()):
            problems.append(f"{record.robot_id} was promoted over a lower-SOC robot")

    # 3. Preemption: only CHARGE_DUE, only a strictly higher-SOC victim,
    #    and only the maximum one.
    for record in preemptions:
        requester = ex.charge.get(record.preempted_by)

        if requester is None:
            problems.append(f"{record.robot_id} was preempted by nobody")
            continue

        at = record.preempted_at

        earlier = [now for now in by_instant if now < at]

        if earlier:
            _, robot_state, _ = by_instant[max(earlier)][-1]

            charging = {
                robot_id: soc
                for robot_id, (_, soc, state) in robot_state.items()
                if state is ChargeState.CHARGING
            }

            if charging and charging.get(record.robot_id) != max(charging.values()):
                problems.append(
                    f"{record.robot_id} was not the highest-SOC charging robot"
                )

        victim_soc = record.arrival_soc + record.charge_gained

        if victim_soc <= requester.request_soc:
            problems.append(f"{record.robot_id} was not above its requester")

        if record.victim_soc_ge_80 is not (victim_soc >= 80):
            problems.append(f"{record.robot_id} victim_soc_ge_80 is wrong")

        # Exactly the interval it charged, not the one scheduled.
        rate = ex.battery.config.charging_percent_per_second

        if record.charge_elapsed_s != at - record.charge_started_at:
            problems.append(f"{record.robot_id} booked the wrong charge interval")

        if record.charge_gained != record.charge_elapsed_s * rate:
            problems.append(f"{record.robot_id} gained the wrong SOC")

        if record.charge_completed_at is not None:
            problems.append(f"{record.robot_id} was recorded as fully charged")

        due = ex.battery.config.charge_due_percent

        if record.preempt_rejoined and record.rejoin_soc <= due:
            problems.append(f"{record.robot_id} rejoined at or below CHARGE_DUE")

        if record.preempt_rejoined is False and record.state is not (
            ChargeState.PREEMPTED
        ):
            problems.append(f"{record.robot_id} ended in {record.state.value}")

    if ex.engine._charging:
        problems.append("a charge session was left open")

    # 4. Bank integrity, instant by instant.
    for now, robot_state, slots in samples:
        held = [
            zone_id
            for zone_id in ex.chargers.slots
            if slots[zone_id][0] is not ResourceState.FREE
        ]

        if len(held) > len(ex.chargers.slots):
            problems.append(f"more chargers held than exist at t={float(now):.4f}")

        for bank in (ex.chargers, ex.holding):
            holders = [
                slots[zone_id][1] for zone_id in bank.slots if slots[zone_id][1]
            ]

            if len(holders) != len(set(holders)):
                problems.append(f"a robot held two {bank.kind} slots")

        # One robot per charger cell, and only its own holder.
        on_bank = {}

        for robot_id, (cell, _, _) in robot_state.items():
            if cell in charger_cells:
                on_bank.setdefault(cell, []).append(robot_id)

        for cell, here in on_bank.items():
            zone_id = charger_cells[cell]

            if len(here) > 1:
                problems.append(f"{here} shared {zone_id} at t={float(now):.4f}")

            elif slots[zone_id][1] != here[0]:
                problems.append(f"{here[0]} stood on {zone_id} without holding it")

        # Nobody waits beside a charger that is free and unpromised.
        waiting = [
            robot_id
            for robot_id, (_, _, state) in robot_state.items()
            if state is ChargeState.HOLDING
        ]

        offered = [
            zone_id
            for zone_id in ex.chargers.slots
            if slots[zone_id][0] is ResourceState.FREE and slots[zone_id][2] is None
        ]

        if waiting and offered:
            problems.append(
                f"{waiting} waited while {offered} stood free at t={float(now):.4f}"
            )

        for robot_id, (_, soc, _) in robot_state.items():
            if soc < 0:
                problems.append(f"{robot_id} fell below 0% SOC")

    # 5. Nothing is promised to anybody at the end of the run.
    outstanding = [
        zone_id
        for zone_id, slot in ex.chargers.slots.items()
        if slot.promised_to is not None
    ]

    if outstanding:
        problems.append(f"{outstanding} still promised at the end")

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

    print(f"\nB5 promotion and preemption: {sum(ok)}/{len(ok)} scenarios OK")

    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
