"""ILPAllocationStrategy through the real engine, on a short deterministic run."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from .conftest import R_AND_O, REPO, TAUTOLOGY, artifact_content, read_manifest, run_workload
from ilp_learning.bk_builder import candidate_pairs, derive_est_finish_s
from ilp_learning.matcher import BatchMatcher, maximum_cardinality
from ilp_learning.rule_artifact import load_rule_artifact, write_rule_artifact
from task_management.allocation_strategy import BATCH_STRATEGIES, get_batch_strategy


def drive(tmp_path_factory, clauses, make, *options):
    out = tmp_path_factory.mktemp("ilp") / "run"
    path = make(clauses)
    done = run_workload(out, "--strategy", "ilp", "--rule-artifact", str(path), *options)
    assert done.returncode == 0, done.stdout + done.stderr
    return out


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):

    folder = tmp_path_factory.mktemp("artifacts")

    def make(clauses):
        name = hashlib.sha256(repr(clauses).encode()).hexdigest()[:8]
        path = folder / f"{name}.json"
        write_rule_artifact(path, artifact_content(clauses))
        return path

    return make


@pytest.fixture(scope="module")
def rule_run(tmp_path_factory, artifacts):
    return drive(tmp_path_factory, [R_AND_O], artifacts)


@pytest.fixture(scope="module")
def empty_run(tmp_path_factory, artifacts):
    return drive(tmp_path_factory, [], artifacts)


def frame_of(run_dir):
    manifest = read_manifest(run_dir)
    frame = pd.read_parquet(Path(run_dir) / "decisions.parquet")
    return frame.assign(est_finish_s=derive_est_finish_s(frame, manifest))


def trace_of(run_dir):
    return pd.read_parquet(Path(run_dir) / "rule_trace.parquet")


def selected_pairs(rows):
    chosen = rows[rows["selected"]]
    return set(zip(chosen["robot_id"], chosen["order_id"]))


def sweep(ordered):
    robots, orders, chosen = set(), set(), set()
    for row in ordered.itertuples():
        if row.robot_id not in robots and row.order_id not in orders:
            robots.add(row.robot_id)
            orders.add(row.order_id)
            chosen.add((row.robot_id, row.order_id))
    return chosen


def nearest_astar_choice(rows):
    return sweep(rows.sort_values(["c_static_m", "fifo_position", "robot_id"]))


def fifo_choice(rows):
    return sweep(rows.sort_values(["fifo_position", "c_static_m", "est_finish_s", "robot_id"]))


def test_run_completes_with_every_order_assigned(rule_run):
    manifest = read_manifest(rule_run)
    assert manifest["strategy"] == "ilp"
    frame = frame_of(rule_run)
    assert frame[frame["selected"]]["order_id"].nunique() == 30


@pytest.mark.parametrize("name", ["rule_run", "empty_run"])
def test_no_ineligible_assignment_and_each_at_most_once(name, request):
    frame = frame_of(request.getfixturevalue(name))
    chosen = frame[frame["selected"]]
    assert chosen["eligible"].all()
    assert not chosen.duplicated(["decision_id", "robot_id"]).any()
    assert not chosen.duplicated(["decision_id", "order_id"]).any()
    assert chosen["order_id"].is_unique


def test_two_runs_are_identical(tmp_path_factory, artifacts, rule_run):
    again = drive(tmp_path_factory, [R_AND_O], artifacts)
    assert read_manifest(again)["run_id"] == read_manifest(rule_run)["run_id"]
    assert (again / "decisions.parquet").read_bytes() == (rule_run / "decisions.parquet").read_bytes()
    pd.testing.assert_frame_equal(trace_of(again), trace_of(rule_run))


def test_empty_rule_runs_fifo_not_nearest_astar(empty_run):
    frame = frame_of(empty_run)
    eligible = frame[frame["eligible"]]
    decisions = list(eligible.groupby("decision_id"))
    assert all(selected_pairs(rows) == fifo_choice(rows) for _, rows in decisions)
    differs = [i for i, rows in decisions if selected_pairs(rows) != nearest_astar_choice(rows)]
    assert differs, "the fixture must separate FIFO from nearest_astar"
    assert not trace_of(empty_run)["matched_by_rule"].any()
    assert trace_of(empty_run)["fallback"].all()
    assert trace_of(empty_run)["clause_rank"].isna().all()


def test_tautological_rule_runs_the_same_fifo(tmp_path_factory, artifacts, empty_run):
    tautology = drive(tmp_path_factory, [TAUTOLOGY], artifacts)
    columns = ["decision_id", "robot_id", "order_id", "selected"]
    pd.testing.assert_frame_equal(
        frame_of(tautology)[columns], frame_of(empty_run)[columns]
    )
    assert trace_of(tautology)["matched_by_rule"].all()


def test_replay_from_the_recorded_table_gives_the_online_picks(rule_run, artifacts):
    # Online pairs come from OrderCandidate and DecisionTiming; offline ones from
    # decisions.parquet and run.json. One definition must read both alike.
    program, _ = load_rule_artifact(artifacts([R_AND_O]))
    frame = frame_of(rule_run)
    trace = trace_of(rule_run)
    matcher = BatchMatcher("iterative")
    replayed = []
    for decision_id, rows in frame[frame["eligible"]].groupby("decision_id"):
        for a in matcher.match_pairs(candidate_pairs(rows), program):
            replayed.append(
                (decision_id, a.round, a.robot_id, a.order_id, a.matched_by_rule, a.tie_level)
            )
    online = list(
        trace[["decision_id", "round", "robot_id", "order_id", "matched_by_rule", "tie_level"]]
        .itertuples(index=False, name=None)
    )
    assert online == replayed


def test_trace_decision_ids_are_the_recorded_decisions(rule_run):
    frame = frame_of(rule_run)
    trace = trace_of(rule_run)
    chosen = frame[frame["selected"]]
    assert set(zip(chosen["decision_id"], chosen["robot_id"], chosen["order_id"])) == set(
        zip(trace["decision_id"], trace["robot_id"], trace["order_id"])
    )
    assert (trace["run_id"] == read_manifest(rule_run)["run_id"]).all()


def test_trace_reports_cardinality_against_the_maximum(rule_run):
    frame = frame_of(rule_run)
    eligible = frame[frame["eligible"]]
    for decision_id, picks in trace_of(rule_run).groupby("decision_id"):
        rows = eligible[eligible["decision_id"] == decision_id]
        assert (picks["cardinality"] == len(picks)).all()
        assert (picks["maximum_cardinality"] == maximum_cardinality(zip(rows["robot_id"], rows["order_id"]))).all()
        assert (picks["cardinality"] <= picks["maximum_cardinality"]).all()


def test_m_card_runs_through_the_same_harness(tmp_path_factory, artifacts):
    out = drive(tmp_path_factory, [R_AND_O], artifacts, "--rule-matching", "cardinality")
    trace = trace_of(out)
    assert (trace["matching"] == "cardinality").all()
    assert read_manifest(out)["strategy_identity"]["parameters"] == {"matching": "cardinality"}


def test_identity_carries_the_rule_artifact_by_content(rule_run):
    identity = read_manifest(rule_run)["strategy_identity"]
    (record,) = identity["artifacts"]
    assert record["role"] == "rule_artifact"
    assert record["sha256"] == hashlib.sha256(Path(record["path"]).read_bytes()).hexdigest()


def test_two_rules_never_share_a_strategy_hash(rule_run, empty_run):
    assert read_manifest(rule_run)["strategy_sha256"] != read_manifest(empty_run)["strategy_sha256"]
    assert read_manifest(rule_run)["run_id"] != read_manifest(empty_run)["run_id"]


def test_no_sidecar_key_reaches_a_conventional_run(teacher_run):
    assert not (teacher_run / "rule_trace.parquet").exists()
    manifest = read_manifest(teacher_run)
    assert "rule_artifact" not in json.dumps(manifest)
    assert "rule_trace" not in json.dumps(manifest)


def test_rule_runs_write_the_same_table_columns_as_conventional_runs(rule_run, teacher_run):
    assert list(frame_of(rule_run).columns) == list(frame_of(teacher_run).columns)
    assert set(read_manifest(rule_run)) == set(read_manifest(teacher_run))


def test_strategy_without_an_artifact_refuses_to_allocate():
    strategy = get_batch_strategy("ilp")
    assert strategy.identity()["artifacts"] == []
    with pytest.raises(ValueError, match="no rule artifact"):
        strategy.assign(None, [], [], {})


def test_strategy_needs_decision_timing(make_artifact):
    strategy = get_batch_strategy("ilp", rule_artifact=make_artifact([R_AND_O]))
    with pytest.raises(ValueError, match="decision timing"):
        strategy.assign(None, [], [], {})


def test_run_workload_refuses_mismatched_options(tmp_path, make_artifact):
    missing = run_workload(tmp_path / "a", "--strategy", "ilp")
    assert missing.returncode == 2 and "--rule-artifact" in missing.stderr
    stray = run_workload(
        tmp_path / "b", "--strategy", "nearest_astar", "--rule-artifact", str(make_artifact([]))
    )
    assert stray.returncode == 2


def test_existing_strategies_do_not_import_the_learner():
    code = (
        "import sys; import task_management.allocation_strategy as a;"
        "[a.get_batch_strategy(n).identity() for n in a.BATCH_STRATEGIES];"
        "print(any(m.split('.')[0] == 'ilp_learning' for m in sys.modules))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, check=True, cwd=REPO,
        env={"PYTHONPATH": ":".join(f"src/{p}" for p in ("mrta_core", "task_management")), "PATH": ""},
    )
    assert done.stdout.strip() == "False"


def test_registry_holds_the_learned_strategy_beside_the_others():
    assert "ilp" in BATCH_STRATEGIES
    assert "nearest_astar" in BATCH_STRATEGIES
