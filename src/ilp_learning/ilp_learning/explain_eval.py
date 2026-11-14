"""M7: evaluate a learned policy's explanations on recorded runs (display only).

A run that executed learned rules records every candidate pair of every decision
(decisions.parquet, decision-time values only) and every pick the matcher made
(rule_trace.parquet: robot, order, round, rule or fallback, clause). This module
rebuilds each decision's pair set from those rows, re-runs the same iterative matcher
with the same rule artifact, and checks that it reproduces the recorded picks exactly.
Only a reproduced pick is explained, so an explanation is the reason the run acted,
never a story told afterwards.

For every pick it writes the plain-language explanation (ilp_learning.explain) and,
for a rule pick, a contrast: the runner-up robot for the same order and the rule
conditions that robot failed, or the tie-break it lost.

    python -m ilp_learning.explain_eval runs/campaign/final \\
        --arm ilp_outcome_multi_criteria \\
        --rules runs/gate2/rules/o300_robust/oracle_multi_criteria/rule_artifact.json \\
        --out runs/campaign/final/explain --log-seed 201

Outputs in --out: runs.csv (one row per run), summary.json (totals and shares),
logs/<setting>_s<seed>.jsonl and .md for the --log-seed runs, examples.md.
Nothing here changes a decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pandas as pd

from ilp_learning.bk_builder import derive_est_finish_s
from ilp_learning.explain import explain_decision, pair_text
from ilp_learning.matcher import Assignment, BatchMatcher
from ilp_learning.predicates import (
    BATTERY_FIELDS, LOAD_FIELDS, CandidatePair, DecisionFacts, cost_regret,
)
from ilp_learning.rule_artifact import load_rule_artifact
from ilp_learning.rule_compiler import Clause, RuleProgram

# What a recorded decision row can give a pair. Tier B congestion and the workload
# feature are not in decisions.parquet, so a program that needs them is refused.
_RECORDED = ("robot_id", "order_id", "c_static_m", "est_finish_s", "fifo_position",
             *LOAD_FIELDS, *BATTERY_FIELDS)


class NotExplainable(ValueError):
    """The program uses a feature the run did not record."""


def check_explainable(program: RuleProgram) -> None:
    if program.needs_congestion or program.needs_workload:
        raise NotExplainable("the rules use congestion or workload features, which "
                             "decisions.parquet does not record")


def decision_pairs(decisions: pd.DataFrame, manifest: dict) -> dict[int, list[CandidatePair]]:
    """Each decision's eligible pairs, as the strategy built them online."""
    rows = decisions[decisions["eligible"]].copy()
    rows["est_finish_s"] = derive_est_finish_s(rows, manifest)
    fields = [f for f in _RECORDED if f in rows.columns]
    out: dict[int, list[CandidatePair]] = {}
    for decision_id, group in rows.groupby("decision_id", sort=True):
        out[int(decision_id)] = [CandidatePair(**dict(zip(fields, r)))
                                 for r in group[fields].itertuples(index=False, name=None)]
    return out


def _holds(clause: Clause, literal, facts: DecisionFacts, robot: str, order: str) -> bool:
    """Whether one body literal holds for this pair, with the clause's own variables."""
    single = Clause(clause.decision, clause.robot, clause.order, (literal,))
    return (robot, order) in single.covers(facts)


def _best(pairs, key, value):
    vals = [getattr(p, value) for p in pairs if getattr(p, key[0]) == key[1]]
    return min(vals) if vals else None


def missing_statement(name: str, alt: CandidatePair, left: Sequence[CandidatePair],
                      program: RuleProgram) -> str:
    """Why one condition fails for the runner-up pair, with the numbers behind it."""
    R, O = alt.robot_id, alt.order_id  # noqa: E741
    if name == "best_finish_for_order":
        mine, best = pair_text(alt.est_finish_s, min(p.est_finish_s for p in left if p.order_id == O), " s")
        return f"{R} would finish {O} in {mine}, not the soonest ({best})"
    if name == "near_best_cost_for_robot":
        best_m = _best(left, ("robot_id", R), "c_static_m")
        mine, best = pair_text(alt.c_static_m, best_m, " m")
        return (f"{O} is a detour for {R}: {mine} against {best} to its closest order "
                f"({cost_regret(alt.c_static_m, best_m):.0%} longer)")
    if name == "best_margin_for_order":
        mine, best = pair_text(alt.energy_margin_percent,
                               max(p.energy_margin_percent for p in left if p.order_id == O), "%")
        return f"{R} has less battery to spare for {O} ({mine} against {best})"
    if name == "earliest_static_finish":
        mine, best = pair_text(alt.est_finish_s, _best(left, ("robot_id", R), "est_finish_s"), " s")
        return f"{O} is not the order {R} finishes soonest ({mine} against {best})"
    if name == "near_charger_after_order":
        cutoff = float(program.thresholds.cutoffs["charger_distance"][0])
        return (f"after {O}, {R} would end {alt.predicted_charger_distance_m:.0f} m from a charger "
                f"(the rule needs at most {cutoff:.0f} m)")
    if name == "nearest_static":
        best = _best(left, ("robot_id", R), "c_static_m")
        return f"{O} is not {R}'s closest order ({alt.c_static_m:.0f} m against {best:.0f} m)"
    if name == "oldest_fifo":
        return f"{O} is not the oldest open order"
    return f"{name} does not hold for {R} and {O}"


