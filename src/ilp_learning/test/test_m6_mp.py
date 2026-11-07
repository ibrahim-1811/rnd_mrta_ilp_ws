"""M6-MP: battery predicates, the jw1 common-workload objective, and stratified sampling."""

import json

import numpy as np
import pandas as pd
import pytest

from ilp_learning.bias_templates import BATTERY_PREDICATES, generate_bias_pl
from ilp_learning.m6_sampling import CONTROL, STRATA, coverage, decision_strata, sample_decisions
from ilp_learning.predicates import CandidatePair, DecisionFacts, charging_share
from ilp_learning.supervision.outcome_common import (
    JW1,
    CommonConfig,
    evaluated_orders,
    label_decision,
)
from ilp_learning.thresholds import ThresholdModel, add_battery_columns


# ---- battery predicates ------------------------------------------------------------

def pair(robot, order, margin, charger, charging=0, free=2, busy=2):
    return CandidatePair(robot, order, 10.0, 10.0, 0, free_robots=free, window_orders=2,
                         pending_orders=5, robots_busy=busy, robots_charging=charging,
                         energy_margin_percent=margin, predicted_charger_distance_m=charger)


CUTS = ThresholdModel({"energy_margin": [20.0], "charger_distance": [10.0], "charging_share": [0.2]}, {})


def facts(pairs):
    return DecisionFacts(pairs, CUTS)


def test_best_margin_for_order_keeps_ties():
    f = facts([pair("r1", "a", 30, 5), pair("r2", "a", 30, 50), pair("r3", "a", 10, 5)])
    assert f.relation("best_margin_for_order") == {("r1", "a"), ("r2", "a")}


def test_low_margin_and_near_charger_use_the_training_cutoffs():
    f = facts([pair("r1", "a", 5, 5), pair("r2", "a", 25, 50)])
    assert f.relation("low_energy_margin") == {("r1", "a")}
    assert f.relation("near_charger_after_order") == {("r1", "a")}


def test_low_bands_include_a_value_on_the_cutoff():
    # A quartile on a mass point of discrete distances (52 m in the pilot).
    f = facts([pair("r1", "a", 20.0, 10.0), pair("r2", "a", 21, 11)])
    assert f.relation("near_charger_after_order") == {("r1", "a")}
    assert f.relation("low_energy_margin") == {("r1", "a")}


def test_no_robot_charging_is_never_pressure():
    zero = ThresholdModel({"charging_share": [0.0]}, {})  # a corpus where nobody charged
    assert DecisionFacts([pair("r1", "a", 5, 5, charging=0)], zero).relation("high_charging_pressure") == set()
    assert facts([pair("r1", "a", 5, 5, charging=3, free=1, busy=6)]).relation("high_charging_pressure") == {()}
    assert facts([pair("r1", "a", 5, 5, charging=1, free=4, busy=5)]).relation("high_charging_pressure") == set()


def test_charging_share_is_over_the_whole_fleet():
    assert charging_share(pair("r", "o", 0, 0, charging=3, free=1, busy=6)) == 0.3
    frame = pd.DataFrame([{"free_robots": 1, "robots_busy": 6, "robots_charging": 3, "pending_orders": 0}])
    assert add_battery_columns(frame)["charging_share"].iloc[0] == 0.3


def test_battery_vocabulary_is_off_by_default():
    assert generate_bias_pl() == generate_bias_pl(include_battery=False)
    with_battery = generate_bias_pl(include_battery=True)
    for name, *_ in BATTERY_PREDICATES:
        assert f"body_pred({name}," not in generate_bias_pl(include_tier_b=True, include_extended=True)
        assert f"body_pred({name}," in with_battery


# ---- jw1: the common-workload objective ---------------------------------------------

def branch(rank, robot, order, completions, waiting=0.0, forced="forced", swap="", status="complete"):
    return {
        "decision_id": 3, "branch": f"c{rank}", "candidate_rank": rank, "candidate_sources": "x",
        "candidate_p_select": 1.0, "forced": forced, "forced_robot": robot, "forced_order": order,
        "swap": swap, "waiting_in_h_s": waiting, "rollout_status": status,
        "window_completion_s": json.dumps(completions),
    }


A = ["O1", "O2"]


def labels(rows, a=A, **kwargs):
    out = label_decision(pd.DataFrame(rows), a, **kwargs)
    return dict(zip(out["branch"], out["label"])), out


def test_every_branch_is_charged_for_the_same_orders():
    # Branch c1 finishes O1 sooner but delays O2: frozen J would see only O1.
    rows = [branch(0, "R1", "O1", {"O1": 50, "O2": 60}, forced="strategy_pair"),
            branch(1, "R2", "O1", {"O1": 40, "O2": 90}, swap="R1:O2")]
    got, out = labels(rows)
    assert list(out["mission_sum_s"]) == [110, 130]
    assert got == {"c0": "pos", "c1": "neg"}


