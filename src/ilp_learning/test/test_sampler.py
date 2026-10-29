import pandas as pd
from ilp_learning.atoms import ProvenanceTracker
from ilp_learning.sampler import StratifiedExampleSampler


def _make_candidate_df():
    # Decision 0: 1 robot x 1 order (non-choice, should be skipped)
    # Decision 1: 1 robot x 4 orders (choice-bearing, 1 pos, 3 neg)
    return pd.DataFrame([
        {
            "decision_id": 0,
            "robot_id": "R01",
            "order_id": "O001",
            "c_static_m": 10.0,
            "selected": True,
            "eligible": True,
            "t": 0.0,
        },
        {
            "decision_id": 1,
            "robot_id": "R02",
            "order_id": "O010",
            "c_static_m": 12.0,
            "selected": True,
            "eligible": True,
            "t": 3.0,
        },
        {
            "decision_id": 1,
            "robot_id": "R02",
            "order_id": "O011",
            "c_static_m": 15.0,
            "selected": False,
            "eligible": True,
            "t": 3.0,
        },
        {
            "decision_id": 1,
            "robot_id": "R02",
            "order_id": "O012",
            "c_static_m": 18.0,
            "selected": False,
            "eligible": True,
            "t": 3.0,
        },
        {
            "decision_id": 1,
            "robot_id": "R02",
            "order_id": "O013",
            "c_static_m": 22.0,
            "selected": False,
            "eligible": True,
            "t": 3.0,
        },
    ])


def test_sampler_filters_non_choice_and_samples_k_neg():
    tracker = ProvenanceTracker()
    df = _make_candidate_df()
    sampler = StratifiedExampleSampler(k_neg=3, seed=42)

    examples = sampler.sample_examples_from_decisions("run_001", df, tracker)

    # Decision 0 should be skipped (only 1 candidate)
    dec_atoms = {e.decision_atom for e in examples}
    assert len(dec_atoms) == 1

    pos_exs = [e for e in examples if e.is_positive]
    neg_exs = [e for e in examples if not e.is_positive]

    assert len(pos_exs) == 1
    assert len(neg_exs) == 3

    # Disjointness invariant E+ ∩ E- == ∅
    pos_tuples = {(e.decision_atom, e.robot_atom, e.order_atom) for e in pos_exs}
    neg_tuples = {(e.decision_atom, e.robot_atom, e.order_atom) for e in neg_exs}
    assert pos_tuples.isdisjoint(neg_tuples)


def test_sampler_determinism():
    tracker1 = ProvenanceTracker()
    tracker2 = ProvenanceTracker()
    df = _make_candidate_df()

    s1 = StratifiedExampleSampler(k_neg=3, seed=123)
    ex1 = s1.sample_examples_from_decisions("run_001", df, tracker1)

    s2 = StratifiedExampleSampler(k_neg=3, seed=123)
    ex2 = s2.sample_examples_from_decisions("run_001", df, tracker2)

    assert [e.to_prolog() for e in ex1] == [e.to_prolog() for e in ex2]
