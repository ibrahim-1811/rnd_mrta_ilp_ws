"""Artifact provenance, stratified sampling, and the training pipeline's refusals."""

import json
from pathlib import Path
import shutil

import pandas as pd
import pytest

from .conftest import ACCEPTANCE_RUN, artifact_content, read_manifest
from ilp_learning.rule_artifact import (
    RuleArtifactError,
    content_sha256,
    load_rule_artifact,
    write_rule_artifact,
)
from ilp_learning.rule_compiler import RuleRefused
from ilp_learning.sampler import labelled_decisions, stratified_decisions
from ilp_learning.thresholds import induction_seeds_of
from ilp_learning.train_rules import TrainingError, load_induction_run, train
from simulation_env.seed_split import SeedLeakageError

CLAUSE = "assign(D,R,O):- nearest_static(D,R,O)."


def test_artifact_round_trip_gives_the_program(make_artifact):
    program, artifact = load_rule_artifact(make_artifact([CLAUSE]))
    assert [c.text for c in program.clauses] == [CLAUSE]
    assert artifact["content_sha256"] == content_sha256(artifact)


def test_artifact_of_the_empty_rule_loads_as_no_clauses(make_artifact):
    program, _ = load_rule_artifact(make_artifact([]))
    assert program.clauses == ()


def tamper(path, change):
    artifact = json.loads(Path(path).read_text())
    change(artifact)
    Path(path).write_text(json.dumps(artifact))


def test_an_edited_artifact_is_refused(make_artifact):
    path = make_artifact([CLAUSE])
    tamper(path, lambda a: a["clauses"][0].update(clause="assign(D,R,O):- oldest_fifo(D,O)."))
    with pytest.raises(RuleArtifactError, match="content hash"):
        load_rule_artifact(path)


def test_a_resealed_out_of_vocabulary_artifact_is_still_refused(make_artifact):
    path = make_artifact([CLAUSE])

    def forge(artifact):
        artifact["clauses"][0]["clause"] = "assign(D,R,O):- robot_is_first(D,R,O)."
        artifact["content_sha256"] = content_sha256(artifact)

    tamper(path, forge)
    with pytest.raises(RuleRefused):
        load_rule_artifact(path)


def test_an_incomplete_artifact_is_refused(tmp_path):
    content = artifact_content([])
    del content["bias_sha256"]
    with pytest.raises(RuleArtifactError, match="bias_sha256"):
        write_rule_artifact(tmp_path / "a.json", content)


def test_ranks_must_be_contiguous(make_artifact):
    path = make_artifact([CLAUSE])

    def forge(artifact):
        artifact["clauses"][0]["rank"] = 3
        artifact["content_sha256"] = content_sha256(artifact)

    tamper(path, forge)
    with pytest.raises(RuleArtifactError, match="ranks"):
        load_rule_artifact(path)


def strata(sizes):
    return {key: [("run", key * 1000 + i) for i in range(n)] for key, n in sizes.items()}


