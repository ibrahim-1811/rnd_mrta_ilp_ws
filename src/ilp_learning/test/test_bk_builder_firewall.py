import re
import pandas as pd
import pytest

from ilp_learning.atoms import ProvenanceTracker
from ilp_learning.bk_builder import BKBuilder
from ilp_learning.thresholds import ThresholdModel


def _make_dummy_decisions():
    return pd.DataFrame([
        {
            "decision_id": 0,
            "robot_id": "R01",
            "order_id": "O001",
            "c_static_m": 10.0,
            "est_finish_s": 8.0,
            "fifo_position": 0,
            "eligible": True,
            "sim_time": 0.0,
            "free_robots": 2,
            "window_orders": 5,
        },
        {
            "decision_id": 0,
            "robot_id": "R01",
            "order_id": "O002",
            "c_static_m": 25.0,
            "est_finish_s": 18.0,
            "fifo_position": 1,
            "eligible": True,
            "sim_time": 0.0,
            "free_robots": 2,
            "window_orders": 5,
        },
        {
            "decision_id": 0,
            "robot_id": "R01",
            "order_id": "O003",
            "c_static_m": 30.0,
            "est_finish_s": 22.0,
            "fifo_position": 2,
            "eligible": False,  # Ineligible pair!
            "sim_time": 0.0,
            "free_robots": 2,
            "window_orders": 5,
        },
    ])


def _make_dummy_features():
    return pd.DataFrame([
        {
            "decision_id": 0,
            "robot_id": "R01",
            "order_id": "O001",
            "occupied_now": 2.0,
            "st_conflict_robots": 1.0,
            "st_conflicts_30s": 2.0,
            "local_density": 3.0,
            "overlap_ratio": 0.1,
            # Pruned / forbidden features:
            "shelf_load": 0.5,
            "route_cells": 15,
            "overlap_cells": 4,
            "robot_soc_percent": 99.5,
        },
        {
            "decision_id": 0,
            "robot_id": "R01",
            "order_id": "O002",
            "occupied_now": 6.0,
            "st_conflict_robots": 3.0,
            "st_conflicts_30s": 5.0,
            "local_density": 3.0,
            "overlap_ratio": 0.4,
            "shelf_load": 0.0,
            "route_cells": 30,
            "overlap_cells": 12,
            "robot_soc_percent": 99.5,
        },
    ])


def test_firewall_rejection():
    builder = BKBuilder()
    df_with_outcomes = _make_dummy_decisions()
    df_with_outcomes["mission_s"] = 12.5  # Outcome leakage!

    with pytest.raises(ValueError, match="State_t firewall violation"):
        builder.build_from_frames("run_001", df_with_outcomes)


def test_hard_eligibility_gate():
    tracker = ProvenanceTracker()
    builder = BKBuilder(tracker=tracker)
    dec_df = _make_dummy_decisions()

    facts = builder.build_from_frames("run_001", dec_df)
    fact_text = "\n".join(facts)

    o3_atom = tracker.orders[tracker.get_or_create_order("run_001", 0, "O003")].atom
    r1_atom = tracker.robots[tracker.get_or_create_robot("run_001", 0, "R01")].atom

    # Ineligible pair must NOT be present in candidate_pair
    assert "candidate_pair(" in fact_text
    assert "candidate_pair(" not in fact_text or f"{r1_atom},{o3_atom}" not in fact_text


def test_pure_datalog_no_floats(tmp_path):
    thresholds = ThresholdModel(
        cutoffs={
            "fleet_load": [2.0, 10.0],
            "free_robots": [1.0, 3.0],
            "local_density": [4.0],
            "overlap_ratio": [0.2],
            "occupied_now": [5.0],
            "st_conflict_robots": [2.0],
            "st_conflicts_30s": [4.0],
        }
    )
    builder = BKBuilder(thresholds=thresholds)
    dec_df = _make_dummy_decisions()
    feat_df = _make_dummy_features()

    builder.build_from_frames("run_001", dec_df, feat_df)
    bk_file = tmp_path / "bk.pl"
    builder.write_bk_pl(bk_file)

    with open(bk_file) as f:
        content = f.read()

    # Zero raw floating point numbers allowed in Datalog facts
    float_matches = re.findall(r"\b\d+\.\d+\b", content)
    assert len(float_matches) == 0, f"Found floats in bk.pl: {float_matches}"


def test_tier_a_and_tier_b_whitelist():
    thresholds = ThresholdModel(
        cutoffs={
            "fleet_load": [2.0, 10.0],
            "free_robots": [1.0, 3.0],
            "local_density": [4.0],
            "overlap_ratio": [0.2],
            "occupied_now": [5.0],
            "st_conflict_robots": [2.0],
            "st_conflicts_30s": [4.0],
        }
    )
    builder = BKBuilder(thresholds=thresholds)
    dec_df = _make_dummy_decisions()
    feat_df = _make_dummy_features()

    facts = builder.build_from_frames("run_001", dec_df, feat_df)
    fact_text = "\n".join(facts)

    # Approved Tier A predicates
    assert "nearest_static(" in fact_text
    assert "earliest_static_finish(" in fact_text
    assert "closer_static(" in fact_text
    assert "earlier_static_finish(" in fact_text
    assert "oldest_fifo(" in fact_text
    assert "prior_fifo(" in fact_text

    # Approved Tier B predicates
    assert "lowest_occupied_for_robot(" in fact_text
    assert "fewest_conflict_robots_for_robot(" in fact_text
    assert "fewest_conflicts_for_robot(" in fact_text
    assert "low_local_density(" in fact_text
    assert "low_route_overlap(" in fact_text

    # Pruned / forbidden predicates must NOT be generated
    assert "shelf_load" not in fact_text
    assert "dropoff_load" not in fact_text
    assert "route_cells" not in fact_text
    assert "overlap_cells" not in fact_text
    assert "robot_soc" not in fact_text
    assert "low_soc_robot" not in fact_text
