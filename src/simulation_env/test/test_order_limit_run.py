"""A run on fewer orders: what it executes, and what it records.

The headless runner (``--orders N``) and the ROS node
(``order_limit:=N``) run the first N logical orders through the same
select_orders(), and every recorded run says what it really ran:
run.json carries the effective order count, seed, robots, strategy,
speed and dispatch quantum, and two different conditions never share a
run_id. rclpy is not available to these tests, so the ROS side is
checked in its source: the parameter, the shared rule and the manifest
arguments.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from mrta_core.orders import load_orders_csv, select_orders


PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[1]
RUN_WORKLOAD = PACKAGE / "scripts" / "run_workload.py"
FLEET_NODE = PACKAGE / "simulation_env" / "fleet_node.py"
LAUNCH = PACKAGE / "launch" / "warehouse_sim.launch.py"
ORDERS = PACKAGE / "config" / "orders.csv"


def run_workload(tmp_path, name, *args):
    """One headless run with a dataset; returns (stdout, run directory)."""

    run_dir = tmp_path / name
    env = {"PYTHONPATH": ":".join(str(ROOT / "src" / p) for p in ("mrta_core", "simulation_env", "task_management"))}

    result = subprocess.run(
        [sys.executable, str(RUN_WORKLOAD), "--quiet", "--progress", "0", "--dataset", str(run_dir), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
        timeout=300,
    )

    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]

    return result.stdout, run_dir


def read(run_dir, name):
    return json.loads((run_dir / name).read_text())


@pytest.fixture(scope="module")
def five(tmp_path_factory):
    """5 orders, with every effective condition set away from the config."""

    return run_workload(
        tmp_path_factory.mktemp("five"),
        "run",
        "--robots", "3", "--orders", "5", "--seed", "7",
        "--speed", "1.0", "--quantum", "0.5", "--strategy", "random",
    )


def test_a_limited_run_executes_exactly_the_first_n_orders(five):
    stdout, run_dir = five

    pyarrow = pytest.importorskip("pyarrow.parquet")
    outcomes = pyarrow.read_table(run_dir / "outcomes.parquet").to_pydict()

    first_five = [order.order_id for order in select_orders(load_orders_csv(ORDERS), 5)]

    assert sorted(outcomes["order_id"]) == sorted(first_five)
    assert "first 5 of 2000 logical orders" in stdout
    assert "completed        : 5/5" in stdout


def test_run_json_records_what_was_really_run(five):
    _, run_dir = five
    manifest = read(run_dir, "run.json")
    expected_items = sum(len(o.items) for o in select_orders(load_orders_csv(ORDERS), 5))

    assert manifest["orders"] == 5
    assert manifest["line_items"] == expected_items
    assert manifest["orders_available"] == 2000
    assert manifest["order_limit"] == 5
    assert manifest["seed"] == 7
    assert manifest["robot_count"] == 3
    assert manifest["strategy"] == "random"
    assert manifest["speed_mps"] == 1.0
    assert manifest["dispatch_quantum_s"] == 0.5
    assert manifest["outcome_rows"] == 5


def test_metrics_and_dataset_agree_on_the_order_count(five):
    _, run_dir = five
    metrics = read(run_dir, "metrics.json")
    manifest = read(run_dir, "run.json")

    assert metrics["orders"] == metrics["orders_completed"] == 5
    assert metrics["seed"] == manifest["seed"] == 7
    assert metrics["robots"] == manifest["robot_count"] == 3


def test_a_limit_above_the_workload_runs_every_order(tmp_path):
    """And names the same run as no limit at all."""

    # A three-order file for the current map, from the real one.
    rows = ORDERS.read_text().splitlines()
    keep = {order.order_id for order in select_orders(load_orders_csv(ORDERS), 3)}
    small = tmp_path / "three_orders.csv"
    small.write_text("\n".join([rows[0], *(r for r in rows[1:] if r.split(",")[0] in keep)]) + "\n")

    common = ("--robots", "2", "--orders-file", str(small))

    over, over_dir = run_workload(tmp_path, "over", *common, "--orders", "10")
    _, all_dir = run_workload(tmp_path, "all", *common)

    assert "the limit 10 is more than the file holds" in over

    limited, unlimited = read(over_dir, "run.json"), read(all_dir, "run.json")

    assert limited["orders"] == unlimited["orders"] == 3
    assert (limited["order_limit"], unlimited["order_limit"]) == (10, 0)
    # Same orders, same everything else: the same run.
    assert limited["run_id"] == unlimited["run_id"]


# ----------------------------------------------------------------------
# The ROS side, read from its source.
# ----------------------------------------------------------------------


def calls(path, name):
    """Every call to ``name`` in a Python file, as ast.Call nodes."""

    tree = ast.parse(path.read_text())

    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None)) == name
    ]


def test_ros_and_headless_select_orders_the_same_way():
    for path in (FLEET_NODE, RUN_WORKLOAD):
        assert calls(path, "select_orders"), path.name
        assert calls(path, "describe_order_selection"), path.name


def test_the_node_has_an_order_limit_defaulting_to_all():
    declared = {
        node.args[0].value: node.args[1].value
        for node in calls(FLEET_NODE, "declare_parameter")
        if isinstance(node.args[1], ast.Constant)
    }

    assert declared["order_limit"] == 0


def test_the_node_records_its_effective_conditions():
    (call,) = calls(FLEET_NODE, "build_manifest")

    keywords = {keyword.arg for keyword in call.keywords}

    assert {
        "seed", "speed_mps", "dispatch_quantum_s",
        "orders", "order_limit", "orders_available", "strategy",
    } <= keywords


def test_the_launch_keeps_existing_commands_working():
    """orders:=true still switches the workload on; order_limit is new."""

    arguments = {
        node.args[0].value: node.keywords[0].value.value
        for node in calls(LAUNCH, "DeclareLaunchArgument")
        if node.keywords and isinstance(node.keywords[0].value, ast.Constant)
    }

    assert arguments["orders"] == "false"
    assert arguments["order_limit"] == "0"

    source = LAUNCH.read_text()

    assert '"order_limit": LaunchConfiguration("order_limit")' in source
    assert '"load_orders": LaunchConfiguration("orders")' in source
    assert '"run_workload": LaunchConfiguration("orders")' in source
