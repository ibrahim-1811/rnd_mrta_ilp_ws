"""Explicit observed single-run mode. Original scientific modules stay unchanged.

This duplicates only the verified CLI's setup/result orchestration. Stdlib and
scientific dependencies suffice in the scientific interpreter; no ASGI imports.
"""

import argparse
from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[5]
RUNNER_SHA256 = "11b49cf70b5a1c07a5df97c1771547b46b095332b2a83614bbf868df5cd8427a"


def source_matches():
    return (
        hashlib.sha256(
            (REPO / "src/simulation_env/scripts/run_workload.py").read_bytes()
        ).hexdigest()
        == RUNNER_SHA256
    )


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--config", default=str(REPO / "src/simulation_env/config/warehouse.yaml")
    )
    p.add_argument("--strategy", default="nearest_astar")
    p.add_argument("--robots", type=int, default=0)
    p.add_argument("--orders", type=int, default=0)
    p.add_argument("--orders-file")
    p.add_argument("--seed", type=int)
    p.add_argument("--window")
    p.add_argument("--quantum", type=float)
    p.add_argument("--speed", type=float, default=0)
    p.add_argument("--progress", type=float, default=10)
    p.add_argument("--dataset", required=True)
    p.add_argument("--export", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--telemetry-output", required=True)
    p.add_argument("--job-id", required=True)
    p.add_argument("--source-fingerprint", required=True)
    p.add_argument("--sample-interval", type=float, default=1)
    return p


def run(args):
    from mrta_core.order_queue import OrderQueue
    from mrta_core.orders import load_orders_csv, select_orders
    from mrta_core.sim_config import load_sim_config
    from mrta_core.warehouse_map import ZoneType
    from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
    from simulation_env.dataset import RunRecorder, build_manifest
    from simulation_env.fleet import build_fleet
    from simulation_env.run_metrics import run_metrics, write_metrics
    from simulation_env.workload_executor import WorkloadExecutor, battery_event_line
    from task_management.allocation_strategy import get_batch_strategy
    from control_center.services.observation import TraceRecorder

    if not source_matches():
        raise ValueError(
            "Original runner changed since observation parity; use original CLI mode"
        )
    if args.orders < 0:
        raise ValueError("orders must be nonnegative")
    config = load_sim_config(args.config)
    if args.robots:
        config = replace(config, robot_count=args.robots)
    if args.orders_file:
        path = Path(args.orders_file)
        if not path.is_absolute() and not path.exists():
            path = config.config_path.parent / path
        if not path.is_file():
            raise ValueError("orders-file does not exist")
        config = replace(config, orders_path=path)
    warehouse = load_warehouse_xlsx(config.map_path)
    speed = args.speed or config.speed_mps
    robots = build_fleet(config, warehouse)
    for robot in robots:
        robot.speed_mps = speed
    available = load_orders_csv(config.orders_path, warehouse)
    orders = select_orders(available, args.orders)
    queue = OrderQueue(orders)
    window = config.allocation_window if args.window is None else args.window
    width = WorkloadExecutor.resolve_window(window, len(robots))
    quantum = config.dispatch_quantum_s if args.quantum is None else args.quantum
    seed = config.seed if args.seed is None else args.seed
    allocator = get_batch_strategy(args.strategy, seed=seed)
    manifest = build_manifest(
        config,
        strategy=args.strategy,
        strategy_identity=allocator.identity(),
        window=window,
        window_width=width,
        seed=seed,
        speed_mps=speed,
        dispatch_quantum_s=quantum,
        orders=orders,
        order_limit=args.orders,
        orders_available=len(available),
        repo=REPO,
    )
    recorder = RunRecorder(run_id=manifest["run_id"])

    def announce(record):
        if not args.quiet:
            print(
                f"  t={record.sim_time:8.3f} {record.robot_id} -> {record.order_id} C_static={record.static_cost_m:.1f} m",
                flush=True,
            )

    def lines(record):
        for line in record.lines():
            print(line, flush=True)

    trace = None

    def observe_tick(sim_time, batch):
        if trace is not None:
            trace.observer.on_tick(sim_time, batch)

    started = time.perf_counter()
    executor = WorkloadExecutor(
        warehouse=warehouse,
        robots=robots,
        queue=queue,
        allocator=allocator,
        resolution=config.resolution_m,
        speed_mps=speed,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
        on_dispatch=announce,
        on_progress=lines,
        progress_interval_s=args.progress,
        on_perf=lines,
        battery=config.battery,
        on_battery_event=lambda event: print(battery_event_line(event), flush=True),
        window=window,
        dispatch_quantum_s=quantum,
        recorder=recorder,
        strategy_name=args.strategy,
        on_tick=observe_tick,
    )
    try:
        try:
            trace = TraceRecorder(
                args.telemetry_output,
                args.job_id,
                args.source_fingerprint,
                executor=executor,
                interval_s=args.sample_interval,
            )
            trace.observer.capture(force=True)
        except Exception as exc:
            print(f"Telemetry unavailable: {type(exc).__name__}: {exc}", flush=True)
        try:
            result = executor.run()
        except RuntimeError as exc:
            print(f"STALLED: {exc}", flush=True)
            return 1
        wall = time.perf_counter() - started
        output = recorder.write(args.dataset, manifest)
        write_metrics(
            output,
            run_metrics(executor, strategy=args.strategy, seed=seed, wall_s=wall),
        )
        if args.export:
            from simulation_env.dataset_export import write_csv, write_prolog

            write_csv(output)
            write_prolog(output)
        complete = len(result.completions) == len(queue)
        print(
            f"RESULT completed={len(result.completions)}/{len(queue)} dataset={output} run_id={manifest['run_id']}",
            flush=True,
        )
        if trace is not None:
            trace.close(complete=complete)
        return 0 if complete else 1
    finally:
        if trace is not None and not trace.done.is_set():
            trace.close(complete=False)
        if trace is not None and trace.error:
            print(f"Telemetry unavailable/partial: {trace.error}", flush=True)


def main():
    return run(parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
