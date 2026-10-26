"""Paired comparison of strategies over workload seeds.

docs/DECISIONS.md, "Workload Seeds and Paired Comparison": a seed
selects a workload, every strategy runs the same workload at a given
seed and fleet size, and strategies are compared by their per-seed
differences with a 95% confidence interval, not by rankings.

For one strategy against the baseline, at one fleet size and on one
metric, the pairs are the seeds both ran. The difference is
``strategy - baseline``, so a negative makespan difference means the
strategy finished earlier. The interval is the paired t-interval:

    mean(d) +/- t(0.975, n - 1) * sd(d) / sqrt(n)

Read only: rows are summary.csv rows from run_experiment.py.
"""

from collections import defaultdict
from math import sqrt
from statistics import mean, stdev

from scipy.stats import t as student_t


# The headline metrics (DECISIONS.md, "End of Workload") and the
# productive parts of the operational ones.
METRICS = (
    "order_makespan_s",
    "order_throughput_per_h",
    "mean_completion_time_s",
    "productive_distance_m",
    "productive_waiting_s",
    "total_distance_m",
    "energy_consumed_percent",
)

CONFIDENCE = 0.95


class PairingError(ValueError):
    """Two runs of one seed did not execute the same workload."""


def interval(differences, confidence=CONFIDENCE):
    """(mean, low, high) of the paired differences; None bounds if n < 2."""

    n = len(differences)
    centre = mean(differences)

    if n < 2:
        return centre, None, None

    half = student_t.ppf((1 + confidence) / 2, n - 1) * stdev(differences) / sqrt(n)

    return centre, centre - half, centre + half


def _keyed(rows):
    """(strategy, robots) -> seed -> row, refusing a duplicate run."""

    table = defaultdict(dict)

    for row in rows:
        key = (row["strategy"], int(row["robots"]))
        seed = int(row["seed"])

        if seed in table[key]:
            raise PairingError(f"{key[0]} at {key[1]} robots ran seed {seed} twice")

        table[key][seed] = row

    return table


def _same_workload(a, b):
    for field in ("orders_sha256", "orders"):
        if a.get(field) != b.get(field):
            raise PairingError(
                f"seed {int(a['seed'])}, {int(a['robots'])} robots: "
                f"{a['strategy']} and {b['strategy']} differ in {field} "
                f"({a.get(field)} vs {b.get(field)})"
            )


def paired_differences(rows, baseline, metrics=METRICS):
    """One record per (strategy, robots, metric) against the baseline.

    Each record: strategy, robots, metric, n (paired seeds), baseline
    mean, mean difference, its 95% bounds, and the mean difference as a
    percentage of the baseline mean.
    """

    table = _keyed(rows)
    strategies = sorted({s for s, _ in table if s != baseline})
    fleet = sorted({r for _, r in table})
    records = []

    for strategy in strategies:
        for robots in fleet:
            ours = table.get((strategy, robots), {})
            theirs = table.get((baseline, robots), {})
            seeds = sorted(set(ours) & set(theirs))

            for seed in seeds:
                _same_workload(ours[seed], theirs[seed])

            for metric in metrics:
                pairs = [
                    (ours[s][metric], theirs[s][metric])
                    for s in seeds
                    if isinstance(ours[s].get(metric), float)
                    and isinstance(theirs[s].get(metric), float)
                ]

                if not pairs:
                    continue

                differences = [a - b for a, b in pairs]
                reference = mean(b for _, b in pairs)
                centre, low, high = interval(differences)

                records.append(
                    {
                        "strategy": strategy,
                        "baseline": baseline,
                        "robots": robots,
                        "metric": metric,
                        "n": len(pairs),
                        "baseline_mean": reference,
                        "mean_difference": centre,
                        "ci_low": low,
                        "ci_high": high,
                        "percent_of_baseline": (
                            100 * centre / reference if reference else None
                        ),
                    }
                )

    return records
