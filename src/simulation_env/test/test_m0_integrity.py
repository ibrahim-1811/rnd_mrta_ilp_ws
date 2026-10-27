"""M0: research integrity and experimental reproducibility.

  F5  decision linkage   an outcome row names the decision that made its
                         pairing, also when the mission launched later
  F6  failure kinds      a run that fails says why, and only a traffic
                         failure is called a stall
  F7  conditions         --quantum and --window reach every run of an
                         experiment and its record
  F8  strategy identity  a run names its strategy's parameters, solver
                         and artifacts; one name, one identity
  seed split             training and validation seeds never touch the
                         evaluation or the reserved final seeds

The end-to-end tests use real headless runs of a few orders.
"""

import csv
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pytest

from mrta_core.order_queue import OrderQueue
from mrta_core.orders import load_orders_csv
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.charging import ChargingSaturationError
from simulation_env.dataset import RunRecorder, build_manifest
from simulation_env.failures import (
    CHARGING_SATURATION,
    ELIGIBILITY_VIOLATION,
    EXECUTION_ERROR,
    INVALID_ASSIGNMENT,
    SOLVER_FAILURE,
    TRAFFIC_DEADLOCK,
    WORKLOAD_STALL,
    classify,
    read_failure,
)
from simulation_env.fleet import build_fleet
from simulation_env.fleet_engine import DeadlockError
from simulation_env.paired import PairingError, check_strategies
from simulation_env.seed_split import (
    EVALUATION,
    FINAL_RESERVED,
    ROLES,
    SeedLeakageError,
    TRAINING,
    VALIDATION,
    check_purpose,
    role_of,
)
from simulation_env.workload_executor import (
    IneligibleAssignmentError,
    InvalidAssignmentError,
    WorkloadExecutor,
    WorkloadStalledError,
)
from task_management.allocation_strategy import (
    BATCH_STRATEGIES,
    AllocationSolverError,
    NearestAStarAllocator,
    OrderAssignment,
    artifact_record,
    get_batch_strategy,
    identity_sha256,
)


PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[1]
SCRIPTS = PACKAGE / "scripts"
FIXTURE = PACKAGE / "test" / "fixtures" / "v2_reference.yaml"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import compare_paired  # noqa: E402
import run_experiment  # noqa: E402
import run_workload  # noqa: E402


@pytest.fixture(scope="module")
def config():
    return load_sim_config(FIXTURE)


@pytest.fixture(scope="module")
def warehouse(config):
    return load_warehouse_xlsx(config.map_path)


def executor_for(config, warehouse, *, orders, allocator=None, quantum=None):
    """One instrumented executor over the first ``orders`` V2 orders."""

    loaded = load_orders_csv(config.orders_path, warehouse)
    recorder = RunRecorder(run_id="m0")

    executor = WorkloadExecutor(
        warehouse=warehouse,
        robots=build_fleet(config, warehouse),
        queue=OrderQueue(loaded[:orders]),
        allocator=allocator or get_batch_strategy("nearest_astar"),
        resolution=config.resolution_m,
        speed_mps=config.speed_mps,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
        progress_interval_s=0,
        battery=config.battery,
        window=config.allocation_window,
        dispatch_quantum_s=(
            config.dispatch_quantum_s if quantum is None else quantum
        ),
        recorder=recorder,
        strategy_name="nearest_astar",
    )

    return executor, recorder


# ======================================================================
# F5: decision linkage under deferral
# ======================================================================


def defer_some(executor, every=3, hold=2):
    """Make the first plans of every ``every``-th order fail.

    An affected order's mission is refused until ``hold`` further
    decisions have been made (or nothing is left pending), so it can
    only launch under a later dispatch than the one that assigned it --
    the case the old ``decisions - 1`` stamp got wrong. Returns a log of
    what happened.
    """

    original = executor._build_mission

    log = {
        "decided": {},
        "launched_under": {},
        "failed_attempts": defaultdict(int),
        "max_overlap": 0,
    }

    def build(assignment):
        order_id = assignment.order_id
        current = executor.result.decisions - 1

        # The first attempt is made inside the dispatch that decided.
        log["decided"].setdefault(order_id, current)

        waiting = {
            log["decided"][a.order_id]
            for a in executor.deferred.values()
            if a.order_id in log["decided"]
        }
        log["max_overlap"] = max(log["max_overlap"], len(waiting))

        if (
            executor.fifo_position[order_id] % every == 1
            and executor.queue.pending()
            and current < log["decided"][order_id] + hold
        ):
            log["failed_attempts"][order_id] += 1
            return None

        log["launched_under"][order_id] = current

        return original(assignment)

    executor._build_mission = build

    return log


