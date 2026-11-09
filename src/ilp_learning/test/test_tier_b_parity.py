"""Tier B congestion predicates mean the same offline and online.

bk.pl and rule execution read one definition, and that definition is fed by
the one feature code that recorded the training data.
"""

from pathlib import Path
import shutil

import pandas as pd
import pytest

from .conftest import ACCEPTANCE_RUN, artifact_content, read_manifest, run_workload
from ilp_learning.bias_templates import BATTERY_PREDICATES, TIER_B_CONGESTION_PREDICATES, WORKLOAD_PREDICATES
from ilp_learning.bk_builder import BKBuilder, candidate_pairs, derive_est_finish_s
from ilp_learning.matcher import BatchMatcher
from ilp_learning.predicates import (
    BATTERY_SIGNATURES,
    EXTENDED_SIGNATURES,
    PREDICATE_SIGNATURES,
    TIER_A_SIGNATURES,
    TIER_B_SIGNATURES,
    WORKLOAD_SIGNATURES,
    CandidatePair,
    DecisionFacts,
    MissingCongestionData,
)
from ilp_learning.rule_artifact import RuleArtifactError, load_rule_artifact, write_rule_artifact
from ilp_learning.rule_compiler import RuleProgram, parse_clause
from ilp_learning.thresholds import ThresholdModel
from ilp_learning.train_rules import TrainingError, panel_labelled, train
from task_management.allocation_strategy import get_batch_strategy


CUTOFFS = {
    "local_density": [3.0],
    "overlap_ratio": [0.5],
    "occupied_now": [2.0],
    "st_conflict_robots": [2.0],
    "st_conflicts_30s": [4.0],
    "fleet_load": [2.0, 4.0],
    "free_robots": [2.0, 4.0],
}
THRESHOLDS = ThresholdModel(CUTOFFS, {"seeds_used": [104]})
TIER_B_RULE = "assign(A,B,C):- fewest_conflicts_for_robot(A,B,C),nearest_static(A,B,C)."
# One live run per clause, so no clause is starved by a better-ranked one and every
# Tier B predicate is exercised, ranked and thresholded. The cutoffs suit a short run.
TIER_B_PROGRAMS = [
    "assign(A,B,C):- high_conflict_robots(A,B,C),lowest_occupied_for_robot(A,B,C).",
    "assign(A,B,C):- high_occupied_now(A,B,C),nearest_static(A,B,C).",
    TIER_B_RULE,
    "assign(A,B,C):- fewest_conflict_robots_for_robot(A,B,C),low_local_density(A,B).",
    "assign(A,B,C):- low_route_overlap(A,B,C),nearest_static(A,B,C).",
    "assign(A,B,C):- near_best_cost_for_robot(A,B,C),oldest_eligible_for_robot(A,B,C).",
    "assign(A,B,C):- aging_window_order(A,C),best_finish_for_order(A,B,C).",
    "assign(A,B,C):- batch_contention(A),nearest_static(A,B,C).",
    "assign(A,B,C):- high_fleet_busy_ratio(A),nearest_static(A,B,C).",
    "assign(A,B,C):- high_queue_pressure(A),nearest_static(A,B,C).",
]
LIVE_THRESHOLDS = ThresholdModel(
    {
        **CUTOFFS, "occupied_now": [1.0], "st_conflict_robots": [1.0], "overlap_ratio": [0.3],
        "cost_regret": [0.5], "busy_ratio": [0.5], "queue_pressure": [0.5],
    },
    {"seeds_used": [104]},
)


def pair(robot, order, cost, occupied, robots, conflicts, overlap, density):
    return CandidatePair(
        robot, order, cost, cost, 0,
        occupied_now=occupied,
        st_conflict_robots=robots,
        st_conflicts_30s=conflicts,
        overlap_ratio=overlap,
        local_density=density,
    )


PAIRS = [
    pair("r1", "a", 5.0, 0, 0, 1, 0.2, 1),
    pair("r1", "b", 6.0, 3, 2, 1, 0.8, 1),
    pair("r2", "a", 7.0, 2, 1, 5, 0.6, 4),
    pair("r2", "b", 8.0, 2, 3, 2, 0.1, 4),
]


def test_vocabulary_is_the_bias():
    assert set(TIER_B_SIGNATURES) == {name for name, *_ in TIER_B_CONGESTION_PREDICATES}
    assert PREDICATE_SIGNATURES == {
        **TIER_A_SIGNATURES, **TIER_B_SIGNATURES, **EXTENDED_SIGNATURES, **BATTERY_SIGNATURES,
        **WORKLOAD_SIGNATURES,
    }
    assert set(BATTERY_SIGNATURES) == {name for name, *_ in BATTERY_PREDICATES}
    assert set(WORKLOAD_SIGNATURES) == {name for name, *_ in WORKLOAD_PREDICATES}


