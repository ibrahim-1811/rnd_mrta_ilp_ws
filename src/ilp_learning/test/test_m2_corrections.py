"""Regression tests for the six M2 corrections and the decision-time firewall."""

from pathlib import Path

import pandas as pd
import pytest

from ilp_learning import popper_runner
from ilp_learning.atoms import ProvenanceTracker
from ilp_learning.bias_templates import generate_bias_pl
from ilp_learning.bk_builder import (
    ALLOWED_DECISION_COLUMNS,
    BKBuilder,
    derive_est_finish_s,
)
from ilp_learning.matcher import BatchMatcher, MissingDecisionDataError
from ilp_learning.sampler import DEFAULT_CORPUS_SEED, StratifiedExampleSampler
from ilp_learning.supervision.single_teacher import build_single_teacher_supervision
from ilp_learning.thresholds import fit_thresholds_from_frames
from simulation_env.seed_split import (
    SeedLeakageError,
    check_run_is_induction,
    sealed_inputs,
)

RUN = "run_001"
REAL_RUN_DIR = Path("runs/m2_acceptance/r30_q3_s104")
MANIFEST = {"speed_mps": 2.0, "shelf_pick_s": 1.0, "dropoff_s": 1.0}


def row(robot, order, cost, fifo=0, decision=0, **extra):
    base = {
        "decision_id": decision,
        "robot_id": robot,
        "order_id": order,
        "c_static_m": cost,
        "est_finish_s": cost,
        "fifo_position": fifo,
        "eligible": True,
        "selected": False,
        "sim_time": 12.0,
    }
    return {**base, **extra}


def facts_of(rows, **kwargs):
    tracker = ProvenanceTracker()
    builder = BKBuilder(tracker=tracker)
    facts = set(builder.build_from_frames(RUN, pd.DataFrame(rows), **kwargs))
    robot = lambda r: tracker.get_or_create_robot(RUN, 0, r)  # noqa: E731
    order = lambda o: tracker.get_or_create_order(RUN, 0, o)  # noqa: E731
    return facts, tracker.get_or_create_decision(RUN, 0), robot, order


# 1. Tie-inclusive rank predicates ------------------------------------------


def test_every_tied_order_is_nearest():
    facts, d, r, o = facts_of(
        [row("R1", "A", 10.0, 0), row("R1", "B", 10.0, 1), row("R1", "C", 20.0, 2)]
    )
    assert f"nearest_static({d},{r('R1')},{o('A')})." in facts
    assert f"nearest_static({d},{r('R1')},{o('B')})." in facts
    assert f"nearest_static({d},{r('R1')},{o('C')})." not in facts


def test_second_rank_is_the_second_distinct_value():
    facts, d, r, o = facts_of(
        [
            row("R1", "A", 10.0, 0),
            row("R1", "B", 10.0, 1),
            row("R1", "C", 20.0, 2),
            row("R1", "D", 20.0, 3),
        ]
    )
    assert f"second_nearest_static({d},{r('R1')},{o('B')})." not in facts
    assert f"second_nearest_static({d},{r('R1')},{o('C')})." in facts
    assert f"second_nearest_static({d},{r('R1')},{o('D')})." in facts


def test_comparison_is_strict_between_tied_orders():
    facts, d, r, o = facts_of([row("R1", "A", 10.0, 0), row("R1", "B", 10.0, 1)])
    assert not [f for f in facts if f.startswith("closer_static(")]


def test_tie_facts_do_not_depend_on_row_order():
    rows = [row("R1", "A", 10.0, 0), row("R1", "B", 10.0, 1), row("R1", "C", 12.0, 2)]
    assert facts_of(rows)[0] == facts_of(list(reversed(rows)))[0]


def test_near_ties_do_not_chain_past_the_tolerance():
    facts, d, r, o = facts_of(
        [row("R1", "A", 10.0), row("R1", "B", 10.0 + 0.9e-9), row("R1", "C", 10.0 + 1.8e-9)]
    )
    assert f"nearest_static({d},{r('R1')},{o('B')})." in facts
    assert f"nearest_static({d},{r('R1')},{o('C')})." not in facts