def selected_decision(recorder):
    return {
        row["order_id"]: (row["decision_id"], row["robot_id"])
        for row in recorder.decisions
        if row["selected"]
    }


@pytest.mark.parametrize("quantum", [0.0, 3.0])
def test_a_deferred_order_keeps_the_decision_that_assigned_it(config, warehouse, quantum):
    executor, recorder = executor_for(config, warehouse, orders=40, quantum=quantum)
    log = defer_some(executor)

    result = executor.run()

    assert len(result.completions) == 40
    assert result.plan_failures > 0

    late = {
        order_id
        for order_id, decided in log["decided"].items()
        if log["launched_under"][order_id] > decided
    }

    # The scenario really exercises the bug: orders launched under a
    # later decision, some after more than one failed attempt, and at
    # some instant deferred assignments from several decisions were
    # outstanding together.
    assert len(late) >= 5
    assert max(log["failed_attempts"].values()) >= 2
    assert log["max_overlap"] >= 2

    chosen = selected_decision(recorder)
    outcomes = {row["order_id"]: row for row in recorder.outcomes}

    assert set(outcomes) == set(chosen)

    for order_id, row in outcomes.items():
        decision_id, robot_id = chosen[order_id]

        assert row["decision_id"] == decision_id == log["decided"][order_id]
        assert row["robot_id"] == robot_id

    # The launch time follows the deferral, and the decision row is
    # still the one made before it.
    when = {row["decision_id"]: row["sim_time"] for row in recorder.decisions}

    for order_id in late:
        assert outcomes[order_id]["launched_at"] >= when[chosen[order_id][0]]


def test_without_deferral_the_linkage_is_unchanged(config, warehouse):
    executor, recorder = executor_for(config, warehouse, orders=20)

    result = executor.run()

    assert result.plan_failures == 0

    chosen = selected_decision(recorder)

    assert {row["order_id"]: row["decision_id"] for row in recorder.outcomes} == {
        order_id: decision for order_id, (decision, _) in chosen.items()
    }


# ======================================================================
# F6: failure classification
# ======================================================================


@pytest.mark.parametrize(
    "error, kind",
    [
        (DeadlockError("cycle"), TRAFFIC_DEADLOCK),
        (WorkloadStalledError("stalled"), WORKLOAD_STALL),
        (InvalidAssignmentError("twice"), INVALID_ASSIGNMENT),
        (IneligibleAssignmentError("soc"), ELIGIBILITY_VIOLATION),
        (AllocationSolverError("infeasible"), SOLVER_FAILURE),
        (ChargingSaturationError("full"), CHARGING_SATURATION),
        (RuntimeError("anything else"), EXECUTION_ERROR),
        (KeyError("robot_404"), EXECUTION_ERROR),
    ],
)
def test_every_failure_has_its_own_kind(error, kind):
    assert classify(error) == kind


class Misbehaving(NearestAStarAllocator):
    """nearest_astar, corrupted on its ``at``-th decision by ``how``."""

    def __init__(self, how, at=2, seed=0):
        self.how, self.at, self.calls = how, at, 0
        super().__init__(seed)

    def assign(self, warehouse, robots, orders, fifo_position, cache=None, battery=None, timing=None):
        good = super().assign(warehouse, robots, orders, fifo_position, cache, battery, timing)
        self.calls += 1

        if self.calls != self.at:
            return good

        return self.how(good, robots, orders)


def _first(good):
    assert good, "the corrupted decision must have made a pair"
    return good[0]


def twice_same_robot(good, robots, orders):
    first = _first(good)
    other = next(o for o in orders if o.order_id != first.order_id)
    return [first, OrderAssignment(first.robot_id, other.order_id, first.cost, first.route, first.energy)]


def order_outside_window(good, robots, orders):
    first = _first(good)
    return [OrderAssignment(first.robot_id, "O9999", first.cost, first.route, first.energy)]


