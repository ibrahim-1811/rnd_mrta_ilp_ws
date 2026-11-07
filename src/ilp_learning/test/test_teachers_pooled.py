"""Single-teacher matching per teacher, and pooled multi-teacher demonstrations."""

import json
import shutil

import pandas as pd
import pytest

from ilp_learning.fidelity import score_decision
from ilp_learning.predicates import CandidatePair
from ilp_learning.rule_compiler import RuleProgram
from ilp_learning.teachers import TEACHERS, matching_for, matching_of_artifact
from ilp_learning.train_rules import TrainingError, _strata, load_induction_run, relabel_earliest_finish, train

from .conftest import ACCEPTANCE_RUN

needs_run = pytest.mark.skipif(not ACCEPTANCE_RUN.exists(), reason="runs/m2_acceptance absent")


# ---- which matcher a teacher's rules run under --------------------------------------

def test_every_conventional_teacher_has_a_matcher():
    assert len(TEACHERS) == 8
    assert {t: matching_for(t) for t in TEACHERS} == {
        "random": "iterative", "nearest_manhattan": "iterative", "nearest_astar": "iterative",
        "earliest_finish": "iterative", "multi_criteria": "iterative", "auction": "iterative",
        "hungarian": "cardinality", "milp": "cardinality",
    }


def test_label_sources_without_one_teacher_use_the_default():
    for source in ("pooled:auction+milp", "panel_majority", "outcome_aware"):
        assert matching_for(source) == "iterative"
    with pytest.raises(ValueError):
        matching_for("ilp")


def test_fidelity_scores_under_the_requested_matcher():
    pairs = [CandidatePair("r1", "a", 1.0, 1.0, 0), CandidatePair("r1", "b", 2.0, 2.0, 1),
             CandidatePair("r2", "a", 1.5, 1.5, 0)]
    empty = RuleProgram(())
    teacher = {("r1", "b"), ("r2", "a")}           # a one-shot teacher's full matching
    one_shot = score_decision(pairs, teacher, empty, "cardinality")
    greedy = score_decision(pairs, teacher, empty, "iterative")
    # M-Card fills both robots; the queue-first iterative fallback takes r1:a and
    # leaves r2 with nothing, so a one-shot teacher's rules must be scored under M-Card.
    assert (one_shot["picks"], one_shot["agreed"]) == (2, 2)
    assert (greedy["picks"], greedy["agreed"]) == (1, 0)


# ---- pooled demonstrations ------------------------------------------------------------

def _second_teacher(tmp_path):
    """The acceptance run, and a copy labelled by earliest_finish as its own run."""
    a = tmp_path / "nearest_astar_run"
    b = tmp_path / "earliest_finish_run"
    shutil.copytree(ACCEPTANCE_RUN, a)
    shutil.copytree(ACCEPTANCE_RUN, b)
    manifest = json.loads((b / "run.json").read_text())
    manifest.update(strategy="earliest_finish", run_id="ef00000000000001")
    (b / "run.json").write_text(json.dumps(manifest))
    decisions = pd.read_parquet(b / "decisions.parquet")
    frame = decisions.assign(est_finish_s=decisions["c_static_m"])
    relabelled = relabel_earliest_finish(frame).drop(columns="est_finish_s")
    relabelled.assign(run_id="ef00000000000001").to_parquet(b / "decisions.parquet")
    return a, b


@needs_run
def test_mixed_strategies_are_refused_unless_pooled(tmp_path):
    a, b = _second_teacher(tmp_path)
    with pytest.raises(TrainingError, match="driven by"):
        train([a, b], tmp_path / "out")


@needs_run
def test_pooled_refuses_other_label_sources(tmp_path):
    a, b = _second_teacher(tmp_path)
    with pytest.raises(TrainingError, match="own picks"):
        train([a, b], tmp_path / "out", pooled=True, label_policy="majority")
    # Pooled outcome labels (2026-11-07) only when every run is continued by its own strategy.
    with pytest.raises(TrainingError, match="--continuation behaviour"):
        train([a, b], tmp_path / "out", pooled=True, outcome_rollouts={})
    with pytest.raises(TrainingError, match="--continuation behaviour"):
        train([a, b], tmp_path / "out", pooled=True, outcome_rollouts={}, continuation="nearest_astar")


@needs_run
def test_pooled_strata_share_the_cap_between_teachers(tmp_path):
    a, b = _second_teacher(tmp_path)
    runs = [load_induction_run(a), load_induction_run(b)]
    by_strategy = _strata(runs, by_strategy=True)
    assert {key[0] for key in by_strategy} == {"nearest_astar", "earliest_finish"}
    # Without pooling the keys are unchanged (single-teacher training stays as it was).
    assert all(not isinstance(key[0], str) for key in _strata(runs[:1]))


@needs_run
def test_pooled_training_records_each_teachers_contribution(tmp_path):
    a, b = _second_teacher(tmp_path)
    artifact = train([a, b], tmp_path / "out", pooled=True, decision_cap=12, timeout_s=20)
    assert artifact["teacher"] == "pooled:earliest_finish+nearest_astar"
    assert artifact["behaviour_policy"] == "earliest_finish+nearest_astar"
    pool = artifact["pool"]
    assert set(pool) == {"earliest_finish", "nearest_astar"}
    assert sum(p["decisions"] for p in pool.values()) == artifact["sampling"]["decisions"] == 12
    assert all(p["decisions"] >= 5 and p["positives"] > 0 for p in pool.values())
    assert sum(p["positives"] for p in pool.values()) == artifact["sampling"]["positives"]
    assert matching_of_artifact(tmp_path / "out" / "rule_artifact.json") == "iterative"
