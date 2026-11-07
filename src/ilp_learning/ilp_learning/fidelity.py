"""Offline, open-loop fidelity of a rule artifact on recorded teacher runs (M3).

    python -m ilp_learning.fidelity ARTIFACT RUN_DIR... --out metrics.json

Each recorded decision's eligible pairs are replayed through the fixed iterative
executor (matcher.py) and the picks compared with the teacher's selection. The
state stays the teacher's: no closed-loop effect is measured here. Reads
run.json and decisions.parquet only, and refuses a run that is not induction data.

    frozen_key      |picks & teacher| / |teacher|, the G1 and G5 quantity
    tie_neutral     as frozen_key, comparing c_static_m multisets, so a pick
                    differing from the teacher's only by an equal-cost swap agrees
    distinct_cost   as frozen_key, after a tiny jitter makes costs follow the
                    teacher's own order (cost, fifo_position, robot_id)
    exact_decision  share of decisions whose whole pick set equals the teacher's
    rule_coverage   share of picks made by a rule (G3); fallback is the rest (G4)

Each is reported over choice-bearing decisions (primary) and over all decisions.
The matcher is the artifact teacher's (ilp_learning.teachers): iterative for teachers
that pick one pair at a time, one-shot M-Card for hungarian and milp; --matching
overrides it.
Clause and program precision and recall are static, over all eligible pairs of
choice-bearing decisions; a clause's picks are reported beside them.
"""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd

from ilp_learning.bk_builder import candidate_pairs, derive_est_finish_s
from ilp_learning.matcher import BatchMatcher, PickKey
from ilp_learning.predicates import CandidatePair, TierAFacts
from ilp_learning.rule_artifact import load_rule_artifact
from ilp_learning.rule_compiler import RuleProgram
from ilp_learning.teachers import matching_of_artifact
from ilp_learning.train_rules import InductionRun, load_induction_run
from simulation_env.failures import FAILURE_FILE
from simulation_env.seed_split import role_of, workload_seed_of


# Both steps sit above the predicates' tie tolerance. Their largest total, at a
# fifo_position of 299, stays far below the 1 m gap between distinct W100 costs.
FIFO_JITTER = 1e-5
ROBOT_JITTER = 1e-7
COST_DIGITS = 6
SCOPES = ("choice_bearing", "all")
COUNTERS = (
    "decisions", "teacher_pairs", "picks", "agreed", "tie_agreed", "distinct_agreed",
    "exact_decisions", "rule_picks", "fallback_picks",
)


def distinct_cost_pairs(pairs: Sequence[CandidatePair]) -> list[CandidatePair]:
    """The pairs with ties split the way the teacher splits them."""
    ranks = {robot: i for i, robot in enumerate(sorted({p.robot_id for p in pairs}))}
    return [
        p._replace(
            c_static_m=p.c_static_m + FIFO_JITTER * p.fifo_position + ROBOT_JITTER * ranks[p.robot_id]
        )
        for p in pairs
    ]


def _cost_multiset(pairs: Sequence[CandidatePair]) -> Counter:
    return Counter(round(p.c_static_m, COST_DIGITS) for p in pairs)


def score_decision(
    pairs: Sequence[CandidatePair], teacher: set[tuple[str, str]], program: RuleProgram,
    matching: str = "iterative",
) -> dict:
    """One decision: counters, and the picks made by each clause."""
    matcher = BatchMatcher(matching)
    by_pair = {(p.robot_id, p.order_id): p for p in pairs}
    picks = matcher.match_pairs(pairs, program)
    chosen = {(a.robot_id, a.order_id) for a in picks}
    distinct = matcher.match_pairs(distinct_cost_pairs(pairs), program)

    tied = _cost_multiset([by_pair[pair] for pair in chosen]) & _cost_multiset(
        [by_pair[pair] for pair in teacher]
    )
    clause_picks: Counter = Counter()
    clause_agreed: Counter = Counter()
    for a in picks:
        if a.matched_by_rule:
            clause_picks[a.rule_rank] += 1
            clause_agreed[a.rule_rank] += (a.robot_id, a.order_id) in teacher
    return {
        "decisions": 1,
        "teacher_pairs": len(teacher),
        "picks": len(picks),
        "agreed": len(chosen & teacher),
        "tie_agreed": sum(tied.values()),
        "distinct_agreed": len({(a.robot_id, a.order_id) for a in distinct} & teacher),
        "exact_decisions": int(chosen == teacher),
        "rule_picks": sum(a.matched_by_rule for a in picks),
        "fallback_picks": sum(not a.matched_by_rule for a in picks),
        "clause_picks": clause_picks,
        "clause_agreed": clause_agreed,
    }