def robot_not_offered(good, robots, orders):
    first = _first(good)
    return [OrderAssignment("robot_999", first.order_id, first.cost, first.route, first.energy)]


def twice_same_order(good, robots, orders):
    first = _first(good)
    other = next(r for r, _ in robots if r != first.robot_id)
    return [first, OrderAssignment(other, first.order_id, first.cost, first.route, first.energy)]


@pytest.mark.parametrize(
    "how, message",
    [
        (twice_same_robot, "robot assigned twice"),
        (order_outside_window, "order is not in the candidate window"),
        (robot_not_offered, "robot was not offered"),
        (twice_same_order, "order assigned twice"),
    ],
)
def test_a_contract_breach_is_an_invalid_assignment(config, warehouse, how, message):
    # Decision 1 is the opening batch (every robot free), so the second
    # decision, with one robot, cannot make two pairs; corrupt the first.
    executor, _ = executor_for(
        config, warehouse, orders=30, allocator=Misbehaving(how, at=1)
    )

    with pytest.raises(InvalidAssignmentError, match=message):
        executor.run()


def run_script(monkeypatch, tmp_path, allocator, *extra):
    """run_workload.main() in process, with ``allocator`` swapped in."""

    monkeypatch.setattr(run_workload, "get_batch_strategy", lambda name, seed=0: allocator)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_workload.py",
            "--config", str(FIXTURE),
            "--orders", "30",
            "--quiet",
            "--progress", "0",
            "--dataset", str(tmp_path / "run"),
            *extra,
        ],
    )

    return run_workload.main()


def raising(error):
    def how(good, robots, orders):
        raise error

    return how


@pytest.mark.parametrize(
    "how, kind, stalled",
    [
        (twice_same_robot, INVALID_ASSIGNMENT, False),
        (raising(AllocationSolverError("milp stage 1 failed")), SOLVER_FAILURE, False),
        (raising(DeadlockError("wait cycle")), TRAFFIC_DEADLOCK, True),
        (raising(WorkloadStalledError("no progress")), WORKLOAD_STALL, True),
        (raising(ValueError("a defect")), EXECUTION_ERROR, False),
    ],
)
def test_a_failed_run_records_its_kind_and_no_result(
    monkeypatch, tmp_path, capsys, how, kind, stalled
):
    code = run_script(monkeypatch, tmp_path, Misbehaving(how, at=1))
    out = capsys.readouterr().out

    assert code == 1

    run_dir = tmp_path / "run"
    failure = read_failure(run_dir)

    assert failure["kind"] == kind
    assert failure["stalled"] is stalled
    assert failure["strategy"] == "nearest_astar"
    assert failure["run_id"] and failure["strategy_sha256"]
    assert failure["orders"]["total"] == 30
    assert failure["orders"]["completed"] < 30

    # A failed run leaves no result that could be compared, and no
    # outcome is invented for an order that did not finish.
    assert not (run_dir / "metrics.json").exists()
    assert not (run_dir / "run.json").exists()
    assert not (run_dir / "outcomes.parquet").exists()

    assert f"FAILURE KIND: {kind}" in out
    assert ("STALLED:" in out) is stalled


def test_an_eligibility_violation_is_not_a_stall(monkeypatch, tmp_path, capsys):
    # The executor's own re-check refusing a pair, as it does for a
    # strategy that returns a battery-ineligible one
    # (test_battery_eligibility.py covers the check itself).
    def refuse(self, assignments, battery):
        raise IneligibleAssignmentError("battery-ineligible")

    monkeypatch.setattr(WorkloadExecutor, "_check_battery_feasibility", refuse)

    code = run_script(monkeypatch, tmp_path, get_batch_strategy("nearest_astar"))
    out = capsys.readouterr().out

    assert code == 1
    assert read_failure(tmp_path / "run")["kind"] == ELIGIBILITY_VIOLATION
    assert "STALLED:" not in out


def test_a_successful_rerun_clears_an_old_failure(monkeypatch, tmp_path, capsys):
    assert run_script(monkeypatch, tmp_path, Misbehaving(twice_same_robot, at=1)) == 1
    assert read_failure(tmp_path / "run") is not None

    assert run_script(monkeypatch, tmp_path, get_batch_strategy("nearest_astar")) == 0
    capsys.readouterr()

    assert read_failure(tmp_path / "run") is None
    assert (tmp_path / "run" / "metrics.json").exists()