def contrast(program: RuleProgram, pick: Assignment, left: Sequence[CandidatePair]) -> dict | None:
    """The runner-up robot for the picked order, and why it did not get the order.

    The runner-up is the other robot that would finish the order soonest. Either it
    fails some condition of the clause that made the pick, or it met that clause too
    and lost the matcher's tie-break (fifo position, route length, finish, robot_id).
    """
    others = [p for p in left if p.order_id == pick.order_id and p.robot_id != pick.robot_id]
    if not others:
        return None
    alt = min(others, key=lambda p: (p.est_finish_s, p.c_static_m, p.robot_id))
    clause = program.clauses[int(pick.rule_rank)]
    facts = DecisionFacts(left, program.thresholds)
    failed = [lit.predicate for lit in clause.body
              if not _holds(clause, lit, facts, alt.robot_id, alt.order_id)]
    if failed:
        text = "; ".join(missing_statement(n, alt, left, program) for n in failed)
    else:
        text = f"{alt.robot_id} met rule {int(pick.rule_rank) + 1} too; " + tie_break_text(pick, alt)
    return {"robot": alt.robot_id, "failed": failed, "text": text}


def tie_break_text(pick: Assignment, alt: CandidatePair) -> str:
    """Which matcher key separated two pairs for the same order that met the same rule.

    The keys after the clause are fifo position (equal: same order), route length,
    estimated finish, and robot_id, which is an engineering tie-break only.
    """
    if alt.c_static_m != pick.c_static_m:
        mine, theirs = pair_text(pick.c_static_m, alt.c_static_m, " m")
        return f"{pick.robot_id} has the shorter route ({mine} against {theirs})"
    if alt.est_finish_s != pick.est_finish_s:
        mine, theirs = pair_text(pick.est_finish_s, alt.est_finish_s, " s")
        return f"{pick.robot_id} finishes earlier ({mine} against {theirs})"
    return ("every value was equal, so the fixed robot-ID order decided "
            "(an engineering tie-break, not a learned preference)")


def _trace_key(t) -> tuple:
    rank = None if t.fallback else int(t.clause_rank)
    return (t.round, t.robot_id, t.order_id, bool(t.matched_by_rule), rank)


def _pick_key(a: Assignment) -> tuple:
    return (a.round, a.robot_id, a.order_id, a.tier == 0, a.rule_rank if a.tier == 0 else None)


@dataclass
class RunResult:
    row: dict
    log: list[dict] | None