def test_an_unfinished_evaluated_order_censors_the_branch():
    rows = [branch(0, "R1", "O1", {"O1": 50, "O2": 60}, forced="strategy_pair"),
            branch(1, "R2", "O1", {"O1": 10}),           # O2 never finished in this branch
            branch(2, "R3", "O1", {"O1": 80, "O2": 90})]
    got, out = labels(rows)
    assert out.set_index("branch").at["c1", "status"] == "censored"
    # Conservative: the censored branch might have been best, so no positive.
    assert got == {"c0": "abstain", "c1": "excluded", "c2": "neg"}
    assert labels(rows, mode="m6_1")[0] == {"c0": "pos", "c1": "excluded", "c2": "neg"}


def test_horizon_completion_rule_is_stricter():
    rows = [branch(0, "R1", "O1", {"O1": 50, "O2": 130}, forced="strategy_pair"),
            branch(1, "R2", "O1", {"O1": 60, "O2": 70})]
    assert labels(rows)[0] == {"c0": "neg", "c1": "pos"}
    strict = CommonConfig(completion="horizon")
    got, out = labels(rows, config=strict)
    assert out.set_index("branch").at["c0", "status"] == "censored"
    assert got == {"c0": "excluded", "c1": "abstain"}


def test_stalled_and_not_forced_are_excluded():
    rows = [branch(0, "R1", "O1", {"O1": 50, "O2": 60}, forced="strategy_pair"),
            branch(1, "R2", "O1", {"O1": 1, "O2": 1}, status="stalled"),
            branch(2, "R3", "O1", {"O1": 1, "O2": 1}, forced="ineligible")]
    _, out = labels(rows)
    assert list(out["status"]) == ["valid", "failed", "not_forced"]
    assert set(out["label"][1:]) == {"excluded"}


def test_ties_within_delta_abstain():
    rows = [branch(0, "R1", "O1", {"O1": 50, "O2": 60}, forced="strategy_pair"),
            branch(1, "R2", "O1", {"O1": 51, "O2": 60.5})]
    assert labels(rows)[0] == {"c0": "abstain", "c1": "abstain"}


def test_waiting_enters_with_the_frozen_lambda():
    rows = [branch(0, "R1", "O1", {"O1": 50, "O2": 60}, waiting=10, forced="strategy_pair"),
            branch(1, "R2", "O1", {"O1": 50, "O2": 60}, waiting=0)]
    _, out = labels(rows)
    assert list(out["j_w_s"]) == [110 + JW1.lambda_wait * 10, 110]


def test_same_joint_action_twice_counts_once_or_is_a_conflict():
    same = [branch(0, "R1", "O1", {"O1": 50, "O2": 60}, forced="strategy_pair"),
            branch(1, "R2", "O1", {"O1": 40, "O2": 50}, swap="R1:O2"),
            branch(2, "R1", "O2", {"O1": 40, "O2": 50}, swap="R2:O1")]  # the same swap
    got, out = labels(same)
    assert out.set_index("branch").at["c2", "status"] == "duplicate"
    assert got == {"c0": "neg", "c1": "pos", "c2": "excluded"}
    differs = [*same[:2], branch(2, "R1", "O2", {"O1": 45, "O2": 50}, swap="R2:O1")]
    got, out = labels(differs)
    assert got == {"c0": "abstain", "c1": "abstain", "c2": "excluded"}
    assert set(out["reason"][:2]) == {"conflict"}


def test_atoms_are_the_pairs_a_branch_adds():
    rows = [branch(0, "R1", "O1", {"O1": 50, "O2": 60}, forced="strategy_pair"),
            branch(1, "R2", "O1", {"O1": 40, "O2": 50}, swap="R1:O2")]
    _, out = labels(rows)
    assert list(out["atoms"]) == [(("R1", "O1"),), (("R1", "O2"), ("R2", "O1"))]


def test_evaluated_orders_come_from_the_candidates_and_the_behaviour():
    decision = pd.DataFrame([
        {"robot_id": "R1", "order_id": "O1", "selected": True},
        {"robot_id": "R2", "order_id": "O2", "selected": True},
        {"robot_id": "R2", "order_id": "O1", "selected": False},
        {"robot_id": "R3", "order_id": "O3", "selected": False},
    ])
    # R2 is moved onto O1, so the order R2 had (O2) is charged too; R3 had none.
    assert evaluated_orders(decision, [("R1", "O1"), ("R2", "O1"), ("R3", "O3")]) == ["O1", "O2", "O3"]


def test_unknown_mode_or_completion_rule_refused():
    rows = [branch(0, "R1", "O1", {"O1": 1, "O2": 1}, forced="strategy_pair")]
    with pytest.raises(ValueError):
        label_decision(pd.DataFrame(rows), A, mode="vote")
    with pytest.raises(ValueError):
        label_decision(pd.DataFrame(rows), A, config=CommonConfig(completion="never"))


