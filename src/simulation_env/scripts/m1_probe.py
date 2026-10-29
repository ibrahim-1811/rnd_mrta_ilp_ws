#!/usr/bin/env python3

"""M1 probe: one workload, recorded, plus congestion features and rollouts.

Builds the run exactly as run_workload.py does -- same config, fleet,
orders, window, quantum, strategy, manifest and run_id -- and writes the
same dataset to --out, so outcome_signal.py reads a probe like any run.
Two diagnostics can be added, neither of which changes a decision:

    --features     decision-time congestion and route-overlap features
                   for every candidate pair (simulation_env/congestion.py)
                   -> features.parquet
    --rollouts N   N evenly spaced decisions are replayed from a clone
                   taken just before them: the strategy's own choice and
                   up to --alternatives other orders for the same robot,
                   each rolled forward until the decision's window orders
                   are done and --horizon seconds have passed
                   (simulation_env/rollout.py) -> rollouts.csv
                   The first --verify clone points are resumed untouched
                   and must reproduce the run exactly.

A seed selects a workload as in run_experiment.py (2000 logical orders
generated from the config's map; --orders takes the first N). Seeds of
the evaluation and final roles are refused (docs/DECISIONS.md, "Seed
Split"): M1 must not look at them.

    python3 src/simulation_env/scripts/m1_probe.py --out runs/m1/probe_r30_q3 \\
        --robots 30 --orders 300 --workload-seed 101 --quantum 3 \\
        --features --rollouts 20
"""

import argparse
import csv
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = Path(__file__).resolve().parent

for package in ("mrta_core", "simulation_env", "task_management"):
    if str(ROOT / "src" / package) not in sys.path:
        sys.path.insert(0, str(ROOT / "src" / package))

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import generate_orders  # noqa: E402
from mrta_core.order_queue import OrderQueue  # noqa: E402
from mrta_core.orders import load_orders_csv, select_orders  # noqa: E402
from mrta_core.sim_config import load_sim_config  # noqa: E402
from mrta_core.warehouse_map import ZoneType  # noqa: E402
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx  # noqa: E402
from simulation_env.congestion import FeatureObserver  # noqa: E402
from simulation_env.dataset import RunRecorder, build_manifest  # noqa: E402
from simulation_env.fleet import build_fleet  # noqa: E402
from simulation_env.rollout import decision_ticks, probe, tables, verified  # noqa: E402
from simulation_env.run_metrics import run_metrics, write_metrics  # noqa: E402
from simulation_env.dataset import sha256_of  # noqa: E402
from simulation_env.seed_split import sealed_inputs, workload_seed_of  # noqa: E402
from simulation_env.workload_executor import WorkloadExecutor  # noqa: E402
from task_management.allocation_strategy import get_batch_strategy  # noqa: E402

CONFIG = ROOT / "src" / "simulation_env" / "config" / "warehouse.yaml"
WORKLOAD_ORDERS = 2000


def builder(args, orders_file):
    """A function that builds a fresh, identical executor each call."""

    config = load_sim_config(args.config)

    if args.robots:
        config = replace(config, robot_count=args.robots)

    if orders_file is not None:
        config = replace(config, orders_path=Path(orders_file))

    speed = config.speed_mps
    window = config.allocation_window if args.window is None else args.window
    quantum = config.dispatch_quantum_s if args.quantum is None else args.quantum
    seed = config.seed if args.seed is None else args.seed

    def build(observe=False):
        # Everything loaded afresh: orders carry their own status and are
        # changed by a run, so a second build must not share them.
        warehouse = load_warehouse_xlsx(config.map_path)
        available = load_orders_csv(config.orders_path, warehouse)
        orders = select_orders(available, args.orders)
        robots = build_fleet(config, warehouse)

        for robot in robots:
            robot.speed_mps = speed

        allocator = get_batch_strategy(args.strategy, seed=seed)

        manifest = build_manifest(
            config,
            strategy=args.strategy,
            strategy_identity=allocator.identity(),
            window=window,
            window_width=WorkloadExecutor.resolve_window(window, len(robots)),
            seed=seed,
            speed_mps=speed,
            dispatch_quantum_s=quantum,
            orders=orders,
            order_limit=args.orders,
            orders_available=len(available),
            repo=ROOT,
        )

        observer = FeatureObserver(allocator) if observe else None

        executor = WorkloadExecutor(
            warehouse=warehouse,
            robots=robots,
            queue=OrderQueue(orders),
            allocator=observer or allocator,
            resolution=config.resolution_m,
            speed_mps=speed,
            service_seconds={
                ZoneType.SHELF: config.shelf_pick_s,
                ZoneType.DROPOFF: config.dropoff_s,
            },
            progress_interval_s=0,
            battery=config.battery,
            window=window,
            dispatch_quantum_s=quantum,
            recorder=RunRecorder(run_id=manifest["run_id"]),
            strategy_name=args.strategy,
        )

        if observer is not None:
            observer.executor = executor

        executor.manifest = manifest
        executor.observer = observer

        return executor

    return build, seed


