"""Phase 8: the decision and outcome dataset.

The tests that matter here are not about column names. They are:

  * recording changes nothing -- an instrumented run and a bare run
    produce the same warehouse behaviour, or the dataset is measuring
    something other than the system under study
  * no leakage -- a decision row is finalized before anything is
    planned or executed, proven by truncating a run and showing the
    rows are identical to the full run's
  * the join holds -- every realized outcome points at the decision
    that caused it, and every selected pair has exactly one outcome
  * predicted and realized are comparable -- they cover the same route,
    so predicted-vs-realized means something

See docs/DECISIONS.md, "Decision and Outcome Dataset" through
"Run Identity".
"""

from pathlib import Path

import pytest

from mrta_core.order_queue import OrderQueue
from mrta_core.orders import load_orders_csv, select_orders
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.dataset import (
    DECISIONS_FILE,
    MANIFEST_FILE,
    OUTCOMES_FILE,
    SCHEMA_VERSION,
    RunRecorder,
    build_manifest,
    exact,
)
from simulation_env.fleet import build_fleet
from simulation_env.workload_executor import WorkloadExecutor
from task_management.allocation_strategy import get_batch_strategy


CONFIGS = Path(__file__).resolve().parent / "fixtures"
REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def config():
    return load_sim_config(CONFIGS / "v2_reference.yaml")


@pytest.fixture(scope="module")
def warehouse(config):
    return load_warehouse_xlsx(config.map_path)


def manifest_of(config, *, orders=12, **changes):
    """A manifest for the first ``orders`` orders; ``changes`` override."""

    available = load_orders_csv(config.orders_path)

    params = dict(
        strategy="nearest_astar",
        window=config.allocation_window,
        window_width=config.window_width(),
        seed=config.seed,
        speed_mps=config.speed_mps,
        dispatch_quantum_s=config.dispatch_quantum_s,
        orders=select_orders(available, orders),
        order_limit=orders,
        orders_available=len(available),
        repo=REPO,
    )
    params.update(changes)

    return build_manifest(config, **params)


def build(config, warehouse, *, recorder=None, orders=None, on_dispatch=None):
    """One executor over the shipped workload, optionally instrumented."""

    loaded = load_orders_csv(config.orders_path, warehouse)

    queue = OrderQueue(loaded if orders is None else loaded[:orders])

    executor = WorkloadExecutor(
        warehouse=warehouse,
        robots=build_fleet(config, warehouse),
        queue=queue,
        allocator=get_batch_strategy("nearest_astar"),
        resolution=config.resolution_m,
        speed_mps=config.speed_mps,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
        progress_interval_s=0,
        battery=config.battery,
        window=config.allocation_window,
        dispatch_quantum_s=config.dispatch_quantum_s,
        recorder=recorder,
        strategy_name="nearest_astar",
        on_dispatch=on_dispatch,
    )

    return executor, queue


def recorded(config, warehouse, **kwargs):
    recorder = RunRecorder(run_id="test")

    executor, queue = build(config, warehouse, recorder=recorder, **kwargs)

    return executor, queue, recorder


# ----------------------------------------------------------------------
# 1. Recording must not change the thing being measured.
# ----------------------------------------------------------------------


def test_recording_does_not_change_the_simulation(config, warehouse):
    """An instrumented run and a bare run are the same run.

    Recording scores the candidate set a second time, through the same
    shared ``score()`` and the same RouteCache. If that second pass
    could perturb anything -- a cache, a reservation, the clock -- the
    dataset would describe a different system than the baseline does.
    """

    bare, _ = build(config, warehouse)
    bare.run()

    instrumented, _, _ = recorded(config, warehouse)
    instrumented.run()

    assert instrumented.totals() == bare.totals()
    assert instrumented.result.decision_log == bare.result.decision_log
    assert instrumented.result.decisions == bare.result.decisions
    assert [
        (d.robot_id, d.order_id) for d in instrumented.result.dispatches
    ] == [(d.robot_id, d.order_id) for d in bare.result.dispatches]


# ----------------------------------------------------------------------
# 2. No leakage. The decision row cannot know the future because it is
#    finished before the future happens.
# ----------------------------------------------------------------------


