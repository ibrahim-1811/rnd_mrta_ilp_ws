"""M1 diagnostics: outcome signal, congestion features, rollouts.

None of them may change what a run does. The features are decision-time
and must survive the truncation test; a resumed clone must reproduce
the run; a forced branch differs from the run at its decision and
nowhere before it.
"""

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from mrta_core.order_queue import OrderQueue
from mrta_core.orders import load_orders_csv
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.congestion import FEATURE_COLUMNS, FeatureObserver, candidate_features
from simulation_env.dataset import RunRecorder
from simulation_env.fleet import build_fleet
from simulation_env.outcome_signal import per_order, rollout_summary, summarize
from simulation_env.rollout import (
    ForcedDecision,
    clone,
    decision_ticks,
    resume,
    tables,
)
from simulation_env.workload_executor import WorkloadExecutor
from task_management.allocation_strategy import get_batch_strategy


PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[1]
SCRIPTS = PACKAGE / "scripts"
FIXTURE = PACKAGE / "test" / "fixtures" / "v2_reference.yaml"


@pytest.fixture(scope="module")
def config():
    return load_sim_config(FIXTURE)


def build(config, *, orders=40, quantum=3.0, observe=False, stop_after=None):
    """A fresh V2 executor; optionally observed, optionally truncated."""

    warehouse = load_warehouse_xlsx(config.map_path)
    loaded = load_orders_csv(config.orders_path, warehouse)
    allocator = get_batch_strategy("nearest_astar")
    observer = FeatureObserver(allocator) if observe else None

    executor = WorkloadExecutor(
        warehouse=warehouse,
        robots=build_fleet(config, warehouse),
        queue=OrderQueue(loaded[:orders]),
        allocator=observer or allocator,
        resolution=config.resolution_m,
        speed_mps=config.speed_mps,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
        progress_interval_s=0,
        battery=config.battery,
        window=config.allocation_window,
        dispatch_quantum_s=quantum,
        recorder=RunRecorder(run_id="m1"),
        strategy_name="nearest_astar",
    )

    if observer is not None:
        observer.executor = executor

    return executor, observer


MANIFEST = {"speed_mps": 1.5, "shelf_pick_s": 1.0, "dropoff_s": 1.0, "battery": {"charge_due_percent": 10.0}}


# ======================================================================
# Outcome signal
# ======================================================================


def test_the_residual_is_the_mission_minus_the_estimate(config):
    executor, _ = build(config)
    executor.run()

    decisions = pd.DataFrame(executor.recorder.decisions)
    outcomes = pd.DataFrame(executor.recorder.outcomes)
    manifest = {
        "speed_mps": config.speed_mps,
        "shelf_pick_s": config.shelf_pick_s,
        "dropoff_s": config.dropoff_s,
        "battery": {"charge_due_percent": float(config.battery.charge_due_percent)},
    }

    rows = per_order(decisions, outcomes, manifest)

    assert len(rows) == 40
    # Travel and service are predicted exactly, so the residual is the
    # waiting (plus any side-step travel), to the float.
    assert (
        (rows["residual_s"] - rows["realized_waiting_s"] - rows["extra_travel_s"] - rows["service_diff_s"])
        .abs()
        .max()
        < 1e-6
    )
    assert rows["service_diff_s"].abs().max() < 1e-6
    assert (rows["window_flow_s"] > 0).all()

    summary = summarize(rows, decisions, manifest)
    assert summary["orders"] == 40
    assert 0 <= summary["residual_share_of_mission"] < 1