def write_rows(path, rows):
    if not rows:
        return

    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--strategy", default="nearest_astar")
    parser.add_argument("--robots", type=int, default=0)
    parser.add_argument("--orders", type=int, default=0, help="0 = the whole order file")
    parser.add_argument("--workload-seed", type=int, default=None, help="generate this seed's workload")
    parser.add_argument("--orders-file", default=None, help="or run this order file")
    parser.add_argument("--seed", type=int, default=None, help="strategy seed (default: the workload seed)")
    parser.add_argument("--quantum", type=float, default=None)
    parser.add_argument("--window", default=None)
    parser.add_argument("--features", action="store_true")
    parser.add_argument("--rollouts", type=int, default=0, help="decisions to branch")
    parser.add_argument("--alternatives", type=int, default=3)
    parser.add_argument("--horizon", type=float, default=120.0, help="s after the decision")
    parser.add_argument(
        "--verify", type=int, default=2,
        help="clone points checked for exactness; at least 1 with --rollouts",
    )
    args = parser.parse_args()

    if args.workload_seed is not None and args.orders_file:
        parser.error("--workload-seed and --orders-file exclude each other")

    if args.rollouts and args.verify < 1:
        parser.error("--rollouts needs --verify >= 1: rollout evidence stands on a verified clone")

    if args.seed is None and args.workload_seed is not None:
        args.seed = args.workload_seed

    # The effective inputs, resolved before anything is written: the
    # strategy seed the config would supply, and the workload actually
    # run, whose seed is known only from a generated workload's name.
    config = load_sim_config(args.config)
    strategy_seed = config.seed if args.seed is None else args.seed

    if args.workload_seed is not None:
        origin, orders_path = "generated", args.out / f"orders_s{args.workload_seed}.csv"
    elif args.orders_file:
        origin, orders_path = "orders_file", Path(args.orders_file)
    else:
        origin, orders_path = "config", config.orders_path

    sealed = sealed_inputs(strategy_seed=strategy_seed, orders_path=orders_path)

    if sealed:
        parser.error(f"{', '.join(sealed)}: M1 must not look at it")

    if args.out.exists() and any(args.out.iterdir()):
        parser.error(f"{args.out} is not empty; choose a new --out")

    args.out.mkdir(parents=True, exist_ok=True)

    orders_file = args.orders_file

    if args.workload_seed is not None:
        shelves, dropoffs = generate_orders.stations_of(args.config)
        orders_file = args.out / f"orders_s{args.workload_seed}.csv"
        orders_file.write_text(
            generate_orders.build(WORKLOAD_ORDERS, args.workload_seed, shelves, dropoffs)
        )

    build, seed = builder(args, orders_file)

    started = time.perf_counter()
    executor = build(observe=args.features)
    executor.run()
    wall = time.perf_counter() - started

    run_dir = executor.recorder.write(args.out, executor.manifest)
    write_metrics(run_dir, run_metrics(executor, strategy=args.strategy, seed=seed, wall_s=wall))

    report = {
        "args": {k: str(v) for k, v in vars(args).items()},
        "run_id": executor.manifest["run_id"],
        # Where the workload came from, kept apart from the strategy's
        # random seed. A config or hand-made file has no known seed.
        "workload": {
            "origin": origin,
            "path": str(orders_path),
            "sha256": sha256_of(orders_path),
            "seed": workload_seed_of(orders_path),
        },
        "strategy_seed": strategy_seed,
        "decisions": executor.result.decisions,
        "orders": len(executor.recorder.outcomes),
        "run_wall_s": wall,
    }

    if executor.observer is not None:
        import pandas as pd

        pd.DataFrame(executor.observer.rows).to_parquet(args.out / "features.parquet")
        report["feature_rows"] = len(executor.observer.rows)

    if args.rollouts:
        started = time.perf_counter()
        first, made_in = decision_ticks(lambda: build())
        candidates = sorted(made_in)
        step = max(1, len(candidates) // args.rollouts)
        chosen = candidates[step // 2 :: step][: args.rollouts]

        rows, checks = probe(
            lambda: build(),
            chosen,
            made_in,
            alternatives=args.alternatives,
            horizon_s=args.horizon,
            verify=args.verify,
            baseline=tables(first),
        )

        write_rows(args.out / "rollouts.csv", rows)
        report["rollout_decisions"] = chosen
        report["rollout_branches"] = len(rows)
        report["clone_checks"] = checks
        report["clone_verified"] = verified(checks)
        report["rollout_branches_unavailable"] = (
            len(chosen) * (args.alternatives + 1) - len(rows)
        )
        report["rollout_wall_s"] = time.perf_counter() - started

    (args.out / "probe.json").write_text(json.dumps(report, indent=2, default=str))

    print(json.dumps({k: v for k, v in report.items() if k != "args"}, indent=2, default=str))

    if args.rollouts and not report["clone_verified"]:
        # Failed, or nothing could be checked: either way the rollouts
        # are not evidence.
        print("CLONE NOT VERIFIED: no clone point reproduced the run")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