def test_decision_rows_are_identical_when_the_run_is_truncated(config, warehouse):
    """The truncation test: stop after the first dispatch and compare.

    This is the leakage proof. If any decision field were populated
    from post-decision state -- realized waiting, actual energy, a
    completion time -- then the run that stopped immediately and the run
    that carried on would disagree about decision 0. They cannot
    disagree if the row was finalized before execution.
    """

    class Stop(RuntimeError):
        pass

    def abort(_record):
        raise Stop

    truncated = RunRecorder(run_id="test")

    executor, _ = build(config, warehouse, recorder=truncated, on_dispatch=abort)

    with pytest.raises(Stop):
        executor.run()

    full, _, complete = recorded(config, warehouse)
    full.run()

    assert truncated.decisions, "nothing was recorded before the abort"

    first = [row for row in complete.decisions if row["decision_id"] == 0]

    assert truncated.decisions == first


def test_decision_rows_are_identical_when_truncated_mid_run(config, warehouse):
    """The same proof part-way through, with the fleet in motion.

    Decision 0 happens with every robot parked and idle. A mid-run
    decision happens while robots are moving, waiting and servicing,
    which is where a leaked realized value would actually come from.
    """

    class Stop(RuntimeError):
        pass

    dispatched = []

    def abort(record):
        dispatched.append(record)

        if len(dispatched) > 40:
            raise Stop

    truncated = RunRecorder(run_id="test")

    executor, _ = build(config, warehouse, recorder=truncated, on_dispatch=abort)

    with pytest.raises(Stop):
        executor.run()

    full, _, complete = recorded(config, warehouse)
    full.run()

    seen = {row["decision_id"] for row in truncated.decisions}

    assert len(seen) > 1, "the abort came too early to prove anything"

    expected = [row for row in complete.decisions if row["decision_id"] in seen]

    assert truncated.decisions == expected


def test_no_realized_column_appears_in_the_decision_table(config, warehouse):
    """The decision schema may not name an outcome, by construction."""

    from simulation_env.dataset import _decision_schema

    names = {field.name for field in _decision_schema()}

    forbidden = {
        "realized_distance_m",
        "realized_travel_time_s",
        "realized_waiting_s",
        "realized_service_s",
        "realized_duration_s",
        "completed_at",
        "energy_consumed_percent",
        "soc_at_completion_percent",
        "charge_sessions_before",
        "yield_events_during",
        "success",
    }

    assert names & forbidden == set()


# ----------------------------------------------------------------------
# 3. The join, and the labels the learner reads.
# ----------------------------------------------------------------------


def test_every_selected_pair_has_exactly_one_outcome(config, warehouse):
    executor, queue, recorder = recorded(config, warehouse)
    executor.run()

    selected = [row for row in recorder.decisions if row["selected"]]

    assert len(selected) == len(queue)
    assert len(recorder.outcomes) == len(queue)

    # One outcome per order, and each points at a decision that exists.
    assert len({row["order_id"] for row in recorder.outcomes}) == len(queue)

    ids = {row["decision_id"] for row in recorder.decisions}

    assert all(row["decision_id"] in ids for row in recorder.outcomes)

    # And at the pair level: the outcome's robot is the one selected.
    chosen = {(row["order_id"], row["robot_id"]) for row in selected}

    assert all(
        (row["order_id"], row["robot_id"]) in chosen for row in recorder.outcomes
    )


def test_unselected_candidates_are_kept_as_negative_examples(config, warehouse):
    """ILP needs the pairs that were available and not taken."""

    executor, queue, recorder = recorded(config, warehouse)
    executor.run()

    negatives = [
        row for row in recorder.decisions if row["eligible"] and not row["selected"]
    ]

    assert negatives, "no negative examples: nothing could be learned"
    assert len(recorder.decisions) > len(queue)

    # Exactly one of the two labels, never both.
    assert all(
        not (row["selected"] and not row["eligible"]) for row in recorder.decisions
    )


def test_a_deferral_is_recorded_as_a_decision(config, warehouse):
    """A decision where nothing was assignable is still a decision.

    Dropping it would bias the dataset towards decisions that happened
    to succeed, which is exactly the kind of selection effect that
    invalidates a learned rule.
    """

    executor, _, recorder = recorded(config, warehouse)
    executor.run()

    with_outcome = {row["decision_id"] for row in recorder.outcomes}
    recorded_ids = {row["decision_id"] for row in recorder.decisions}

    # Every decision is in the table whether or not it led anywhere.
    assert recorded_ids >= with_outcome
    assert len(recorded_ids) == executor.result.decisions


# ----------------------------------------------------------------------
# 4. Predicted and realized have to be comparable.
# ----------------------------------------------------------------------


