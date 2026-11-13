"""Gate 2 verdict: does each rule set beat its teacher, closed loop? (DECISIONS.md,
"Gate 2 Pass Criterion and Order", 2026-11-10)

    python -m ilp_learning.gate2_verdict runs/teachers/eval/.../summary.csv ... \
        --pair ilp_oracle_milp=milp --pair ilp_oracle_nearest_astar=nearest_astar ... \
        [--control ilp_oracle_milp=ilp_imitation_milp ...] [--gate1 runs/gate1/o300/wholerun] \
        --out runs/gate2/verdict.json

A pair is student=teacher. Per pair, on the runs both share (robots x validation seed):

  1. Problem A gate: the rules fire (rule_picks > 0 in every run) and fallback_rate <= 0.5
     (mean over the runs)
  2. mean order makespan below the teacher's, paired Wilcoxon signed-rank on
     student - teacher, Holm over all pairs given, alpha 0.05

Pass = 1 and 2. Reported, not gated: wins, mean difference, flow time, waiting, and with
--gate1 the recovered share (teacher - student) / (teacher - oracle) from the Gate 1
oracle runs on the same seeds. --control student=other (the same-state imitation
control) is a second Holm family: the H1 contrast, oracle labels against imitation
labels, reported only. A pair with a missing run on either side is refused, never
dropped: the comparison is paired.

The test is two-sided by default (conservative; pass still needs the mean to be lower).
--alternative less is the one-sided test of "student lower"; whichever is used is
recorded in the output.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Iterable, Mapping

import pandas as pd
from simulation_env.campaign_stats import holm, paired_wilcoxon

PRIMARY = "order_makespan_s"
REPORTED = ("mean_completion_time_s", "total_waiting_s", "total_distance_m")
KEY = ("robots", "seed")
ALPHA = 0.05
MAX_FALLBACK = 0.5


class VerdictError(ValueError):
    """The runs cannot be paired as the criterion requires."""


def _paired(rows: pd.DataFrame, a: str, b: str) -> pd.DataFrame:
    """One row per (robots, seed) with both strategies' columns, suffixed _a and _b."""
    left = rows[rows["strategy"] == a].set_index(list(KEY))
    right = rows[rows["strategy"] == b].set_index(list(KEY))
    if left.empty or right.empty:
        raise VerdictError(f"no runs for {a if left.empty else b}")
    for frame, name in ((left, a), (right, b)):
        if frame.index.duplicated().any():
            raise VerdictError(f"{name} has more than one run for one (robots, seed)")
    if set(left.index) != set(right.index):
        missing = sorted(set(left.index) ^ set(right.index))
        raise VerdictError(f"{a} and {b} do not share the same runs; unpaired: {missing}")
    return left.join(right, lsuffix="_a", rsuffix="_b").sort_index()


def _test(diff: pd.Series, alternative: str) -> float:
    return paired_wilcoxon(diff, alternative)


def compare(rows: pd.DataFrame, student: str, teacher: str, alternative: str,
            oracle: Mapping[tuple, float] | None = None) -> dict:
    """Paired statistics of student against teacher (or another arm)."""
    paired = _paired(rows, student, teacher)
    diff = paired[f"{PRIMARY}_a"] - paired[f"{PRIMARY}_b"]
    out = {
        "student": student, "teacher": teacher, "pairs": int(len(paired)),
        "mean_student": float(paired[f"{PRIMARY}_a"].mean()),
        "mean_teacher": float(paired[f"{PRIMARY}_b"].mean()),
        "mean_diff_s": float(diff.mean()),
        "mean_diff_pct": float((diff / paired[f"{PRIMARY}_b"]).mean() * 100),
        "wins": int((diff < 0).sum()), "losses": int((diff > 0).sum()),
        "p": _test(diff, alternative),
        "reported": {
            col: float((paired[f"{col}_a"] - paired[f"{col}_b"]).mean())
            for col in REPORTED if f"{col}_a" in paired and f"{col}_b" in paired
        },
    }
    if "rule_picks_a" in paired:
        picks = paired["rule_picks_a"].fillna(0)
        fallback = paired["fallback_rate_a"].astype(float)
        out["rules_fire"] = bool((picks > 0).all())
        out["fallback_rate"] = float(fallback.mean())
        out["problem_a_gate"] = out["rules_fire"] and out["fallback_rate"] <= MAX_FALLBACK
    if oracle is not None:
        keys = [(teacher, *k) for k in paired.index]
        if all(k in oracle for k in keys):
            gap = paired[f"{PRIMARY}_b"].mean() - sum(oracle[k] for k in keys) / len(keys)
            out["oracle_gap_s"] = float(gap)
            out["recovered_share"] = float(-diff.mean() / gap) if gap > 0 else math.nan
    return out