def explain_run(run_dir: Path, rules: Path, keep_log: bool = False) -> RunResult:
    """Replay, explain and contrast every decision of one run."""
    manifest = json.loads((run_dir / "run.json").read_text())
    program, _ = load_rule_artifact(rules)
    check_explainable(program)
    recorded = [a["sha256"] for a in manifest["strategy_identity"]["artifacts"]
                if a["role"] == "rule_artifact"]
    sha = hashlib.sha256(rules.read_bytes()).hexdigest()
    if recorded != [sha]:
        raise ValueError(f"{run_dir}: run used rule artifact {recorded}, not {sha}")

    pairs_by = decision_pairs(pd.read_parquet(run_dir / "decisions.parquet"), manifest)
    trace = pd.read_parquet(run_dir / "rule_trace.parquet").sort_values(["decision_id", "round"])
    matcher = BatchMatcher(mode="iterative")

    n_dec = n_picks = n_rule = mismatched = no_alt = tie_lost = 0
    clauses: Counter = Counter()
    failed_by: Counter = Counter()
    words: list[int] = []
    margins: list[float] = []
    log: list[dict] = []
    for decision_id, recorded_picks in trace.groupby("decision_id", sort=True):
        pairs = pairs_by[int(decision_id)]
        picks = matcher.match_pairs(pairs, program)
        n_dec += 1
        if [_pick_key(a) for a in picks] != [_trace_key(t) for t in recorded_picks.itertuples()]:
            mismatched += 1
            continue
        explained = explain_decision(program, pairs, picks)
        used_r: set[str] = set()
        used_o: set[str] = set()
        for a, e in zip(sorted(picks, key=lambda a: a.round), explained):
            left = [p for p in pairs if p.robot_id not in used_r and p.order_id not in used_o]
            n_picks += 1
            entry = {"decision": int(decision_id), "round": a.round, "robot": a.robot_id,
                     "order": a.order_id, "rule": e["rule"], "because": e["text"]}
            if e["rule"] is not None:
                n_rule += 1
                clauses[e["rule"]] += 1
                words.append(len(e["text"].split()))
                c = contrast(program, a, left)
                if c is None:
                    no_alt += 1
                else:
                    entry["why_not"] = c
                    failed_by.update(c["failed"] or ["tie_break"])
                    tie_lost += not c["failed"]
                    alt = next(p for p in left if p.robot_id == c["robot"] and p.order_id == a.order_id)
                    margins.append(alt.est_finish_s - a.est_finish_s)
            if keep_log:
                log.append(entry)
            used_r.add(a.robot_id)
            used_o.add(a.order_id)

    parts = run_dir.parts
    row = {"run": run_dir.name, "orders": int(manifest["orders"]), "robots": int(manifest["robot_count"]),
           "seed": int(manifest["seed"]), "decisions": n_dec, "mismatched_decisions": mismatched,
           "picks": n_picks, "rule_picks": n_rule, "fallback_picks": n_picks - n_rule,
           **{f"clause_{k}": clauses.get(k, 0) for k in range(1, len(program.clauses) + 1)},
           "with_runner_up": n_rule - no_alt, "runner_up_tie_break": tie_lost,
           **{f"runner_up_failed_{k}": v for k, v in failed_by.items() if k != "tie_break"},
           "words_mean": sum(words) / len(words) if words else math.nan,
           "finish_margin_median_s": float(pd.Series(margins).median()) if margins else math.nan,
           "path": "/".join(parts[-4:])}
    return RunResult(row, log if keep_log else None)


def _log_markdown(name: str, log: list[dict], limit: int) -> str:
    lines = [f"# Explanations: {name}", "",
             "Every pick of the run, in order. Rule n = the learned clause that made the pick; "
             "fallback = no clause covered any remaining pair. Why not = the runner-up robot "
             "for the same order and the condition it failed.", ""]
    current = None
    for e in log[:limit]:
        if e["decision"] != current:
            current = e["decision"]
            lines += ["", f"## Decision {current}", ""]
        tag = f"rule {e['rule']}" if e["rule"] else "fallback"
        lines.append(f"- **{e['robot']} → {e['order']}** ({tag}): {e['because']}")
        if "why_not" in e:
            lines.append(f"  - why not {e['why_not']['robot']}: {e['why_not']['text']}")
    if len(log) > limit:
        lines += ["", f"... {len(log) - limit} more picks in the .jsonl file"]
    return "\n".join(lines) + "\n"


def _examples(logs: dict[str, list[dict]]) -> str:
    """One worked example per kind: each clause, a fallback, a lost tie-break."""
    want = {"rule 1": lambda e: e["rule"] == 1 and e.get("why_not", {}).get("failed"),
            "rule 2": lambda e: e["rule"] == 2 and e.get("why_not", {}).get("failed"),
            "rule 3": lambda e: e["rule"] == 3 and e.get("why_not", {}).get("failed"),
            "fallback": lambda e: e["rule"] is None,
            "tie-break": lambda e: "why_not" in e and not e["why_not"]["failed"]}
    out = ["# Worked examples", "",
           "Taken from the logged runs; the first pick of each kind after the first 50 "
           "decisions, so the fleet is already moving.", ""]
    for kind, test in want.items():
        found = next(((n, e) for n, log in logs.items() for e in log
                      if e["decision"] >= 50 and test(e)), None)
        if found is None:
            out += [f"## {kind}", "", "not found in the logged runs", ""]
            continue
        n, e = found
        out += [f"## {kind}: {n}, decision {e['decision']}", "",
                f"**{e['robot']} takes {e['order']}.** Because {e['because']}."]
        if "why_not" in e:
            out.append(f"Why not {e['why_not']['robot']}: {e['why_not']['text']}.")
        out.append("")
    return "\n".join(out) + "\n"


