"""Oracle rollout policy: an upper bound for outcome-aware rules (2026-11-09)."""

import json

import pandas as pd
import pytest

from ilp_learning.m6_oracle import (
    _ScheduledPairs,
    _advance,
    _run_to_end,
    oracle_run,
    replay_checked,
    replay_with_labels,
    write_trainable_run,
)
from ilp_learning.m6_rollouts import AlignmentError, corpus_builder
from ilp_learning.train_rules import load_induction_run
from simulation_env.rollout import tables
from simulation_env.teacher_panel import PANEL_FILE

from .conftest import ACCEPTANCE_RUN

needs_run = pytest.mark.skipif(not ACCEPTANCE_RUN.exists(), reason="runs/m2_acceptance absent")


def _build():
    manifest = json.loads((ACCEPTANCE_RUN / "run.json").read_text())
    return manifest, corpus_builder(manifest)


@needs_run
def test_pausing_and_resuming_replays_the_run_exactly():
    _, build = _build()
    whole = build()
    whole.run()
    paused = build()
    finished = _advance(paused, 1)
    while not finished:
        finished = _advance(paused, 23)
    assert tables(paused) == tables(whole)


@needs_run
def test_an_oracle_that_decides_nothing_is_the_teacher():
    manifest, build = _build()
    whole = build()
    whole.run()
    executor, verdicts = oracle_run(build, run_id=manifest["run_id"], behaviour=manifest["strategy"],
                                    max_decisions=0)
    assert verdicts == []
    assert tables(executor) == tables(whole)


@needs_run
def test_overrides_are_the_pairs_the_run_then_makes():
    manifest, build = _build()
    executor, verdicts = oracle_run(build, run_id=manifest["run_id"], behaviour=manifest["strategy"],
                                    max_decisions=4)
    assert len(verdicts) == 4
    decisions = pd.DataFrame(executor.recorder.decisions)
    for v in verdicts:
        assert v["verdict"] in {"keep", "override", "no_choice"}
        if v["verdict"] == "override":
            robot, order = v["pair"].split(":")
            row = decisions[(decisions["decision_id"] == v["decision_id"])
                            & (decisions["robot_id"] == robot) & (decisions["order_id"] == order)]
            assert row["selected"].all() and len(row) == 1
            assert v["gain_s"] >= 2.0  # decisive by the jw2 delta
    assert executor.finished()
    assert _run_to_end(executor, lambda _: False)


@needs_run
@pytest.mark.parametrize("objective", ["wholerun", "horizon"])
def test_other_objectives_decide_and_the_run_makes_the_pick(objective):
    manifest, build = _build()
    executor, verdicts = oracle_run(build, run_id=manifest["run_id"], behaviour=manifest["strategy"],
                                    max_decisions=1, objective=objective, manifest=manifest)
    (v,) = verdicts
    assert v["verdict"] in {"keep", "override", "no_choice"}
    if v["verdict"] != "no_choice":
        assert v["teacher_score"] is not None
        assert v["teacher_pair"].count(":") == 1
    if v["verdict"] == "override":
        robot, order = v["pair"].split(":")
        d = pd.DataFrame(executor.recorder.decisions)
        row = d[(d["decision_id"] == v["decision_id"]) & (d["robot_id"] == robot) & (d["order_id"] == order)]
        assert row["selected"].all() and len(row) == 1 and v["gain_s"] >= 2.0
    assert executor.finished()


def test_unknown_objective_is_refused():
    with pytest.raises(ValueError, match="unknown objective"):
        oracle_run(lambda: None, run_id="r", behaviour="nearest_astar", objective="best")


def _a_non_teacher_pair(build):
    """(decision_id, robot_id, order_id): an eligible pair the teacher did not make."""
    whole = build()
    whole.run()
    d = pd.DataFrame(whole.recorder.decisions)
    row = d[d["eligible"] & ~d["selected"]].iloc[0]
    return whole, int(row["decision_id"]), row["robot_id"], row["order_id"]


@needs_run
def test_a_forced_override_is_replayed_and_observing_changes_nothing():
    manifest, build = _build()
    _, decision_id, robot, order = _a_non_teacher_pair(build)
    verdicts = [{"decision_id": decision_id, "verdict": "override", "pair": f"{robot}:{order}"}]
    forced = build()
    forced.allocator = _ScheduledPairs(forced.allocator, forced, {decision_id: (robot, order)})
    forced.run()
    replay, features, panel = replay_with_labels(build, verdicts, int(manifest["seed"]))
    assert tables(replay) == tables(forced)
    d = pd.DataFrame(replay.recorder.decisions)
    picked = d[(d["decision_id"] == decision_id) & (d["robot_id"] == robot) & (d["order_id"] == order)]
    assert picked["selected"].all() and len(picked) == 1
    assert features and panel


@needs_run
def test_a_replay_that_differs_from_the_oracle_is_refused():
    manifest, build = _build()
    teacher, decision_id, robot, order = _a_non_teacher_pair(build)
    verdicts = [{"decision_id": decision_id, "verdict": "override", "pair": f"{robot}:{order}"}]
    with pytest.raises(AlignmentError, match="differs from the oracle"):
        replay_checked(build, teacher, verdicts, int(manifest["seed"]))


@needs_run
def test_the_oracle_run_is_a_training_run(tmp_path):
    manifest, build = _build()
    executor, verdicts = oracle_run(build, run_id=manifest["run_id"], behaviour=manifest["strategy"],
                                    max_decisions=4)
    replay, features, panel = replay_checked(build, executor, verdicts, int(manifest["seed"]))
    written = write_trainable_run(tmp_path, manifest, replay, features, panel, objective="jw2",
                                  horizon_s=120.0, verdicts=verdicts)
    run = load_induction_run(tmp_path)  # the induction-seed guard of train_rules
    assert run.run_id == written["run_id"] != manifest["run_id"]
    assert run.manifest["label_source"] == "oracle"
    assert run.manifest["oracle"]["source_run_id"] == manifest["run_id"]
    assert run.manifest["strategy"] == manifest["strategy"]
    assert set(run.decisions["run_id"]) == {written["run_id"]}
    oracle_picks = pd.DataFrame(executor.recorder.decisions)["selected"]
    assert run.decisions["selected"].tolist() == oracle_picks.tolist()
    assert run.features is not None and len(run.features)
    assert run.teacher_labels is not None and set(run.teacher_labels["teacher"]) >= {"nearest_astar", "milp"}
    assert (tmp_path / PANEL_FILE).exists() and (tmp_path / "outcomes.parquet").exists()