def test_predicted_and_realized_cover_the_same_route(config, warehouse):
    """C_static includes the post-drop-off retreat, so realized must too.

    The outcome row is written at retirement, after the retreat is
    driven, not at the drop-off service. Written at the drop-off, every
    order would read one cell short of its prediction and every
    predicted-vs-realized comparison would carry a constant bias.

    Equality here is also an independent check that the router's
    prediction and the executor's realization agree cell for cell, which
    is what makes the comparison meaningful at all.
    """

    executor, _, recorder = recorded(config, warehouse)
    executor.run()

    for row in recorder.outcomes:
        assert row["realized_distance_m"] == row["predicted_distance_m"]


def test_realized_values_are_never_negative_and_exclude_other_orders(
    config, warehouse
):
    """Counter differencing must attribute to one order, not to a robot."""

    executor, queue, recorder = recorded(config, warehouse)
    executor.run()

    for row in recorder.outcomes:
        assert row["realized_distance_m"] >= 0
        assert row["realized_travel_time_s"] >= 0
        assert row["realized_waiting_s"] >= 0
        assert row["realized_service_s"] >= 0
        assert row["completed_at"] >= row["launched_at"]
        assert row["retreat_finished_at"] >= row["completed_at"]

    # Per-order distance sums to less than the fleet total: travel to a
    # Stage Zone at the end of the run belongs to no order.
    per_order = sum(row["realized_distance_m"] for row in recorder.outcomes)

    assert 0 < per_order < executor.totals()["distance_m"]


def test_completion_time_is_the_drop_off_not_the_retreat(config, warehouse):
    """An order is fulfilled when it is dropped off."""

    executor, _, recorder = recorded(config, warehouse)
    executor.run()

    # At least one order's retreat finishes strictly after its drop-off,
    # or the two instants are being conflated.
    assert any(
        row["retreat_finished_at"] > row["completed_at"] for row in recorder.outcomes
    )


# ----------------------------------------------------------------------
# 5. Exactness and provenance.
# ----------------------------------------------------------------------


def test_exact_columns_round_trip_through_fraction(config, warehouse):
    """float64 is for plotting; the exact string is for reproducing."""

    from fractions import Fraction

    executor, _, recorder = recorded(config, warehouse, orders=12)
    executor.run()

    for row in recorder.decisions[:50]:
        parsed = Fraction(row["sim_time_exact"])

        # The exact string is authoritative and the float is the lossy
        # view of it, not the other way round.
        assert str(parsed) == row["sim_time_exact"]
        assert float(parsed) == row["sim_time"]

        if row["robot_soc_exact"]:
            assert float(Fraction(row["robot_soc_exact"])) == pytest.approx(
                row["robot_soc_percent"]
            )


def test_exact_handles_none():
    assert exact(None) == ""


def test_the_manifest_identifies_the_run(config):
    manifest = manifest_of(config)

    assert manifest["run_id"]
    assert manifest["strategy"] == "nearest_astar"
    assert manifest["robot_count"] == config.robot_count
    assert manifest["allocation_window"] == "fleet_size"
    assert manifest["window_width"] == config.robot_count

    # The inputs are named by content, not just by path: a map or order
    # file edited in place must not pass as the same run.
    assert len(manifest["map_sha256"]) == 64
    assert len(manifest["orders_sha256"]) == 64
    assert len(manifest["config_sha256"]) == 64

    assert "dirty" in manifest["git"]
    assert manifest["battery"]["reserve_percent"] == 10.0


def test_the_run_id_changes_when_an_input_changes(config):
    def run_id(**kwargs):
        return manifest_of(config, **kwargs)["run_id"]

    base = run_id()

    assert run_id(strategy="random") != base
    assert run_id(window="legacy") != base
    assert run_id(window_width=4) != base
    # The run's effective conditions, not the config's defaults.
    assert run_id(seed=config.seed + 1) != base
    assert run_id(speed_mps=config.speed_mps + 0.5) != base
    assert run_id(dispatch_quantum_s=config.dispatch_quantum_s + 3.0) != base
    assert run_id(orders=5) != base
    assert run_id() == base


# ----------------------------------------------------------------------
# 6. Writing the dataset.
# ----------------------------------------------------------------------


