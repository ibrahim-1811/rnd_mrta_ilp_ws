"""M8 final campaign statistics (DECISIONS.md, "M8 Final Campaign"; EXPERIMENT_PROTOCOL.md F9).

    python -m simulation_env.campaign_stats runs/campaign/final/o*/r*/summary.csv --out DIR

Input: the summary.csv of each experiment (one per length and fleet size), as
run_experiment writes them; a learned arm appears as ilp_<label>. Per length (orders) and
fleet size (robots), for every metric:

  Friedman     over every arm present, blocks = the seeds on which every arm has a run;
               mean rank per arm and Kendall's W beside it
  pairs        two-sided paired Wilcoxon signed-rank on the seeds both arms share,
               Holm within each family per length and metric:
                 ilp           single_<T> vs T; pooled, majority, plurality vs nearest_astar;
                               outcome_multi_criteria vs multi_criteria and vs
                               single_multi_criteria
                 conventional  each conventional strategy vs nearest_astar
               beside each: mean difference (a - b), its paired 95 % t-interval, d_z,
               wins and losses
  failures     a run missing from a summary (failed or stalled) is a missing pair, never
               imputed: expected = arms seen x seeds seen, per length and fleet size

The primary metric is order_makespan_s; the others are secondary, each its own family.
Output: friedman.csv, pairs.csv, failures.csv in --out, and the primary tables printed.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys
from typing import Iterable, Mapping

import numpy as np
import pandas as pd
from scipy.stats import friedmanchisquare, wilcoxon

from simulation_env.paired import interval

PRIMARY = "order_makespan_s"
SECONDARY = ("mean_completion_time_s", "total_distance_m", "total_waiting_s",
             "energy_consumed_percent", "charge_sessions", "charger_wait_time_s")
CONVENTIONAL = ("random", "nearest_manhattan", "nearest_astar", "earliest_finish",
                "multi_criteria", "auction", "hungarian", "milp")
BASELINE = "nearest_astar"
OUTCOME_ARM = "ilp_outcome_multi_criteria"
ALPHA = 0.05


def holm(pvalues: Mapping[str, float]) -> dict[str, float]:
    """Holm-Bonferroni adjusted p-values (step-down, monotone, capped at 1); NaN stays NaN."""
    known = {k: p for k, p in pvalues.items() if not math.isnan(p)}
    ordered = sorted(known.items(), key=lambda item: item[1])
    m = len(ordered)
    adjusted, running = {}, 0.0
    for rank, (name, p) in enumerate(ordered):
        running = max(running, min(1.0, (m - rank) * p))
        adjusted[name] = running
    return {k: adjusted.get(k, math.nan) for k in pvalues}


def paired_wilcoxon(diff: Iterable[float], alternative: str = "two-sided") -> float:
    """Wilcoxon signed-rank p of paired differences; 1.0 when every difference is 0."""
    diff = np.asarray(list(diff), dtype=float)
    if len(diff) == 0:
        return math.nan
    if (diff == 0).all():
        return 1.0
    return float(wilcoxon(diff, alternative=alternative, zero_method="wilcox").pvalue)


def families(arms: Iterable[str]) -> dict[str, list[tuple[str, str]]]:
    """The pre-listed comparison pairs (a, b) whose arms are both present."""
    present = set(arms)
    ilp = [(f"ilp_single_{t}", t) for t in CONVENTIONAL]
    ilp += [(f"ilp_{c}", BASELINE) for c in ("pooled", "majority", "plurality")]
    ilp += [(OUTCOME_ARM, "multi_criteria"), (OUTCOME_ARM, "ilp_single_multi_criteria")]
    conventional = [(t, BASELINE) for t in CONVENTIONAL if t != BASELINE]
    keep = lambda pairs: [(a, b) for a, b in pairs if a in present and b in present]  # noqa: E731
    return {"ilp": keep(ilp), "conventional": keep(conventional)}


def load(paths: Iterable[Path | str]) -> pd.DataFrame:
    frames = [pd.read_csv(p) for p in paths]
    if not frames:
        raise ValueError("no summary.csv given")
    rows = pd.concat(frames, ignore_index=True)
    dup = rows.duplicated(["orders", "robots", "strategy", "seed"])
    if dup.any():
        raise ValueError(f"{int(dup.sum())} duplicate (orders, robots, strategy, seed) rows")
    return rows


def failures(rows: pd.DataFrame) -> pd.DataFrame:
    """Per length, fleet size and arm: expected runs (seeds seen), present, missing."""
    out = []
    for (orders, robots), g in rows.groupby(["orders", "robots"]):
        seeds = set(g["seed"])
        for arm in sorted(rows.loc[rows["orders"] == orders, "strategy"].unique()):
            have = set(g.loc[g["strategy"] == arm, "seed"])
            out.append({"orders": orders, "robots": robots, "strategy": arm, "expected": len(seeds),
                        "present": len(have), "missing": len(seeds - have),
                        "missing_seeds": " ".join(map(str, sorted(seeds - have)))})
    return pd.DataFrame(out)


def friedman(rows: pd.DataFrame, metric: str = PRIMARY) -> pd.DataFrame:
    out = []
    for (orders, robots), g in rows.groupby(["orders", "robots"]):
        table = g.pivot(index="seed", columns="strategy", values=metric).dropna()
        k, n = table.shape[1], table.shape[0]
        rec = {"orders": orders, "robots": robots, "metric": metric, "arms": k, "blocks": n,
               "blocks_dropped": int(g["seed"].nunique() - n)}
        if k >= 3 and n >= 2 and not (table.nunique(axis=1) == 1).all():
            stat, p = friedmanchisquare(*[table[c].to_numpy() for c in table.columns])
            rec.update(chi2=float(stat), p=float(p), kendall_w=float(stat / (n * (k - 1))))
        else:
            rec.update(chi2=math.nan, p=math.nan, kendall_w=math.nan)
        ranks = table.rank(axis=1, method="average").mean()
        rec["mean_ranks"] = "; ".join(f"{a} {r:.2f}" for a, r in ranks.sort_values().items())
        out.append(rec)
    return pd.DataFrame(out)


def compare(rows: pd.DataFrame, a: str, b: str, metric: str) -> dict:
    """Paired statistics of arm a against arm b on the seeds both have."""
    left = rows[rows["strategy"] == a].set_index("seed")[metric]
    right = rows[rows["strategy"] == b].set_index("seed")[metric]
    both = left.index.intersection(right.index)
    diff = (left.loc[both] - right.loc[both]).astype(float)
    centre, low, high = interval(list(diff)) if len(diff) else (math.nan, None, None)
    sd = float(diff.std(ddof=1)) if len(diff) > 1 else math.nan
    return {"a": a, "b": b, "metric": metric, "pairs": int(len(diff)),
            "mean_a": float(left.loc[both].mean()) if len(both) else math.nan,
            "mean_b": float(right.loc[both].mean()) if len(both) else math.nan,
            "mean_diff": float(centre),
            "ci_low": math.nan if low is None else float(low),
            "ci_high": math.nan if high is None else float(high),
            "mean_diff_pct": float((diff / right.loc[both]).mean() * 100) if len(both) else math.nan,
            "d_z": float(centre / sd) if sd and not math.isnan(sd) else math.nan,
            "wins": int((diff < 0).sum()), "losses": int((diff > 0).sum()),
            "p": paired_wilcoxon(diff)}


def pairs(rows: pd.DataFrame, metrics: Iterable[str]) -> pd.DataFrame:
    """Every pre-listed pair at every fleet size, Holm within family x length x metric."""
    out = []
    for orders, by_length in rows.groupby("orders"):
        fams = families(by_length["strategy"].unique())
        for metric in metrics:
            if metric not in by_length:
                continue
            for family, plist in fams.items():
                block = []
                for robots, g in by_length.groupby("robots"):
                    for a, b in plist:
                        block.append({"orders": orders, "robots": robots, "family": family,
                                      **compare(g, a, b, metric)})
                adjusted = holm({i: r["p"] for i, r in enumerate(block)})
                for i, r in enumerate(block):
                    r["p_holm"] = adjusted[i]
                    r["significant"] = bool(r["p_holm"] < ALPHA) if not math.isnan(r["p_holm"]) else False
                    r["primary"] = metric == PRIMARY
                out.extend(block)
    return pd.DataFrame(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("summaries", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    rows = load(args.summaries)
    args.out.mkdir(parents=True, exist_ok=True)
    fail = failures(rows)
    fr = pd.concat([friedman(rows, m) for m in (PRIMARY, *SECONDARY) if m in rows], ignore_index=True)
    pr = pairs(rows, (PRIMARY, *SECONDARY))
    fail.to_csv(args.out / "failures.csv", index=False)
    fr.to_csv(args.out / "friedman.csv", index=False)
    pr.to_csv(args.out / "pairs.csv", index=False)

    pd.set_option("display.width", 220)
    print(f"runs {len(rows)}; missing {int(fail['missing'].sum())} "
          f"(per arm in {args.out / 'failures.csv'})")
    print(f"\nFriedman on {PRIMARY} (blocks = seeds with every arm)")
    print(fr[fr["metric"] == PRIMARY][["orders", "robots", "arms", "blocks", "blocks_dropped",
                                       "chi2", "p", "kendall_w"]].to_string(index=False))
    show = pr[pr["primary"]][["orders", "robots", "family", "a", "b", "pairs", "mean_diff",
                              "ci_low", "ci_high", "mean_diff_pct", "d_z", "wins", "losses", "p_holm"]]
    print(f"\nPaired comparisons on {PRIMARY} (a - b; negative = a faster), Holm per family x length")
    print(show.round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
