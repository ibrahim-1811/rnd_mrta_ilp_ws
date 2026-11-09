"""Outcome-corrected demonstrations (group D, decision of 2026-11-07)."""

import json

import pandas as pd
import pytest

from ilp_learning.atoms import ProvenanceTracker
from ilp_learning.supervision.outcome_common import window_orders
from ilp_learning.supervision.outcome_corrected import (
    DEMOTED,
    PROMOTED,
    CorrectionError,
    corrected_examples,
    corrected_frame,
    corrections,
)
from ilp_learning.train_rules import TrainingError, train

from .conftest import ACCEPTANCE_RUN

needs_run = pytest.mark.skipif(not ACCEPTANCE_RUN.exists(), reason="runs/m2_acceptance absent")


def _label(branch, atoms, label, sources="x", margin=0.0, j=1000.0, decision=7):
    return {"run_id": "run", "decision_id": decision, "branch": branch, "atoms": atoms,
            "label": label, "candidate_sources": sources, "margin_s": margin, "j_w_s": j}


def _rows(decision=7):
    # R1-R3 free, O1-O3 waiting; the teacher picked R1:O1, R2:O2, R3:O3.
    rows = []
    for r in ("R1", "R2", "R3"):
        for o in ("O1", "O2", "O3"):
            rows.append({"decision_id": decision, "robot_id": r, "order_id": o, "eligible": True,
                         "selected": r[1] == o[1], "sim_time": 30.0,
                         "c_static_m": 10.0 * int(r[1]) + int(o[1]), "fifo_position": int(o[1])})
    return pd.DataFrame(rows)


def test_teacher_within_delta_is_kept():
    labels = pd.DataFrame([
        _label("c0", "R1:O1", "abstain", sources="behaviour", margin=1.0),
        _label("c1", "R1:O2", "pos"),
    ])
    assert corrections(labels) == []


def test_no_decisive_best_is_kept():
    labels = pd.DataFrame([
        _label("c0", "R1:O1", "neg", sources="behaviour", margin=5.0),
        _label("c1", "R1:O2", "abstain"),
    ])
    assert corrections(labels) == []


def test_teacher_that_lost_by_delta_is_corrected():
    labels = pd.DataFrame([
        _label("c0", "R1:O1", "neg", sources="behaviour+panel:auction", margin=40.0),
        _label("c1", "R1:O2 R2:O1", "pos", j=900.0),
        _label("c2", "R3:O1", "neg", margin=60.0),
    ])
    (fix,) = corrections(labels)
    assert fix.demoted == (("R1", "O1"),)
    assert fix.promoted == (("R1", "O2"), ("R2", "O1"))
    assert fix.margin_s == 40.0 and fix.best_j_w_s == 900.0


def test_corrected_frame_moves_the_picks_and_abstains_displaced_ones():
    labels = pd.DataFrame([
        _label("c0", "R1:O1", "neg", sources="behaviour", margin=40.0),
        _label("c1", "R1:O2", "pos"),
    ])
    frame = corrected_frame(_rows(), corrections(labels)).set_index(["robot_id", "order_id"])
    assert not frame.loc[("R1", "O1"), "selected"]          # teacher's pair demoted
    assert frame.loc[("R1", "O2"), "selected"]               # best pair promoted
    assert not frame.loc[("R2", "O2"), "selected"]           # shares O2: displaced
    assert frame.loc[("R2", "O2"), "abstain"]
    assert frame.loc[("R3", "O3"), "selected"]               # untouched teacher pick
    # One robot, one order among the positives.
    picked = frame[frame["selected"]].reset_index()
    assert picked["robot_id"].is_unique and picked["order_id"].is_unique


def test_examples_carry_their_origin_and_the_demoted_pair_is_negative():
    labels = pd.DataFrame([
        _label("c0", "R1:O1", "neg", sources="behaviour", margin=40.0),
        _label("c1", "R1:O2", "pos"),
    ])
    tracker = ProvenanceTracker()
    examples = corrected_examples("run", _rows(), corrections(labels), tracker, k_neg=1)
    by = {(tracker.robots[e.robot_atom].robot_id, tracker.orders[e.order_atom].order_id): e for e in examples}
    assert by[("R1", "O2")].is_positive and by[("R1", "O2")].origin == PROMOTED
    assert not by[("R1", "O1")].is_positive and by[("R1", "O1")].origin == DEMOTED
    assert by[("R3", "O3")].is_positive and by[("R3", "O3")].origin == "teacher_selected"
    assert ("R2", "O2") not in by or not by[("R2", "O2")].is_positive