@pytest.mark.parametrize(
    "name, expected",
    [
        ("lowest_occupied_for_robot", {("r1", "a"), ("r2", "a"), ("r2", "b")}),
        ("fewest_conflict_robots_for_robot", {("r1", "a"), ("r2", "a")}),
        ("fewest_conflicts_for_robot", {("r1", "a"), ("r1", "b"), ("r2", "b")}),
        ("low_local_density", {("r1",)}),
        ("low_route_overlap", {("r1", "a"), ("r2", "b")}),
        ("high_occupied_now", {("r1", "b"), ("r2", "a"), ("r2", "b")}),
        ("high_conflict_robots", {("r1", "b"), ("r2", "b")}),
    ],
)
def test_relations(name, expected):
    assert DecisionFacts(PAIRS, THRESHOLDS).relation(name) == expected


def loaded(robot, order, cost, fifo, busy=6, pending=20):
    return CandidatePair(
        robot, order, cost, cost, fifo, free_robots=2, window_orders=4,
        pending_orders=pending, robots_busy=busy, robots_charging=2,
    )


LOADED = [
    loaded("r1", "a", 10.0, 0), loaded("r1", "b", 11.0, 1),
    loaded("r1", "c", 30.0, 3), loaded("r2", "b", 8.0, 1), loaded("r2", "c", 9.0, 3),
]
EXTENDED_THRESHOLDS = ThresholdModel(
    {"cost_regret": [0.2], "busy_ratio": [0.5], "queue_pressure": [3.0]}
)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("near_best_cost_for_robot", {("r1", "a"), ("r1", "b"), ("r2", "b"), ("r2", "c")}),
        ("best_finish_for_order", {("r1", "a"), ("r2", "b"), ("r2", "c")}),
        ("oldest_eligible_for_robot", {("r1", "a"), ("r2", "b")}),
        ("aging_window_order", {("c",)}),
        ("batch_contention", {()}),
        ("high_fleet_busy_ratio", {()}),
        ("high_queue_pressure", set()),
    ],
)
def test_extended_relations(name, expected):
    assert DecisionFacts(LOADED, EXTENDED_THRESHOLDS).relation(name) == expected


def test_a_load_rule_without_load_data_is_refused_not_silent():
    with pytest.raises(MissingCongestionData):
        DecisionFacts(PAIRS, EXTENDED_THRESHOLDS).relation("batch_contention")


def test_a_relation_is_defined_over_the_pairs_it_is_given():
    remaining = [p for p in PAIRS if p.order_id != "a"]
    facts = DecisionFacts(remaining, THRESHOLDS)
    assert facts.relation("lowest_occupied_for_robot") == {("r1", "b"), ("r2", "b")}


def test_tier_a_relations_ignore_the_features():
    plain = [CandidatePair(*p[:5]) for p in PAIRS]
    with_features = DecisionFacts(PAIRS, THRESHOLDS)
    without = DecisionFacts(plain)
    for name in TIER_A_SIGNATURES:
        assert with_features.relation(name) == without.relation(name)


def test_a_congestion_rule_without_congestion_data_is_refused_not_silent():
    plain = [CandidatePair(*p[:5]) for p in PAIRS]
    with pytest.raises(MissingCongestionData):
        DecisionFacts(plain, THRESHOLDS).relation("lowest_occupied_for_robot")


def test_a_thresholded_predicate_without_thresholds_is_refused():
    with pytest.raises(MissingCongestionData):
        DecisionFacts(PAIRS).relation("low_route_overlap")


def test_bk_facts_are_the_shared_relations():
    rows = pd.DataFrame([
        {
            "run_id": "run", "decision_id": 0, "sim_time": 0.0, "eligible": True,
            "robot_id": p.robot_id, "order_id": p.order_id, "c_static_m": p.c_static_m,
            "est_finish_s": p.est_finish_s, "fifo_position": p.fifo_position,
        }
        for p in PAIRS
    ])
    features = pd.DataFrame([
        {
            "decision_id": 0, "robot_id": p.robot_id, "order_id": p.order_id,
            "occupied_now": p.occupied_now, "st_conflict_robots": p.st_conflict_robots,
            "st_conflicts_30s": p.st_conflicts_30s, "overlap_ratio": p.overlap_ratio,
            "local_density": p.local_density,
        }
        for p in PAIRS
    ])
    builder = BKBuilder(thresholds=THRESHOLDS)
    written = builder.build_from_frames("run", rows, features_df=features)
    tracker = builder.tracker
    decision = next(iter(tracker.decisions))
    robots = {atom: v.robot_id for atom, v in tracker.robots.items()}
    orders = {atom: v.order_id for atom, v in tracker.orders.items()}
    shared = DecisionFacts(PAIRS, THRESHOLDS)

    for name, kinds in TIER_B_SIGNATURES.items():
        found = set()
        for fact in written:
            if fact.startswith(name + "("):
                atoms = fact[len(name) + 1:-2].split(",")
                assert atoms[0] == decision
                found.add(
                    tuple(
                        (robots if kind == "robot" else orders)[atom]
                        for kind, atom in zip(kinds, atoms[1:])
                    )
                )
        assert found == shared.relation(name), name