def test_compare_names_the_failure_kind(tmp_path):
    run_dir = tmp_path / "random" / "r4_s2"
    run_dir.mkdir(parents=True)
    (run_dir / "run.log").write_text("FAILED: twice\n")
    (run_dir / "failure.json").write_text(
        json.dumps({"kind": INVALID_ASSIGNMENT, "stalled": False})
    )

    assert compare_paired.missing_reason(run_dir) == (
        "failed: invalid_assignment; see failure.json"
    )

    (run_dir / "failure.json").write_text(
        json.dumps({"kind": TRAFFIC_DEADLOCK, "stalled": True})
    )

    assert "traffic_deadlock (stalled)" in compare_paired.missing_reason(run_dir)
    assert compare_paired.missing_reason(tmp_path / "nowhere") == "not run"


# ======================================================================
# F7: dispatch quantum and window through the experiment
# ======================================================================


def test_the_command_carries_the_condition_only_when_given(tmp_path):
    plain = run_experiment.workload_command(tmp_path, "random", 4, 1, 3, False)

    assert "--quantum" not in plain and "--window" not in plain

    batched = run_experiment.workload_command(
        tmp_path, "random", 4, 1, 3, False, quantum=3.0, window="5"
    )

    assert batched[batched.index("--quantum") + 1] == "3.0"
    assert batched[batched.index("--window") + 1] == "5"

    continuous = run_experiment.workload_command(
        tmp_path, "random", 4, 1, 3, False, quantum=0.0
    )

    # 0 is a real condition (continuous dispatch), not "unset".
    assert continuous[continuous.index("--quantum") + 1] == "0.0"


def experiment(tmp_path, name, *extra):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "run_experiment.py"),
            "--out", str(tmp_path),
            "--name", name,
            "--strategies", "nearest_astar",
            "--robots", "4",
            "--orders", "3",
            "--jobs", "2",
            *extra,
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=600,
    )


def manifests(base):
    return [json.loads(path.read_text()) for path in sorted(base.glob("*/r*_s*/run.json"))]


def summary_rows(base):
    with open(base / "summary.csv") as handle:
        return list(csv.DictReader(handle))


def test_quantum_and_window_reach_every_run(tmp_path):
    q3 = experiment(tmp_path, "q3", "--seeds", "1", "2", "--quantum", "3", "--window", "5")
    assert q3.returncode == 0, q3.stdout + q3.stderr

    q0 = experiment(tmp_path, "q0", "--seeds", "1", "2", "--quantum", "0")
    assert q0.returncode == 0, q0.stdout + q0.stderr

    default = experiment(tmp_path, "default", "--seeds", "1")
    assert default.returncode == 0, default.stdout + default.stderr

    spec = json.loads((tmp_path / "q3" / "experiment.json").read_text())["spec"]
    assert spec["quantum"] == 3.0 and spec["window"] == "5"

    for manifest in manifests(tmp_path / "q3"):
        assert manifest["dispatch_quantum_s"] == 3.0
        assert manifest["allocation_window"] == "5"
        assert manifest["window_width"] == 5

    for manifest in manifests(tmp_path / "q0"):
        assert manifest["dispatch_quantum_s"] == 0.0

    # No flag: the one config decides, and the record says so.
    configured = load_sim_config(ROOT / run_workload.CONFIG)

    for manifest in manifests(tmp_path / "default"):
        assert manifest["dispatch_quantum_s"] == float(configured.dispatch_quantum_s)
        assert manifest["allocation_window"] == str(configured.allocation_window)

    # Two conditions never share a settings signature, so they can
    # never be paired.
    signatures = {
        name: {row["settings_sha256"] for row in summary_rows(tmp_path / name)}
        for name in ("q3", "q0")
    }
    assert len(signatures["q3"]) == len(signatures["q0"]) == 1
    assert signatures["q3"] != signatures["q0"]

    # Resuming with another condition is refused.
    other = experiment(tmp_path, "q3", "--seeds", "1", "2", "--quantum", "0", "--window", "5", "--resume")
    assert other.returncode == 2
    assert "quantum changed" in other.stdout


