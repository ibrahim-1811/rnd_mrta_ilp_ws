"""Offline fidelity replay of a rule program against a teacher's recorded picks."""

import pandas as pd
import pytest

from ilp_learning.fidelity import (
    distinct_cost_pairs,
    evaluate_frame,
    pooled,
    rates,
    score_decision,
)
from ilp_learning.predicates import CandidatePair
from ilp_learning.rule_compiler import RuleProgram, parse_clause

R_AND_O = RuleProgram(
    (parse_clause("assign(A,B,C):- best_cost_for_order(A,B,C),nearest_static(A,B,C)."),)
)
EMPTY = RuleProgram()


def P(robot, order, cost, fifo):
    return CandidatePair(robot, order, cost, cost, fifo)


def frame(rows):
    """rows: (decision_id, robot, order, cost, fifo, selected, choice_bearing)."""
    columns = [
        "decision_id", "robot_id", "order_id", "c_static_m", "fifo_position",
        "selected", "choice_bearing",
    ]
    table = pd.DataFrame(rows, columns=columns)
    return table.assign(eligible=True, est_finish_s=table["c_static_m"])


def test_a_rule_matching_the_teacher_scores_one():
    pairs = [P("r1", "a", 1, 0), P("r1", "b", 5, 1), P("r2", "a", 4, 0), P("r2", "b", 2, 1)]
    scored = score_decision(pairs, {("r1", "a"), ("r2", "b")}, R_AND_O)
    assert scored["agreed"] == scored["teacher_pairs"] == 2
    assert scored["exact_decisions"] == 1
    assert scored["rule_picks"] == 2 and scored["fallback_picks"] == 0


def test_an_empty_program_is_all_fallback_and_follows_the_queue():
    pairs = [P("r1", "a", 9, 0), P("r1", "b", 1, 1)]
    scored = score_decision(pairs, {("r1", "b")}, EMPTY)
    assert scored["agreed"] == 0 and scored["exact_decisions"] == 0
    assert scored["fallback_picks"] == 1 and scored["rule_picks"] == 0


def test_an_equal_cost_swap_misses_frozen_key_but_not_tie_neutral():
    # r1 and r2 both reach a and b at equal cost. The teacher takes the older order
    # first with the cheaper robot; the frozen key is the same here, so force the
    # difference by naming the other pairing as the teacher's.
    pairs = [P("r1", "a", 3, 0), P("r1", "b", 3, 1), P("r2", "a", 3, 0), P("r2", "b", 3, 1)]
    scored = score_decision(pairs, {("r1", "b"), ("r2", "a")}, R_AND_O)
    assert scored["agreed"] == 0
    assert scored["tie_agreed"] == 2


def test_distinct_cost_jitter_follows_the_teachers_order_and_exceeds_the_tolerance():
    pairs = [P("r2", "a", 3, 0), P("r1", "b", 3, 1), P("r1", "a", 3, 0)]
    jittered = {(p.robot_id, p.order_id): p.c_static_m for p in distinct_cost_pairs(pairs)}
    assert jittered[("r1", "a")] < jittered[("r2", "a")] < jittered[("r1", "b")]
    assert jittered[("r2", "a")] - jittered[("r1", "a")] > 1e-9


def test_scopes_keep_forced_decisions_out_of_the_primary_rate():
    table = frame(
        [
            (0, "r1", "a", 1, 0, True, False),
            (1, "r1", "b", 9, 0, False, True),
            (1, "r1", "c", 1, 1, True, True),
        ]
    )
    result = evaluate_frame(table, EMPTY)
    assert rates(result["scopes"]["all"])["frozen_key"] == 0.5
    assert rates(result["scopes"]["choice_bearing"])["frozen_key"] == 0.0


def test_static_quality_counts_every_eligible_pair():
    table = frame(
        [
            (0, "r1", "a", 1, 0, True, True),
            (0, "r1", "b", 2, 1, False, True),
            (0, "r2", "b", 1, 1, False, True),
            (0, "r2", "a", 2, 0, True, True),
        ]
    )
    merged = pooled([evaluate_frame(table, R_AND_O)])
    assert merged["static"]["program"] == {
        "tp": 1, "fp": 1, "fn": 1, "precision": 0.5, "recall": 0.5,
    }


def test_a_teacher_pair_outside_the_eligible_set_is_refused():
    table = frame([(0, "r1", "a", 1, 0, True, True)]).assign(eligible=False)
    with pytest.raises(ValueError):
        evaluate_frame(table, EMPTY)


# ---- Tier B rules scored offline (2026-11-07) ---------------------------------------
# A learned rule with a congestion predicate reads the run's features.parquet; fidelity
# joins it as train_rules does, and refuses (never silently mis-scores) without it.

def _tier_b_program():
    from ilp_learning.rule_artifact import load_rule_artifact

    from .test_tier_b_parity import LIVE_THRESHOLDS, TIER_B_RULE, congestion_artifact

    def make(tmp_path):
        program, _ = load_rule_artifact(
            congestion_artifact(tmp_path, clauses=[TIER_B_RULE], thresholds=LIVE_THRESHOLDS))
        return program

    return make


def test_a_congestion_rule_is_scored_with_the_runs_features(tmp_path):
    from ilp_learning.fidelity import evaluate_runs

    from .conftest import ACCEPTANCE_RUN

    if not (ACCEPTANCE_RUN / "features.parquet").exists():
        pytest.skip("runs/m2_acceptance absent")
    program = _tier_b_program()(tmp_path)
    assert program.needs_congestion
    result = evaluate_runs([ACCEPTANCE_RUN], program)
    clause_picks = next(iter(result["per_seed_results"].values()))["clause_picks"]
    assert sum(clause_picks.values()) > 0


def test_a_congestion_rule_without_features_is_refused(tmp_path):
    import shutil

    from ilp_learning.fidelity import evaluate_runs
    from ilp_learning.predicates import MissingCongestionData

    from .conftest import ACCEPTANCE_RUN

    if not ACCEPTANCE_RUN.exists():
        pytest.skip("runs/m2_acceptance absent")
    bare = tmp_path / "bare"
    bare.mkdir()
    for name in ("run.json", "decisions.parquet"):
        shutil.copy(ACCEPTANCE_RUN / name, bare / name)
    with pytest.raises(MissingCongestionData):
        evaluate_runs([bare], _tier_b_program()(tmp_path))
