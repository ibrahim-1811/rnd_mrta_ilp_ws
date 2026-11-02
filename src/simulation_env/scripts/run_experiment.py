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

Integrity (E1). runs/<name>/experiment.json records the grid, the
workload settings and the git state; compare_paired.py refuses an
experiment whose runs do not match that grid or do not share one map
and simulator setting (summary.csv carries each run's
settings_sha256). An existing runs/<name> is never overwritten:

    --resume   run only what is missing or failed; the grid and the
               workload files must match what experiment.json recorded
    --force    start again; the old directory is kept, renamed to
               runs/<name>.replaced-<UTC time>

The dispatch quantum and the candidate window come from the one config
unless the experiment overrides them for every run:

    --quantum 3        the primary learning / evaluation condition
    --quantum 0        continuous dispatch, the secondary comparison
    --window W         fleet_size, legacy or a positive integer

Each run's run.json records the effective values and they are part of
the settings signature, so two conditions can never be paired; run them
as separate experiments (EXPERIMENT_PROTOCOL.md, "Dispatch Conditions").

--purpose training | validation | evaluation | final declares which
seed role the experiment serves, and refuses any seed outside it; with
no purpose, the reserved final seeds are refused (docs/DECISIONS.md,
"Seed Split"). Every strategy's identity hash (its parameters, solver
and artifacts) is part of the spec, so --resume refuses to mix two
versions of one strategy.

A run that fails leaves failure.json with its classified kind instead
of metrics.json (docs/DECISIONS.md, "Run Failure Classification").

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
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import generate_orders


ROOT = Path(__file__).resolve().parents[3]
RUN_WORKLOAD = ROOT / "src" / "simulation_env" / "scripts" / "run_workload.py"
PACKAGES = ("mrta_core", "simulation_env", "task_management")

for package in PACKAGES:
    if str(ROOT / "src" / package) not in sys.path:
        sys.path.insert(0, str(ROOT / "src" / package))

from simulation_env.dataset import git_state  # noqa: E402
from simulation_env.failures import read_failure  # noqa: E402
from simulation_env.paired import settings_signature  # noqa: E402
from simulation_env.seed_split import ROLES, SUBROLES, SeedLeakageError, check_purpose  # noqa: E402
from simulation_env.workload_executor import WorkloadExecutor  # noqa: E402
from task_management.allocation_strategy import (  # noqa: E402
    get_batch_strategy,
    identity_sha256,
)

EXPERIMENT_FILE = "experiment.json"

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

    Rewriting an existing experiment's files is harmless while they come
    out the same: same seed, map and size, same bytes. If a recorded
    workload would now come out different (the map changed), the runs
    already made used another workload, and this refuses.
    """

    folder = out / "workloads"
    folder.mkdir(parents=True, exist_ok=True)

    index_path = folder / "workloads.json"
    recorded = json.loads(index_path.read_text()) if index_path.exists() else {}

    shelves, dropoffs = generate_orders.stations_of(generate_orders.DEFAULT_CONFIG)
    paths, index = {}, dict(recorded)

    for seed in seeds:
        text = generate_orders.build(orders, seed, shelves, dropoffs)
        digest = sha256(text.encode()).hexdigest()
        before = recorded.get(str(seed))

        if before is not None and before["sha256"] != digest:
            raise SystemExit(
                f"{index_path}: seed {seed} was recorded as {before['sha256'][:12]}, "
                f"now generates {digest[:12]}; the map or workload size changed"
            )

        path = folder / f"orders_s{seed}.csv"
        path.write_text(text)

        paths[seed] = path
        index[str(seed)] = {
            "file": path.name,
            "sha256": digest,
            "orders": orders,
            "line_items": len(text.strip().splitlines()) - 1,
        }

    index_path.write_text(json.dumps(index, indent=2))

    return paths


def run_dir_of(out, strategy, robots, seed) -> Path:
    return out / strategy / f"r{robots}_s{seed}"


def finished(run_dir) -> bool:
    """A run that left both its manifest and its metrics."""

    return (run_dir / "metrics.json").exists() and (run_dir / "run.json").exists()


def prepare(out, spec, resume, force) -> str | None:
    """Guard runs/<name> against overwrites; None when clear to run.

    Returns the reason to refuse, or None. With --force the old
    directory is renamed, never deleted.
    """

    if not out.exists() or not any(out.iterdir()):
        return None

    if force:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        kept = out.with_name(f"{out.name}.replaced-{stamp}")
        out.rename(kept)
        print(f"  --force: kept the old experiment as {kept}")
        return None

    if not resume:
        return (
            f"{out} already holds an experiment; use --resume to finish it "
            "or --force to start again (the old one is kept)"
        )

    path = out / EXPERIMENT_FILE

    if not path.exists():
        return f"{out} has no {EXPERIMENT_FILE}, so it cannot be resumed; use --force"

    recorded = json.loads(path.read_text())["spec"]

    # Compared key by key, so a spec field added later (quantum, window,
    # purpose, strategy identities) reads as "changed" against an older
    # experiment rather than silently matching.
    changed = sorted(k for k in set(recorded) | set(spec) if recorded.get(k) != spec.get(k))

    if changed:
        return f"--resume with a different grid: {', '.join(changed)} changed"

    return None


def strategy_identities(strategies) -> dict:
    """strategy -> strategy_sha256, as each run's run.json will record it."""

    return {name: identity_sha256(get_batch_strategy(name).identity()) for name in strategies}


