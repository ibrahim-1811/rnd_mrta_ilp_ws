#!/usr/bin/env python3

"""Run a warehouse workload headless: FIFO -> allocator -> fleet.

No ROS and no Gazebo: this is the execution path on its own, so a run
can be checked without a display or a live node.

    python3 src/simulation_env/scripts/run_workload.py
    python3 src/simulation_env/scripts/run_workload.py --orders 18
    python3 src/simulation_env/scripts/run_workload.py --robots 6 --speed 1.0

Progress is printed as it happens, so a long run shows what it is doing
rather than going silent.
"""

import argparse
from dataclasses import replace
import time

from mrta_core.order_queue import OrderQueue
from mrta_core.orders import load_orders_csv
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.battery_ledger import battery_report_lines
from simulation_env.fleet import build_fleet, home_stations
from simulation_env.workload_executor import (
    PerfBudget,
    WorkloadExecutor,
    battery_event_line,
    charging_report_lines,
    eligibility_report_lines,
)
from task_management.allocation_strategy import get_batch_strategy

CONFIG = "src/simulation_env/config/warehouse_sim.yaml"


def main() -> int:

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--config", default=CONFIG)
    parser.add_argument("--strategy", default="nearest_astar")
    parser.add_argument(
        "--orders", type=int, default=0,
        help="how many logical orders (0 = the whole CSV)",
    )
    parser.add_argument(
        "--robots", type=int, default=0,
        help="fleet size (0 = the configured fleet.robot_count); "
             "capped by the home cells the map provides",
    )
    parser.add_argument(
        "--speed", type=float, default=0.0,
        help="robot speed in m/s (0 = the configured speed_mps)",
    )
    parser.add_argument(
        "--progress", type=float, default=10.0,
        help="simulated seconds between progress lines (0 = off)",
    )
    parser.add_argument("--quiet", action="store_true")

    args = parser.parse_args()

    config = load_sim_config(args.config)

    # Override fleet.robot_count before the fleet is built, so this
    # can raise the count as well as lower it. Slicing the built fleet
    # could only ever shrink it, which made --robots above the
    # configured count silently do nothing.
    if args.robots:
        config = replace(config, robot_count=args.robots)

    warehouse = load_warehouse_xlsx(config.map_path)

    speed = args.speed or config.speed_mps

    robots = build_fleet(config, warehouse)

    for robot in robots:
        robot.speed_mps = speed

    # Spawn positions only. The bank is a shared resource: where a
    # robot finally parks is decided at return time by directed A*.
    spawn_stations = home_stations(config, warehouse)

    orders = load_orders_csv(config.orders_path, warehouse)

    if args.orders:
        orders = orders[: args.orders]

    queue = OrderQueue(orders)

    print("=" * 70)
    print("WORKLOAD")
    print("=" * 70)
    print(f"  strategy   : {args.strategy}")
    print(f"  robots     : {len(robots)}")
    print(f"  orders     : {len(queue)} ({queue.line_item_count} line items)")
    print(f"  speed      : {speed} m/s")
    print()

    started = time.perf_counter()

    def announce(record):
        if args.quiet:
            return
        print(
            f"  t={record.sim_time:8.3f}  {record.robot_id} -> "
            f"{record.order_id}  C_static={record.static_cost_m:6.1f} m  "
            f"[{', '.join(record.shelf_sequence)}] -> {record.dropoff_id}",
            flush=True,
        )

    def progress(snapshot):
        print()
        for line in snapshot.lines():
            print(line, flush=True)

    def perf(budget):
        for line in budget.lines():
            print(line, flush=True)

    executor = WorkloadExecutor(
        warehouse=warehouse,
        robots=robots,
        queue=queue,
        allocator=get_batch_strategy(args.strategy),
        resolution=config.resolution_m,
        speed_mps=speed,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
        on_dispatch=announce,
        on_progress=progress,
        progress_interval_s=args.progress,
        on_perf=perf,
        battery=config.battery,
        # Battery state changes are rare and always worth seeing.
        on_battery_event=lambda event: print(
            battery_event_line(event), flush=True
        ),
    )

    print("  traffic    : local reactive grid traffic")
    print()

    try:
        result = executor.run()

    except RuntimeError as error:
        print()
        print(f"STALLED: {error}")
        return 1

    wall = time.perf_counter() - started

    totals = executor.totals()

    completed = len(result.completions)

    print()
    print("=" * 70)
    print("RESULT")
    print("=" * 70)
    print(f"  completed        : {completed}/{len(queue)}")
    print(f"  parked at stage  : {len(executor.parked)}/{len(robots)}")
    print(f"  makespan         : {totals['makespan_s']:.4f} s")
    print(f"  total distance   : {totals['distance_m']:.1f} m")
    print(f"  total waiting    : {totals['waiting_time_s']:.4f} s")
    print(f"  total service    : {totals['service_time_s']:.4f} s")
    print(
        f"  reservation viol.: "
        f"{totals['reservation_violations']}"
    )
    print(f"  plan deferrals   : {result.plan_failures}")
    print(f"  peak in-progress : {result.peak_in_progress}")
    print(f"  max deferred-now : {result.max_deferred_now}")
    print()
    print("  planning CPU (wall clock, never simulated time):")
    for stage, spent in result.perf.items():
        print(f"    {stage:<14} : {spent:.3f} s")
    print(f"    slow dispatches: {result.slow_dispatches} "
          f"(>= {PerfBudget.THRESHOLD_S * 1000:.0f} ms)")
    print(f"    worst dispatch : {result.worst_dispatch_s:.3f} s")
    print(f"    static failures reused: {result.static_route_reuses}")
    print(f"  wall clock       : {wall:.1f} s")

    claims = executor.parking.claims

    moved = sum(
        1
        for robot_id, zone_id in claims.items()
        if zone_id != spawn_stations.get(robot_id)
    )

    print()
    print("=" * 70)
    print("FINAL STAGE ZONE OCCUPANCY")
    print("=" * 70)
    print(f"  robots parked    : {len(claims)}/{len(robots)}")
    print(f"  distinct stages  : {len(set(claims.values()))}")
    print(f"  moved off spawn  : {moved}/{len(robots)}")
    print()

    for robot_id in sorted(claims):
        spawn = spawn_stations.get(robot_id, "-")
        final = claims[robot_id]
        mark = " " if final == spawn else "*"
        print(
            f"  {mark} {robot_id}  spawn {spawn:<12} "
            f"final {final:<12} cell {executor.parking.cell_of[final]}"
        )

    print()
    print("  * = parked somewhere other than its spawn Stage Zone")
    print()

    ledger = executor.engine.battery

    print("=" * 70)

    if ledger is None:
        print("BATTERY: disabled (battery.enabled is false)")
    else:
        for line in battery_report_lines(
            ledger,
            executor.engine.time,
            executor.engine.robots,
        ):
            print(line)

        print()

        for line in eligibility_report_lines(executor):
            print(line)

        print()

        for line in charging_report_lines(executor):
            print(line)

    print()

    return 0 if completed == len(queue) else 1


if __name__ == "__main__":
    raise SystemExit(main())