@pytest.mark.parametrize(
    "extra, message",
    [
        (["--quantum", "-1"], "--quantum must be >= 0"),
        (["--window", "0"], "--window 0"),
        (["--strategies", "no_such_strategy"], "unknown batch strategy"),
    ],
)
def test_a_bad_condition_is_refused_before_anything_runs(tmp_path, extra, message):
    refused = experiment(tmp_path, "bad", "--seeds", "1", *extra)

    assert refused.returncode == 2
    assert message in refused.stderr
    assert not (tmp_path / "bad").exists()


# ======================================================================
# F8: strategy identity
# ======================================================================


def test_every_registered_strategy_has_an_identity():
    for name in BATCH_STRATEGIES:
        identity = get_batch_strategy(name).identity()

        assert identity["name"] == name
        assert identity["implementation"].startswith("task_management.allocation_strategy.")
        assert set(identity) == {"name", "implementation", "parameters", "execution", "artifacts"}


def test_the_seed_is_not_part_of_the_identity():
    # The seed is recorded per run; random's seed also picks the workload.
    assert identity_sha256(get_batch_strategy("random", seed=1).identity()) == identity_sha256(
        get_batch_strategy("random", seed=2).identity()
    )


def test_effective_parameters_and_solvers_are_identity():
    default = get_batch_strategy("multi_criteria").identity()
    reweighted = get_batch_strategy(
        "multi_criteria", weights={"finish": 1.0, "fifo_age": 0.0}
    ).identity()

    assert default["parameters"]["weights"]["finish"] == 0.5
    assert identity_sha256(default) != identity_sha256(reweighted)

    for name in ("milp", "hungarian"):
        execution = get_batch_strategy(name).identity()["execution"]

        assert execution["solver"] and execution["scipy"]


class LearnedStub(NearestAStarAllocator):
    """The shape a learned strategy takes: a rule artifact and its settings."""

    name = "learned_stub"

    def __init__(self, rules, seed=0, max_rules=5):
        self.rules, self.max_rules = rules, max_rules
        super().__init__(seed)

    def parameters(self):
        return {"max_rules": self.max_rules}

    def execution(self):
        return {"rule_engine": "compiled-python"}

    def artifacts(self):
        return [artifact_record("rules", self.rules)]


def test_a_rule_artifact_is_identified_by_its_content(tmp_path):
    rules = tmp_path / "rules.pl"
    rules.write_text("assign(D,R,O) :- cost_rank(D,R,O,1).\n")

    first = LearnedStub(rules).identity()
    assert first["artifacts"][0]["sha256"]
    assert first["artifacts"][0]["role"] == "rules"

    # Same file, same identity; a changed rule set or setting is another
    # strategy under the same name.
    assert identity_sha256(LearnedStub(rules).identity()) == identity_sha256(first)

    rules.write_text("assign(D,R,O) :- finish_rank(D,R,O,1).\n")
    assert identity_sha256(LearnedStub(rules).identity()) != identity_sha256(first)

    rules.write_text("assign(D,R,O) :- cost_rank(D,R,O,1).\n")
    assert identity_sha256(LearnedStub(rules, max_rules=3).identity()) != identity_sha256(first)

    with pytest.raises(FileNotFoundError, match="rules"):
        LearnedStub(tmp_path / "missing.pl").identity()


def manifest_for(config, strategy, identity):
    return build_manifest(
        config,
        strategy=strategy,
        strategy_identity=identity,
        window=config.allocation_window,
        window_width=config.window_width(),
        seed=1,
        speed_mps=config.speed_mps,
        dispatch_quantum_s=config.dispatch_quantum_s,
        orders=(),
        order_limit=0,
        orders_available=0,
        repo=ROOT,
    )


def test_the_manifest_carries_the_identity(config, tmp_path):
    identity = get_batch_strategy("multi_criteria").identity()
    manifest = manifest_for(config, "multi_criteria", identity)

    assert manifest["strategy_identity"] == identity
    assert manifest["strategy_sha256"] == identity_sha256(identity)

    other = get_batch_strategy("multi_criteria", weights={"finish": 1.0}).identity()
    assert manifest_for(config, "multi_criteria", other)["run_id"] != manifest["run_id"]

    with pytest.raises(ValueError, match="identity of 'random'"):
        manifest_for(config, "multi_criteria", get_batch_strategy("random").identity())


