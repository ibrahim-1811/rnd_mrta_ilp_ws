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

Before any difference is taken, the experiment's integrity is checked
(E1): every expected strategy x fleet size x seed run is present
(check_complete), every run shares one simulator setting
(check_settings), and the two runs of a pair executed the same
workload at the same fleet size (paired_differences).

Read only: rows are summary.csv rows from run_experiment.py.
"""

from collections import defaultdict
from hashlib import sha256
from json import dumps
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


# run.json fields that may legitimately differ between the runs of one
# experiment: what a run is (strategy, seed, fleet size), its workload,
# and the window width, which follows the fleet size. Paths are left
# out because the file hashes stand for them. Everything else -- map,
# config, speed, service times, quantum, window rule, battery, order
# count, code commit -- must be identical across the experiment.
VARIES_BY_RUN = frozenset(
    {
        "strategy",
        "seed",
        "run_id",
        "robot_count",
        "window_width",
        "orders_path",
        "orders_sha256",
        "line_items",
        "config_path",
        "map_path",
        # Outputs RunRecorder adds after the run, not settings.
        "format",
        "decision_rows",
        "outcome_rows",
    }
)

# Summary columns the two runs of one pair must share. Strategy and the
# key (fleet size, seed) are the pair itself.
PAIR_FIELDS = ("orders_sha256", "orders", "line_items", "window_width")


class PairingError(ValueError):
    """The runs cannot be compared as pairs: missing, mixed or mismatched."""


def settings_signature(manifest: dict) -> str:
    """Hash of a run.json with the per-run fields removed.

    Two runs of one experiment have the same signature exactly when
    they ran the same map and simulator settings from the same commit.
    The tree's dirty flag is recorded in run.json but is not part of
    the signature.
    """

    kept = {k: v for k, v in manifest.items() if k not in VARIES_BY_RUN}
    kept["git"] = (manifest.get("git") or {}).get("commit")

    return sha256(dumps(kept, sort_keys=True, default=str).encode()).hexdigest()[:16]


def check_complete(rows, expected) -> None:
    """Every expected (strategy, robots, seed) run is present, no other.

    ``expected`` is the experiment's grid from experiment.json.
    """

    have = {(row["strategy"], int(row["robots"]), int(row["seed"])) for row in rows}
    want = {(s, int(r), int(seed)) for s, r, seed in expected}
    missing = sorted(want - have)
    extra = sorted(have - want)

    if missing or extra:
        raise PairingError(
            f"incomplete experiment: {len(missing)} expected run(s) missing "
            f"{missing[:5]}{' ...' if len(missing) > 5 else ''}, "
            f"{len(extra)} unexpected {extra[:5]}{' ...' if len(extra) > 5 else ''}"
        )


def check_settings(rows) -> None:
    """Every run of the experiment shares one settings signature."""

    groups = defaultdict(list)

    for row in rows:
        groups[row.get("settings_sha256")].append(
            (row["strategy"], int(row["robots"]), int(row["seed"]))
        )

    if None in groups or "" in groups:
        raise PairingError("summary.csv has no settings_sha256: rerun the experiment")

    if len(groups) > 1:
        detail = "; ".join(
            f"{signature}: {len(runs)} run(s), e.g. {sorted(runs)[0]}"
            for signature, runs in sorted(groups.items())
        )
        raise PairingError(f"runs differ in map or simulator settings: {detail}")


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
    for field in PAIR_FIELDS:
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