def test_the_dataset_writes_and_reads_back(config, warehouse, tmp_path):
    pytest.importorskip("pyarrow")

    import pyarrow as pa
    import pyarrow.parquet as pq

    manifest = manifest_of(config)

    recorder = RunRecorder(run_id=manifest["run_id"])

    executor, queue = build(config, warehouse, recorder=recorder, orders=12)
    executor.run()

    run_dir = recorder.write(tmp_path / "run", manifest)

    decisions = pq.read_table(run_dir / DECISIONS_FILE)
    outcomes = pq.read_table(run_dir / OUTCOMES_FILE)

    assert decisions.num_rows == len(recorder.decisions)
    assert outcomes.num_rows == len(queue)
    assert (run_dir / MANIFEST_FILE).is_file()

    from json import loads

    written = loads((run_dir / MANIFEST_FILE).read_text())

    assert written["schema_version"] == SCHEMA_VERSION
    assert written["run_id"] == manifest["run_id"]
    assert written["decision_rows"] == decisions.num_rows
    assert written["outcome_rows"] == outcomes.num_rows

    # Types are declared, not inferred from whichever row came first.
    assert decisions.schema.field("selected").type == pa.bool_()
    assert decisions.schema.field("c_static_m").type == pa.float64()
    assert decisions.schema.field("sim_time_exact").type == pa.string()


# ----------------------------------------------------------------------
# 7. Reading the dataset back out: CSV for people, Prolog for a learner.
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def exported(config, warehouse, tmp_path_factory):
    """One recorded 12-order run, written and exported once."""

    pytest.importorskip("pyarrow")

    manifest = manifest_of(config)

    recorder = RunRecorder(run_id=manifest["run_id"])

    executor, queue = build(config, warehouse, recorder=recorder, orders=12)
    executor.run()

    run_dir = recorder.write(tmp_path_factory.mktemp("export") / "run", manifest)

    from simulation_env.dataset_export import write_csv, write_prolog

    write_csv(run_dir)
    write_prolog(run_dir)

    return run_dir, recorder, len(queue)


def test_csv_carries_every_row_and_column(exported):
    from csv import DictReader

    run_dir, recorder, _ = exported

    with (run_dir / "decisions.csv").open() as handle:
        rows = list(DictReader(handle))

    assert len(rows) == len(recorder.decisions)

    from simulation_env.dataset import _decision_schema

    assert list(rows[0]) == [field.name for field in _decision_schema()]

    # The exact column survives as text, which is the whole reason it
    # exists: a CSV float would lose the rational.
    from fractions import Fraction

    assert all(Fraction(row["sim_time_exact"]) >= 0 for row in rows)


def test_the_export_derives_nothing(exported):
    """Every exported value is a value that was recorded."""

    run_dir, recorder, _ = exported

    from csv import DictReader

    with (run_dir / "decisions.csv").open() as handle:
        rows = list(DictReader(handle))

    for written, source in zip(rows, recorder.decisions):
        assert written["order_id"] == source["order_id"]
        assert written["sim_time_exact"] == source["sim_time_exact"]
        assert float(written["c_static_m"]) == source["c_static_m"]


def test_prolog_examples_are_the_selected_pair_against_the_rest(exported):
    run_dir, recorder, orders = exported

    examples = (run_dir / "exs.pl").read_text().splitlines()

    positives = [line for line in examples if line.startswith("pos(")]
    negatives = [line for line in examples if line.startswith("neg(")]

    assert len(positives) == orders
    assert negatives

    # An ineligible pair is not a negative example: explaining it would
    # mean learning the feasibility mask, which is fixed infrastructure.
    eligible = sum(1 for row in recorder.decisions if row["eligible"])

    assert len(positives) + len(negatives) == eligible


def test_every_prolog_example_names_a_declared_candidate(exported):
    """An example whose terms have no facts is unusable to a learner."""

    run_dir, _, _ = exported

    facts = (run_dir / "bk.pl").read_text().splitlines()
    examples = (run_dir / "exs.pl").read_text().splitlines()

    candidates = {
        line[len("candidate(") : -2]
        for line in facts
        if line.startswith("candidate(")
    }

    for line in examples:
        if not line.startswith(("pos(", "neg(")):
            continue

        terms = line[line.index("assign(") + len("assign(") : line.index("))")]

        assert terms in candidates


def test_prolog_keeps_counts_as_integers(exported):
    """A count compared as a float reads as though it were measured."""

    run_dir, _, _ = exported

    facts = (run_dir / "bk.pl").read_text().splitlines()

    def last_argument(line):
        return line[line.index("(") + 1 : line.rindex(")")].rsplit(", ", 1)[-1]

    for predicate in ("free_robots", "pending_orders", "fifo_position"):
        lines = [line for line in facts if line.startswith(predicate + "(")]

        assert lines

        for line in lines:
            assert last_argument(line).isdigit()

    # And the converse: a measured quantity stays a float.
    cost = [line for line in facts if line.startswith("cost(")]

    assert cost
    assert all("." in last_argument(line) for line in cost)


