"""M6-2: rollouts on the exact corpus states, and outcome labels in train_rules."""

import json
from pathlib import Path
import shutil

import pandas as pd
import pytest

from ilp_learning.m6_rollouts import (
    AlignmentError,
    compare_decisions,
    evenly_spaced,
    rollouts_for_run,
)
from ilp_learning.supervision.outcome_aware import outcome_labels
from ilp_learning.train_rules import TrainingError, train
from simulation_env.seed_split import SeedLeakageError

from .conftest import ACCEPTANCE_RUN

needs_run = pytest.mark.skipif(not ACCEPTANCE_RUN.exists(), reason="runs/m2_acceptance absent")


def frame():
    return pd.DataFrame({
        "run_id": ["a", "a"], "decision_id": [0, 0], "robot_id": ["R1", "R2"],
        "c_static_m": [1.0, 2.0], "selected": [True, False],
    })


def test_replay_compared_on_every_column_but_run_id():
    other = frame().assign(run_id="b")
    assert compare_decisions(other, frame()) == []
    changed = other.copy()
    changed.loc[1, "c_static_m"] = 2.5
    assert compare_decisions(changed, frame()) == ["c_static_m"]
    assert compare_decisions(other.iloc[:1], frame()) == ["rows 1 != 2"]
    assert compare_decisions(other.drop(columns="selected"), frame()) == ["columns"]


def test_evenly_spaced_is_deterministic_and_bounded():
    pool = list(range(10, 80))
    assert evenly_spaced(pool, None) == pool
    assert evenly_spaced(pool, 200) == pool
    picked = evenly_spaced(pool, 2)
    assert picked == evenly_spaced(pool, 2) and len(picked) == 2 and set(picked) <= set(pool)


@needs_run
@pytest.mark.parametrize("seed", [101, 111, 116, 201])
def test_rollouts_refuse_a_non_induction_run_before_simulating(tmp_path, seed):
    run = tmp_path / "run"
    run.mkdir()
    manifest = json.loads((ACCEPTANCE_RUN / "run.json").read_text())
    manifest.update(seed=seed, orders_path=f"x/orders_s{seed}.csv")
    (run / "run.json").write_text(json.dumps(manifest))
    with pytest.raises(SeedLeakageError):
        rollouts_for_run(run, tmp_path / "out", limit=1)
    assert not (tmp_path / "out").exists()


@needs_run
def test_changed_input_file_is_refused(tmp_path):
    run = tmp_path / "run"
    shutil.copytree(ACCEPTANCE_RUN, run)
    manifest = json.loads((run / "run.json").read_text())
    manifest["orders_sha256"] = "0" * 64
    (run / "run.json").write_text(json.dumps(manifest))
    with pytest.raises(AlignmentError, match="no longer hashes"):
        rollouts_for_run(run, tmp_path / "out", limit=1)


@needs_run
def test_rollouts_on_the_exact_recorded_states(tmp_path):
    """Replays a recorded run, proves it identical, and branches one of its decisions."""
    report = rollouts_for_run(ACCEPTANCE_RUN, tmp_path / "out", limit=1)
    assert report["replay_identical"] and report["clone_verified"]
    assert report["run_id"] == json.loads((ACCEPTANCE_RUN / "run.json").read_text())["run_id"]
    out = tmp_path / "out" / "r30_s104"
    labels = outcome_labels(ACCEPTANCE_RUN, out)
    assert set(labels["decision_id"]) == set(report["rollout_decisions"])
    assert set(labels["label"]) <= {"pos", "neg", "abstain", "excluded"}


@needs_run
def test_train_refuses_outcome_mixed_with_teacher_labels(tmp_path):
    with pytest.raises(TrainingError, match="exclude"):
        train([ACCEPTANCE_RUN], tmp_path, outcome_rollouts={}, relabel_finish=True)
    with pytest.raises(TrainingError, match="exclude"):
        train([ACCEPTANCE_RUN], tmp_path, outcome_rollouts={}, permute_seed=1)


@needs_run
def test_train_refuses_rollouts_of_another_run(tmp_path):
    with pytest.raises(TrainingError, match="not in the corpus"):
        train([ACCEPTANCE_RUN], tmp_path, outcome_rollouts={"feedfacefeedface": tmp_path})


@needs_run
def test_train_refuses_an_unknown_outcome_mode(tmp_path):
    with pytest.raises(TrainingError, match="mode"):
        train([ACCEPTANCE_RUN], tmp_path, outcome_rollouts={}, outcome_mode="vote")


@needs_run
def test_outcome_training_with_no_positive_is_refused(tmp_path):
    with pytest.raises(TrainingError, match="no positive"):
        train([ACCEPTANCE_RUN], tmp_path / "art", outcome_rollouts={}, timeout_s=5)
    assert not (Path(tmp_path) / "art" / "rule_artifact.json").exists()
