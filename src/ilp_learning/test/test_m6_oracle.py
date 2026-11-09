"""Oracle rollout policy: an upper bound for outcome-aware rules (2026-11-09)."""

import json

import pandas as pd
import pytest

from ilp_learning.m6_oracle import _advance, _run_to_end, oracle_run
from ilp_learning.m6_rollouts import corpus_builder
from simulation_env.rollout import tables

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
    if v["verdict"] == "override":
        robot, order = v["pair"].split(":")
        d = pd.DataFrame(executor.recorder.decisions)
        row = d[(d["decision_id"] == v["decision_id"]) & (d["robot_id"] == robot) & (d["order_id"] == order)]
        assert row["selected"].all() and len(row) == 1 and v["gain_s"] >= 2.0
    assert executor.finished()


def test_unknown_objective_is_refused():
    with pytest.raises(ValueError, match="unknown objective"):
        oracle_run(lambda: None, run_id="r", behaviour="nearest_astar", objective="best")