def workload_command(
    run_dir, strategy, robots, seed, orders, export,
    orders_file=None, quantum=None, window=None, purpose=None, features=False,
    teacher_panel=False,
) -> list:
    """The run_workload.py command line of one run.

    ``quantum`` and ``window`` override the one config's dispatch
    quantum and candidate window for this experiment; None keeps the
    config's value. Either way run.json records the effective value.
    """

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

    if quantum is not None:
        cmd += ["--quantum", str(quantum)]

    if window is not None:
        cmd += ["--window", str(window)]

    if purpose is not None:
        cmd += ["--purpose", purpose]

    if orders_file is not None:
        cmd += ["--orders-file", str(orders_file)]

    if export:
        cmd.append("--export")

    if features:
        cmd.append("--features")

    if teacher_panel:
        cmd.append("--teacher-panel")

    return cmd


def run_one(
    out, strategy, robots, seed, orders, export,
    orders_file=None, quantum=None, window=None, purpose=None, features=False,
    teacher_panel=False,
):
    run_dir = run_dir_of(out, strategy, robots, seed)
    run_dir.mkdir(parents=True, exist_ok=True)

    cmd = workload_command(
        run_dir, strategy, robots, seed, orders, export, orders_file, quantum, window, purpose,
        features, teacher_panel,
    )

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
    failure = read_failure(run_dir) if code else None
    status = "ok " if code == 0 else f"FAILED[{failure['kind']}]" if failure else "FAILED"
    print(f"  {status} {strategy:<18} robots {robots:<4} seed {seed:<3} {took:6.1f} s  {run_dir}", flush=True)

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
        "--features", action="store_true", help="also write the congestion features of each run"
    )
    parser.add_argument(
        "--teacher-panel", action="store_true", help="also write each teacher's picks of each run"
    )
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
    parser.add_argument(
        "--quantum",
        type=float,
        default=None,
        help="dispatch quantum in simulated s for every run (default: the "
        "config's); 0 = continuous dispatch, 3 = the primary condition",
    )
    parser.add_argument(
        "--window",
        default=None,
        help="candidate window for every run: fleet_size, legacy or a "
        "positive integer (default: the config's)",
    )
    parser.add_argument(
        "--purpose",
        choices=sorted({*ROLES, *SUBROLES}),
        default=None,
        help="the seed role this experiment serves; every seed must belong "
        "to it (docs/DECISIONS.md, 'Seed Split'). Omitted: development, "
        "any seed outside the reserved final set",
    )
    parser.add_argument("--resume", action="store_true", help="run only what is missing or failed")
    parser.add_argument("--force", action="store_true", help="start again; the old directory is kept, renamed")
    args = parser.parse_args()

    if args.resume and args.force:
        parser.error("--resume and --force exclude each other")

    if args.quantum is not None and args.quantum < 0:
        parser.error("--quantum must be >= 0")

    if args.window is not None:
        try:
            WorkloadExecutor.resolve_window(args.window, 1)
        except ValueError as error:
            parser.error(f"--window {args.window}: {error}")

    try:
        check_purpose(args.seeds, args.purpose)
    except SeedLeakageError as error:
        parser.error(str(error))

    try:
        identities = strategy_identities(args.strategies)
    except KeyError as error:
        parser.error(str(error))

    out = ROOT / args.out / args.name
    paired = args.workloads == "seeded"
    grid = list(runs_for(args.strategies, args.robots, args.seeds, paired))

    # What defines the experiment; --resume must find exactly this.
    spec = {
        "strategies": list(args.strategies),
        "robots": list(args.robots),
        "seeds": list(args.seeds),
        "orders": args.orders,
        "workloads": args.workloads,
        "workload_orders": args.workload_orders if paired else None,
        "quantum": args.quantum,
        "window": args.window,
        "purpose": args.purpose,
        "features": args.features,
        "teacher_panel": args.teacher_panel,
        "strategy_sha256": identities,
    }

    refusal = prepare(out, spec, args.resume, args.force)

    if refusal is not None:
        print(f"refused: {refusal}")
        return 2

    out.mkdir(parents=True, exist_ok=True)
    record_path = out / EXPERIMENT_FILE
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if record_path.exists():
        record = json.loads(record_path.read_text())
        record.setdefault("resumed", []).append({"at": now, "git": git_state(ROOT)})
    else:
        record = {
            "name": args.name,
            "spec": spec,
            "runs": [list(run) for run in grid],
            "created": now,
            "git": git_state(ROOT),
        }

    record_path.write_text(json.dumps(record, indent=2))

    files = write_workloads(out, args.seeds, args.workload_orders) if paired else {}
    pending = [run for run in grid if not finished(run_dir_of(out, *run))]

    print(
        f"EXPERIMENT {args.name}: {len(grid)} runs, {len(grid) - len(pending)} already "
        f"done, {len(pending)} to run, {args.jobs} at a time -> {out}"
    )

    if paired:
        print(f"  workloads: {len(files)} seeds x {args.workload_orders} orders in {out / 'workloads'}")

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        done = dict(
            zip(
                pending,
                pool.map(
                    lambda run: run_one(
                        out, *run, args.orders, args.export, files.get(run[2]),
                        quantum=args.quantum, window=args.window,
                        purpose=args.purpose, features=args.features,
                        teacher_panel=args.teacher_panel,
                    ),
                    pending,
                ),
            )
        )

    rows = []

    for run in grid:
        run_dir = run_dir_of(out, *run)

        if not finished(run_dir):
            code = done[run][1] if run in done else "?"
            failure = read_failure(run_dir)
            why = f"failed: {failure['kind']}; " if failure else ""
            print(f"  no metrics from {run_dir} (exit {code}); {why}see run.log")
            continue

        # What each run really executed, from its own manifest, so
        # compare_paired.py can check the pairs and the experiment.
        manifest = json.loads((run_dir / "run.json").read_text())
        where = run_dir.relative_to(ROOT) if run_dir.is_relative_to(ROOT) else run_dir

        rows.append(
            json.loads((run_dir / "metrics.json").read_text())
            | {
                "workload": args.workloads,
                "orders_sha256": manifest["orders_sha256"],
                "line_items": manifest["line_items"],
                "window_width": manifest["window_width"],
                "settings_sha256": settings_signature(manifest),
                # Absent from a run made before strategy identities
                # existed; compare_paired refuses such a run.
                "strategy_sha256": manifest.get("strategy_sha256", ""),
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
