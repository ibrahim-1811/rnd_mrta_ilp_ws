"""Paired experiment integrity (E1).

Four guarantees before a paired comparison is reported:

  * complete    every expected strategy x fleet size x seed run exists
  * settings    every run shares one map and simulator setting and one
                code commit; only strategy, seed, fleet size and the
                workload differ
  * overwrite   an existing experiment is never overwritten; --resume
                finishes it, --force keeps the old one renamed
  * pairs       the two runs of a pair ran the same workload

The end-to-end tests use real headless runs of a few orders.
"""

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from simulation_env.paired import (
    PairingError,
    check_complete,
    check_settings,
    paired_differences,
    settings_signature,
)


PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[1]
SCRIPTS = PACKAGE / "scripts"


# -- settings signature ------------------------------------------------


def manifest(**changes):
    base = {
        "git": {"commit": "abc", "branch": "b", "dirty": False},
        "config_path": "/a/warehouse.yaml",
        "config_sha256": "c" * 64,
        "map_path": "/a/W100.xlsx",
        "map_sha256": "m" * 64,
        "orders_path": "/x/orders_s1.csv",
        "orders_sha256": "o" * 64,
        "orders": 50,
        "line_items": 92,
        "robot_count": 10,
        "speed_mps": 1.5,
        "strategy": "nearest_astar",
        "seed": 1,
        "allocation_window": "fleet",
        "window_width": 10,
        "dispatch_quantum_s": 0.0,
        "battery": {"enabled": True, "initial_soc_percent": 100.0},
        "run_id": "r1",
    }

    return base | changes


def test_runs_of_one_experiment_share_a_signature():
    one = manifest()
    other = manifest(
        strategy="random",
        seed=7,
        robot_count=30,
        window_width=30,
        orders_path="/x/orders_s7.csv",
        orders_sha256="p" * 64,
        line_items=88,
        run_id="r2",
        git={"commit": "abc", "branch": "b", "dirty": True},
        # Outputs the recorder adds after the run.
        format="parquet",
        decision_rows=2567,
        outcome_rows=100,
    )

    assert settings_signature(one) == settings_signature(other)


@pytest.mark.parametrize(
    "change",
    [
        {"map_sha256": "n" * 64},
        {"config_sha256": "d" * 64},
        {"speed_mps": 2.0},
        {"dispatch_quantum_s": 3.0},
        {"allocation_window": "legacy"},
        {"orders": 60},
        {"battery": {"enabled": True, "initial_soc_percent": 80.0}},
        {"git": {"commit": "def", "branch": "b", "dirty": False}},
    ],
)
def test_a_changed_setting_changes_the_signature(change):
    assert settings_signature(manifest()) != settings_signature(manifest(**change))


# -- completeness and settings ----------------------------------------


def row(strategy, robots, seed, **extra):
    return {
        "strategy": strategy,
        "robots": float(robots),
        "seed": float(seed),
        "orders": 50.0,
        "orders_sha256": f"w{seed}",
        "line_items": 90.0 + seed,
        "window_width": float(robots),
        "settings_sha256": "s1",
        "order_makespan_s": 100.0 + seed,
    } | extra


GRID = [["nearest_astar", 10, 1], ["nearest_astar", 10, 2], ["random", 10, 1], ["random", 10, 2]]


def test_a_complete_experiment_passes():
    rows = [row(s, r, seed) for s, r, seed in GRID]

    check_complete(rows, GRID)
    check_settings(rows)


def test_a_missing_run_is_refused():
    rows = [row(s, r, seed) for s, r, seed in GRID[:-1]]

    with pytest.raises(PairingError, match="1 expected run"):
        check_complete(rows, GRID)


def test_an_unexpected_run_is_refused():
    rows = [row(s, r, seed) for s, r, seed in GRID] + [row("random", 10, 3)]

    with pytest.raises(PairingError, match="1 unexpected"):
        check_complete(rows, GRID)


def test_mixed_settings_are_refused():
    rows = [row(s, r, seed) for s, r, seed in GRID]
    rows[2]["settings_sha256"] = "s2"

    with pytest.raises(PairingError, match="differ in map or simulator settings"):
        check_settings(rows)


def test_a_summary_without_signatures_is_refused():
    rows = [row(s, r, seed, settings_sha256="") for s, r, seed in GRID]

    with pytest.raises(PairingError, match="no settings_sha256"):
        check_settings(rows)


@pytest.mark.parametrize("field", ["line_items", "window_width", "orders"])
def test_a_pair_that_differs_in_its_workload_or_window_is_refused(field):
    rows = [row(s, r, seed) for s, r, seed in GRID]
    rows[2][field] = rows[2][field] + 1

    with pytest.raises(PairingError, match=field):
        paired_differences(rows, "nearest_astar", ["order_makespan_s"])


