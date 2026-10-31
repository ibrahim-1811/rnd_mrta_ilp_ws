"""The shared Tier A predicates agree exactly with what bk.pl always held.

reference_facts below is the original pandas definition of the Tier A facts, kept
here as an independent oracle. bk_builder now calls ilp_learning.predicates, so
comparing the two modules with each other would prove nothing.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from ilp_learning.atoms import ProvenanceTracker
from ilp_learning.bias_templates import BASE_PREDICATES
from ilp_learning.bk_builder import BKBuilder, candidate_pairs, derive_est_finish_s
from ilp_learning.predicates import TIER_A_SIGNATURES, CandidatePair, TierAFacts
from .conftest import ACCEPTANCE_RUN

TOLERANCE = 1e-9


def _levels(values):
    levels = {}
    level, start = -1, None
    for index, value in values.dropna().sort_values().items():
        if start is None or value > start + TOLERANCE:
            level, start = level + 1, value
        levels[index] = level
    return pd.Series(levels, index=values.index, dtype=float)


def _ranks(rows, column, best, second, compare, facts):
    for robot, group in rows.groupby("robot_id"):
        level = _levels(group[column])
        orders = group["order_id"]
        facts[best].update((robot, o) for o in orders[level == 0])
        facts[second].update((robot, o) for o in orders[level == 1])
        for near in range(3):
            for far in range(near + 1, near + 3):
                facts[compare].update(
                    (robot, a, b) for a in orders[level == near] for b in orders[level == far]
                )


def reference_facts(rows):
    facts = {name: set() for name in TIER_A_SIGNATURES}
    facts["candidate_pair"].update(zip(rows["robot_id"], rows["order_id"]))
    facts["candidate_robot"].update((r,) for r in rows["robot_id"])
    facts["candidate_order"].update((o,) for o in rows["order_id"])

    fifo = rows.groupby("order_id")["fifo_position"].min().sort_values()
    queue = list(fifo.index)
    facts["oldest_fifo"].add((queue[0],))
    for i in range(len(queue)):
        for j in range(i + 1, min(i + 4, len(queue))):
            facts["prior_fifo"].add((queue[i], queue[j]))

    for order, group in rows.groupby("order_id"):
        known = group[group["c_static_m"].notna()]
        lowest = known["c_static_m"].min() + TOLERANCE
        facts["best_cost_for_order"].update(
            (r, order) for r in known.loc[known["c_static_m"] <= lowest, "robot_id"]
        )

    _ranks(rows, "c_static_m", "nearest_static", "second_nearest_static", "closer_static", facts)
    _ranks(
        rows, "est_finish_s", "earliest_static_finish",
        "second_earliest_static_finish", "earlier_static_finish", facts,
    )
    return facts


def shared_facts(rows):
    facts = TierAFacts(candidate_pairs(rows))
    return {name: set(facts.relation(name)) for name in TIER_A_SIGNATURES}


def eligible_frame(run_dir):
    manifest = json.loads((Path(run_dir) / "run.json").read_text())
    frame = pd.read_parquet(Path(run_dir) / "decisions.parquet")
    frame = frame.assign(est_finish_s=derive_est_finish_s(frame, manifest))
    return manifest, frame[frame["eligible"]]


def tied_decisions(frame):
    """Decisions where some robot has two orders at exactly one cost."""
    ties = frame.groupby(["decision_id", "robot_id"])["c_static_m"].apply(
        lambda c: c.duplicated().any()
    )
    return set(ties[ties].index.get_level_values("decision_id"))


def assert_agrees(frame):
    for decision_id, rows in frame.groupby("decision_id"):
        assert shared_facts(rows) == reference_facts(rows), f"decision {decision_id}"


def test_the_vocabulary_is_the_twelve_tier_a_predicates():
    assert set(TIER_A_SIGNATURES) == {name for name, *_ in BASE_PREDICATES}
    assert len(TIER_A_SIGNATURES) == 12


def test_a_short_real_run_agrees_on_every_decision(teacher_run):
    _, frame = eligible_frame(teacher_run)
    assert frame["decision_id"].nunique() > 5
    assert_agrees(frame)


@pytest.mark.skipif(not ACCEPTANCE_RUN.exists(), reason="acceptance run not generated")
def test_recorded_induction_run_agrees_including_ties():
    _, frame = eligible_frame(ACCEPTANCE_RUN)
    assert tied_decisions(frame), "this run must exercise real ties"
    assert_agrees(frame)


@pytest.mark.skipif(not ACCEPTANCE_RUN.exists(), reason="acceptance run not generated")
def test_a_residual_pair_set_agrees():
    # Iterative execution evaluates after robots and orders have left.
    _, frame = eligible_frame(ACCEPTANCE_RUN)
    biggest = frame.groupby("decision_id").size().idxmax()
    rows = frame[frame["decision_id"] == biggest]
    for _ in range(min(rows["robot_id"].nunique(), rows["order_id"].nunique()) - 1):
        first = rows.iloc[0]
        rows = rows[(rows["robot_id"] != first["robot_id"]) & (rows["order_id"] != first["order_id"])]
        assert shared_facts(rows) == reference_facts(rows)


def test_exact_and_near_ties_agree():
    base = 10.0
    rows = pd.DataFrame(
        [
            ("r1", "a", base, base, 0), ("r1", "b", base, base, 1),
            ("r1", "c", base + 4e-10, base, 2), ("r1", "d", base + 8e-10, base, 3),
            ("r1", "e", base + 12e-10, base + 1, 4), ("r2", "a", base, base + 1, 0),
            ("r2", "b", 11.0, 12.0, 1),
        ],
        columns=["robot_id", "order_id", "c_static_m", "est_finish_s", "fifo_position"],
    )
    assert shared_facts(rows) == reference_facts(rows)
    assert ("r1", "a") in shared_facts(rows)["nearest_static"]
    assert ("r1", "b") in shared_facts(rows)["nearest_static"]


def test_bk_facts_are_the_shared_relations_on_real_data(teacher_run):
    manifest, frame = eligible_frame(teacher_run)
    tracker = ProvenanceTracker()
    facts = set(BKBuilder(tracker=tracker).build_from_frames("u", frame, run_manifest=manifest))
    expected = set()
    for decision_id, rows in frame.groupby("decision_id"):
        d = tracker.get_or_create_decision("u", int(decision_id))
        atom = {
            "robot": lambda r: tracker.get_or_create_robot("u", int(decision_id), r),
            "order": lambda o: tracker.get_or_create_order("u", int(decision_id), o),
        }
        relations = TierAFacts(candidate_pairs(rows))
        for name, kinds in TIER_A_SIGNATURES.items():
            for values in relations.relation(name):
                args = ",".join([d, *(atom[k](v) for k, v in zip(kinds, values))])
                expected.add(f"{name}({args}).")
    tier_a = {f for f in facts if f.split("(")[0] in TIER_A_SIGNATURES}
    assert tier_a == expected


def test_relations_do_not_depend_on_pair_order():
    pairs = [
        CandidatePair("r1", "a", 5.0, 7.0, 1), CandidatePair("r1", "b", 5.0, 6.0, 0),
        CandidatePair("r2", "a", 3.0, 4.0, 1), CandidatePair("r2", "b", 9.0, 9.0, 0),
    ]
    forward, backward = TierAFacts(pairs), TierAFacts(pairs[::-1])
    for name in TIER_A_SIGNATURES:
        assert forward.relation(name) == backward.relation(name)


def test_a_blank_cost_has_no_rank_but_stays_a_candidate():
    facts = TierAFacts([CandidatePair("r1", "a", float("nan"), 1.0, 0), CandidatePair("r1", "b", 2.0, 1.0, 1)])
    assert facts.relation("nearest_static") == {("r1", "b")}
    assert ("r1", "a") in facts.relation("candidate_pair")


def test_an_unknown_predicate_is_refused():
    with pytest.raises(KeyError):
        TierAFacts([]).relation("robot_is_first")
