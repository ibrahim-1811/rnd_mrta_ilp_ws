"""Shared fixtures: rule artifacts written by hand, and short real recorded runs."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ilp_learning.rule_artifact import SCHEMA_VERSION, write_rule_artifact
from ilp_learning.rule_compiler import parse_clause

REPO = Path(__file__).resolve().parents[3]
ACCEPTANCE_RUN = REPO / "runs/m2_acceptance/r30_q3_s104"
RUN_WORKLOAD = REPO / "src/simulation_env/scripts/run_workload.py"

R_AND_O = "assign(A,B,C):- nearest_static(A,B,C),best_cost_for_order(A,B,C)."
TAUTOLOGY = "assign(A,B,C):- candidate_pair(A,B,C)."

# A development seed: not in any reserved role (simulation_env.seed_split).
DEVELOPMENT_SEED = "42"


def artifact_content(clauses):
    return {
        "schema_version": SCHEMA_VERSION,
        "teacher": "nearest_astar",
        "arm": "A",
        "raw_clauses": list(clauses),
        "clauses": [
            {"rank": rank, "clause": parse_clause(text).text, "metrics": {}}
            for rank, text in enumerate(clauses)
        ],
        "pruned_clauses": [],
        "k_max": 5,
        "thresholds_sha256": "t",
        "bias_sha256": "b",
        "source_runs": [],
        "git": {},
        "popper": {},
        "solver_settings": {},
        "sampling": {},
    }


@pytest.fixture
def make_artifact(tmp_path):
    def make(clauses, name="rule_artifact.json"):
        path = tmp_path / name
        write_rule_artifact(path, artifact_content(clauses))
        return path

    return make


def run_workload(out, *options):
    """One short real headless run through run_workload.py."""
    env = {k: v for k, v in os.environ.items() if k != "AMENT_PREFIX_PATH"}
    env["PYTHONPATH"] = os.pathsep.join(
        str(REPO / "src" / package)
        for package in ("mrta_core", "simulation_env", "task_management", "ilp_learning")
    )
    return subprocess.run(
        [
            sys.executable, str(RUN_WORKLOAD), "--config",
            str(REPO / "src/simulation_env/config/warehouse.yaml"),
            "--robots", "6", "--orders", "30", "--quantum", "3", "--seed", DEVELOPMENT_SEED,
            "--quiet", "--progress", "0", "--dataset", str(out), *options,
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )


@pytest.fixture(scope="module")
def teacher_run(tmp_path_factory):
    """A short nearest_astar run: real recorded decisions, built by the engine."""
    out = tmp_path_factory.mktemp("teacher") / "run"
    done = run_workload(out, "--strategy", "nearest_astar")
    assert done.returncode == 0, done.stdout + done.stderr
    return out


def read_manifest(run_dir):
    return json.loads((Path(run_dir) / "run.json").read_text())