def test_stratified_sample_is_even_and_capped():
    picked = stratified_decisions(strata({1: 50, 2: 50, 3: 50}), cap=30, seed=7)
    assert len(picked) == len(set(picked)) == 30
    counts = pd.Series([d // 1000 for _, d in picked]).value_counts()
    assert set(counts) == {10}


def test_a_small_stratum_leaves_its_share_to_the_others():
    picked = stratified_decisions(strata({1: 2, 2: 50}), cap=20, seed=7)
    assert len(picked) == 20
    assert sum(d // 1000 == 1 for _, d in picked) == 2


def test_a_cap_above_the_corpus_takes_everything():
    assert len(stratified_decisions(strata({1: 3, 2: 4}), cap=800, seed=7)) == 7


def test_sampling_is_deterministic_and_seeded():
    groups = strata({1: 40, 2: 40})
    assert stratified_decisions(groups, 10, 7) == stratified_decisions(dict(reversed(groups.items())), 10, 7)
    assert stratified_decisions(groups, 10, 7) != stratified_decisions(groups, 10, 8)


def test_adding_a_stratum_keeps_what_the_others_contributed():
    before = stratified_decisions(strata({1: 40, 2: 40}), 800, 7)
    after = stratified_decisions(strata({1: 40, 2: 40, 3: 40}), 800, 7)
    assert [p for p in after if p[1] // 1000 != 3] == sorted(before, key=after.index)


def test_seeds_come_from_the_manifests():
    seeds = induction_seeds_of(
        [{"seed": 104, "orders_path": "x/orders_s105.csv"}, {"seed": 106, "orders_path": "orders_s106.csv"}]
    )
    assert seeds == [104, 105, 106]


def forged_run(source, tmp_path, name, **changes):
    run = tmp_path / name
    shutil.copytree(source, run)
    manifest = read_manifest(run)
    manifest.update(changes)
    (run / "run.json").write_text(json.dumps(manifest))
    return run


@pytest.mark.parametrize(
    "changes",
    [
        {"seed": 111}, {"seed": 101}, {"seed": 205}, {"seed": 118},
        {"orders_path": "orders_s112.csv"}, {"orders_path": "orders_s103.csv"},
        {"orders_path": "orders_s117.csv"}, {"orders_path": "renamed.csv"},
    ],
)
def test_a_run_outside_the_induction_pool_is_refused_before_training(teacher_run, tmp_path, changes):
    base = {"seed": 104, "orders_path": "orders_s104.csv", **changes}
    with pytest.raises(SeedLeakageError):
        train([forged_run(teacher_run, tmp_path, "run", **base)], tmp_path / "out")
    assert not (tmp_path / "out").exists()


def inducted(teacher_run, tmp_path, name="run", **changes):
    return forged_run(
        teacher_run, tmp_path, name, seed=104, orders_path="orders_s104.csv", **changes
    )


def test_a_failed_run_is_refused(teacher_run, tmp_path):
    run = inducted(teacher_run, tmp_path)
    (run / "failure.json").write_text("{}")
    with pytest.raises(TrainingError, match="failed"):
        load_induction_run(run)


def test_runs_of_another_teacher_are_refused(teacher_run, tmp_path):
    run = inducted(teacher_run, tmp_path, strategy="random")
    with pytest.raises(TrainingError, match="driven by"):
        train([run], tmp_path / "out")


def test_the_same_run_twice_is_refused(teacher_run, tmp_path):
    run = inducted(teacher_run, tmp_path)
    with pytest.raises(TrainingError, match="twice"):
        train([run, run], tmp_path / "out")


def test_no_outcome_table_is_read(teacher_run, tmp_path):
    run = inducted(teacher_run, tmp_path)
    (run / "outcomes.parquet").write_bytes(b"not a table")
    assert load_induction_run(run).decisions is not None


@pytest.mark.skipif(not ACCEPTANCE_RUN.exists(), reason="acceptance run not generated")
def test_popper_trains_a_provenanced_artifact_end_to_end(tmp_path):
    artifact = train([ACCEPTANCE_RUN], tmp_path, decision_cap=20, timeout_s=30)
    manifest = read_manifest(ACCEPTANCE_RUN)

    program, loaded = load_rule_artifact(tmp_path / "rule_artifact.json")
    assert loaded == artifact
    assert artifact["source_runs"][0]["run_id"] == manifest["run_id"]
    assert artifact["source_runs"][0]["orders_sha256"] == manifest["orders_sha256"]
    assert artifact["popper"]["version"] and artifact["solver_settings"]["tier_b"] is False
    assert artifact["sampling"]["decisions"] <= 20
    assert set(artifact["git"]) >= {"commit", "dirty"}
    assert "body_pred(lowest_occupied_for_robot" not in (tmp_path / "kb/bias.pl").read_text()
    assert len(program.clauses) == len(artifact["clauses"])
    for entry in artifact["clauses"]:
        assert {"tp", "fp", "precision", "recall", "rule_coverage"} <= set(entry["metrics"])


def test_labelled_decisions_need_a_selected_choice_pair():
    frame = pd.DataFrame(
        [
            {"decision_id": 0, "robot_id": "r", "order_id": "a", "eligible": True, "selected": True},
            {"decision_id": 1, "robot_id": "r", "order_id": "a", "eligible": True, "selected": True},
            {"decision_id": 1, "robot_id": "r", "order_id": "b", "eligible": True, "selected": False},
            {"decision_id": 2, "robot_id": "r", "order_id": "a", "eligible": True, "selected": False},
            {"decision_id": 2, "robot_id": "r", "order_id": "b", "eligible": True, "selected": False},
        ]
    )
    assert labelled_decisions(frame) == [1]