def toy_tables():
    """One decision, one robot, three orders; o1 chosen."""

    decision = {
        "decision_id": 0, "sim_time": 0.0, "free_robots": 1, "window_orders": 3,
        "choice_bearing": True, "robot_id": "r1", "eligible": True,
        "order_shelf_count": 1,
    }
    decisions = pd.DataFrame(
        [
            decision | {"order_id": "o1", "c_static_m": 15.0, "selected": True},
            decision | {"order_id": "o2", "c_static_m": 30.0, "selected": False},
            decision | {"order_id": "o3", "c_static_m": 45.0, "selected": False},
        ]
    )
    outcome = {
        "decision_id": 0, "robot_id": "r1", "launched_at": 0.0,
        "realized_service_s": 2.0, "soc_at_launch_percent": 90.0,
        "soc_at_completion_percent": 80.0,
    }
    outcomes = pd.DataFrame(
        [
            # est o1 = 15/1.5 + 1 + 1 = 12 s; it took 25 s (13 s waiting)
            outcome | {"order_id": "o1", "realized_travel_time_s": 10.0,
                       "realized_waiting_s": 13.0, "completed_at": 24.0,
                       "retreat_finished_at": 25.0},
            outcome | {"order_id": "o2", "robot_id": "r1", "launched_at": 30.0,
                       "realized_travel_time_s": 20.0, "realized_waiting_s": 0.0,
                       "completed_at": 50.0, "retreat_finished_at": 52.0, "decision_id": 0},
        ]
    )
    return decisions, outcomes.iloc[:1]


def test_margin_and_exceedance_on_a_toy_decision():
    decisions, outcomes = toy_tables()
    rows = per_order(decisions, outcomes, MANIFEST)
    row = rows.iloc[0]

    assert row["est_finish_s"] == pytest.approx(12.0)
    assert row["residual_s"] == pytest.approx(13.0)
    # next-best estimate: o2 = 30/1.5 + 2 = 22 s, so the margin is 10 s
    assert row["margin_s"] == pytest.approx(10.0)
    assert bool(row["exceeds_margin"]) is True
    # window: o1 completed at 24 s; o2, o3 have no outcome in this toy
    assert row["window_flow_s"] == pytest.approx(24.0)


def test_rollout_summary_counts_only_meaningful_flips():
    rows = []
    for decision_id, flows in ((1, (100.0, 99.5)), (2, (100.0, 93.0)), (3, (100.0, 101.0))):
        for branch, flow, est in zip(("teacher", "alt1"), flows, (50.0, 60.0)):
            rows.append(
                {"decision_id": decision_id, "branch": branch, "window_complete": True,
                 "window_flow_s": flow, "est_s": est, "copy_s": 0.1, "wall_s": 1.0}
            )

    summary = rollout_summary(pd.DataFrame(rows))

    assert summary["rollout_decisions"] == 3
    assert summary["rollout_flip_share"] == pytest.approx(2 / 3)
    assert summary["rollout_flip_share_gt_1s"] == pytest.approx(1 / 3)
    assert summary["rollout_flip_share_gt_5s"] == pytest.approx(1 / 3)
    assert summary["rollout_outcome_vs_estimate_share"] == pytest.approx(2 / 3)


# ======================================================================
# Congestion features
# ======================================================================


def test_the_observer_changes_nothing(config):
    plain, _ = build(config)
    plain.run()

    observed, observer = build(config, observe=True)
    observed.run()

    assert tables(observed) == tables(plain)
    assert len(observer.rows) == sum(1 for r in plain.recorder.decisions if r["route_reachable"])
    assert set(FEATURE_COLUMNS) <= set(observer.rows[0])


class Truncate(Exception):
    pass