def test_a_teacher_pair_that_was_not_picked_is_refused():
    labels = pd.DataFrame([
        _label("c0", "R1:O2", "neg", sources="behaviour", margin=40.0),
        _label("c1", "R1:O3", "pos"),
    ])
    with pytest.raises(CorrectionError, match="not a recorded pick"):
        corrected_frame(_rows(), corrections(labels))


@needs_run
def test_outcome_correct_needs_behaviour_continuation(tmp_path):
    with pytest.raises(TrainingError, match="--outcome-correct needs"):
        train([ACCEPTANCE_RUN], tmp_path, outcome_correct=True)
    with pytest.raises(TrainingError, match="--outcome-correct needs"):
        train([ACCEPTANCE_RUN], tmp_path, outcome_rollouts={}, continuation="nearest_astar",
              outcome_correct=True)


def _probed_with_teacher(tmp_path, scores):
    """A real decision where c0 is the teacher's own recorded pick (source behaviour)."""
    run = tmp_path / "run"
    run.mkdir(parents=True)
    corpus = pd.read_parquet(ACCEPTANCE_RUN / "decisions.parquet")
    corpus.to_parquet(run / "decisions.parquet")
    manifest = json.loads((ACCEPTANCE_RUN / "run.json").read_text())
    (run / "run.json").write_text(json.dumps(manifest))
    for d, g in corpus[corpus["eligible"]].groupby("decision_id"):
        picked = g[g["selected"]]
        if not len(picked) or int(d) <= 2:
            continue
        teacher = picked.iloc[0]
        others = g[~g["selected"] & (g["robot_id"] != teacher["robot_id"])]
        if others["robot_id"].nunique() >= 2:
            break
    alts = others.drop_duplicates("robot_id").head(2)
    pairs = [(teacher, "behaviour", "strategy_pair")] + [(a, "panel:auction", "forced") for _, a in alts.iterrows()]
    window = window_orders(corpus[corpus["decision_id"] == d])
    rows = []
    for rank, ((pair, sources, forced), j) in enumerate(zip(pairs, scores)):
        rows.append({
            "decision_id": int(d), "continuation": manifest["strategy"], "branch": f"c{rank}",
            "candidate_rank": rank, "candidate_sources": sources, "candidate_p_select": 1.0,
            "forced": forced, "forced_robot": pair["robot_id"], "forced_order": pair["order_id"],
            "swap": "", "waiting_in_h_s": 0.0, "rollout_status": "complete",
            "window_completion_s": json.dumps({o: j / len(window) for o in window}),
        })
    out = tmp_path / "rollouts"
    out.mkdir()
    pd.DataFrame(rows).to_csv(out / "rollouts.csv", index=False)
    (out / "probe.json").write_text(json.dumps({
        "arm": "mp1", "run_id": manifest["run_id"], "clone_verified": True, "replay_identical": True,
        "args": {"horizon": "120.0"}, "evaluated_orders": {}, "continuations": [manifest["strategy"]],
    }))
    return run, out, manifest, int(d)


@needs_run
def test_training_records_the_corrections(tmp_path):
    run, out, manifest, decision = _probed_with_teacher(tmp_path, [300.0, 100.0, 200.0])
    artifact = train(
        [run], tmp_path / "rules", teacher=manifest["strategy"],
        outcome_rollouts={manifest["run_id"]: out}, only_decisions={manifest["run_id"]: {decision}},
        outcome_objective="jw2", outcome_mode="conservative", continuation="behaviour",
        outcome_correct=True, timeout_s=20,
    )
    supervision = artifact["supervision"]
    assert artifact["teacher"] == f"outcome_corrected:{manifest['strategy']}"
    assert supervision["kind"] == "outcome_corrected"
    assert supervision["corrections"]["corrected_decisions"] == 1
    assert supervision["corrections"]["promoted_pairs"] == 1
    exs = (tmp_path / "rules" / "kb" / "exs.pl").read_text()
    assert exs.count("pos(") >= 1 and exs.count("neg(") >= 1


def test_corrected_rules_run_under_their_teachers_matcher():
    from ilp_learning.teachers import matching_for

    assert matching_for("outcome_corrected:hungarian") == matching_for("hungarian") == "cardinality"
    assert matching_for("outcome_corrected:nearest_astar") == matching_for("nearest_astar")
    assert matching_for("outcome_corrected:auction+hungarian") == matching_for("outcome_aware")
