"""Reproduce the M3 induction evidence from a clean commit, end to end.

    pixi run m3-induction

Arm A on induction seeds 104-110 (.scratch/m3-popper-learner/preregistration.md):
teacher runs, full-data and leave-one-seed-out training, the G6 sampling seeds, the
G10 controls, closed-loop runs of each full-data rule, fidelity, then the G5 limits
and the G8 bounds. Finished steps are
skipped, so a stopped run can be started again. This module has no seed argument:
validation, backup and sealed seeds cannot be reached from here.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
import statistics
import subprocess
import sys
from typing import Sequence

import pandas as pd

from ilp_learning.fidelity import evaluate_runs
from ilp_learning.rule_artifact import load_rule_artifact
from ilp_learning.rule_compiler import RuleProgram, parse_clause
from ilp_learning.sampler import DEFAULT_CORPUS_SEED
from simulation_env.seed_split import TRAINING_INDUCTION


SEEDS = tuple(TRAINING_INDUCTION)
FLEETS = (30, 70, 100)
QUANTA = {"q3": 3.0, "q0": 0.0}  # q3 is the primary condition
ORDERS = 300
WINDOW = "fleet_size"
TEACHER = "nearest_astar"
FULL = "full"
METRICS = ("order_makespan_s", "mean_completion_time_s")
SAMPLING_SEEDS = (DEFAULT_CORPUS_SEED, DEFAULT_CORPUS_SEED + 1, DEFAULT_CORPUS_SEED + 2)
PERMUTE_SEED = DEFAULT_CORPUS_SEED
TARGET_O = "best_cost_for_order"
PROBE_SOC = 20.0
MAX_JOBS = 8  # amendment item 9
G8_MARGIN_CAP = 0.05  # researcher decision 5, 2026-11-01
CONTROLS = {
    "relabel_finish": ["--relabel-finish"],
    "permuted": ["--permute-seed", str(PERMUTE_SEED)],
    "without_o": ["--exclude", TARGET_O],
}
HAND_WRITTEN_R_AND_O = "assign(A,B,C):- best_cost_for_order(A,B,C),nearest_static(A,B,C)."
T_90_4DF_OVER_ROOT_5 = 0.953  # half-width factor of the G8 90% CI with 5 pairs
REPO = Path(__file__).resolve().parents[3]


def head_commit() -> str:
    done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True)
    return done.stdout.strip()


def check_fresh(base: Path) -> None:
    """Finished steps are skipped, so a folder holds one commit's evidence only."""
    marker = base / "commit.txt"
    if marker.exists():
        stale = marker.read_text().strip() != head_commit()
    else:
        stale = base.exists() and any(base.iterdir())
    if stale:
        raise SystemExit(f"refused: {base} holds evidence from another commit; use a new folder")
    base.mkdir(parents=True, exist_ok=True)
    marker.write_text(head_commit() + "\n", encoding="utf-8")


def conditions() -> list[tuple[str, int]]:
    return [(q, robots) for q in QUANTA for robots in FLEETS]


def teacher_run(base: Path, q: str, robots: int, seed: int) -> Path:
    return base / q / TEACHER / f"r{robots}_s{seed}"


def rule_dir(base: Path, q: str, robots: int, fold: str) -> Path:
    return base / "rules" / q / f"r{robots}" / fold


def folds() -> list[str]:
    return [FULL, *(f"loso_{seed}" for seed in SEEDS)]


def extra_rules() -> dict[str, list[str]]:
    """Fold name -> train_rules options, for G6 and G10. All train on every seed."""
    sampled = {f"sample_{seed}": ["--corpus-seed", str(seed)] for seed in SAMPLING_SEEDS[1:]}
    return {**sampled, **CONTROLS}


def training_seeds(fold: str) -> list[int]:
    if not fold.startswith("loso_"):
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
        [
            sys.executable, "-m", "ilp_learning.train_rules", *runs, "--out", str(out),
            *extra_rules().get(fold, []),
        ],
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
    kinds = ("hand", *folds(), *extra_rules())
    work = [(str(base), q, r, kind) for q, r in conditions() for kind in kinds]
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
                "margin": min(delta, G8_MARGIN_CAP),
                "paired_sd": spread,
                "mean_log_ratio": statistics.mean(ratios),
                "max_sd_for_equivalence": min(delta, G8_MARGIN_CAP) / T_90_4DF_OVER_ROOT_5,
                "can_show_equivalence": spread <= min(delta, G8_MARGIN_CAP) / T_90_4DF_OVER_ROOT_5,
            }
    return bounds


def rule_texts(base: Path) -> dict:
    texts = {}
    for q, robots in conditions():
        for fold in (*folds(), *extra_rules()):
            _, artifact = load_rule_artifact(rule_dir(base, q, robots, fold) / "rule_artifact.json")
            texts[f"{q}/{robots}/{fold}"] = [entry["clause"] for entry in artifact["clauses"]]
    return texts


def g10_controls(results: dict, texts: dict) -> dict:
    """The pre-registered negative controls, on induction seeds."""

    def key(condition: str, kind: str) -> float:
        return results[f"{condition}/{kind}"]["pooled"]["scopes"]["choice_bearing"]["rates"]["frozen_key"]

    controls = {}
    for q, robots in conditions():
        condition = f"{q}/{robots}"
        permuted = texts[f"{condition}/permuted"]
        controls[condition] = {
            "relabel_differs": texts[f"{condition}/relabel_finish"] != texts[f"{condition}/{FULL}"],
            "permuted_clauses": len(permuted),
            "permuted_fidelity": key(condition, "permuted"),
            "permuted_pass": not permuted or key(condition, "permuted") < 0.1,
            "without_o_drop": key(condition, FULL) - key(condition, "without_o"),
            "without_o_pass": key(condition, FULL) - key(condition, "without_o") >= 0.3,
        }
    return controls


