"""Random-key and teacher-key replay, the G10 controls, and the G5 and G8 arithmetic."""

import json

import pandas as pd
import pytest

from ilp_learning import m3_induction as m3
from ilp_learning import m3_validation as v
from ilp_learning.bias_templates import generate_bias_pl
from ilp_learning.fidelity import key_agreement, load_validation_run, random_key, teacher_key
from ilp_learning.matcher import BatchMatcher
from ilp_learning.predicates import CandidatePair
from ilp_learning.rule_compiler import RuleProgram, parse_clause
from ilp_learning.train_rules import permute_labels, relabel_earliest_finish
from simulation_env.failures import FAILURE_FILE


PAIRS = [
    CandidatePair("r1", "o1", 5.0, 9.0, 0),
    CandidatePair("r1", "o2", 4.0, 7.0, 1),
    CandidatePair("r2", "o1", 3.0, 8.0, 0),
    CandidatePair("r2", "o2", 6.0, 6.0, 1),
]
EMPTY = RuleProgram(())


def _frame():
    rows = [
        dict(decision_id=0, robot_id=p.robot_id, order_id=p.order_id, c_static_m=p.c_static_m,
             est_finish_s=p.est_finish_s, fifo_position=p.fifo_position, eligible=True,
             choice_bearing=True, selected=False)
        for p in PAIRS
    ]
    frame = pd.DataFrame(rows)
    frame.loc[[1, 2], "selected"] = True  # r1-o2 and r2-o1: the nearest_astar answer
    return frame


def test_the_default_key_is_untouched_and_a_pick_key_replaces_only_the_tie_fields():
    frozen = BatchMatcher("iterative").match_pairs(PAIRS, EMPTY)
    assert [(a.robot_id, a.order_id) for a in frozen] == [("r2", "o1"), ("r1", "o2")]
    rule = RuleProgram((parse_clause("assign(A,B,C):- nearest_static(A,B,C)."),))
    keyed = BatchMatcher("iterative", pick_key=random_key(0)).match_pairs(PAIRS, rule)
    assert all(pick.matched_by_rule for pick in keyed)


def test_random_key_repeats_exactly_and_differs_between_repeats():
    assert random_key(3)(PAIRS[0]) == random_key(3)(PAIRS[0])
    assert random_key(3)(PAIRS[0]) != random_key(4)(PAIRS[0])


def test_teacher_key_puts_the_teachers_pairs_first():
    key = teacher_key({("r1", "o1")})
    assert key(PAIRS[0]) < key(PAIRS[1])
    assert key_agreement(_frame(), EMPTY) == {"agreed": [2], "teacher_pairs": 2}


def test_controls_keep_the_pick_count_and_stay_one_to_one():
    frame = _frame()
    for changed in (permute_labels(frame, 1), relabel_earliest_finish(frame)):
        picked = changed[changed["selected"]]
        assert len(picked) == 2
        assert picked["robot_id"].is_unique and picked["order_id"].is_unique


def test_relabel_follows_the_finish_estimate():
    picked = relabel_earliest_finish(_frame())
    chosen = set(zip(picked[picked["selected"]]["robot_id"], picked[picked["selected"]]["order_id"]))
    assert chosen == {("r2", "o2"), ("r1", "o1")}


def test_removing_a_predicate_removes_it_from_the_bias_only():
    bias = generate_bias_pl(include_tier_b=False, exclude=("best_cost_for_order",))
    assert "best_cost_for_order" not in bias and "nearest_static" in bias


@pytest.mark.parametrize(
    "ratios, verdict",
    [
        ([0.0] * 5, "identical"),
        ([0.001, -0.001, 0.0, 0.002, -0.002], "equivalent"),
        ([0.5, 0.5, 0.51, 0.49, 0.5], "non-equivalent"),
        ([0.0, 0.1, -0.1, 0.05, 0.01], "inconclusive"),
    ],
)
def test_g8_verdicts(ratios, verdict):
    assert v.equivalence(ratios, 0.05)["verdict"] == verdict


def test_a_learned_campaign_cannot_start_without_the_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(v, "git_state", lambda: ("abc", False, []))
    with pytest.raises(SystemExit, match="not tagged"):
        v.check_may_start(tmp_path, tmp_path)
    monkeypatch.setattr(v, "git_state", lambda: ("abc", True, [v.TAG]))
    with pytest.raises(SystemExit, match="not clean"):
        v.check_may_start(tmp_path, tmp_path)