def test_an_indexed_lookup_finds_what_a_scan_finds():
    facts = DecisionFacts(PAIRS, THRESHOLDS)
    for name in ("closer_static", "lowest_occupied_for_robot", "oldest_fifo"):
        arity = len(PREDICATE_SIGNATURES[name])
        for values in facts.relation(name):
            for position in range(arity):
                found = facts.matching(name, {position: values[position]})
                assert set(found) == {v for v in facts.relation(name) if v[position] == values[position]}
        assert set(facts.matching(name, {})) == set(facts.relation(name))
        assert list(facts.matching(name, {0: "nobody"})) == []


def test_program_uses_its_thresholds_and_knows_it_needs_congestion():
    tier_a = RuleProgram((parse_clause("assign(A,B,C):- nearest_static(A,B,C)."),))
    tier_b = RuleProgram((parse_clause(TIER_B_RULE),), THRESHOLDS)
    assert not tier_a.needs_congestion
    assert tier_b.needs_congestion
    assert tier_b.coverage(PAIRS) == {("r1", "a"): 0}


@pytest.mark.parametrize("mode", ["iterative", "cardinality", "greedy"])
def test_every_matching_mode_gives_the_rule_its_congestion_data(mode):
    program = RuleProgram((parse_clause(TIER_B_RULE),), THRESHOLDS)
    picks = {(a.robot_id, a.order_id): a for a in BatchMatcher(mode=mode).match_pairs(PAIRS, program)}
    assert picks[("r1", "a")].matched_by_rule


def congestion_artifact(tmp_path, *, embed=True, clauses=(TIER_B_RULE,), thresholds=THRESHOLDS):
    path = tmp_path / "rule_artifact.json"
    content = artifact_content(list(clauses))
    content["thresholds_sha256"] = thresholds.sha256
    if embed:
        content["thresholds"] = thresholds.to_dict()
    write_rule_artifact(path, content)
    return path


def test_artifact_carries_the_thresholds_the_rule_was_learned_with(tmp_path):
    program, _ = load_rule_artifact(congestion_artifact(tmp_path))
    assert program.needs_congestion
    assert program.thresholds.cutoffs == THRESHOLDS.cutoffs


def test_artifact_of_a_congestion_rule_without_thresholds_is_refused(tmp_path):
    with pytest.raises(RuleArtifactError, match="thresholds"):
        load_rule_artifact(congestion_artifact(tmp_path, embed=False))


def test_artifact_with_other_thresholds_is_refused(tmp_path):
    path = tmp_path / "rule_artifact.json"
    content = artifact_content([TIER_B_RULE])
    content["thresholds_sha256"] = "another"
    content["thresholds"] = THRESHOLDS.to_dict()
    write_rule_artifact(path, content)
    with pytest.raises(RuleArtifactError, match="thresholds"):
        load_rule_artifact(path)