def verdict(rows: pd.DataFrame, pairs: Mapping[str, str], *, alternative: str = "two-sided",
            alpha: float = ALPHA, controls: Mapping[str, str] | None = None,
            oracle: Mapping[tuple, float] | None = None) -> dict:
    """The Gate 2 verdict for every student=teacher pair, and the H1 contrasts."""
    results = [compare(rows, s, t, alternative, oracle) for s, t in pairs.items()]
    adjusted = holm({r["student"]: r["p"] for r in results})
    for r in results:
        r["p_holm"] = adjusted[r["student"]]
        r["significant"] = r["p_holm"] < alpha
        r["pass"] = bool(r.get("problem_a_gate", False) and r["mean_diff_s"] < 0 and r["significant"])
    contrasts = [compare(rows, s, o, alternative) for s, o in (controls or {}).items()]
    adjusted = holm({f"{c['student']}|{c['teacher']}": c["p"] for c in contrasts})
    for c in contrasts:
        c["p_holm"] = adjusted[f"{c['student']}|{c['teacher']}"]
        c["significant"] = c["p_holm"] < alpha
    return {
        "criterion": "DECISIONS.md, Gate 2 Pass Criterion and Order (2026-11-10)",
        "metric": PRIMARY, "test": "paired Wilcoxon signed-rank", "alternative": alternative,
        "correction": "Holm", "alpha": alpha, "max_fallback_rate": MAX_FALLBACK,
        "pairs": results, "h1_contrasts": contrasts,
    }


def oracle_makespans(root: Path) -> dict[tuple, float]:
    """(teacher, robots, seed) -> the oracle's order makespan, from Gate 1 oracle.json files."""
    found = {}
    for path in Path(root).glob("*/r*_s*/oracle.json"):
        report = json.loads(path.read_text(encoding="utf-8"))
        robots, seed = path.parent.name[1:].split("_s")
        found[(report["behaviour"], int(robots), int(seed))] = report["oracle"]["order_makespan_s"]
    return found


def _mapping(items: Iterable[str], flag: str) -> dict[str, str]:
    out = {}
    for item in items:
        left, sep, right = item.partition("=")
        if not sep or not left or not right:
            raise SystemExit(f"{flag} takes student=other, got {item!r}")
        out[left] = right
    return out


def _print(result: dict) -> None:
    print(f"Gate 2: {result['metric']}, {result['test']} ({result['alternative']}), "
          f"Holm, alpha {result['alpha']}")
    print(f"{'student':34}{'teacher':16}{'n':>3}{'diff s':>9}{'diff %':>8}{'W/L':>7}"
          f"{'p_holm':>9}{'fallback':>9}{'recov':>7}  verdict")
    for r in result["pairs"]:
        recov = r.get("recovered_share")
        print(f"{r['student']:34}{r['teacher']:16}{r['pairs']:>3}{r['mean_diff_s']:>9.1f}"
              f"{r['mean_diff_pct']:>8.2f}{r['wins']:>4}/{r['losses']:<2}{r['p_holm']:>9.4f}"
              f"{r.get('fallback_rate', math.nan):>9.2f}"
              f"{(recov if recov is not None else math.nan):>7.2f}  {'PASS' if r['pass'] else 'fail'}")
    for c in result["h1_contrasts"]:
        print(f"H1 {c['student']} vs {c['teacher']}: diff {c['mean_diff_s']:.1f} s "
              f"({c['wins']}/{c['losses']}), p_holm {c['p_holm']:.4f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("summaries", nargs="+", type=Path, help="summary.csv files of the evaluation")
    parser.add_argument("--pair", action="append", default=[], required=True, help="student=teacher")
    parser.add_argument("--control", action="append", default=[], help="student=imitation arm (H1)")
    parser.add_argument("--gate1", type=Path, help="Gate 1 oracle results for the recovered share")
    parser.add_argument("--alternative", choices=("two-sided", "less"), default="two-sided")
    parser.add_argument("--alpha", type=float, default=ALPHA)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    rows = pd.concat([pd.read_csv(p) for p in args.summaries], ignore_index=True)
    oracle = oracle_makespans(args.gate1) if args.gate1 else None
    try:
        result = verdict(rows, _mapping(args.pair, "--pair"), alternative=args.alternative,
                         alpha=args.alpha, controls=_mapping(args.control, "--control"), oracle=oracle)
    except VerdictError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    result["summaries"] = [str(p) for p in args.summaries]
    _print(result)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
