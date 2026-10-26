#!/usr/bin/env python3

"""Compare strategies by paired differences over workload seeds.

    python3 src/simulation_env/scripts/compare_paired.py --name main

reads runs/<name>/summary.csv from a paired run_experiment.py run and
writes, for every strategy against the baseline (default
nearest_astar), at every fleet size and on every headline metric, the
mean per-seed difference and its 95% confidence interval:

    runs/<name>/paired.csv
    runs/<name>/paired.md

A difference is strategy minus baseline. An interval that does not
contain 0 is marked. Refuses a summary whose pairs did not run the
same workload. See simulation_env/paired.py and docs/DECISIONS.md,
"Workload Seeds and Paired Comparison".
"""

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

for package in ("mrta_core", "simulation_env", "task_management"):
    if str(ROOT / "src" / package) not in sys.path:
        sys.path.insert(0, str(ROOT / "src" / package))

from simulation_env.paired import PairingError, paired_differences  # noqa: E402


def load(path):
    with open(path) as handle:
        rows = list(csv.DictReader(handle))

    for row in rows:
        for key, value in row.items():
            try:
                row[key] = float(value)
            except (TypeError, ValueError):
                pass

    return rows


def _number(value):
    return "" if value is None else f"{value:.3f}"


def table(records):
    lines = [
        "| strategy | robots | metric | n | baseline mean | difference | 95% CI | % |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for r in records:
        if r["ci_low"] is None:
            ci = "n < 2"
        else:
            excludes_zero = r["ci_low"] > 0 or r["ci_high"] < 0
            ci = f"[{r['ci_low']:.3f}, {r['ci_high']:.3f}]" + (" *" if excludes_zero else "")

        lines.append(
            f"| {r['strategy']} | {r['robots']} | {r['metric']} | {r['n']} | "
            f"{r['baseline_mean']:.3f} | {r['mean_difference']:.3f} | {ci} | "
            f"{_number(r['percent_of_baseline'])} |"
        )

    return "\n".join(lines) + "\n\n* the interval excludes 0\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", default="experiment")
    parser.add_argument("--out", default="runs")
    parser.add_argument("--baseline", default="nearest_astar")
    args = parser.parse_args()

    base = ROOT / args.out / args.name
    rows = load(base / "summary.csv")

    if any(row.get("workload") != "seeded" for row in rows):
        print("not a paired experiment: run run_experiment.py with --workloads seeded")
        return 1

    try:
        records = paired_differences(rows, args.baseline)
    except PairingError as error:
        print(f"refused: {error}")
        return 1

    with open(base / "paired.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]) if records else ["strategy"])
        writer.writeheader()
        writer.writerows(records)

    (base / "paired.md").write_text(table(records))

    print(f"  wrote {base / 'paired.csv'} ({len(records)} comparisons)")
    print(f"  wrote {base / 'paired.md'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
