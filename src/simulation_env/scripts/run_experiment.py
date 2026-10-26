#!/usr/bin/env python3

"""Run an experiment grid headless: strategies x fleet sizes x seeds.

Every run is an ordinary run_workload.py run with --dataset, so each one
leaves its own directory:

    runs/<name>/<strategy>/r<robots>_s<seed>/
        run.log             the run's full terminal output
        run.json            provenance: map, config, orders, code
        metrics.json        the run's results (EXPERIMENT_PROTOCOL.md)
        decisions.parquet   every scored robot-order pair, decision time
        outcomes.parquet    what happened to each order
        decisions.csv, outcomes.csv, bk.pl, exs.pl   with --export

and the experiment gets one table of every run's metrics:

    runs/<name>/summary.csv

A seed selects a workload (docs/DECISIONS.md, "Workload Seeds and
Paired Comparison"). For each seed, generate_orders.py writes one
deterministic order file from the current map,

    runs/<name>/workloads/orders_s<seed>.csv
    runs/<name>/workloads/workloads.json    seed -> file, sha256, size

and every strategy runs that same file at every fleet size, so the
runs of one seed are paired. A stochastic strategy takes the same seed
for its own choices. --orders N runs the first N orders of each file;
a generated file's first N orders are exactly what generating N would
give.

    python3 src/simulation_env/scripts/run_experiment.py --name main \\
        --robots 10 30 50 70 100 --seeds $(seq 1 30)

--workloads reference runs the committed order file instead, as every
experiment did before paired seeds: a deterministic strategy once, with
the first seed, and only a stochastic one over every seed.

Then plot it with plot_experiment.py and compare strategies with
compare_paired.py.
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path

import generate_orders


ROOT = Path(__file__).resolve().parents[3]
RUN_WORKLOAD = ROOT / "src" / "simulation_env" / "scripts" / "run_workload.py"
PACKAGES = ("mrta_core", "simulation_env", "task_management")

# A strategy whose choices depend on its seed. On the reference
# workload every other strategy is deterministic and needs one run per
# fleet size.
STOCHASTIC = {"random"}

# Logical orders in each generated workload, the size of the committed
# reference file (seed 42).
WORKLOAD_ORDERS = 2000


def runs_for(strategies, robots, seeds, paired=True):
    for strategy in strategies:
        for count in robots:
            if paired or strategy in STOCHASTIC:
                chosen = seeds
            else:
                chosen = seeds[:1]

            for seed in chosen:
                yield strategy, count, seed


def write_workloads(out, seeds, orders=WORKLOAD_ORDERS) -> dict:
    """One order file per seed, from the current map; seed -> path.

    Rewriting an existing experiment's files is harmless: same seed and
    map, same bytes.
    """

    folder = out / "workloads"
    folder.mkdir(parents=True, exist_ok=True)

    shelves, dropoffs = generate_orders.stations_of(generate_orders.DEFAULT_CONFIG)
    paths, index = {}, {}

    for seed in seeds:
        text = generate_orders.build(orders, seed, shelves, dropoffs)
        path = folder / f"orders_s{seed}.csv"
        path.write_text(text)

        paths[seed] = path
        index[str(seed)] = {
            "file": path.name,
            "sha256": sha256(text.encode()).hexdigest(),
            "orders": orders,
            "line_items": len(text.strip().splitlines()) - 1,
        }

    (folder / "workloads.json").write_text(json.dumps(index, indent=2))

    return paths


def run_one(out, strategy, robots, seed, orders, export, orders_file=None):
    run_dir = out / strategy / f"r{robots}_s{seed}"
    run_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(RUN_WORKLOAD),
        "--strategy", strategy,
        "--robots", str(robots),
        "--seed", str(seed),
        "--orders", str(orders),
        "--quiet",
        "--progress", "0",
        "--dataset", str(run_dir),
    ]

    if orders_file is not None:
        cmd += ["--orders-file", str(orders_file)]

    if export:
        cmd.append("--export")

    started = time.perf_counter()

    # The three source packages, so the runs need no PYTHONPATH set.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "src" / package) for package in PACKAGES]
        + [env.get("PYTHONPATH", "")]
    )

    with open(run_dir / "run.log", "w") as log:
        code = subprocess.run(
            cmd, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT, env=env
        ).returncode

    took = time.perf_counter() - started
    print(f"  {'ok ' if code == 0 else 'FAILED'} {strategy:<18} robots {robots:<4} seed {seed:<3} {took:6.1f} s  {run_dir}", flush=True)

    return run_dir, code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", default="experiment", help="runs/<name>/")
    parser.add_argument("--out", default="runs", help="parent directory")
    parser.add_argument("--strategies", nargs="+", default=["nearest_astar", "nearest_manhattan", "random"])
    parser.add_argument("--robots", nargs="+", type=int, default=[30, 40, 50])
    parser.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3])
    parser.add_argument("--orders", type=int, default=0, help="0 = the whole order file")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    parser.add_argument("--export", action="store_true", help="also write CSV and Prolog")
    parser.add_argument(
        "--workload-orders",
        type=int,
        default=WORKLOAD_ORDERS,
        help="logical orders in each generated workload (seeded only)",
    )
    parser.add_argument(
        "--workloads",
        choices=("seeded", "reference"),
        default="seeded",
        help="seeded: one generated order file per seed, shared by every "
        "strategy (default); reference: the committed order file",
    )
    args = parser.parse_args()

    out = ROOT / args.out / args.name
    paired = args.workloads == "seeded"
    grid = list(runs_for(args.strategies, args.robots, args.seeds, paired))
    files = write_workloads(out, args.seeds, args.workload_orders) if paired else {}

    print(f"EXPERIMENT {args.name}: {len(grid)} runs, {args.jobs} at a time -> {out}")

    if paired:
        print(f"  workloads: {len(files)} seeds x {args.workload_orders} orders in {out / 'workloads'}")

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        done = list(
            pool.map(
                lambda spec: run_one(
                    out, *spec, args.orders, args.export, files.get(spec[2])
                ),
                grid,
            )
        )

    rows = []

    for run_dir, code in done:
        path = run_dir / "metrics.json"

        if code != 0 or not path.exists():
            print(f"  no metrics from {run_dir} (exit {code}); see run.log")
            continue

        # The workload each run really executed, from its own manifest,
        # so compare_paired.py can check that a pair shares one.
        manifest = json.loads((run_dir / "run.json").read_text())
        where = run_dir.relative_to(ROOT) if run_dir.is_relative_to(ROOT) else run_dir

        rows.append(
            json.loads(path.read_text())
            | {
                "workload": args.workloads,
                "orders_sha256": manifest["orders_sha256"],
                "run_dir": str(where),
            }
        )

    if rows:
        summary = out / "summary.csv"

        with open(summary, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

        print(f"SUMMARY {summary} ({len(rows)} runs)")

    return 0 if len(rows) == len(grid) else 1


if __name__ == "__main__":
    raise SystemExit(main())
