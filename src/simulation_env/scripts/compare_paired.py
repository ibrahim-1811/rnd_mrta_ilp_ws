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
contain 0 is marked. Refuses (E1) an experiment whose runs do not match
the grid in experiment.json, whose runs do not share one map and
simulator setting, whose strategy ran as more than one identity (its
parameters, solver or rule artifact changed between runs), or whose
pairs did not run the same workload.

--allow-incomplete compares the runs that are there, and says so:
paired.md opens with an INCOMPLETE banner, lists every missing run
(stalled, failed or never run) and every run left out, and gives each
comparison's expected and actual paired sample size; paired.csv
carries expected_n and complete for every row. A stalled run is never
shown as a success or dropped without being listed. See simulation_env/paired.py
and docs/DECISIONS.md, "Workload Seeds and Paired Comparison".
"""

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

for package in ("mrta_core", "simulation_env", "task_management"):
    if str(ROOT / "src" / package) not in sys.path:
        sys.path.insert(0, str(ROOT / "src" / package))

from simulation_env.failures import read_failure  # noqa: E402
from simulation_env.paired import (  # noqa: E402
    PairingError,
    check_complete,
    check_settings,
    check_strategies,
    paired_differences,
)


# Identity columns stay text: a hex digest such as "12e4..." or one of
# only digits would otherwise parse as a number.
TEXT_COLUMNS = frozenset(
    {
        "strategy",
        "workload",
        "orders_sha256",
        "settings_sha256",
        "strategy_sha256",
        "run_dir",
    }
)


def missing_reason(run_dir) -> str:
    """Why an expected run has no metrics, as precisely as it is known.

    failure.json first (docs/DECISIONS.md, "Run Failure Classification");
    a run made before failures were classified is read from its log.
    """

    if not run_dir.exists():
        return "not run"

    failure = read_failure(run_dir)

    if failure is not None:
        stalled = " (stalled)" if failure.get("stalled") else ""
        return f"failed: {failure['kind']}{stalled}; see failure.json"

    log = run_dir / "run.log"

    if log.exists() and "STALLED" in log.read_text(errors="replace"):
        return "stalled, unclassified (run predates failure.json); see run.log"

    return "failed, no metrics; see run.log"


def load(path):
    with open(path) as handle:
        rows = list(csv.DictReader(handle))

    for row in rows:
        for key, value in row.items():
            if key in TEXT_COLUMNS:
                continue

            try:
                row[key] = float(value)
            except (TypeError, ValueError):
                pass

    return rows


def _number(value):
    return "" if value is None else f"{value:.3f}"


def completeness(base, rows, expected_runs, baseline):
    """What is missing from the grid, and what each comparison lost.

    Returns (missing, extra, samples): missing runs with why they are
    missing, runs present but outside the grid, and per (strategy,
    robots) the expected seeds and the seeds actually paired with the
    baseline.
    """

    expected = {(s, int(r), int(seed)) for s, r, seed in expected_runs}
    have = {(row["strategy"], int(row["robots"]), int(row["seed"])) for row in rows}

    missing = []

    for strategy, robots, seed in sorted(expected - have):
        why = missing_reason(base / strategy / f"r{robots}_s{seed}")
        missing.append((strategy, robots, seed, why))

    extra = sorted(have - expected)

    seeds = {}

    for strategy, robots, seed in expected:
        seeds.setdefault((strategy, robots), set()).add(seed)

    present = {}

    for strategy, robots, seed in have & expected:
        present.setdefault((strategy, robots), set()).add(seed)

    samples = []

    for strategy, robots in sorted(seeds):
        if strategy == baseline:
            continue

        want = seeds[(strategy, robots)] & seeds.get((baseline, robots), set())
        paired = present.get((strategy, robots), set()) & present.get((baseline, robots), set())
        samples.append((strategy, robots, len(want), len(paired), sorted(want - paired)))

    return missing, extra, samples


def incomplete_banner(missing, extra, samples, expected_count, present_count):
    lines = [
        "# INCOMPLETE EXPERIMENT -- not a complete result",
        "",
        f"Compared with --allow-incomplete: {present_count} of {expected_count} "
        "expected runs present. The differences below use only the seeds "
        "both runs of a pair completed; read every n against its expected n.",
        "",
        "## Missing runs",
        "",
        "| strategy | robots | seed | status |",
        "|---|---|---|---|",
    ]

    lines += [f"| {s} | {r} | {seed} | {why} |" for s, r, seed, why in missing]

    if extra:
        lines += [
            "",
            "## Runs outside the experiment grid, left out",
            "",
            "| strategy | robots | seed |",
            "|---|---|---|",
        ]
        lines += [f"| {s} | {r} | {seed} |" for s, r, seed in extra]

    lines += [
        "",
        "## Paired sample per comparison",
        "",
        "| strategy | robots | expected n | paired n | seeds left out |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {s} | {r} | {want} | {got} | {', '.join(map(str, out)) or '-'} |"
        for s, r, want, got, out in samples
    ]

    return "\n".join(lines) + "\n\n## Paired differences\n\n"


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
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="compare the seeds present even if expected runs are missing",
    )
    args = parser.parse_args()

    base = ROOT / args.out / args.name
    rows = load(base / "summary.csv")

    if any(row.get("workload") != "seeded" for row in rows):
        print("not a paired experiment: run run_experiment.py with --workloads seeded")
        return 1

    record_path = base / "experiment.json"

    if not record_path.exists():
        print(f"refused: {record_path} missing; rerun with the current run_experiment.py")
        return 1

    record = json.loads(record_path.read_text())
    banner = ""

    try:
        if args.allow_incomplete:
            try:
                check_complete(rows, record["runs"])
            except PairingError as error:
                missing, extra, samples = completeness(
                    base, rows, record["runs"], args.baseline
                )
                grid = {(s, int(r), int(seed)) for s, r, seed in record["runs"]}

                # Runs outside the grid are listed, never compared.
                rows = [
                    row
                    for row in rows
                    if (row["strategy"], int(row["robots"]), int(row["seed"])) in grid
                ]
                banner = incomplete_banner(
                    missing, extra, samples, len(grid), len(rows)
                )
                expected_n = {(s, r): want for s, r, want, _, _ in samples}

                print(f"  INCOMPLETE (--allow-incomplete): {error}")
                print(f"  {len(missing)} missing run(s) listed in paired.md")
        else:
            check_complete(rows, record["runs"])

        check_settings(rows)
        check_strategies(rows)
        records = paired_differences(rows, args.baseline)
    except PairingError as error:
        print(f"refused: {error}")
        return 1

    if banner:
        for r in records:
            r["expected_n"] = expected_n.get((r["strategy"], r["robots"]), 0)
            r["complete"] = r["n"] == r["expected_n"]

    with open(base / "paired.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]) if records else ["strategy"])
        writer.writeheader()
        writer.writerows(records)

    (base / "paired.md").write_text(banner + table(records))

    # Recorded, not enforced: a result from an uncommitted tree is
    # still a result, but its manifest says so.
    if any((entry.get("git") or {}).get("dirty") for entry in [record, *record.get("resumed", [])]):
        print("  note: the experiment ran on a tree with uncommitted changes (see experiment.json)")

    print(f"  wrote {base / 'paired.csv'} ({len(records)} comparisons)")
    print(f"  wrote {base / 'paired.md'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
