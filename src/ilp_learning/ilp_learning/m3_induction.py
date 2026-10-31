"""Reproduce the M3 induction evidence from a clean commit, end to end.

    pixi run m3-induction --out runs/m3_evidence

Arm A on induction seeds 104-110 (.scratch/m3-popper-learner/preregistration.md):
teacher runs, full-data and leave-one-seed-out training, closed-loop runs of each
full-data rule, fidelity, then the G5 limits and the G8 bounds. Finished steps are
skipped, so a stopped run can be started again. This module has no seed argument:
validation, backup and sealed seeds cannot be reached from here.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
import statistics
import subprocess
import sys
from typing import Sequence

from ilp_learning.fidelity import evaluate_runs
from ilp_learning.rule_artifact import load_rule_artifact
from ilp_learning.rule_compiler import RuleProgram, parse_clause
from simulation_env.seed_split import TRAINING_INDUCTION


SEEDS = tuple(TRAINING_INDUCTION)
FLEETS = (30, 70, 100)
QUANTA = {"q3": 3.0, "q0": 0.0}  # q3 is the primary condition
ORDERS = 300
WINDOW = "fleet_size"
TEACHER = "nearest_astar"
FULL = "full"
METRICS = ("order_makespan_s", "mean_completion_time_s")
HAND_WRITTEN_R_AND_O = "assign(A,B,C):- best_cost_for_order(A,B,C),nearest_static(A,B,C)."
T_90_4DF_OVER_ROOT_5 = 0.953  # half-width factor of the G8 90% CI with 5 pairs
REPO = Path(__file__).resolve().parents[3]


def conditions() -> list[tuple[str, int]]:
    return [(q, robots) for q in QUANTA for robots in FLEETS]


def teacher_run(base: Path, q: str, robots: int, seed: int) -> Path:
    return base / q / TEACHER / f"r{robots}_s{seed}"


def rule_dir(base: Path, q: str, robots: int, fold: str) -> Path:
    return base / "rules" / q / f"r{robots}" / fold


def folds() -> list[str]:
    return [FULL, *(f"loso_{seed}" for seed in SEEDS)]


def training_seeds(fold: str) -> list[int]:
    if fold == FULL:
        return list(SEEDS)
    held_out = int(fold.removeprefix("loso_"))
    return [seed for seed in SEEDS if seed != held_out]


def _call(command: Sequence[str], log: Path) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as stream:
        done = subprocess.run(command, cwd=REPO, stdout=stream, stderr=subprocess.STDOUT)
    if done.returncode != 0:
        raise RuntimeError(f"{command[:4]} failed with {done.returncode}; see {log}")


def run_teachers(base: Path, jobs: int) -> None:
    for q, quantum in QUANTA.items():
        _call(
            [
                sys.executable, "src/simulation_env/scripts/run_experiment.py",
                "--name", q, "--out", str(base), "--strategies", TEACHER,
                "--robots", *map(str, FLEETS), "--seeds", *map(str, SEEDS),
                "--orders", str(ORDERS), "--workloads", "seeded", "--workload-orders", str(ORDERS),
                "--quantum", str(quantum), "--window", WINDOW, "--purpose", "induction",
                "--jobs", str(jobs), "--resume",
            ],
            base / "logs" / f"teacher_{q}.log",
        )


def train_one(base: Path, q: str, robots: int, fold: str) -> None:
    out = rule_dir(base, q, robots, fold)
    if (out / "rule_artifact.json").exists():
        return
    runs = [str(teacher_run(base, q, robots, seed)) for seed in training_seeds(fold)]
    _call(
        [sys.executable, "-m", "ilp_learning.train_rules", *runs, "--out", str(out)],
        base / "logs" / f"train_{q}_r{robots}_{fold}.log",
    )


def closed_loop_one(base: Path, q: str, robots: int, seed: int) -> None:
    out = base / "closed_loop" / q / f"r{robots}_s{seed}"
    if (out / "metrics.json").exists():
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    _call(
        [
            sys.executable, "src/simulation_env/scripts/run_workload.py",
            "--strategy", "ilp",
            "--rule-artifact", str(rule_dir(base, q, robots, FULL) / "rule_artifact.json"),
            "--robots", str(robots), "--seed", str(seed), "--orders", str(ORDERS),
            "--quiet", "--progress", "0", "--dataset", str(out),
            "--quantum", str(QUANTA[q]), "--window", WINDOW, "--purpose", "induction",
            "--orders-file", str(base / q / "workloads" / f"orders_s{seed}.csv"),
        ],
        base / "logs" / f"cl_{q}_r{robots}_s{seed}.log",
    )


def _fidelity_job(job: tuple[str, str, int, str]) -> tuple[str, dict]:
    base, q, robots, kind = job
    root = Path(base)
    program = (
        RuleProgram((parse_clause(HAND_WRITTEN_R_AND_O),))
        if kind == "hand"
        else load_rule_artifact(rule_dir(root, q, robots, kind) / "rule_artifact.json")[0]
    )
    runs = [str(teacher_run(root, q, robots, seed)) for seed in SEEDS]
    return f"{q}/{robots}/{kind}", evaluate_runs(runs, program)


def fidelity(base: Path, jobs: int) -> dict:
    work = [(str(base), q, r, kind) for q, r in conditions() for kind in ("hand", *folds())]
    with ProcessPoolExecutor(jobs) as pool:
        return dict(pool.map(_fidelity_job, work))


def _pooled_key(entry: dict, seeds: Sequence[int]) -> float:
    agreed = teacher = 0
    for seed in seeds:
        counts = entry["per_seed_results"][str(seed)]["scopes"]["choice_bearing"]
        agreed += counts["agreed"]
        teacher += counts["teacher_pairs"]
    return agreed / teacher


def g5_limits(results: dict) -> dict:
    """Limit = max(worst fold drop, 2 x SD of held-out fidelity), per condition."""
    limits = {}
    for q, robots in conditions():
        drops, held = [], []
        for seed in SEEDS:
            entry = results[f"{q}/{robots}/loso_{seed}"]
            out_of_fold = entry["per_seed"][str(seed)]["choice_bearing"]["frozen_key"]
            held.append(out_of_fold)
            drops.append(_pooled_key(entry, [s for s in SEEDS if s != seed]) - out_of_fold)
        worst, spread = max(drops), 2 * statistics.stdev(held)
        limits[f"{q}/{robots}"] = {"worst_drop": worst, "two_sd": spread, "limit": max(worst, spread)}
    return limits


def g8_bounds(base: Path) -> dict:
    """Delta = between-seed CV of the teacher; paired SD of log(ILP / teacher)."""
    bounds = {}
    for q, robots in conditions():
        with (base / q / "summary.csv").open(encoding="utf-8") as stream:
            rows = {
                int(row["seed"]): row
                for row in csv.DictReader(stream)
                if int(row["robots"]) == robots
            }
        for metric in METRICS:
            teacher = [float(rows[seed][metric]) for seed in SEEDS]
            ratios = []
            for seed, value in zip(SEEDS, teacher):
                path = base / "closed_loop" / q / f"r{robots}_s{seed}" / "metrics.json"
                ratios.append(math.log(json.loads(path.read_text(encoding="utf-8"))[metric] / value))
            delta = statistics.stdev(teacher) / statistics.mean(teacher)
            spread = statistics.stdev(ratios)
            bounds[f"{q}/{robots}/{metric}"] = {
                "delta": delta,
                "paired_sd": spread,
                "mean_log_ratio": statistics.mean(ratios),
                "max_sd_for_equivalence": delta / T_90_4DF_OVER_ROOT_5,
                "can_show_equivalence": spread <= delta / T_90_4DF_OVER_ROOT_5,
            }
    return bounds


def rule_texts(base: Path) -> dict:
    texts = {}
    for q, robots in conditions():
        for fold in folds():
            _, artifact = load_rule_artifact(rule_dir(base, q, robots, fold) / "rule_artifact.json")
            texts[f"{q}/{robots}/{fold}"] = [entry["clause"] for entry in artifact["clauses"]]
    return texts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="runs/m3_evidence", help="output directory")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    args = parser.parse_args()
    base = (REPO / args.out).resolve()
    base.mkdir(parents=True, exist_ok=True)

    print("teachers", flush=True)
    run_teachers(base, args.jobs)

    print("training", flush=True)
    with ThreadPoolExecutor(args.jobs) as pool:
        training = [
            pool.submit(train_one, base, q, robots, fold)
            for q, robots in conditions()
            for fold in folds()
        ]
    for future in training:
        future.result()

    print("closed loop", flush=True)
    with ThreadPoolExecutor(args.jobs) as pool:
        loops = [
            pool.submit(closed_loop_one, base, q, robots, seed)
            for q, robots in conditions()
            for seed in SEEDS
        ]
    for future in loops:
        future.result()

    print("analysis", flush=True)
    results = fidelity(base, args.jobs)
    (base / "fidelity.json").write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    summary = {
        "rules": rule_texts(base),
        "frozen_key": {
            key: entry["pooled"]["scopes"]["choice_bearing"]["rates"]["frozen_key"]
            for key, entry in results.items()
            if key.rsplit("/", 1)[1] in ("hand", FULL)
        },
        "g5": g5_limits(results),
        "g8": g8_bounds(base),
    }
    (base / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"summary: {base / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
