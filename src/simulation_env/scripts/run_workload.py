#!/usr/bin/env python3

"""Run a warehouse workload headless: FIFO -> allocator -> fleet.

No ROS and no Gazebo: this is the execution path on its own, so a run
can be checked without a display or a live node.

    python3 src/simulation_env/scripts/run_workload.py
    python3 src/simulation_env/scripts/run_workload.py --orders 18
    python3 src/simulation_env/scripts/run_workload.py --robots 6 --speed 1.0
    python3 src/simulation_env/scripts/run_workload.py \
        --dataset runs/nearest_astar --export

Progress is printed as it happens, so a long run shows what it is doing
rather than going silent.
"""

import argparse
from dataclasses import replace
from pathlib import Path
import time

from mrta_core.order_queue import OrderQueue
from mrta_core.orders import describe_order_selection, load_orders_csv, select_orders
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.battery_ledger import battery_report_lines
from simulation_env.dataset import RunRecorder, build_manifest
from simulation_env.fleet import build_fleet, home_stations
from simulation_env.workload_executor import (
    PerfBudget,
    WorkloadExecutor,
    battery_event_line,
    charging_report_lines,
    decision_report_lines,
    eligibility_report_lines,
)
from task_management.allocation_strategy import get_batch_strategy

CONFIG = "src/simulation_env/config/warehouse.yaml"


