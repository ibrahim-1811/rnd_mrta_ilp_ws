"""M7 explanation evaluation: replay of recorded picks, contrasts and refusal."""

import json

import pandas as pd
import pytest

from ilp_learning.explain import explain_decision, pair_text
from ilp_learning.explain_eval import (
    NotExplainable, check_explainable, contrast, decision_pairs, explain_run, main,
)
from ilp_learning.matcher import BatchMatcher
from ilp_learning.predicates import CandidatePair
from ilp_learning.rule_compiler import RuleProgram, parse_clause

from .conftest import DEVELOPMENT_SEED, R_AND_O, artifact_content, run_workload

RULE = "assign(A,B,C):- best_finish_for_order(A,B,C),nearest_static(A,B,C)."


def pair(robot, order, cost, finish, fifo):
    return CandidatePair(robot, order, cost, finish, fifo)


def test_pair_text_adds_decimals_only_when_rounding_would_hide_a_difference():
    assert pair_text(41.2, 40.8, " s") == ("41.2 s", "40.8 s")
    assert pair_text(41.0, 52.0, " s") == ("41 s", "52 s")
    assert pair_text(84.0, 84.0, "%") == ("84%", "84%")


def test_contrast_names_the_runner_up_and_the_condition_it_failed():
    program = RuleProgram((parse_clause(RULE),))
    pairs = [pair("r1", "a", 10.0, 20.0, 0), pair("r2", "a", 12.0, 26.0, 0),
             pair("r2", "b", 40.0, 50.0, 1)]
    first = BatchMatcher(mode="iterative").match_pairs(pairs, program)[0]
    assert (first.robot_id, first.order_id) == ("r1", "a")
    c = contrast(program, first, pairs)
    assert c["robot"] == "r2" and c["failed"] == ["best_finish_for_order"]
    assert "r2 would finish a in 26 s, not the soonest (20 s)" in c["text"]


def test_contrast_reports_the_tie_break_when_the_runner_up_met_the_rule_too():
    program = RuleProgram((parse_clause("assign(A,B,C):- candidate_pair(A,B,C)."),))
    pairs = [pair("r1", "a", 10.0, 20.0, 0), pair("r2", "a", 12.0, 20.0, 0)]
    first = BatchMatcher(mode="iterative").match_pairs(pairs, program)[0]
    c = contrast(program, first, pairs)
    assert c["failed"] == [] and "r1 has the shorter route (10 m against 12 m)" in c["text"]


def test_no_runner_up_when_one_robot_can_take_the_order():
    program = RuleProgram((parse_clause(RULE),))
    pairs = [pair("r1", "a", 10.0, 20.0, 0)]
    first = BatchMatcher(mode="iterative").match_pairs(pairs, program)[0]
    assert contrast(program, first, pairs) is None


def test_rules_on_unrecorded_features_are_refused():
    workload = RuleProgram((parse_clause(
        "assign(A,B,C):- least_driven_robot(A,B),nearest_static(A,B,C)."),))
    with pytest.raises(NotExplainable):
        check_explainable(workload)


def test_decision_pairs_keep_eligible_rows_and_rebuild_the_finish_estimate():
    rows = pd.DataFrame({
        "decision_id": [0, 0, 1], "robot_id": ["r1", "r2", "r1"], "order_id": ["a", "a", "b"],
        "c_static_m": [15.0, 30.0, 3.0], "fifo_position": [0, 0, 0],
        "order_shelf_count": [2, 2, 1], "eligible": [True, False, True]})
    out = decision_pairs(rows, {"speed_mps": 1.5, "shelf_pick_s": 1.0, "dropoff_s": 1.0})
    assert [p.robot_id for p in out[0]] == ["r1"]
    assert out[0][0].est_finish_s == pytest.approx(15.0 / 1.5 + 2 + 1)
    assert out[1][0].est_finish_s == pytest.approx(3.0 / 1.5 + 1 + 1)


@pytest.fixture(scope="module")
def recorded_run(tmp_path_factory):

    from ilp_learning.rule_artifact import write_rule_artifact

    folder = tmp_path_factory.mktemp("explain")
    rules = folder / "rule_artifact.json"
    write_rule_artifact(rules, artifact_content([R_AND_O]))
    out = folder / "o60" / "r12" / "ilp_test" / f"r12_s{DEVELOPMENT_SEED}"
    done = run_workload(out, "--strategy", "ilp", "--rule-artifact", str(rules),
                        "--robots", "12", "--orders", "60", "--seed", DEVELOPMENT_SEED)
    assert done.returncode == 0, done.stdout + done.stderr
    return out, rules


def test_every_recorded_pick_of_a_real_run_is_replayed_and_explained(recorded_run):
    run, rules = recorded_run
    result = explain_run(run, rules, keep_log=True)
    row = result.row
    assert row["mismatched_decisions"] == 0
    assert row["picks"] == 60 and len(result.log) == 60
    assert row["rule_picks"] + row["fallback_picks"] == row["picks"]
    assert all(e["because"] for e in result.log)


def test_a_tampered_trace_is_counted_as_a_mismatch(recorded_run, tmp_path):
    import shutil

    run, rules = recorded_run
    copy = tmp_path / run.name
    shutil.copytree(run, copy)
    trace = pd.read_parquet(copy / "rule_trace.parquet")
    first = trace.index[(trace.decision_id == trace.decision_id.min())][0]
    trace.loc[first, "order_id"] = "not_an_order"
    trace.to_parquet(copy / "rule_trace.parquet")
    assert explain_run(copy, rules).row["mismatched_decisions"] == 1


def test_a_run_made_with_other_rules_is_refused(recorded_run, make_artifact):
    run, _ = recorded_run
    other = make_artifact(["assign(A,B,C):- nearest_static(A,B,C)."], name="other.json")
    with pytest.raises(ValueError, match="run used rule artifact"):
        explain_run(run, other)


def test_the_command_writes_logs_summary_and_exits_zero(recorded_run, tmp_path):
    run, rules = recorded_run
    campaign = run.parents[3]
    code = main([str(campaign), "--arm", "ilp_test", "--rules", str(rules), "--out", str(tmp_path),
                 "--log-seed", DEVELOPMENT_SEED, "--workers", "1"])
    assert code == 0
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["mismatched_decisions"] == 0 and summary["picks"] == 60
    assert (tmp_path / "logs" / f"o60_r12_s{DEVELOPMENT_SEED}.md").exists()
    assert (tmp_path / "examples.md").exists() and (tmp_path / "trace_all_arms.csv").exists()


def test_live_explanations_unchanged_for_tier_a_rules():
    program = RuleProgram((parse_clause(RULE),))
    pairs = [pair("r1", "a", 10.0, 20.0, 0), pair("r2", "a", 25.0, 35.0, 0)]
    picks = BatchMatcher(mode="iterative").match_pairs(pairs, program)
    assert explain_decision(program, pairs, picks)[0]["text"].startswith(
        "r1 finishes a soonest (20 s, next best 35 s)")
