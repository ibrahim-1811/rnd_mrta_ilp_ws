"""Gate 2: rules trained on oracle runs, and the guards around them (2026-11-10)."""

import json

import pytest

from ilp_learning.m6_oracle import oracle_run, replay_checked, write_trainable_run
from ilp_learning.m6_rollouts import corpus_builder
from ilp_learning.teachers import matching_for, matching_of_artifact
from ilp_learning.train_rules import TrainingError, train

from .conftest import ACCEPTANCE_RUN

needs_run = pytest.mark.skipif(not ACCEPTANCE_RUN.exists(), reason="runs/m2_acceptance absent")


@pytest.fixture(scope="module")
def oracle_dir(tmp_path_factory):
    """The acceptance run's first four decisions judged by the oracle, as a run folder."""
    manifest = json.loads((ACCEPTANCE_RUN / "run.json").read_text())
    build = corpus_builder(manifest)
    executor, verdicts = oracle_run(build, run_id=manifest["run_id"], behaviour=manifest["strategy"],
                                    max_decisions=4)
    replay, features, panel = replay_checked(build, executor, verdicts, int(manifest["seed"]))
    out = tmp_path_factory.mktemp("oracle")
    write_trainable_run(out, manifest, replay, features, panel, objective="jw2", horizon_s=120.0,
                        verdicts=verdicts)
    return out


def test_oracle_labels_run_under_their_teachers_matcher():
    assert matching_for("oracle:milp") == "cardinality"
    assert matching_for("oracle:nearest_astar") == "iterative"
    assert matching_for("oracle:milp+multi_criteria+nearest_astar") == "iterative"


@needs_run
def test_oracle_runs_need_a_label_choice(oracle_dir, tmp_path):
    with pytest.raises(TrainingError, match="these are oracle runs"):
        train([oracle_dir], tmp_path)


@needs_run
def test_oracle_and_teacher_runs_never_share_a_corpus(oracle_dir, tmp_path):
    with pytest.raises(TrainingError, match="one kind of run"):
        train([oracle_dir, ACCEPTANCE_RUN], tmp_path, oracle_labels=True)


@needs_run
def test_oracle_labels_need_oracle_runs(tmp_path):
    with pytest.raises(TrainingError, match="needs oracle runs"):
        train([ACCEPTANCE_RUN], tmp_path, oracle_labels=True)


@needs_run
@pytest.mark.parametrize("other", [{"label_teacher": "milp"}, {"label_policy": "majority"},
                                   {"permute_seed": 1}, {"relabel_finish": True}])
def test_oracle_labels_take_no_other_labels(oracle_dir, tmp_path, other):
    with pytest.raises(TrainingError, match="no other labels"):
        train([oracle_dir], tmp_path, oracle_labels=True, **other)


@needs_run
def test_oracle_and_imitation_arms_differ_in_labels_only(oracle_dir, tmp_path):
    oracle = train([oracle_dir], tmp_path / "oracle", oracle_labels=True, decision_cap=12, timeout_s=20)
    control = train([oracle_dir], tmp_path / "control", label_teacher="nearest_astar",
                    decision_cap=12, timeout_s=20)
    assert oracle["teacher"] == "oracle:nearest_astar"
    assert control["teacher"] == "nearest_astar"
    assert oracle["oracle"]["labels"] == "oracle" and control["oracle"]["labels"] == "nearest_astar"
    assert oracle["oracle"]["objectives"] == ["jw2"]
    # Same states, bias and thresholds: only the labels differ (H1, proposal-map.md).
    for key in ("bias_sha256", "thresholds_sha256", "source_runs"):
        assert oracle[key] == control[key]
    assert oracle["sampling"]["decisions"] == control["sampling"]["decisions"]
    assert matching_of_artifact(tmp_path / "oracle" / "rule_artifact.json") == "iterative"
