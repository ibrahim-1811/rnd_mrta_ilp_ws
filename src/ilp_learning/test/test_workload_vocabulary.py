"""Workload vocabulary: work done so far (decision of 2026-11-07, built 2026-11-09)."""

import shutil
from pathlib import Path

import pandas as pd
import pytest

from .conftest import ACCEPTANCE_RUN, artifact_content, read_manifest, run_workload
from ilp_learning.bias_templates import generate_bias_pl
from ilp_learning.bk_builder import BKBuilder, candidate_pairs, derive_est_finish_s
from ilp_learning.matcher import BatchMatcher
from ilp_learning.predicates import CandidatePair, DecisionFacts, MissingCongestionData
from ilp_learning.rule_artifact import load_rule_artifact, write_rule_artifact
from ilp_learning.train_rules import TrainingError, train

WORKLOAD_RULE = "assign(A,B,C):- least_driven_robot(A,B),nearest_static(A,B,C)."
KEYS = ["decision_id", "robot_id", "order_id"]


def pair(robot, order, driven):
    return CandidatePair(robot, order, 10.0, 10.0, 0, robot_distance_m=driven)


def test_least_driven_robot_keeps_ties_and_ignores_orders():
    facts = DecisionFacts([pair("r1", "a", 120.0), pair("r1", "b", 120.0),
                           pair("r2", "a", 80.0), pair("r3", "a", 80.0)])
    assert facts.relation("least_driven_robot") == {("r2",), ("r3",)}


def test_least_driven_robot_without_the_feature_is_refused():
    with pytest.raises(MissingCongestionData, match="robot_distance_m"):
        DecisionFacts([CandidatePair("r1", "a", 1.0, 1.0, 0)]).relation("least_driven_robot")


def test_the_predicate_is_searched_only_when_switched_on():
    assert "least_driven_robot" not in generate_bias_pl(include_tier_b=False)
    assert "body_pred(least_driven_robot,2)." in generate_bias_pl(include_tier_b=False, include_workload=True)


@pytest.fixture(scope="module")
def workload_run(tmp_path_factory):
    folder = tmp_path_factory.mktemp("workload")
    path = folder / "rule_artifact.json"
    write_rule_artifact(path, artifact_content([WORKLOAD_RULE]))
    out = folder / "run"
    done = run_workload(out, "--strategy", "ilp", "--rule-artifact", str(path), "--features",
                        "--robots", "12", "--orders", "60")
    assert done.returncode == 0, done.stdout + done.stderr
    return out, path


def joined(run_dir):
    decisions = pd.read_parquet(Path(run_dir) / "decisions.parquet")
    decisions = decisions.assign(est_finish_s=derive_est_finish_s(decisions, read_manifest(run_dir)))
    features = pd.read_parquet(Path(run_dir) / "features.parquet")
    columns = [c for c in features.columns if c not in decisions.columns or c in KEYS]
    return decisions.merge(features[columns], on=KEYS, how="left")


def test_recorded_work_only_grows(workload_run):
    rows = joined(workload_run[0])
    eligible = rows[rows["eligible"]]
    assert eligible["robot_distance_m"].notna().all()
    per_robot = eligible.drop_duplicates(["decision_id", "robot_id"]).sort_values("decision_id")
    for _, g in per_robot.groupby("robot_id"):
        assert g["robot_distance_m"].is_monotonic_increasing
    assert per_robot["robot_distance_m"].max() > 0


def test_offline_replay_reproduces_every_online_pick(workload_run):
    """Parity: the recorded work and the shared relation give the live picks."""
    run_dir, artifact = workload_run
    program, _ = load_rule_artifact(artifact)
    assert program.needs_workload
    trace = pd.read_parquet(Path(run_dir) / "rule_trace.parquet")
    assert trace["matched_by_rule"].any()
    matcher = BatchMatcher(mode="iterative")
    rows = joined(run_dir)
    checked = 0
    for decision_id, group in rows[rows["eligible"]].groupby("decision_id"):
        replayed = matcher.match_pairs(candidate_pairs(group), program)
        chosen = group[group["selected"]]
        assert {(p.robot_id, p.order_id) for p in replayed} == set(
            zip(chosen["robot_id"], chosen["order_id"])), decision_id
        checked += 1
    assert checked > 0


@pytest.mark.skipif(not ACCEPTANCE_RUN.exists(), reason="acceptance run not generated")
def test_training_needs_recorded_work(tmp_path):
    run = tmp_path / "run"
    shutil.copytree(ACCEPTANCE_RUN, run)
    with pytest.raises(TrainingError, match="robot_distance_m"):
        train([run], tmp_path / "rules", workload=True)


def test_bk_has_the_workload_facts_of_the_recorded_run(workload_run):
    run_dir, _ = workload_run
    decisions = pd.read_parquet(Path(run_dir) / "decisions.parquet")
    features = pd.read_parquet(Path(run_dir) / "features.parquet")
    builder = BKBuilder(workload=True)
    facts = builder.build_from_frames("run", decisions, features_df=features[[*KEYS, "robot_distance_m"]],
                                      run_manifest=read_manifest(run_dir))
    rows = joined(run_dir)
    expected = sum(len(DecisionFacts(candidate_pairs(g)).relation("least_driven_robot"))
                   for _, g in rows[rows["eligible"]].groupby("decision_id"))
    assert sum(f.startswith("least_driven_robot(") for f in facts) == expected > 0