def test_a_blank_value_has_no_rank():
    frame = [row("R1", "A", 10.0), row("R1", "B", 10.0, est_finish_s=None)]
    facts, d, r, o = facts_of(frame)
    assert f"earliest_static_finish({d},{r('R1')},{o('B')})." not in facts


def test_congestion_ranks_are_tie_inclusive():
    rows = [row("R1", "A", 10.0, 0), row("R1", "B", 11.0, 1)]
    features = pd.DataFrame(
        [
            {"decision_id": 0, "robot_id": "R1", "order_id": "A", "occupied_now": 2.0},
            {"decision_id": 0, "robot_id": "R1", "order_id": "B", "occupied_now": 2.0},
        ]
    )
    facts, d, r, o = facts_of(rows, features_df=features)
    assert f"lowest_occupied_for_robot({d},{r('R1')},{o('A')})." in facts
    assert f"lowest_occupied_for_robot({d},{r('R1')},{o('B')})." in facts


# 2. best_cost_for_order ----------------------------------------------------


def test_best_cost_for_order_is_tie_inclusive_and_in_the_bias():
    facts, d, r, o = facts_of(
        [row("R1", "A", 5.0), row("R2", "A", 5.0), row("R3", "A", 9.0)]
    )
    assert f"best_cost_for_order({d},{r('R1')},{o('A')})." in facts
    assert f"best_cost_for_order({d},{r('R2')},{o('A')})." in facts
    assert f"best_cost_for_order({d},{r('R3')},{o('A')})." not in facts
    assert "body_pred(best_cost_for_order,3)." in generate_bias_pl()


# 3. Missing estimated finish time ------------------------------------------


@pytest.mark.parametrize("mode", ["cardinality", "greedy"])
def test_matcher_refuses_a_missing_finish_estimate(mode):
    batch = pd.DataFrame([row("R1", "A", 5.0), row("R2", "A", 6.0)]).drop(
        columns="est_finish_s"
    )
    with pytest.raises(MissingDecisionDataError, match="est_finish_s"):
        BatchMatcher(mode=mode).match(batch)


@pytest.mark.parametrize("mode", ["cardinality", "greedy"])
def test_matcher_refuses_a_blank_finish_estimate(mode):
    batch = pd.DataFrame([row("R1", "A", 5.0), row("R2", "A", 6.0, est_finish_s=None)])
    with pytest.raises(MissingDecisionDataError, match="est_finish_s"):
        BatchMatcher(mode=mode).match(batch)


def test_finish_estimate_matches_decision_timing():
    df = pd.DataFrame({"c_static_m": [10.0], "order_shelf_count": [3]})
    assert derive_est_finish_s(df, MANIFEST).iloc[0] == 10.0 / 2.0 + 3 * 1.0 + 1.0


def test_builder_derives_finish_facts_when_the_log_omits_them():
    rows = [
        {**row("R1", "A", 10.0, 0), "order_shelf_count": 9},
        {**row("R1", "B", 20.0, 1), "order_shelf_count": 1},
    ]
    frame = pd.DataFrame(rows).drop(columns="est_finish_s")
    tracker = ProvenanceTracker()
    facts = BKBuilder(tracker=tracker).build_from_frames(RUN, frame, run_manifest=MANIFEST)
    # A: 5 + 9 + 1 = 15 s; B: 10 + 1 + 1 = 12 s, nearer in time though further in metres.
    b = tracker.get_or_create_order(RUN, 0, "B")
    assert any(f.startswith("earliest_static_finish(") and b in f for f in facts)


def test_builder_refuses_when_finish_cannot_be_derived():
    frame = pd.DataFrame([row("R1", "A", 10.0)]).drop(columns="est_finish_s")
    with pytest.raises(ValueError, match="est_finish_s"):
        BKBuilder().build_from_frames(RUN, frame)


# 4. Popper budget ----------------------------------------------------------


