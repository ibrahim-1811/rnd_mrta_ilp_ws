"""Oracle rollout policy: an upper bound for outcome-aware rules (2026-11-09)."""

import json

import pandas as pd
import pytest

from ilp_learning.m6_oracle import (
    _ScheduledPairs,
    _advance,
    _run_to_end,
    oracle_run,
    oracle_run_id,
    replay_checked,
    replay_with_labels,
    robust_choice,
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


# Robust labels (DECISIONS.md, "Robust Oracle Labels and the Combined Campaign", 2026-11-12)

def test_one_continuation_is_the_plain_oracle_rule():
    # rank 0 is the teacher's pair; rank 1 is 3 s better on makespan; rank 2 is 1 s better.
    scores = {0: [(300.0, 9000.0)], 1: [(297.0, 9100.0)], 2: [(299.0, 8000.0)]}
    assert robust_choice(scores, 0, "wholerun") == (1, 1)
    near = {0: [(300.0, 9000.0)], 1: [(299.0, 9000.0)]}  # best, but within delta
    assert robust_choice(near, 0, "wholerun") == (None, 1)
    tie = {0: [(300.0, 9000.0)], 1: [(300.0, 8990.0)]}  # equal makespan, sum lower by 10 s
    assert robust_choice(tie, 0, "wholerun") == (1, 1)
    assert robust_choice(tie, 0, "horizon") == (None, 1)  # the tie-break is wholerun only


def test_a_pick_lucky_in_one_future_is_not_taken():
    # Under continuation A the candidate wins by 20 s, under B it loses by 1 s: mean is better,
    # but it does not beat the teacher in every future.
    scores = {0: [(300.0, 0.0), (300.0, 0.0)], 1: [(280.0, 0.0), (301.0, 0.0)]}
    assert robust_choice(scores, 0, "wholerun") == (None, 1)


def test_a_pick_that_wins_in_every_future_is_taken():
    scores = {0: [(300.0, 0.0), (310.0, 0.0), (305.0, 0.0)],
              1: [(297.0, 0.0), (306.0, 0.0), (300.0, 0.0)],
              2: [(296.0, 0.0), (330.0, 0.0), (299.0, 0.0)]}  # best in one future, worse in another
    assert robust_choice(scores, 0, "wholerun") == (1, 1)


def test_no_teacher_score_or_the_teacher_best_keeps_the_teacher():
    assert robust_choice({1: [(290.0, 0.0)]}, None, "wholerun") == (None, 1)
    assert robust_choice({0: [(290.0, 0.0)], 1: [(300.0, 0.0)]}, 0, "wholerun") == (None, 0)
    assert robust_choice({}, 0, "wholerun") == (None, None)


def test_mean_ties_go_to_the_lower_rank():
    scores = {0: [(300.0, 0.0)], 2: [(290.0, 0.0)], 1: [(290.0, 0.0)]}
    assert robust_choice(scores, 0, "wholerun") == (1, 1)


def test_the_plain_oracle_keeps_its_run_id_and_a_robust_one_gets_its_own():
    plain = oracle_run_id("abc", "wholerun", 120.0)
    assert oracle_run_id("abc", "wholerun", 120.0, ("nearest_astar",)) == plain
    robust = oracle_run_id("abc", "wholerun", 120.0, ("nearest_astar", "multi_criteria", "milp"))
    assert robust != plain
    assert robust != oracle_run_id("abc", "wholerun", 120.0, ("nearest_astar", "milp"))


def test_robust_continuations_need_a_rollout_objective():
    with pytest.raises(ValueError, match="robust continuations"):
        oracle_run(lambda: None, run_id="r", behaviour="nearest_astar", continuations=(None, "milp"))


@needs_run
def test_the_behaviours_own_name_is_the_plain_oracle():
    manifest, build = _build()
    kwargs = dict(run_id=manifest["run_id"], behaviour=manifest["strategy"], max_decisions=2,
                  objective="wholerun", manifest=manifest)
    executor, plain = oracle_run(build, **kwargs)
    same, named = oracle_run(build, continuations=(manifest["strategy"],), **kwargs)
    assert named == plain
    assert tables(same) == tables(executor)


@needs_run
def test_a_robust_override_beats_the_teacher_in_every_future_and_is_made():
    manifest, build = _build()
    names = (manifest["strategy"], "milp")
    executor, verdicts = oracle_run(build, run_id=manifest["run_id"], behaviour=manifest["strategy"],
                                    max_decisions=3, objective="wholerun", manifest=manifest,
                                    continuations=names, seed=int(manifest["seed"]))
    d = pd.DataFrame(executor.recorder.decisions)
    for v in verdicts:
        if v["verdict"] == "no_choice":
            continue
        assert len(v["teacher_scores"]) == len(names)
        if v["verdict"] == "override":
            assert v["pair"] == v["best_pair"] and v["pair_sources"]
            for (bm, bf), (tm, tf) in zip(v["best_scores"], v["teacher_scores"]):
                assert bm <= tm - 2.0 or (bm <= tm + 1e-9 and bf <= tf - 2.0)
            robot, order = v["pair"].split(":")
            row = d[(d["decision_id"] == v["decision_id"]) & (d["robot_id"] == robot) & (d["order_id"] == order)]
            assert row["selected"].all() and len(row) == 1
    assert executor.finished()