def test_the_campaign_writes_only_to_its_one_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(v, "git_state", lambda: ("abc", False, [v.TAG]))
    with pytest.raises(SystemExit, match="writes only to"):
        v.check_may_start(tmp_path, tmp_path)


def test_a_second_campaign_from_another_commit_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(v, "REPO", tmp_path)
    monkeypatch.setattr(v, "check_induction", lambda *args: None)
    out = (tmp_path / v.DEFAULT_OUT).resolve()
    monkeypatch.setattr(v, "git_state", lambda: ("abc", False, [v.TAG]))
    v.check_may_start(out, tmp_path)
    monkeypatch.setattr(v, "git_state", lambda: ("def", False, [v.TAG]))
    with pytest.raises(SystemExit, match="another commit"):
        v.check_may_start(out, tmp_path)


def test_incomplete_induction_evidence_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="does not exist"):
        v.check_induction(tmp_path, "abc")
    (tmp_path / "summary.json").write_text(json.dumps({"rules": {}}))
    with pytest.raises(SystemExit, match="lacks"):
        v.check_induction(tmp_path, "abc")


def test_a_folder_with_another_commits_evidence_is_refused(tmp_path, monkeypatch):
    (tmp_path / "commit.txt").write_text("old\n")
    monkeypatch.setattr(m3, "head_commit", lambda: "new")
    with pytest.raises(SystemExit, match="another commit"):
        m3.check_fresh(tmp_path)


def test_an_unmarked_folder_with_content_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(m3, "head_commit", lambda: "new")
    (tmp_path / "old_run").mkdir()
    with pytest.raises(SystemExit, match="another commit"):
        m3.check_fresh(tmp_path)


def _campaign(tmp_path):
    return v.Campaign(tmp_path, tmp_path, (104, 105), "induction", 1)


def test_a_failed_teacher_is_recorded_and_both_quanta_are_tried(tmp_path, monkeypatch):
    tried = []

    def fail(command, log):
        tried.append(command[command.index("--name") + 1])
        raise RuntimeError("boom")

    monkeypatch.setattr(m3, "_call", fail)
    failed = v.run_teachers(_campaign(tmp_path))
    assert tried == ["q3", "q0"] and len(failed) == 2


def test_a_learned_run_that_did_not_finish_is_not_scored(tmp_path):
    with pytest.raises(RuntimeError, match="did not finish"):
        v.condition_gates(_campaign(tmp_path), "q3", 30, {}, {})


def test_a_dry_run_may_not_write_to_the_campaign_folder(monkeypatch):
    monkeypatch.setattr("sys.argv", ["m3_validation", "--dry-run"])
    with pytest.raises(SystemExit, match="dry run may not"):
        v.main()


def test_validation_loading_refuses_other_seeds_and_failed_runs(tmp_path):
    (tmp_path / "run.json").write_text(json.dumps({"orders_path": "w/orders_s104.csv"}))
    with pytest.raises(ValueError, match="not a validation seed"):
        load_validation_run(tmp_path)
    (tmp_path / "run.json").write_text(json.dumps({"orders_path": "w/orders_s111.csv"}))
    (tmp_path / FAILURE_FILE).write_text("{}")
    with pytest.raises(ValueError, match="failed run"):
        load_validation_run(tmp_path)


def test_a_robot_on_two_missions_at_once_is_counted():
    outcomes = pd.DataFrame({
        "robot_id": ["r1", "r1", "r2"], "launched_at": [0.0, 5.0, 0.0], "completed_at": [10.0, 20.0, 5.0],
    })
    assert v.overlapping_missions(outcomes) == 1
    assert v.overlapping_missions(outcomes.assign(launched_at=[0.0, 10.0, 0.0])) == 0


def test_stop_rule_ignores_quantum_zero():
    fine = {"G6": {"g1_met_on": 3, "pass": True}, "G7": {"pass": True}}
    bad = {"G6": {"g1_met_on": 0, "pass": False}, "G7": {"pass": False}}
    gates = {"conditions": {"q3/30": fine, "q0/70": bad}, "G11": {"pass": True}}
    assert not v.stop_rule(gates)["stop"]
    gates["conditions"]["q3/70"] = bad
    assert v.stop_rule(gates)["stop"]


def test_distractors_never_include_the_target_pair():
    programs = v.distractor_programs()
    assert "nearest_static+best_cost_for_order" not in programs
    assert len(programs) == 5 + 9