def test_popper_defaults_are_the_spec_budget(tmp_path, monkeypatch):
    (tmp_path / "bias.pl").write_text("")
    (tmp_path / "bk.pl").write_text("")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["timeout"] = cmd, kwargs["timeout"]
        return type("P", (), {"returncode": 1, "stderr": "stop", "stdout": ""})()

    monkeypatch.setattr(popper_runner.subprocess, "run", fake_run)
    popper_runner.run_popper(tmp_path)
    assert seen["cmd"][-4:] == ["300", "1", "0.05", "24"]
    assert seen["timeout"] == 300 + popper_runner.WORKER_GRACE_S


def test_a_hung_popper_worker_is_a_reported_failure(tmp_path, monkeypatch):
    (tmp_path / "bias.pl").write_text("")
    (tmp_path / "bk.pl").write_text("")

    def hang(cmd, **kwargs):
        raise popper_runner.subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(popper_runner.subprocess, "run", hang)
    result = popper_runner.run_popper(tmp_path)
    assert result.success is False and result.timed_out is True


# 5. Seed isolation ---------------------------------------------------------


@pytest.mark.parametrize("seed", [111, 115])
def test_validation_workloads_are_sealed_from_diagnostics(seed):
    assert sealed_inputs(strategy_seed=None, orders_path=f"a/orders_s{seed}.csv") == [
        f"workload seed {seed} (validation)"
    ]


def test_a_renamed_workload_no_longer_passes_as_clean():
    assert sealed_inputs(strategy_seed=101, orders_path="a/copy.csv") == []
    assert sealed_inputs(
        strategy_seed=101, orders_path="a/copy.csv", require_known_workload=True
    ) == ["workload a/copy.csv of unknown seed"]


@pytest.mark.parametrize(
    "manifest",
    [
        {"seed": 104, "orders_path": "a/orders_s111.csv"},  # safe seed, validation workload
        {"seed": 104, "orders_path": "a/orders.csv"},  # workload of unknown seed
        {"seed": 111, "orders_path": "a/orders_s104.csv"},
        {"seed": 104},
        {"orders_path": "a/orders_s104.csv"},  # strategy seed not recorded
        {"seed": None, "orders_path": "a/orders_s111.csv"},
    ],
)
def test_run_outside_the_induction_pool_is_refused(manifest):
    with pytest.raises(SeedLeakageError):
        check_run_is_induction(manifest)


def test_induction_run_is_accepted():
    check_run_is_induction({"seed": 104, "orders_path": "a/orders_s105.csv"})


def test_supervision_cannot_skip_the_seed_check():
    with pytest.raises(TypeError):
        build_single_teacher_supervision(RUN, pd.DataFrame(), ProvenanceTracker())


def test_threshold_fit_must_name_its_seeds():
    with pytest.raises(SeedLeakageError, match="no seeds declared"):
        fit_thresholds_from_frames([pd.DataFrame({"local_density": [1.0]})], seeds_used=[])


# 6. Negative sampling ------------------------------------------------------


def batch(robots=3, orders=6):
    selected = {(i, i) for i in range(robots)}
    return pd.DataFrame(
        [
            row(f"R{r}", f"O{o}", 10.0 + r * 3 + o, o, selected=(r, o) in selected)
            for r in range(robots)
            for o in range(orders)
        ]
    )


def sample(frame, **kwargs):
    tracker = ProvenanceTracker()
    examples = StratifiedExampleSampler(**kwargs).sample_examples_from_decisions(
        RUN, frame, tracker
    )
    pairs = {
        e: (tracker.robots[e.robot_atom].robot_id, tracker.orders[e.order_atom].order_id)
        for e in examples
    }
    return examples, pairs


def test_negative_cap_is_per_decision_and_scales_with_positives():
    # Two positives allow six negatives, wherever they fall: R1 has one
    # alternative, so a cap of three per robot would stop at four.
    frame = pd.DataFrame(
        [row("R0", f"O{o}", 10.0 + o, o, selected=o == 0) for o in range(9)]
        + [row("R1", "P0", 5.0, 20, selected=True), row("R1", "P1", 6.0, 21)]
    )
    examples, _ = sample(frame)
    assert sum(e.is_positive for e in examples) == 2
    assert sum(not e.is_positive for e in examples) == 6