def _static_quality(pairs, teacher, program: RuleProgram) -> dict[str, Counter]:
    """tp/fp/fn on the decision's full pair set: per clause, and -1 for the program."""
    facts = TierAFacts(pairs)
    quality: dict[str, Counter] = {}
    union: set = set()
    for rank, clause in enumerate(program.clauses):
        covered = clause.covers(facts)
        union |= covered
        quality[str(rank)] = Counter(
            tp=len(covered & teacher), fp=len(covered - teacher), fn=len(teacher - covered)
        )
    quality["program"] = Counter(
        tp=len(union & teacher), fp=len(union - teacher), fn=len(teacher - union)
    )
    return quality


def evaluate_frame(frame: pd.DataFrame, program: RuleProgram, matching: str = "iterative") -> dict:
    """Counters by scope, for one run's decision rows carrying est_finish_s."""
    totals = {scope: Counter() for scope in SCOPES}
    clause_picks: Counter = Counter()
    clause_agreed: Counter = Counter()
    quality: dict[str, Counter] = {}

    for _, rows in frame[frame["eligible"]].groupby("decision_id"):
        chosen_rows = rows[rows["selected"]]
        teacher = set(zip(chosen_rows["robot_id"], chosen_rows["order_id"]))
        if not teacher:
            continue
        pairs = candidate_pairs(rows)
        scored = score_decision(pairs, teacher, program, matching)
        bearing = bool(rows["choice_bearing"].iloc[0])
        counts = {key: scored[key] for key in COUNTERS}
        totals["all"].update(counts)
        if bearing:
            totals["choice_bearing"].update(counts)
            clause_picks.update(scored["clause_picks"])
            clause_agreed.update(scored["clause_agreed"])
            for key, counter in _static_quality(pairs, teacher, program).items():
                quality.setdefault(key, Counter()).update(counter)

    unselected = int((frame["selected"] & ~frame["eligible"]).sum())
    if unselected:
        raise ValueError(f"{unselected} teacher pairs are not eligible")
    return {
        "scopes": {scope: dict(counter) for scope, counter in totals.items()},
        "clause_picks": {str(k): v for k, v in clause_picks.items()},
        "clause_agreed": {str(k): v for k, v in clause_agreed.items()},
        "static": {key: dict(counter) for key, counter in quality.items()},
    }


def rates(counts: Mapping[str, int]) -> dict[str, float]:
    """Rates of one scope's counters."""
    teacher, picks = counts.get("teacher_pairs", 0), counts.get("picks", 0)
    decisions = counts.get("decisions", 0)

    def share(top: int, bottom: int) -> float:
        return top / bottom if bottom else float("nan")

    return {
        "frozen_key": share(counts.get("agreed", 0), teacher),
        "tie_neutral": share(counts.get("tie_agreed", 0), teacher),
        "distinct_cost": share(counts.get("distinct_agreed", 0), teacher),
        "exact_decision": share(counts.get("exact_decisions", 0), decisions),
        "rule_coverage": share(counts.get("rule_picks", 0), picks),
        "fallback": share(counts.get("fallback_picks", 0), picks),
    }


def pooled(results: Sequence[Mapping]) -> dict:
    """Counters summed over runs, with their rates."""
    merged: dict = {"scopes": {}, "clause_picks": Counter(), "clause_agreed": Counter(), "static": {}}
    for scope in SCOPES:
        total: Counter = Counter()
        for result in results:
            total.update(result["scopes"][scope])
        merged["scopes"][scope] = {"counts": dict(total), "rates": rates(total)}
    for result in results:
        merged["clause_picks"].update(result["clause_picks"])
        merged["clause_agreed"].update(result["clause_agreed"])
        for key, counter in result["static"].items():
            merged["static"].setdefault(key, Counter()).update(counter)
    for key, counter in merged["static"].items():
        tp, fp, fn = counter["tp"], counter["fp"], counter["fn"]
        merged["static"][key] = {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
        }
    merged["clause_picks"] = dict(merged["clause_picks"])
    merged["clause_agreed"] = dict(merged["clause_agreed"])
    return merged