# ---- stratified sampling ------------------------------------------------------------

def run_table(n=40):
    rng = np.random.default_rng(0)
    rows = []
    for d in range(n):
        for r in range(3):
            rows.append({
                "decision_id": d, "robot_id": f"R{r}", "order_id": f"O{r}", "eligible": bool(r != 2 or d % 7),
                "route_reachable": True, "battery_eligible": not (r == 2 and d % 7 == 0),
                "energy_margin_percent": float(rng.uniform(0, 80)),
                "robots_charging": 3 if 10 <= d < 15 else 0, "free_robots": 3, "robots_busy": 5,
                "pending_orders": d, "window_orders": 3,
            })
    decisions = pd.DataFrame(rows)
    features = decisions[["decision_id", "robot_id", "order_id"]].assign(
        st_conflicts_30s=[float(d % 5) for d in decisions["decision_id"]])
    return decisions, features


def test_strata_flags_from_decision_time_columns():
    decisions, features = run_table()
    table = decision_strata(decisions, features, list(range(40)))
    assert set(table.index[table["charging"]]) == set(range(10, 15))
    assert table.loc[0, "low_battery"]                      # a battery-refused pair
    assert table["congestion"].any() and table["waiting"].any() and table["normal"].any()
    assert not (table["normal"] & table[["charging", "low_battery", "congestion", "waiting"]].any(axis=1)).any()


def test_sample_is_deterministic_bounded_and_has_a_random_control():
    decisions, features = run_table()
    table = decision_strata(decisions, features, list(range(40)))
    a = sample_decisions(table, quota=2, seed=7, run_id="r")
    assert a == sample_decisions(table, quota=2, seed=7, run_id="r")
    assert len({s.decision_id for s in a}) == len(a) <= 2 * (len(STRATA) + 1)
    assert any(s.drawn_from == CONTROL for s in a)
    assert all(0 < s.p_select <= 1 for s in a)
    assert {s.drawn_from for s in a} >= {"charging", "low_battery"}
    other = sample_decisions(table, quota=2, seed=8, run_id="r")
    assert [s.decision_id for s in a] != [s.decision_id for s in other]


def test_coverage_counts_membership():
    decisions, features = run_table()
    table = decision_strata(decisions, features, list(range(40)))
    sample = sample_decisions(table, quota=1, seed=1, run_id="r")
    cov = coverage(table, sample)
    assert cov["charging"]["pool"] == 5 and cov["charging"]["sampled"] >= 1
    assert cov["pool_decisions"] == 40 and cov["sampled_decisions"] == len(sample)


def test_without_features_congestion_is_empty_not_guessed():
    decisions, _ = run_table()
    table = decision_strata(decisions, None, list(range(40)))
    assert not table["congestion"].any()


# ---- jw2: the whole candidate window -----------------------------------------------

from ilp_learning.supervision.outcome_common import JW2, CommonLabelError, window_orders  # noqa: E402


def window_rows(orders=("O1", "O2", "O3"), recorded=3):
    return pd.DataFrame([{"decision_id": 3, "order_id": o, "window_orders": recorded} for o in orders])


def test_window_is_every_candidate_order_and_must_match_the_recorded_count():
    assert window_orders(window_rows()) == ["O1", "O2", "O3"]
    with pytest.raises(CommonLabelError, match="window_orders"):
        window_orders(window_rows(recorded=4))


def test_jw2_charges_orders_the_decision_did_not_touch():
    # c1 serves O1 sooner (jw1 over {O1, O2} prefers it) but delays O3, outside that set.
    win = ["O1", "O2", "O3"]
    rows = [branch(0, "R1", "O1", {"O1": 50, "O2": 60, "O3": 70}, forced="strategy_pair"),
            branch(1, "R2", "O1", {"O1": 40, "O2": 60, "O3": 200}, swap="R1:O2")]
    assert labels(rows, a=["O1", "O2"])[0] == {"c0": "neg", "c1": "pos"}
    assert labels(rows, a=win, config=JW2)[0] == {"c0": "pos", "c1": "neg"}


def test_jw2_censors_a_branch_whose_window_did_not_finish():
    rows = [branch(0, "R1", "O1", {"O1": 50, "O2": 60, "O3": 70}, forced="strategy_pair"),
            branch(1, "R2", "O1", {"O1": 40, "O2": 60})]
    _, out = labels(rows, a=["O1", "O2", "O3"], config=JW2)
    assert out.set_index("branch").at["c1", "status"] == "censored"


def test_unknown_objective_refused():
    rows = [branch(0, "R1", "O1", {"O1": 1, "O2": 1}, forced="strategy_pair")]
    with pytest.raises(ValueError, match="objective"):
        label_decision(pd.DataFrame(rows), A, config=CommonConfig(objective="jw9"))