def main() -> int:

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--config", default=CONFIG)
    parser.add_argument("--strategy", default="nearest_astar")
    parser.add_argument(
        "--orders",
        type=int,
        default=0,
        help="run the first N logical orders, never splitting one "
        "(0 = all of them; more than the file holds = all of them)",
    )
    parser.add_argument(
        "--orders-file",
        default=None,
        metavar="CSV",
        help="use this order file instead of the config's orders.file "
        "(a bare name is looked for next to the config)",
    )
    parser.add_argument(
        "--robots",
        type=int,
        default=0,
        help="fleet size (0 = the configured fleet.robot_count); "
        "capped by the home cells the map provides",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=0.0,
        help="robot speed in m/s (0 = the configured speed_mps)",
    )
    parser.add_argument(
        "--progress",
        type=float,
        default=10.0,
        help="simulated seconds between progress lines (0 = off)",
    )
    parser.add_argument(
        "--window",
        default=None,
        help=(
            "allocation candidate window: 'fleet_size', 'legacy', or a "
            "positive integer (default: the configured allocation.window)"
        ),
    )
    parser.add_argument(
        "--quantum",
        type=float,
        default=None,
        help=(
            "dispatch quantum in simulated seconds: hold a freed robot "
            "this long so several are allocated in one decision "
            "(default: the configured allocation.dispatch_quantum_s)"
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="allocation seed for stochastic strategies "
        "(default: the configured simulation.seed)",
    )
    parser.add_argument(
        "--dataset",
        default=None,
        metavar="DIR",
        help=(
            "record the decision and outcome dataset into DIR "
            "(Phase 8). Off by default: recording is observation only "
            "and changes no result, but it is not free"
        ),
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help=(
            "with --dataset, also write decisions.csv/outcomes.csv and "
            "Prolog bk.pl/exs.pl beside the Parquet tables"
        ),
    )
    parser.add_argument("--quiet", action="store_true")

    args = parser.parse_args()

    if args.orders < 0:
        parser.error("--orders must be 0 (all) or a positive number of orders")

    config = load_sim_config(args.config)

    # Override fleet.robot_count before the fleet is built, so this
    # can raise the count as well as lower it. Slicing the built fleet
    # could only ever shrink it, which made --robots above the
    # configured count silently do nothing.
    if args.robots:
        config = replace(config, robot_count=args.robots)

    if args.orders_file:
        # Resolved like the config's own orders.file: a bare name lives
        # next to the config. The manifest hashes config.orders_path, so
        # a recorded dataset names the file actually used.
        orders_file = Path(args.orders_file)

        if not orders_file.is_absolute() and not orders_file.exists():
            orders_file = config.config_path.parent / orders_file

        if not orders_file.is_file():
            parser.error(f"--orders-file {args.orders_file}: no such file")

        config = replace(config, orders_path=orders_file)

    warehouse = load_warehouse_xlsx(config.map_path)

    speed = args.speed or config.speed_mps

    robots = build_fleet(config, warehouse)

    for robot in robots:
        robot.speed_mps = speed

    # Spawn positions only. The bank is a shared resource: where a
    # robot finally parks is decided at return time by directed A*.
    spawn_stations = home_stations(config, warehouse)

    # The same rule as the ROS node's order_limit:=N: the first N
    # logical orders, never split, the order file untouched.
    available = load_orders_csv(config.orders_path, warehouse)
    orders = select_orders(available, args.orders)

    queue = OrderQueue(orders)

    # The candidate window is a scientific parameter: the command line
    # overrides the config, and both are printed and reported so a run
    # can never be read without knowing which window produced it.
    window = config.allocation_window if args.window is None else args.window
    width = WorkloadExecutor.resolve_window(window, len(robots))
    resolved_window = "available robots" if width is None else width

    quantum = (
        config.dispatch_quantum_s if args.quantum is None else args.quantum
    )

    # Every strategy takes a seed, so a stochastic one is reproducible
    # and a deterministic one simply ignores it.
    seed = config.seed if args.seed is None else args.seed

    # The manifest is built before the run, from the inputs only, so
    # the run_id names what is about to be measured rather than what
    # came out of it.
    manifest = None
    recorder = None

    if args.dataset:
        manifest = build_manifest(
            config,
            strategy=args.strategy,
            window=window,
            window_width=width,
            seed=seed,
            speed_mps=speed,
            dispatch_quantum_s=quantum,
            orders=orders,
            order_limit=args.orders,
            orders_available=len(available),
            repo=Path(__file__).resolve().parents[3],
        )

        recorder = RunRecorder(run_id=manifest["run_id"])

    print("=" * 70)
    print("WORKLOAD")
    print("=" * 70)
    print(f"  strategy   : {args.strategy}  (seed {seed})")
    print(f"  robots     : {len(robots)}")
    print(
        "  orders     : "
        + describe_order_selection(args.orders, available, orders, config.orders_path.name)
    )
    print(f"  speed      : {speed} m/s")
    print(f"  window     : {window} (W = {resolved_window})")
    print(
        f"  quantum    : {quantum} s"
        + (" (continuous dispatch)" if quantum <= 0 else " (batched dispatch)")
    )
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
        allocator=get_batch_strategy(args.strategy, seed=seed),
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
        on_battery_event=lambda event: print(battery_event_line(event), flush=True),
        window=window,
        dispatch_quantum_s=quantum,
        recorder=recorder,
        strategy_name=args.strategy,
    )

    print("  traffic    : local reactive grid traffic")

    if manifest is not None:
        print(f"  dataset    : {args.dataset}")
        print(f"  run_id     : {manifest['run_id']}")

        if manifest["git"]["dirty"]:
            print("  git        : DIRTY TREE -- this run is not reproducible")

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
    print(f"  reservation viol.: {totals['reservation_violations']}")
    print(f"  plan deferrals   : {result.plan_failures}")
    print(f"  peak in-progress : {result.peak_in_progress}")
    print(f"  max deferred-now : {result.max_deferred_now}")
    print()
    print("  planning CPU (wall clock, never simulated time):")
    for stage, spent in result.perf.items():
        print(f"    {stage:<14} : {spent:.3f} s")
    print(
        f"    slow dispatches: {result.slow_dispatches} "
        f"(>= {PerfBudget.THRESHOLD_S * 1000:.0f} ms)"
    )
    print(f"    worst dispatch : {result.worst_dispatch_s:.3f} s")
    print(f"    static failures reused: {result.static_route_reuses}")
    print(f"  wall clock       : {wall:.1f} s")

    print()
    print("=" * 70)
    for line in decision_report_lines(executor):
        print(line)

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

    if recorder is not None:
        run_dir = recorder.write(args.dataset, manifest)

        print("=" * 70)
        print("DATASET")
        print("=" * 70)
        print(f"  directory        : {run_dir}")
        print(f"  decision rows    : {len(recorder.decisions)}")
        print(f"  outcome rows     : {len(recorder.outcomes)}")

        # The run's results, for comparing runs without reading this
        # terminal output (docs/EXPERIMENT_PROTOCOL.md, "Core Metrics").
        from simulation_env.run_metrics import run_metrics, write_metrics

        metrics = write_metrics(
            run_dir,
            run_metrics(executor, strategy=args.strategy, seed=seed, wall_s=wall),
        )
        print(f"  metrics          : {metrics.name}")

        if args.export:
            from simulation_env.dataset_export import write_csv, write_prolog

            for path in [*write_csv(run_dir), *write_prolog(run_dir)]:
                print(f"  exported         : {path.name}")

        print()

    return 0 if completed == len(queue) else 1


if __name__ == "__main__":
    raise SystemExit(main())
