"""Paired workload seeds: one workload per seed, shared by every strategy.

docs/DECISIONS.md, "Workload Seeds and Paired Comparison". The workload
files are deterministic and seed 42 reproduces the committed reference;
every strategy of one seed runs the same file; the paired statistics
are the textbook paired t-interval and refuse pairs that ran different
workloads.
"""

import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from simulation_env.paired import PairingError, interval, paired_differences


PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[1]
SCRIPTS = PACKAGE / "scripts"
REFERENCE = PACKAGE / "config" / "orders.csv"


def script(name):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))

    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


# -- workloads ---------------------------------------------------------


def test_seed_42_reproduces_the_committed_reference_workload(tmp_path):
    experiment = script("run_experiment")

    paths = experiment.write_workloads(tmp_path, [42])

    assert paths[42].read_bytes() == REFERENCE.read_bytes()


def test_a_seed_always_writes_the_same_workload_and_seeds_differ(tmp_path):
    experiment = script("run_experiment")

    first = experiment.write_workloads(tmp_path / "a", [1, 2], orders=50)
    again = experiment.write_workloads(tmp_path / "b", [1, 2], orders=50)

    assert first[1].read_bytes() == again[1].read_bytes()
    assert first[1].read_bytes() != first[2].read_bytes()


def test_the_first_n_orders_of_a_workload_are_the_workload_of_n():
    generate = script("generate_orders")

    whole = generate.build(200, 7)
    short = generate.build(20, 7)

    assert whole.startswith(short)


def test_the_workload_index_names_each_file_and_its_hash(tmp_path):
    experiment = script("run_experiment")

    experiment.write_workloads(tmp_path, [3], orders=10)
    index = json.loads((tmp_path / "workloads" / "workloads.json").read_text())

    assert index["3"]["file"] == "orders_s3.csv"
    assert index["3"]["orders"] == 10
    assert len(index["3"]["sha256"]) == 64


def test_every_strategy_runs_every_seed_when_paired():
    experiment = script("run_experiment")

    paired = list(experiment.runs_for(["nearest_astar", "random"], [10], [1, 2], True))
    reference = list(experiment.runs_for(["nearest_astar", "random"], [10], [1, 2], False))

    assert paired == [
        ("nearest_astar", 10, 1),
        ("nearest_astar", 10, 2),
        ("random", 10, 1),
        ("random", 10, 2),
    ]
    # The reference workload keeps the old rule.
    assert reference == [("nearest_astar", 10, 1), ("random", 10, 1), ("random", 10, 2)]


# -- statistics --------------------------------------------------------


def test_interval_is_the_paired_t_interval():
    # d = 1, 2, 3: mean 2, sd 1, t(0.975, 2) = 4.302653
    centre, low, high = interval([1.0, 2.0, 3.0])

    assert centre == pytest.approx(2.0)
    assert low == pytest.approx(2.0 - 4.302653 / 3**0.5, abs=1e-6)
    assert high == pytest.approx(2.0 + 4.302653 / 3**0.5, abs=1e-6)


def test_one_pair_has_no_interval():
    assert interval([5.0]) == (5.0, None, None)


def row(strategy, seed, makespan, sha="a" * 64, robots=10.0):
    return {
        "strategy": strategy,
        "robots": robots,
        "seed": float(seed),
        "orders": 50.0,
        "orders_sha256": f"{sha}{seed}",
        "order_makespan_s": makespan,
    }


def test_differences_are_strategy_minus_baseline_per_seed():
    rows = [
        row("nearest_astar", 1, 100.0),
        row("nearest_astar", 2, 110.0),
        row("random", 1, 104.0),
        row("random", 2, 116.0),
    ]

    [record] = paired_differences(rows, "nearest_astar", ["order_makespan_s"])

    assert record["n"] == 2
    assert record["mean_difference"] == pytest.approx(5.0)
    assert record["baseline_mean"] == pytest.approx(105.0)
    assert record["percent_of_baseline"] == pytest.approx(100 * 5.0 / 105.0)


def test_pairs_that_ran_different_workloads_are_refused():
    rows = [row("nearest_astar", 1, 100.0), row("random", 1, 104.0, sha="b" * 64)]

    with pytest.raises(PairingError, match="orders_sha256"):
        paired_differences(rows, "nearest_astar", ["order_makespan_s"])


def test_a_seed_run_twice_is_refused():
    rows = [row("random", 1, 100.0), row("random", 1, 101.0)]

    with pytest.raises(PairingError, match="twice"):
        paired_differences(rows, "nearest_astar", ["order_makespan_s"])


# -- end to end --------------------------------------------------------


def test_a_paired_experiment_shares_one_workload_per_seed(tmp_path):
    """Two strategies, two seeds, real headless runs of a few orders."""

    run = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "run_experiment.py"),
            "--out", str(tmp_path),
            "--name", "paired",
            "--strategies", "nearest_astar", "nearest_manhattan",
            "--robots", "5",
            "--seeds", "1", "2",
            "--orders", "4",
            "--jobs", "4",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=600,
    )

    assert run.returncode == 0, run.stdout + run.stderr

    base = tmp_path / "paired"

    with open(base / "summary.csv") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 4
    assert {r["workload"] for r in rows} == {"seeded"}

    by_seed = {}

    for r in rows:
        manifest = json.loads((Path(r["run_dir"]) / "run.json").read_text())
        assert manifest["orders_path"] == str(base / "workloads" / f"orders_s{r['seed']}.csv")
        assert manifest["orders"] == 4
        by_seed.setdefault(r["seed"], set()).add(r["orders_sha256"])

    # One workload per seed, different between seeds.
    assert all(len(hashes) == 1 for hashes in by_seed.values())
    assert by_seed["1"] != by_seed["2"]

    compare = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "compare_paired.py"),
            "--out", str(tmp_path),
            "--name", "paired",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert compare.returncode == 0, compare.stdout + compare.stderr

    with open(base / "paired.csv") as handle:
        records = list(csv.DictReader(handle))

    makespan = next(r for r in records if r["metric"] == "order_makespan_s")

    assert makespan["strategy"] == "nearest_manhattan"
    assert makespan["n"] == "2"