def trace_stats(campaign: Path) -> pd.DataFrame:
    """Rule or fallback share and clause use for every learned arm, from rule_trace only."""
    rows = []
    for arm_dir in sorted(campaign.glob("o*/r*/ilp_*")):
        for run in sorted(arm_dir.glob("r*_s*")):
            f = run / "rule_trace.parquet"
            if not f.exists():
                continue
            t = pd.read_parquet(f, columns=["matched_by_rule", "clause_rank"])
            row = {"arm": arm_dir.name, "orders": int(arm_dir.parts[-3][1:]),
                   "robots": int(arm_dir.parts[-2][1:]), "run": run.name, "picks": len(t),
                   "rule_picks": int(t.matched_by_rule.sum())}
            row.update({f"clause_{int(k) + 1}": int(v) for k, v in
                        t.clause_rank.dropna().value_counts().items()})
            rows.append(row)
    return pd.DataFrame(rows)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("campaign", type=Path, help="campaign folder with o*/r*/<arm>/r*_s*/")
    ap.add_argument("--arm", default="ilp_outcome_multi_criteria")
    ap.add_argument("--rules", type=Path, required=True, help="the rule_artifact.json the runs used")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--log-seed", type=int, action="append", default=[],
                    help="write full logs for this seed (repeatable)")
    ap.add_argument("--log-limit", type=int, default=600, help="picks per .md log")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--limit", type=int, default=0, help="first N runs only (smoke test)")
    a = ap.parse_args(argv)

    runs = sorted(a.campaign.glob(f"o*/r*/{a.arm}/r*_s*"))
    if a.limit:
        runs = runs[: a.limit]
    if not runs:
        print(f"no runs of {a.arm} under {a.campaign}", file=sys.stderr)
        return 2
    program, _ = load_rule_artifact(a.rules)
    check_explainable(program)
    (a.out / "logs").mkdir(parents=True, exist_ok=True)
    print(f"explaining {len(runs)} runs of {a.arm} with {a.workers} workers", flush=True)

    rows, logs, t0 = [], {}, time.time()
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        futures = {}
        for r in runs:
            keep = int(r.name.rsplit("_s", 1)[1]) in a.log_seed
            futures[pool.submit(explain_run, r, a.rules.resolve(), keep)] = r
        for i, f in enumerate(as_completed(futures), 1):
            res = f.result()
            rows.append(res.row)
            if res.log is not None:
                name = f"o{res.row['orders']}_r{res.row['robots']}_s{res.row['seed']}"
                logs[name] = res.log
                with open(a.out / "logs" / f"{name}.jsonl", "w") as fh:
                    for e in res.log:
                        fh.write(json.dumps(e) + "\n")
                (a.out / "logs" / f"{name}.md").write_text(_log_markdown(name, res.log, a.log_limit))
            el = time.time() - t0
            eta = el / i * (len(runs) - i)
            r = res.row
            print(f"  [{i:3d}/{len(runs)}] {r['path']}: {r['picks']} picks, "
                  f"{r['rule_picks'] / max(r['picks'], 1):.1%} by rule, "
                  f"{r['mismatched_decisions']} mismatched | {el / 60:.1f} min, ETA {eta / 60:.1f} min",
                  flush=True)

    df = pd.DataFrame(rows).sort_values(["orders", "robots", "seed"])
    df.to_csv(a.out / "runs.csv", index=False)
    if logs:
        (a.out / "examples.md").write_text(_examples(dict(sorted(logs.items()))))

    clause_cols = [c for c in df.columns if c.startswith("clause_")]
    failed_cols = [c for c in df.columns if c.startswith("runner_up_failed_")]
    rule = int(df.rule_picks.sum())
    alt = int(df.with_runner_up.sum())
    summary = {
        "arm": a.arm, "rules": str(a.rules), "runs": len(df),
        "decisions": int(df.decisions.sum()), "mismatched_decisions": int(df.mismatched_decisions.sum()),
        "picks": int(df.picks.sum()), "rule_share": rule / int(df.picks.sum()),
        "rule_size": {"clauses": len(program.clauses),
                      "literals": sum(len(c.body) for c in program.clauses),
                      "predicates": sorted({lit.predicate for c in program.clauses for lit in c.body})},
        "clause_share": {c: int(df[c].sum()) / rule for c in clause_cols},
        "runner_up": {"picks_with_runner_up": alt,
                      "lost_on_tie_break": int(df.runner_up_tie_break.sum()) / alt if alt else None,
                      "failed_condition_share": {c.removeprefix("runner_up_failed_"): int(df[c].fillna(0).sum()) / alt
                                                 for c in failed_cols} if alt else {}},
        "words_mean": float((df.words_mean * df.rule_picks).sum() / rule) if rule else None,
        "finish_margin_median_s": float(df.finish_margin_median_s.median()),
        "rule_share_by_setting": {f"{o}_{r}": int(g.rule_picks.sum()) / int(g.picks.sum())
                                  for (o, r), g in df.groupby(["orders", "robots"])},
    }
    (a.out / "summary.json").write_text(json.dumps(summary, indent=1))
    trace_stats(a.campaign).to_csv(a.out / "trace_all_arms.csv", index=False)
    print(json.dumps(summary, indent=1))
    return 1 if summary["mismatched_decisions"] else 0


if __name__ == "__main__":
    sys.exit(main())