def identity_row(strategy, robots, seed, sha):
    return {"strategy": strategy, "robots": float(robots), "seed": float(seed), "strategy_sha256": sha}


def test_one_strategy_name_must_run_one_identity():
    rows = [identity_row("milp", 10, s, "aaa") for s in (1, 2)] + [
        identity_row("nearest_astar", 10, s, "bbb") for s in (1, 2)
    ]
    check_strategies(rows)

    rows[1]["strategy_sha256"] = "ccc"

    with pytest.raises(PairingError, match="milp ran as more than one strategy identity"):
        check_strategies(rows)

    rows[1]["strategy_sha256"] = ""

    with pytest.raises(PairingError, match="no strategy_sha256"):
        check_strategies(rows)


def test_compare_and_resume_refuse_a_changed_identity(tmp_path):
    base = tmp_path / "id"
    run = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "run_experiment.py"),
            "--out", str(tmp_path), "--name", "id",
            "--strategies", "nearest_astar", "random",
            "--robots", "4", "--orders", "3", "--seeds", "1", "2", "--jobs", "4",
        ],
        capture_output=True, text=True, cwd=ROOT, timeout=600,
    )
    assert run.returncode == 0, run.stdout + run.stderr

    rows = summary_rows(base)
    expected = identity_sha256(get_batch_strategy("random").identity())

    assert {r["strategy_sha256"] for r in rows if r["strategy"] == "random"} == {expected}
    assert all(m["strategy_sha256"] for m in manifests(base))

    def compare():
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "compare_paired.py"), "--out", str(tmp_path), "--name", "id"],
            capture_output=True, text=True, cwd=ROOT,
        )

    assert compare().returncode == 0

    # One random run made by another version of the strategy.
    rows[-1]["strategy_sha256"] = "0123456789abcdef"

    with open(base / "summary.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    refused = compare()
    assert refused.returncode == 1
    assert "random ran as more than one strategy identity" in refused.stdout

    # An experiment recorded under another strategy version is not resumed.
    record_path = base / "experiment.json"
    record = json.loads(record_path.read_text())
    record["spec"]["strategy_sha256"]["random"] = "0123456789abcdef"
    record_path.write_text(json.dumps(record))

    resumed = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "run_experiment.py"),
            "--out", str(tmp_path), "--name", "id",
            "--strategies", "nearest_astar", "random",
            "--robots", "4", "--orders", "3", "--seeds", "1", "2", "--resume",
        ],
        capture_output=True, text=True, cwd=ROOT, timeout=600,
    )
    assert resumed.returncode == 2
    assert "strategy_sha256 changed" in resumed.stdout


# ======================================================================
# Seed split
# ======================================================================


def test_the_seed_roles_are_disjoint():
    seen = {}

    for role, seeds in ROLES.items():
        for seed in seeds:
            assert seed not in seen, f"seed {seed} is both {seen[seed]} and {role}"
            seen[seed] = role

    assert list(TRAINING) == list(range(101, 111))
    assert list(VALIDATION) == list(range(111, 116))
    assert list(EVALUATION) == list(range(1, 31))
    assert role_of(42) is None


def test_a_purpose_admits_only_its_own_seeds():
    check_purpose(TRAINING, "training")
    check_purpose(VALIDATION, "validation")
    check_purpose([1, 2, 42], None)

    with pytest.raises(SeedLeakageError, match=r"\[1\] are not training"):
        check_purpose([101, 1], "training")

    with pytest.raises(SeedLeakageError, match="not validation"):
        check_purpose([110], "validation")

    with pytest.raises(SeedLeakageError, match="reserved for the final"):
        check_purpose([FINAL_RESERVED[0]], None)

    check_purpose([FINAL_RESERVED[0]], "final")


def test_the_runner_refuses_a_leaking_seed(tmp_path):
    leak = experiment(tmp_path, "leak", "--seeds", "101", "5", "--purpose", "training")

    assert leak.returncode == 2
    assert "not training seeds" in leak.stderr
    assert not (tmp_path / "leak").exists()

    sealed = experiment(tmp_path, "sealed", "--seeds", str(FINAL_RESERVED[0]))

    assert sealed.returncode == 2
    assert "reserved for the final evaluation" in sealed.stderr