# -- end to end: overwrite, resume, force, refusal -----------------------


def experiment(tmp_path, *extra):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "run_experiment.py"),
            "--out", str(tmp_path),
            "--name", "e1",
            "--strategies", "nearest_astar", "random",
            "--robots", "4",
            "--orders", "3",
            "--jobs", "4",
            *extra,
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=600,
    )


def compare(tmp_path, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "compare_paired.py"), "--out", str(tmp_path), "--name", "e1", *extra],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def summary_rows(base):
    with open(base / "summary.csv") as handle:
        return list(csv.DictReader(handle))


def test_overwrite_resume_and_force(tmp_path):
    base = tmp_path / "e1"
    seeds = ["--seeds", "1", "2"]

    first = experiment(tmp_path, *seeds)
    assert first.returncode == 0, first.stdout + first.stderr

    record = json.loads((base / "experiment.json").read_text())
    assert record["runs"] == [
        ["nearest_astar", 4, 1], ["nearest_astar", 4, 2], ["random", 4, 1], ["random", 4, 2]
    ]
    assert record["git"]["commit"]
    assert {r["settings_sha256"] for r in summary_rows(base)} and len(
        {r["settings_sha256"] for r in summary_rows(base)}
    ) == 1

    # The same command again does not touch the experiment.
    before = (base / "nearest_astar" / "r4_s1" / "metrics.json").read_bytes()
    again = experiment(tmp_path, *seeds)
    assert again.returncode == 2
    assert "already holds an experiment" in again.stdout
    assert (base / "nearest_astar" / "r4_s1" / "metrics.json").read_bytes() == before

    # --resume runs only what is missing.
    (base / "random" / "r4_s2" / "metrics.json").unlink()
    resumed = experiment(tmp_path, *seeds, "--resume")
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert "3 already done, 1 to run" in resumed.stdout
    assert (base / "nearest_astar" / "r4_s1" / "metrics.json").read_bytes() == before
    assert len(summary_rows(base)) == 4
    assert len(json.loads((base / "experiment.json").read_text())["resumed"]) == 1

    # --resume with another grid is refused.
    other = experiment(tmp_path, "--seeds", "1", "2", "3", "--resume")
    assert other.returncode == 2
    assert "seeds changed" in other.stdout

    # --force starts again and keeps the old experiment.
    forced = experiment(tmp_path, *seeds, "--force")
    assert forced.returncode == 0, forced.stdout + forced.stderr
    kept = [p for p in tmp_path.iterdir() if p.name.startswith("e1.replaced-")]
    assert len(kept) == 1
    assert (kept[0] / "nearest_astar" / "r4_s1" / "metrics.json").read_bytes() == before


def test_compare_refuses_an_incomplete_or_mixed_experiment(tmp_path):
    base = tmp_path / "e1"
    run = experiment(tmp_path, "--seeds", "1", "2")
    assert run.returncode == 0, run.stdout + run.stderr

    ok = compare(tmp_path)
    assert ok.returncode == 0, ok.stdout + ok.stderr

    rows = summary_rows(base)
    fields = list(rows[0])

    def write(selected):
        with open(base / "summary.csv", "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(selected)

    # One run stalled: no metrics, so it is missing from the summary.
    stalled = rows[-1]
    stalled_dir = base / stalled["strategy"] / f"r{stalled['robots']}_s{stalled['seed']}"
    (stalled_dir / "metrics.json").unlink()
    (stalled_dir / "run.log").write_text("STALLED:\nDEADLOCK DIAGNOSTICS\n")
    write(rows[:-1])

    refused = compare(tmp_path)
    assert refused.returncode == 1
    assert "incomplete experiment" in refused.stdout
    assert not (base / "paired.md").read_text().startswith("# INCOMPLETE")

    allowed = compare(tmp_path, "--allow-incomplete")
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert "INCOMPLETE" in allowed.stdout

    report = (base / "paired.md").read_text()
    assert report.startswith("# INCOMPLETE EXPERIMENT")
    assert "3 of 4 expected runs present" in report
    assert (
        f"| {stalled['strategy']} | {stalled['robots']} | {stalled['seed']} | "
        "stalled (traffic deadlock); see run.log |"
    ) in report
    # random lost seed 2, so its comparison with nearest_astar has n = 1.
    assert "| random | 4 | 2 | 1 | 2 |" in report

    with open(base / "paired.csv") as handle:
        records = list(csv.DictReader(handle))
    assert {(r["n"], r["expected_n"], r["complete"]) for r in records} == {("1", "2", "False")}

    # One run from different settings.
    mixed = [dict(r) for r in rows]
    mixed[0]["settings_sha256"] = "0" * 16
    write(mixed)
    refused = compare(tmp_path)
    assert refused.returncode == 1
    assert "differ in map or simulator settings" in refused.stdout
