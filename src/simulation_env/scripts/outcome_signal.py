#!/usr/bin/env python3

"""M1: outcome-signal report over recorded runs. Read only.

    python3 src/simulation_env/scripts/outcome_signal.py \\
        runs/m1_q3 runs/m1_q0 --out runs/m1_signal

Every directory given is searched for runs (a run.json beside its
tables): experiment runs, M1 probes, or older runs. Nothing is written
into a run directory. --out receives:

    per_order.csv    one row per executed order: estimate, outcome,
                     residual, margin, availability, window flow
    summary.csv      one row per run (simulation_env/outcome_signal.py)
    summary.md       the headline columns, one table

A probe's features.parquet adds rank correlations of the residual with
each congestion feature; its rollouts.csv adds the rollout summary.
Runs whose seed is an evaluation or final seed are refused
(docs/DECISIONS.md, "Seed Split"): M1 must not look at them.
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]

for package in ("mrta_core", "simulation_env", "task_management"):
    if str(ROOT / "src" / package) not in sys.path:
        sys.path.insert(0, str(ROOT / "src" / package))

from simulation_env.dataset import sha256_of  # noqa: E402
from simulation_env.dataset_export import read_run  # noqa: E402
from simulation_env.outcome_signal import (  # noqa: E402
    FEATURES_FILE,
    feature_associations,
    per_order,
    rollout_summary,
    summarize,
)
from simulation_env.seed_split import sealed_inputs  # noqa: E402

# The tracked development workloads, the only ones with no seed in their name.
REFERENCE_WORKLOADS = (
    ROOT / "src" / "simulation_env" / "config" / "orders.csv",
    ROOT / "src" / "simulation_env" / "test" / "fixtures" / "orders.csv",
)

HEADLINE = (
    "strategy",
    "robots",
    "quantum_s",
    "seed",
    "orders",
    "single_robot_share",
    "eligible_pairs_mean",
    "eligible_orders_per_robot_mean",
    "residual_share_of_mission",
    "residual_mean_s",
    "residual_p90_s",
    "orders_residual_gt_5s",
    "margin_median_s",
    "exceeds_margin_share",
    "charge_due_after_share",
    "rho_residual_est_finish_s",
    "rollout_flip_share",
    "rollout_gain_mean_s",
    "rollout_outcome_vs_estimate_share",
)


def find_runs(paths):
    runs = []

    for path in paths:
        path = Path(path)
        runs += [p.parent for p in sorted(path.rglob("run.json"))]

    return sorted(set(runs))


def number(value):
    if isinstance(value, float):
        return f"{value:.3f}"
    return "" if value is None else str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    runs = find_runs(args.paths)

    if not runs:
        print("no runs found")
        return 1

    orders, summaries = [], []
    reference_workloads = {sha256_of(path) for path in REFERENCE_WORKLOADS}

    for run_dir in runs:
        # The seeds first, from the manifest alone: a sealed run's tables
        # are never read. Both the strategy seed and the workload file.
        head = json.loads((run_dir / "run.json").read_text())
        # A reference workload is recognised by content: a name would
        # let a renamed sealed workload through.
        sealed = sealed_inputs(
            strategy_seed=head.get("seed"),
            orders_path=head.get("orders_path", ""),
            require_known_workload=head.get("orders_sha256") not in reference_workloads,
        )
        if sealed:
            print(f"refused: {run_dir} has {', '.join(sealed)}")
            return 2

        decisions, outcomes, manifest = read_run(run_dir)
        decisions = pd.DataFrame(decisions)
        outcomes = pd.DataFrame(outcomes)
        metrics_path = run_dir / "metrics.json"
        metrics = pd.read_json(metrics_path, typ="series").to_dict() if metrics_path.exists() else None

        rows = per_order(decisions, outcomes, manifest)
        summary = summarize(rows, decisions, manifest, metrics)
        summary["run_dir"] = str(run_dir)

        if (run_dir / FEATURES_FILE).exists():
            summary.update(feature_associations(rows, pd.read_parquet(run_dir / FEATURES_FILE)))

        if (run_dir / "rollouts.csv").exists():
            summary.update(rollout_summary(pd.read_csv(run_dir / "rollouts.csv")))

        rows["run_dir"] = str(run_dir)
        orders.append(rows)
        summaries.append(summary)
        print(f"  {run_dir}: {len(rows)} orders, residual share {summary['residual_share_of_mission']:.4f}")

    args.out.mkdir(parents=True, exist_ok=True)
    pd.concat(orders).to_csv(args.out / "per_order.csv", index=False)

    table = pd.DataFrame(summaries)
    table.to_csv(args.out / "summary.csv", index=False)

    columns = [c for c in HEADLINE if c in table]
    lines = [
        "| " + " | ".join(columns) + " |",
        "|" + "---|" * len(columns),
        *("| " + " | ".join(number(row[c]) for c in columns) + " |" for _, row in table.iterrows()),
    ]
    (args.out / "summary.md").write_text("\n".join(lines) + "\n")

    print(f"wrote {args.out / 'summary.csv'} ({len(table)} runs)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
