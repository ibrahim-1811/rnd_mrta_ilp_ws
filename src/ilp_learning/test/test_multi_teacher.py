"""M5 labels: votes decide; a contested pair is no example at all."""

import ast
from pathlib import Path

import pandas as pd
import pytest

from ilp_learning.atoms import ProvenanceTracker
from ilp_learning.sampler import StratifiedExampleSampler
from ilp_learning.supervision import multi_teacher
from ilp_learning.supervision.multi_teacher import consensus_labelled


TEACHERS = ("t1", "t2", "t3", "t4", "t5", "t6")


def frame():
    return pd.DataFrame(
        [
            {
                "decision_id": 0, "robot_id": r, "order_id": o, "eligible": True,
                "sim_time": 0.0, "c_static_m": c, "selected": False,
            }
            for r, o, c in [
                ("r1", "a", 1.0), ("r1", "b", 2.0), ("r1", "c", 3.0),
                ("r2", "a", 2.0), ("r2", "b", 1.0), ("r2", "c", 3.0),
            ]
        ]
    )


def votes(table):
    rows = []
    for (robot, order), count in table.items():
        rows += [
            {"decision_id": 0, "teacher": teacher, "robot_id": robot, "order_id": order}
            for teacher in TEACHERS[:count]
        ]
    return pd.DataFrame(rows)


def by_pair(labelled, column):
    chosen = labelled[labelled[column]]
    return set(zip(chosen["robot_id"], chosen["order_id"]))


LABELS = votes({("r1", "a"): 4, ("r2", "b"): 2, ("r2", "a"): 1})
# Every teacher must appear for the panel size: t5 and t6 voted elsewhere.
LABELS = pd.concat(
    [LABELS, votes({("r1", "b"): 0})]
    + [pd.DataFrame([{"decision_id": 0, "teacher": t, "robot_id": "r1", "order_id": "a"}]) for t in ("t5", "t6")]
)


def test_majority_needs_more_than_half_and_abstains_on_the_rest():
    labelled = consensus_labelled(frame(), LABELS, "majority")
    assert by_pair(labelled, "selected") == {("r1", "a")}
    assert by_pair(labelled, "abstain") == {("r2", "b"), ("r2", "a")}


def test_plurality_keeps_a_matched_pair_with_two_votes():
    labelled = consensus_labelled(frame(), LABELS, "plurality")
    assert by_pair(labelled, "selected") == {("r1", "a"), ("r2", "b")}
    assert by_pair(labelled, "abstain") == {("r2", "a")}


@pytest.mark.parametrize("policy", multi_teacher.POLICIES)
def test_no_pair_is_both_a_positive_and_a_negative(policy):
    labelled = consensus_labelled(frame(), LABELS, policy)
    examples = StratifiedExampleSampler(k_neg=3).sample_examples_from_decisions(
        "run", labelled, ProvenanceTracker()
    )
    positives = {(e.robot_atom, e.order_atom) for e in examples if e.is_positive}
    negatives = {(e.robot_atom, e.order_atom) for e in examples if not e.is_positive}
    assert positives and negatives and not positives & negatives


def test_an_abstained_pair_is_never_sampled_as_a_negative():
    labelled = consensus_labelled(frame(), LABELS, "majority")
    tracker = ProvenanceTracker()
    examples = StratifiedExampleSampler(k_neg=3).sample_examples_from_decisions(
        "run", labelled, tracker
    )
    negatives = {
        (tracker.robots[e.robot_atom].robot_id, tracker.orders[e.order_atom].order_id)
        for e in examples if not e.is_positive
    }
    assert not negatives & by_pair(labelled, "abstain")


def test_a_decision_changes_labels_only():
    labelled = consensus_labelled(frame(), LABELS, "majority")
    pd.testing.assert_frame_equal(
        labelled[["decision_id", "robot_id", "order_id", "c_static_m"]],
        frame()[["decision_id", "robot_id", "order_id", "c_static_m"]],
    )


def test_m5_cannot_read_an_outcome():
    source = Path(multi_teacher.__file__).read_text()
    imported = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    }
    assert imported <= {"__future__", "scipy.optimize"}
    assert "outcomes" not in source.replace("outcome-aware", "") and "rollout" not in source