@pytest.fixture(scope="module", params=TIER_B_PROGRAMS)
def congestion_run(tmp_path_factory, request):
    folder = tmp_path_factory.mktemp("tier_b")
    path = congestion_artifact(folder, clauses=[request.param], thresholds=LIVE_THRESHOLDS)
    out = folder / "run"
    done = run_workload(
        out, "--strategy", "ilp", "--rule-artifact", str(path), "--features",
        "--robots", "30", "--orders", "60",
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return out, path


def joined(run_dir):
    manifest = read_manifest(run_dir)
    decisions = pd.read_parquet(Path(run_dir) / "decisions.parquet")
    decisions = decisions.assign(est_finish_s=derive_est_finish_s(decisions, manifest))
    features = pd.read_parquet(Path(run_dir) / "features.parquet")
    columns = [c for c in features.columns if c not in decisions.columns or c in KEYS]
    return decisions.merge(features[columns], on=KEYS, how="left")


KEYS = ["decision_id", "robot_id", "order_id"]


def test_recording_features_records_every_eligible_pair(congestion_run):
    run_dir, _ = congestion_run
    rows = joined(run_dir)
    assert rows[rows["eligible"]]["occupied_now"].notna().all()


def test_offline_replay_reproduces_every_online_pick(congestion_run):
    """The parity proof: recorded values and shared relations give the live picks."""
    run_dir, artifact = congestion_run
    program, _ = load_rule_artifact(artifact)
    rows = joined(run_dir)
    matcher = BatchMatcher(mode="iterative")
    trace = pd.read_parquet(Path(run_dir) / "rule_trace.parquet")
    assert trace["matched_by_rule"].any() and not trace["matched_by_rule"].all()

    decisions = 0
    for decision_id, group in rows[rows["eligible"]].groupby("decision_id"):
        replayed = matcher.match_pairs(candidate_pairs(group), program)
        chosen = group[group["selected"]]
        assert {(p.robot_id, p.order_id) for p in replayed} == set(
            zip(chosen["robot_id"], chosen["order_id"])
        ), decision_id
        decisions += 1
    assert decisions > 0


def test_online_rule_without_a_bound_executor_refuses(tmp_path):
    strategy = get_batch_strategy("ilp", rule_artifact=congestion_artifact(tmp_path))
    with pytest.raises(ValueError, match="fleet state"):
        strategy.assign(None, [], [], {}, timing=object())


def test_features_do_not_change_a_tier_a_run(tmp_path):
    plain = tmp_path / "plain"
    observed = tmp_path / "observed"
    assert run_workload(plain, "--strategy", "nearest_astar").returncode == 0
    assert run_workload(observed, "--strategy", "nearest_astar", "--features").returncode == 0
    a = pd.read_parquet(plain / "decisions.parquet")
    b = pd.read_parquet(observed / "decisions.parquet")
    pd.testing.assert_frame_equal(a, b)


@pytest.mark.skipif(not ACCEPTANCE_RUN.exists(), reason="acceptance run not generated")
def test_tier_b_training_needs_the_features(tmp_path):
    run = tmp_path / "run"
    shutil.copytree(ACCEPTANCE_RUN, run)
    (run / "features.parquet").unlink()
    with pytest.raises(TrainingError, match="features.parquet"):
        train([run], tmp_path / "out", tier_b=True)


@pytest.mark.skipif(not ACCEPTANCE_RUN.exists(), reason="acceptance run not generated")
def test_extended_training_keeps_tier_a_facts_and_seals_its_thresholds(tmp_path):
    plain = tmp_path / "plain"
    train([ACCEPTANCE_RUN], plain, decision_cap=20, timeout_s=30)
    artifact = train([ACCEPTANCE_RUN], tmp_path / "x", decision_cap=20, timeout_s=30, extended=True)
    assert artifact["thresholds"]["sha256"] == artifact["thresholds_sha256"]
    assert "body_pred(aging_window_order" in (tmp_path / "x/kb/bias.pl").read_text()
    assert "body_pred(aging_window_order" not in (plain / "kb/bias.pl").read_text()
    facts = (tmp_path / "x/kb/bk.pl").read_text()
    assert "near_best_cost_for_robot(" in facts and "batch_contention(" in facts
    load_rule_artifact(tmp_path / "x/rule_artifact.json")


@pytest.mark.skipif(not ACCEPTANCE_RUN.exists(), reason="acceptance run not generated")
def test_tier_b_training_offers_congestion_and_seals_its_thresholds(tmp_path):
    artifact = train([ACCEPTANCE_RUN], tmp_path, decision_cap=20, timeout_s=30, tier_b=True)
    assert artifact["solver_settings"]["tier_b"] is True
    assert "body_pred(lowest_occupied_for_robot" in (tmp_path / "kb/bias.pl").read_text()
    assert "lowest_occupied_for_robot(" in (tmp_path / "kb/bk.pl").read_text()
    assert artifact["thresholds"]["sha256"] == artifact["thresholds_sha256"]
    program, _ = load_rule_artifact(tmp_path / "rule_artifact.json")
    assert len(program.clauses) == len(artifact["clauses"])


def test_panel_labels_keep_the_decisions_and_change_the_selection():
    frame = pd.DataFrame(
        [
            {"decision_id": 0, "robot_id": "r", "order_id": "a", "selected": True},
            {"decision_id": 0, "robot_id": "r", "order_id": "b", "selected": False},
        ]
    )
    labels = pd.DataFrame(
        [
            {"decision_id": 0, "teacher": "milp", "robot_id": "r", "order_id": "b"},
            {"decision_id": 0, "teacher": "auction", "robot_id": "r", "order_id": "a"},
        ]
    )
    relabelled = panel_labelled(frame, labels, "milp")
    assert list(relabelled["selected"]) == [False, True]
    assert list(relabelled["order_id"]) == ["a", "b"]
    with pytest.raises(TrainingError, match="no label"):
        panel_labelled(frame, labels, "hungarian")