def test_negatives_reach_other_robots_competing_for_a_matched_order():
    # R1 has no alternative order and unmatched R2 wants the same one, so a
    # sampler looking only at the matched robot's own orders finds no negative.
    frame = pd.DataFrame([row("R1", "A", 5.0, 0, selected=True), row("R2", "A", 6.0, 0)])
    _, pairs = sample(frame)
    assert ("R2", "A") in {p for e, p in pairs.items() if not e.is_positive}


def test_half_of_the_cap_goes_to_the_closest_competitors():
    frame = batch(robots=2, orders=8)
    _, pairs = sample(frame, k_neg=2)
    negatives = sorted(p for e, p in pairs.items() if not e.is_positive)
    unselected = frame[~frame["selected"]].sort_values(["c_static_m", "robot_id", "order_id"])
    closest = list(zip(unselected["robot_id"], unselected["order_id"]))[:2]
    assert set(closest) <= set(negatives)
    assert len(negatives) == 4


def test_forced_pairs_are_not_examples():
    frame = pd.DataFrame(
        [
            row("R1", "A", 5.0, 0, selected=True),  # alone: no alternative at all
            row("R2", "B", 5.0, 1, selected=True),
            row("R2", "C", 9.0, 2),
        ]
    )
    _, pairs = sample(frame)
    assert ("R1", "A") not in set(pairs.values())
    assert ("R2", "B") in set(pairs.values())


def test_all_isolated_pairs_are_not_a_choice():
    frame = pd.DataFrame(
        [row("R1", "A", 5.0, 0, selected=True), row("R2", "B", 5.0, 1, selected=True)]
    )
    assert sample(frame)[0] == []


def test_sampling_of_a_decision_does_not_depend_on_other_decisions():
    one = batch(robots=2, orders=20).assign(decision_id=7)
    other = batch(robots=2, orders=9).assign(decision_id=3)
    alone, _ = sample(one)
    together, _ = sample(pd.concat([other, one]))
    assert {e.to_prolog() for e in alone} <= {e.to_prolog() for e in together}


def test_default_seed_is_the_pinned_corpus_seed():
    assert StratifiedExampleSampler().seed == DEFAULT_CORPUS_SEED == 20261029


# Decision-time firewall and provenance -------------------------------------


@pytest.mark.parametrize(
    "column",
    [
        "realized_waiting_s",
        "realized_travel_time_s",
        "realized_duration_s",
        "energy_consumed_percent",
        "soc_at_completion_percent",
        "charge_sessions_before",
        "yield_events_during",
        "predicted_distance_m",
        "completed_at",
        "a_column_added_next_year",
    ],
)
def test_columns_outside_the_permitted_schema_are_refused(column):
    frame = pd.DataFrame([row("R1", "A", 5.0)]).assign(**{column: 1.0})
    with pytest.raises(ValueError, match="State_t firewall violation"):
        BKBuilder().build_from_frames(RUN, frame)


def test_the_teacher_choice_cannot_change_the_background_knowledge():
    frame = pd.DataFrame([row("R1", "A", 5.0, 0), row("R1", "B", 8.0, 1)])
    flipped = frame.assign(selected=~frame["selected"], strategy="auction")
    assert set(BKBuilder().build_from_frames(RUN, frame)) == set(
        BKBuilder().build_from_frames(RUN, flipped)
    )


@pytest.mark.skipif(not REAL_RUN_DIR.exists(), reason="Real run dataset not generated")
def test_permitted_schema_covers_the_recorded_tables():
    decisions = pd.read_parquet(REAL_RUN_DIR / "decisions.parquet")
    assert set(decisions.columns) - ALLOWED_DECISION_COLUMNS == {"selected", "strategy"}
    outcomes = pd.read_parquet(REAL_RUN_DIR / "outcomes.parquet")
    shared = {"run_id", "decision_id", "robot_id", "order_id"}
    assert (set(outcomes.columns) - shared).isdisjoint(ALLOWED_DECISION_COLUMNS)


def test_provenance_records_the_decision_time():
    tracker = ProvenanceTracker()
    BKBuilder(tracker=tracker).build_from_frames(RUN, pd.DataFrame([row("R1", "A", 5.0)]))
    assert [p.tick for p in tracker.decisions.values()] == [12.0]
