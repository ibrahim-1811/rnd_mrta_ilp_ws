"""The teacher panel: every teacher labels the same decision, and only one acts."""

import os
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from simulation_env.teacher_panel import PANEL_FILE, PANEL_TEACHERS


REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "src/simulation_env/scripts/run_workload.py"


def workload(*options):
    env = {k: v for k, v in os.environ.items() if k != "AMENT_PREFIX_PATH"}
    env["PYTHONPATH"] = os.pathsep.join(
        str(REPO / "src" / package)
        for package in ("mrta_core", "simulation_env", "task_management", "ilp_learning")
    )
    return subprocess.run(
        [sys.executable, str(SCRIPT), *options],
        capture_output=True, text=True, cwd=REPO, check=False, env=env,
    )


def run(out, strategy, *options):
    done = workload(
        "--strategy", strategy, "--robots", "20", "--orders", "50", "--quantum", "3",
        "--seed", "42", "--quiet", "--progress", "0", "--dataset", str(out), *options,
    )
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.fixture(scope="module")
def panel_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("panel") / "run"
    run(out, "nearest_astar", "--teacher-panel")
    return out


def selected_pairs(run_dir):
    rows = pd.read_parquet(run_dir / "decisions.parquet")
    rows = rows[rows["selected"]]
    return set(zip(rows["decision_id"], rows["robot_id"], rows["order_id"]))


def test_a_teacher_in_the_panel_reproduces_its_own_live_picks(panel_run):
    labels = pd.read_parquet(panel_run / PANEL_FILE)
    own = labels[labels["teacher"] == "nearest_astar"]
    assert set(zip(own["decision_id"], own["robot_id"], own["order_id"])) == selected_pairs(panel_run)


def test_every_teacher_labels_every_decision(panel_run):
    labels = pd.read_parquet(panel_run / PANEL_FILE)
    assert set(labels["teacher"]) == set(PANEL_TEACHERS)
    decisions = set(pd.read_parquet(panel_run / "decisions.parquet")["decision_id"])
    for _, rows in labels.groupby("teacher"):
        assert set(rows["decision_id"]) == decisions


def test_each_teacher_gives_a_legal_matching(panel_run):
    labels = pd.read_parquet(panel_run / PANEL_FILE)
    eligible = pd.read_parquet(panel_run / "decisions.parquet")
    eligible = eligible[eligible["eligible"]]
    legal = set(zip(eligible["decision_id"], eligible["robot_id"], eligible["order_id"]))
    assert set(zip(labels["decision_id"], labels["robot_id"], labels["order_id"])) <= legal
    for _, rows in labels.groupby(["teacher", "decision_id"]):
        assert rows["robot_id"].is_unique and rows["order_id"].is_unique


def test_the_teachers_do_not_all_say_the_same(panel_run):
    labels = pd.read_parquet(panel_run / PANEL_FILE)
    picks = {
        teacher: set(zip(rows["decision_id"], rows["robot_id"], rows["order_id"]))
        for teacher, rows in labels.groupby("teacher")
    }
    assert len({frozenset(p) for p in picks.values()}) > 1


def test_the_panel_changes_nothing_in_the_run(panel_run, tmp_path):
    plain = tmp_path / "plain"
    run(plain, "nearest_astar")
    for table in ("decisions", "outcomes"):
        a = pd.read_parquet(plain / f"{table}.parquet").drop(columns="run_id")
        b = pd.read_parquet(panel_run / f"{table}.parquet").drop(columns="run_id")
        pd.testing.assert_frame_equal(a, b)
    a, b = (pd.read_json(d / "metrics.json", typ="series") for d in (plain, panel_run))
    for key in ("candidate_pairs", "ineligible_pairs"):
        if key in a:
            assert a[key] == b[key]


def test_the_panel_needs_a_dataset():
    done = workload("--teacher-panel", "--orders", "1", "--quiet")
    assert done.returncode != 0 and "--teacher-panel needs --dataset" in done.stderr
