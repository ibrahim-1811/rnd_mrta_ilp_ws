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

Seeds only matter to a stochastic strategy. A deterministic one is run
once, with the first seed, because more seeds would repeat the same run.

    python3 src/simulation_env/scripts/run_experiment.py --name fleet_sweep \\
        --robots 30 40 50 --seeds 1 2 3 --export

Then plot it with plot_experiment.py.
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUN_WORKLOAD = ROOT / "src" / "simulation_env" / "scripts" / "run_workload.py"
PACKAGES = ("mrta_core", "simulation_env", "task_management")

# A strategy whose choices depend on its seed. Every other strategy is
# deterministic and needs one run per fleet size.
STOCHASTIC = {"random"}


def runs_for(strategies, robots, seeds):
    for strategy in strategies:
        for count in robots:
            for seed in seeds if strategy in STOCHASTIC else seeds[:1]:
                yield strategy, count, seed


def run_one(out, strategy, robots, seed, orders, export):
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
    args = parser.parse_args()

    out = ROOT / args.out / args.name
    grid = list(runs_for(args.strategies, args.robots, args.seeds))

    print(f"EXPERIMENT {args.name}: {len(grid)} runs, {args.jobs} at a time -> {out}")

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        done = list(
            pool.map(
                lambda spec: run_one(out, *spec, args.orders, args.export),
                grid,
            )
        )

    rows = []

    for run_dir, code in done:
        path = run_dir / "metrics.json"

        if code != 0 or not path.exists():
            print(f"  no metrics from {run_dir} (exit {code}); see run.log")
            continue

        rows.append(json.loads(path.read_text()) | {"run_dir": str(run_dir.relative_to(ROOT))})

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