def battery_probe(base: Path, q: str = "q3", robots: int = 30, seed: int = 104) -> dict:
    """The pre-registered stress probe; the temporary config stays inside the run folder."""
    results = _probe_at(base, base / "battery", PROBE_SOC, q, robots, seed)
    if not results["informative"]:
        results = {
            "first_attempt": results,
            **_probe_at(base, base / "battery_lowered", PROBE_SOC / 2, q, robots, seed),
        }
    return results


def _probe_at(base: Path, probe: Path, soc: float, q: str, robots: int, seed: int) -> dict:
    probe.mkdir(parents=True, exist_ok=True)
    source = REPO / "src/simulation_env/config/warehouse.yaml"
    config = probe / "warehouse_soc20.yaml"
    text = source.read_text(encoding="utf-8")
    edited = text.replace("initial_soc_percent: 100.0", f"initial_soc_percent: {soc}")
    if edited == text:
        raise RuntimeError("config has no initial_soc_percent: 100.0 line")
    config.write_text(
        edited.replace("map: W100.xlsx", f"map: {source.parents[1] / 'maps/W100.xlsx'}").replace(
            "file: orders.csv", f"file: {source.with_name('orders.csv')}"
        ),
        encoding="utf-8",
    )

    results: dict = {"config": str(config), "initial_soc_percent": soc}
    arms = {
        TEACHER: [],
        "learned": ["--rule-artifact", str(rule_dir(base, q, robots, FULL) / "rule_artifact.json")],
    }
    for arm, extra in arms.items():
        out = probe / arm
        if not (out / "metrics.json").exists():
            _call(
                [
                    sys.executable, "src/simulation_env/scripts/run_workload.py",
                    "--config", str(config),
                    "--strategy", TEACHER if arm == TEACHER else "ilp", *extra,
                    "--robots", str(robots), "--seed", str(seed), "--orders", str(ORDERS),
                    "--quiet", "--progress", "0", "--dataset", str(out),
                    "--quantum", str(QUANTA[q]), "--window", WINDOW, "--purpose", "induction",
                    "--orders-file", str(base / q / "workloads" / f"orders_s{seed}.csv"),
                ],
                base / "logs" / f"battery_{arm}.log",
            )
        metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
        decisions = pd.read_parquet(out / "decisions.parquet")
        chosen = decisions[decisions["selected"]]
        outcomes = pd.read_parquet(out / "outcomes.parquet")
        results[arm] = {
            "orders_completed": metrics["orders_completed"],
            "reservation_violations": metrics["reservation_violations"],
            "charge_sessions": metrics["charge_sessions"],
            "ineligible_rows": int((~decisions["eligible"]).sum()),
            "ineligible_assignments": int((~chosen["eligible"]).sum()),
            "double_assigned_orders": int(outcomes["order_id"].duplicated().sum()),
            "double_assigned_robots": int(chosen.duplicated(["decision_id", "robot_id"]).sum()),
            "min_soc_percent": metrics["min_soc_percent"],
        }
        trace = out / "rule_trace.parquet"
        if trace.exists():
            picks = pd.read_parquet(trace)
            results[arm]["rule_coverage"] = float(picks["matched_by_rule"].mean())
            results[arm]["fallback"] = float(picks["fallback"].mean())
            results[arm]["teacher_agreement"] = _agreement(chosen, probe / TEACHER)
        results[arm]["informative"] = (
            results[arm]["ineligible_rows"] >= 1 and results[arm]["charge_sessions"] >= 1
        )
        results[arm]["pass"] = (
            metrics["orders_completed"] == ORDERS
            and metrics["reservation_violations"] == 0
            and results[arm]["ineligible_assignments"] == 0
            and results[arm]["double_assigned_orders"] == 0
            and results[arm]["double_assigned_robots"] == 0
            and results[arm]["min_soc_percent"] > 0
        )
    results["informative"] = all(results[arm]["informative"] for arm in arms)
    results["pass"] = results["informative"] and all(results[arm]["pass"] for arm in arms)
    return results


def _agreement(chosen: pd.DataFrame, teacher_run: Path) -> float:
    """Share of pairs also assigned by the teacher, at any decision."""
    teacher = pd.read_parquet(teacher_run / "decisions.parquet")
    teacher = teacher[teacher["selected"]]
    pairs = set(zip(teacher["robot_id"], teacher["order_id"]))
    return sum(pair in pairs for pair in zip(chosen["robot_id"], chosen["order_id"])) / len(chosen)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="runs/m3_evidence_1", help="output directory")
    parser.add_argument("--jobs", type=int, default=MAX_JOBS)
    args = parser.parse_args()
    base = (REPO / args.out).resolve()
    check_fresh(base)
    base.mkdir(parents=True, exist_ok=True)

    print("teachers", flush=True)
    run_teachers(base, args.jobs)

    print("training", flush=True)
    with ThreadPoolExecutor(args.jobs) as pool:
        training = [
            pool.submit(train_one, base, q, robots, fold)
            for q, robots in conditions()
            for fold in (*folds(), *extra_rules())
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
    summary["g10"] = g10_controls(results, summary["rules"])
    print("battery probe", flush=True)
    summary["battery_probe"] = battery_probe(base)
    (base / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"summary: {base / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