def load_validation_run(run_dir: Path | str) -> InductionRun:
    """A finished recorded run whose workload is a validation seed."""
    path = Path(run_dir)
    manifest = json.loads((path / "run.json").read_text(encoding="utf-8"))
    seed = workload_seed_of(manifest["orders_path"])
    if role_of(seed) != "validation":
        raise ValueError(f"{path}: workload seed {seed} is not a validation seed")
    if (path / FAILURE_FILE).exists():
        raise ValueError(f"{path} records a failed run")
    return InductionRun(manifest, pd.read_parquet(path / "decisions.parquet"))


def random_key(repeat: int) -> PickKey:
    """A repeatable random order of candidates, the same for every replay of `repeat`."""

    def key(pair: CandidatePair) -> tuple:
        digest = sha256(f"{repeat}|{pair.robot_id}|{pair.order_id}".encode()).hexdigest()
        return (digest, 0.0, 0.0, pair.robot_id)

    return key


def teacher_key(teacher: set[tuple[str, str]]) -> PickKey:
    """Oracle diagnostic: the teacher's own pairs first, then the frozen fields."""

    def key(pair: CandidatePair) -> tuple:
        rank = 0 if (pair.robot_id, pair.order_id) in teacher else 1
        return (rank, pair.fifo_position, pair.c_static_m, pair.robot_id)

    return key


def key_agreement(frame: pd.DataFrame, program: RuleProgram, keys: Sequence[PickKey] = ()) -> dict:
    """Agreed teacher pairs per key over choice-bearing decisions; no keys means teacher-key."""
    agreed = [0] * max(len(keys), 1)
    teacher_pairs = 0
    eligible = frame[frame["eligible"] & frame["choice_bearing"]]
    for _, rows in eligible.groupby("decision_id"):
        chosen = rows[rows["selected"]]
        teacher = set(zip(chosen["robot_id"], chosen["order_id"]))
        if not teacher:
            continue
        teacher_pairs += len(teacher)
        pairs = candidate_pairs(rows)
        for index, key in enumerate(keys or [teacher_key(teacher)]):
            picks = BatchMatcher("iterative", pick_key=key).match_pairs(pairs, program)
            agreed[index] += len({(a.robot_id, a.order_id) for a in picks} & teacher)
    return {"agreed": agreed, "teacher_pairs": teacher_pairs}


def evaluate_runs(run_dirs: Sequence[Path | str], program: RuleProgram, load=load_induction_run,
                  matching: str = "iterative") -> dict:
    """Per-seed results and the pool of a program over recorded runs."""
    per_seed = {}
    for run_dir in run_dirs:
        run = load(run_dir)
        frame = run.decisions.assign(est_finish_s=derive_est_finish_s(run.decisions, run.manifest))
        per_seed[workload_seed_of(run.manifest["orders_path"])] = evaluate_frame(frame, program, matching)
    return {
        "per_seed": {
            str(seed): {
                scope: rates(result["scopes"][scope]) for scope in SCOPES
            }
            for seed, result in sorted(per_seed.items())
        },
        "per_seed_results": {str(seed): result for seed, result in sorted(per_seed.items())},
        "pooled": pooled(list(per_seed.values())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", help="rule_artifact.json")
    parser.add_argument("runs", nargs="+", help="recorded induction run directories")
    parser.add_argument("--out", required=True)
    parser.add_argument("--matching", choices=BatchMatcher.MODES, default=None,
                        help="default: the artifact teacher's matcher (ilp_learning.teachers)")
    args = parser.parse_args()

    program, artifact = load_rule_artifact(args.artifact)
    matching = args.matching or matching_of_artifact(args.artifact)
    result = evaluate_runs(args.runs, program, matching=matching)
    result["artifact_sha256"] = artifact["content_sha256"]
    result["matching"] = matching
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["pooled"]["scopes"]["choice_bearing"]["rates"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