def test_prolog_asserts_nothing_about_an_identifier(exported):
    """Identifiers are traceability, never a scientific predicate.

    No fact may order robots, expose the numeric part of a robot_id, or
    otherwise let a learner key on identity instead of on state.
    """

    run_dir, _, _ = exported

    facts = (run_dir / "bk.pl").read_text().splitlines()

    predicates = {
        line[: line.index("(")] for line in facts if "(" in line and line[0] != "%"
    }

    from simulation_env.dataset_export import (
        PROLOG_DECISION_FEATURES,
        PROLOG_PAIR_FEATURES,
        PROLOG_ROBOT_FEATURES,
    )

    allowed = (
        {"decision", "decision_time", "candidate"}
        | set(PROLOG_DECISION_FEATURES)
        | set(PROLOG_ROBOT_FEATURES)
        | set(PROLOG_PAIR_FEATURES)
    )

    assert predicates <= allowed

    for banned in ("robot_index", "succ", "less_than", "robot_number", "precedes"):
        assert banned not in predicates


def test_a_soc_fact_is_stated_once_per_robot_per_decision(exported):
    """Not once per pair: SOC is a property of the robot at t."""

    run_dir, _, _ = exported

    facts = (run_dir / "bk.pl").read_text().splitlines()

    soc = [line for line in facts if line.startswith("soc(")]

    assert len(soc) == len(set(soc))

    candidates = [line for line in facts if line.startswith("candidate(")]

    # Strictly fewer SOC facts than candidate pairs, or it is being
    # repeated for every order in the window.
    assert len(soc) < len(candidates)


# ----------------------------------------------------------------------
# 8. The CSV fallback, for the live ROS node.
# ----------------------------------------------------------------------


def test_the_csv_fallback_loses_nothing(config, warehouse, tmp_path, monkeypatch):
    """A finished run must survive a missing pyarrow.

    The live ROS node runs on the system interpreter, not the pixi
    environment, so pyarrow cannot be assumed there. Crashing after a
    complete run would throw the whole run away over an optional
    dependency.
    """

    from simulation_env.dataset_export import read_run

    manifest = manifest_of(config)

    recorder = RunRecorder(run_id=manifest["run_id"])

    executor, _ = build(config, warehouse, recorder=recorder, orders=12)
    executor.run()

    # Parquet first, as the reference.
    reference = recorder.write(tmp_path / "parquet", dict(manifest))

    # Then with pyarrow unavailable.
    def no_pyarrow(self, run_dir):
        raise ImportError("no pyarrow")

    monkeypatch.setattr(RunRecorder, "_write_parquet", no_pyarrow)

    fallback = recorder.write(tmp_path / "csv", dict(manifest))

    assert (fallback / "decisions.csv").is_file()
    assert not (fallback / DECISIONS_FILE).exists()

    # The manifest says which format, so a reader never has to guess.
    from json import loads

    assert loads((reference / MANIFEST_FILE).read_text())["format"] == "parquet"
    assert loads((fallback / MANIFEST_FILE).read_text())["format"] == "csv"

    # And the rows come back the same either way, types included.
    expected, expected_out, _ = read_run(reference)
    actual, actual_out, _ = read_run(fallback)

    assert actual == expected
    assert actual_out == expected_out


def test_the_exact_columns_survive_the_csv_fallback(
    config, warehouse, tmp_path, monkeypatch
):
    """The whole point of the exact string is that text cannot lose it."""

    from fractions import Fraction

    from simulation_env.dataset_export import read_run

    recorder = RunRecorder(run_id="test")

    executor, _ = build(config, warehouse, recorder=recorder, orders=12)
    executor.run()

    monkeypatch.setattr(
        RunRecorder,
        "_write_parquet",
        lambda self, run_dir: (_ for _ in ()).throw(ImportError("no pyarrow")),
    )

    run_dir = recorder.write(tmp_path / "csv", {})

    decisions, outcomes, _ = read_run(run_dir)

    for row, source in zip(decisions, recorder.decisions):
        assert Fraction(row["sim_time_exact"]) == Fraction(source["sim_time_exact"])

    assert outcomes