def test_features_use_nothing_after_the_decision(config):
    """Abort right after decision k; features up to k must not change."""

    full, full_observer = build(config, observe=True)
    full.run()
    decisions = full.result.decisions

    for k in (decisions // 4, 3 * decisions // 4):
        cut, observer = build(config, observe=True)
        inner = observer.assign

        def assign(*args, _inner=inner, _cut=cut, _k=k, **kwargs):
            result = _inner(*args, **kwargs)
            if _cut.result.decisions - 1 == _k:
                raise Truncate
            return result

        observer.assign = assign

        with pytest.raises(Truncate):
            cut.run()

        prefix = [row for row in full_observer.rows if row["decision_id"] <= k]
        assert observer.rows == prefix


def test_overlap_and_conflicts_on_a_hand_made_state(config):
    executor, _ = build(config, orders=5)
    warehouse = executor.warehouse
    order = executor.queue.order(executor.queue.order_ids[0])
    robot_id = "robot_001"
    candidate = executor.allocator.score(
        warehouse, [(robot_id, executor.engine.robots[robot_id].cell)], [order],
        executor.fifo_position, executor.routes,
    )[0]
    path = candidate.route.plan.path
    delta = float(config.resolution_m / config.speed_mps)

    # Another robot standing on the route's third cell, planning to
    # stay there: one overlap, one cell occupied now, and a conflict
    # exactly when the candidate would arrive (2 moves later).
    other = "robot_999"
    state = {
        "positions": {robot_id: path[0], other: path[2]},
        "plans": {other: [(path[2], 0.0)]},
        "stops": {other: [order.dropoff_id]},
        "occupancy": {path[2]: [(other, 2 * delta)]},
    }

    features = candidate_features(
        state, robot_id, order, candidate.route, delta, warehouse, executor.engine.service_seconds
    )

    assert features["overlap_cells"] == 1
    assert features["occupied_now"] == 1
    assert features["st_conflicts"] == 1
    assert features["st_conflict_robots"] == 1
    assert features["dropoff_load"] == 1
    assert features["local_density"] == 1


# ======================================================================
# Rollouts
# ======================================================================


def test_a_resumed_clone_reproduces_the_run(config):
    baseline, made_in = decision_ticks(lambda: build(config)[0])
    reference = tables(baseline)
    ids = [d for d in sorted(made_in) if made_in[d] > 1]

    for decision_id in (ids[len(ids) // 4], ids[3 * len(ids) // 4]):
        stop_tick = made_in[decision_id] - 1
        executor, _ = build(config)
        ticks = {"n": 0}
        clones = []

        def tick(sim_time, batch, _ticks=ticks, _executor=executor, _stop=stop_tick):
            _ticks["n"] += 1
            if _ticks["n"] == _stop:
                clones.append(clone(_executor))

        executor._external_tick = tick
        executor.run()

        resume(clones[0])
        assert tables(clones[0]) == reference


def test_a_forced_branch_diverges_only_at_its_decision(config):
    baseline, made_in = decision_ticks(lambda: build(config)[0])
    decisions, _ = tables(baseline)
    ids = [d for d in sorted(made_in) if made_in[d] > 1]
    k = ids[len(ids) // 2]

    executor, _ = build(config)
    ticks = {"n": 0}
    branches = {}

    def tick(sim_time, batch):
        ticks["n"] += 1
        if ticks["n"] != made_in[k] - 1:
            return
        for alternative in (None, 0):
            rollout = clone(executor)
            forced = ForcedDecision(rollout.allocator, rollout, k, alternative)
            rollout.allocator = forced
            resume(rollout)
            branches[alternative] = (rollout, forced)

    executor._external_tick = tick
    executor.run()

    teacher, teacher_forced = branches[None]
    other, other_forced = branches[0]

    assert tables(teacher) == tables(baseline)
    assert teacher_forced.decided["replaced"] is False
    assert other_forced.decided["replaced"] is True
    assert other_forced.decided["order"] != other_forced.decided["teacher_order"]

    def selected(rows, decision_id):
        return {(r["robot_id"], r["order_id"]) for r in rows if r["decision_id"] == decision_id and r["selected"]}

    other_rows = other.recorder.decisions

    for decision_id in range(k):
        assert selected(other_rows, decision_id) == selected(decisions, decision_id)

    assert selected(other_rows, k) != selected(decisions, k)
    # The forced order really ran, on the pivot robot, and finished.
    done = {r["order_id"]: r["robot_id"] for r in other.recorder.outcomes}
    assert done[other_forced.decided["order"]] == other_forced.decided["pivot_robot"]


# ======================================================================
# Scripts
# ======================================================================


def run(script, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        capture_output=True, text=True, cwd=ROOT, timeout=600,
    )


def test_the_probe_records_what_run_workload_records(tmp_path):
    common = ["--config", str(FIXTURE), "--orders", "30", "--quantum", "3"]

    plain = run("run_workload.py", *common, "--quiet", "--progress", "0", "--dataset", str(tmp_path / "plain"))
    assert plain.returncode == 0, plain.stdout + plain.stderr

    probed = run("m1_probe.py", *common, "--out", str(tmp_path / "probe"), "--features")
    assert probed.returncode == 0, probed.stdout + probed.stderr

    a = json.loads((tmp_path / "plain" / "run.json").read_text())
    b = json.loads((tmp_path / "probe" / "run.json").read_text())
    assert a["run_id"] == b["run_id"]

    for table in ("decisions.parquet", "outcomes.parquet"):
        assert pd.read_parquet(tmp_path / "plain" / table).equals(
            pd.read_parquet(tmp_path / "probe" / table)
        )

    assert len(pd.read_parquet(tmp_path / "probe" / "features.parquet")) > 0

    report = run("outcome_signal.py", str(tmp_path / "probe"), "--out", str(tmp_path / "signal"))
    assert report.returncode == 0, report.stdout + report.stderr
    summary = pd.read_csv(tmp_path / "signal" / "summary.csv")
    assert "rho_residual_overlap_cells" in summary


@pytest.mark.parametrize("seed", ["5", "201"])
def test_the_probe_refuses_sealed_seeds(tmp_path, seed):
    refused = run("m1_probe.py", "--out", str(tmp_path / "p"), "--workload-seed", seed)

    assert refused.returncode == 2
    assert "M1 must not look at it" in refused.stderr


def test_the_report_refuses_an_evaluation_run(tmp_path):
    run_dir = tmp_path / "evaluation_run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({"seed": 7}))

    refused = run("outcome_signal.py", str(tmp_path), "--out", str(tmp_path / "out"))

    assert refused.returncode == 2
    assert "strategy seed 7 (evaluation)" in refused.stdout


# ======================================================================
# Review 2026-10-28 (.scratch/m1-review): regression cases
# ======================================================================


def test_rollout_evidence_needs_at_least_one_verified_clone():
    from simulation_env.rollout import verified

    assert verified([]) is False
    assert verified([{"identical": True}]) is True
    assert verified([{"identical": True}, {"identical": False}]) is False


def test_the_probe_refuses_rollouts_without_verification(tmp_path):
    refused = run(
        "m1_probe.py", "--config", str(FIXTURE), "--orders", "10",
        "--out", str(tmp_path / "p"), "--rollouts", "2", "--verify", "0",
    )

    assert refused.returncode == 2
    assert "--verify" in refused.stderr


def test_the_seed_guard_reads_the_workload_file_and_the_effective_seed():
    from simulation_env.seed_split import sealed_inputs, workload_seed_of

    assert workload_seed_of("runs/x/workloads/orders_s205.csv") == 205
    assert workload_seed_of("src/simulation_env/config/orders.csv") is None

    assert sealed_inputs(strategy_seed=42, orders_path="a/orders_s7.csv") == [
        "workload seed 7 (evaluation)"
    ]
    assert sealed_inputs(strategy_seed=205, orders_path="a/orders.csv") == [
        "strategy seed 205 (final)"
    ]
    assert sealed_inputs(strategy_seed=101, orders_path="a/orders_s101.csv") == []


def test_the_probe_refuses_a_sealed_workload_file(tmp_path):
    sealed = tmp_path / "orders_s205.csv"
    sealed.write_text((PACKAGE / "test" / "fixtures" / "orders.csv").read_text())

    refused = run(
        "m1_probe.py", "--config", str(FIXTURE), "--orders", "10",
        "--orders-file", str(sealed), "--out", str(tmp_path / "p"),
    )

    assert refused.returncode == 2
    assert "workload seed 205" in refused.stderr


def test_the_report_refuses_a_sealed_workload_under_a_safe_seed(tmp_path):
    run_dir = tmp_path / "r"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps({"seed": 42, "orders_path": "/x/workloads/orders_s7.csv"})
    )

    refused = run("outcome_signal.py", str(tmp_path), "--out", str(tmp_path / "out"))

    assert refused.returncode == 2
    assert "workload seed 7" in refused.stdout


class _Robot:
    def __init__(self, waiting):
        self.waiting_time_s = waiting


class _Stub:
    """Just enough executor for rollout.measure."""

    def __init__(self, waiting, outcomes, time):
        self.engine = type("E", (), {"robots": {"r": _Robot(waiting)}, "time": time})()
        self.recorder = type("R", (), {"outcomes": outcomes})()

    def finished(self):
        return False


def test_waiting_is_counted_from_the_decision_over_a_common_horizon():
    from simulation_env.rollout import measure

    forced = type("F", (), {})()
    forced.decided = {
        "t": 100.0,
        "window": ("o1",),
        "order": "o1",
        # Booked fleet waiting at the decision and at t + H: anything
        # booked between the clone point and the decision, or after
        # t + H, is not this branch's.
        "waiting_at_decision": 40.0,
        "waiting_at_horizon": 55.0,
    }
    outcome = {"order_id": "o1", "completed_at": 130.0, "launched_at": 100.0,
               "retreat_finished_at": 131.0}

    result = measure(_Stub(waiting=90.0, outcomes=[outcome], time=300.0), forced, 120.0)

    assert result["waiting_in_h_s"] == pytest.approx(15.0)
    assert "waiting_added_s" not in result


def test_a_charge_proxy_is_censored_at_the_last_order():
    decisions, outcomes = toy_tables()
    rows = per_order(decisions, outcomes, MANIFEST)

    # One order, no next launch: unknown, not "did not charge".
    assert rows["soc_rose_before_next"].isna().all()
    assert "charged_before_next" not in rows

    summary = summarize(rows, decisions, MANIFEST)
    assert pd.isna(summary["soc_rose_before_next_share"])


def test_partial_association_removes_a_shared_cause():
    import numpy as np

    from simulation_env.outcome_signal import partial_spearman

    rng = np.random.default_rng(0)
    z = rng.normal(size=2000)
    x = z + 0.3 * rng.normal(size=2000)       # x only shares z with y
    y = z + 0.3 * rng.normal(size=2000)

    frame = pd.DataFrame({"x": x, "y": y, "z": z})

    assert frame[["x", "y"]].corr(method="spearman").iloc[0, 1] > 0.8
    assert abs(partial_spearman(frame, "x", "y", "z")) < 0.1


def test_a_direct_run_refuses_the_sealed_final_seeds():
    script = str(Path(__file__).parents[1] / "scripts" / "run_workload.py")
    refused = subprocess.run(
        [sys.executable, script, "--strategy", "nearest_astar", "--seed", "205", "--orders", "1"],
        capture_output=True,
        text=True,
    )
    assert refused.returncode != 0
    assert "reserved for the final evaluation" in refused.stderr


def test_a_declared_purpose_needs_a_workload_of_known_seed(tmp_path):
    script = str(Path(__file__).parents[1] / "scripts" / "run_workload.py")
    unnamed = tmp_path / "renamed.csv"
    unnamed.write_text((Path(__file__).parents[1] / "config" / "orders.csv").read_text())
    refused = subprocess.run(
        [sys.executable, script, "--strategy", "nearest_astar", "--seed", "104",
         "--purpose", "training", "--orders-file", str(unnamed)],
        capture_output=True,
        text=True,
    )
    assert refused.returncode != 0
    assert "does not identify its seed" in refused.stderr


def test_the_backup_validation_pool_is_sealed():
    from simulation_env.seed_split import SeedLeakageError, check_purpose, sealed_inputs

    with pytest.raises(SeedLeakageError, match="backup validation pool"):
        check_purpose([116], None)

    check_purpose([116], "backup")
    assert sealed_inputs(strategy_seed=None, orders_path="a/orders_s120.csv") == [
        "workload seed 120 (backup)"
    ]
