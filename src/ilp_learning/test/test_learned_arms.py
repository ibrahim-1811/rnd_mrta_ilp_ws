"""Learned rule sets as arms of a paired experiment (run_experiment.py --rules).

Groups B-D are evaluated in closed loop next to the conventional strategies, on the
same workloads. Each rule set must be its own arm (directory, identity, summary key),
run under its label source's matcher, never be evaluated on a seed it was trained on,
and report how many of its picks a rule actually made.
"""

import csv
import json
import os
import subprocess
import sys

import pytest

from ilp_learning.rule_artifact import write_rule_artifact
from simulation_env.seed_split import SeedLeakageError

from .conftest import R_AND_O, REPO, TAUTOLOGY, artifact_content

SCRIPTS = REPO / "src/simulation_env/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_experiment  # noqa: E402


def artifact(path, clauses, teacher="nearest_astar", seeds=()):
    content = artifact_content(clauses) | {
        "teacher": teacher,
        "source_runs": [{"run_id": f"r{s}", "seed": s, "workload_seed": s} for s in seeds],
    }
    write_rule_artifact(path, content)
    return path


# -- parsing, matcher and seed guard -------------------------------------------


def test_each_label_is_its_own_arm_with_its_teachers_matcher(tmp_path):
    arms = run_experiment.learned_arms([
        f"near={artifact(tmp_path / 'a.json', [R_AND_O], seeds=(104, 105))}",
        f"hung={artifact(tmp_path / 'b.json', [R_AND_O], teacher='hungarian')}",
        f"pool={artifact(tmp_path / 'c.json', [R_AND_O], teacher='pooled:auction+milp')}",
    ])

    assert list(arms) == ["ilp_near", "ilp_hung", "ilp_pool"]
    assert arms["ilp_near"]["matching"] == "iterative"
    assert arms["ilp_hung"]["matching"] == "cardinality"
    assert arms["ilp_pool"]["matching"] == "iterative"
    assert arms["ilp_near"]["training_seeds"] == [104, 105]
    assert arms["ilp_pool"]["label_source"] == "pooled:auction+milp"


@pytest.mark.parametrize("entry", ["near", "=x.json", "Near=x.json", "near="])
def test_a_malformed_entry_is_refused(entry):
    with pytest.raises(ValueError, match="LABEL=ARTIFACT"):
        run_experiment.learned_arms([entry])


def test_a_duplicate_label_or_unknown_source_is_refused(tmp_path):
    a = artifact(tmp_path / "a.json", [R_AND_O])
    with pytest.raises(ValueError, match="twice"):
        run_experiment.learned_arms([f"x={a}", f"x={a}"])

    odd = artifact(tmp_path / "odd.json", [R_AND_O], teacher="hand-written")
    with pytest.raises(ValueError, match="unknown label source"):
        run_experiment.learned_arms([f"x={odd}"])

    with pytest.raises(ValueError, match="cannot read"):
        run_experiment.learned_arms([f"x={tmp_path / 'missing.json'}"])


def test_a_rule_set_is_never_evaluated_on_its_training_seeds(tmp_path):
    arms = run_experiment.learned_arms([f"x={artifact(tmp_path / 'a.json', [R_AND_O], seeds=(104,))}"])

    run_experiment.check_rule_seeds(arms, [111, 112])
    with pytest.raises(SeedLeakageError, match=r"ilp_x: trained on seed\(s\) \[104\]"):
        run_experiment.check_rule_seeds(arms, [104, 111])


def test_the_command_runs_the_ilp_strategy_with_artifact_and_matcher(tmp_path):
    arms = run_experiment.learned_arms([f"h={artifact(tmp_path / 'a.json', [R_AND_O], teacher='milp')}"])
    cmd = run_experiment.workload_command(tmp_path, "ilp_h", 4, 1, 3, False, rule=arms["ilp_h"])

    assert cmd[cmd.index("--strategy") + 1] == "ilp"
    assert cmd[cmd.index("--rule-artifact") + 1] == arms["ilp_h"]["artifact"]
    assert cmd[cmd.index("--rule-matching") + 1] == "cardinality"

    plain = run_experiment.workload_command(tmp_path, "auction", 4, 1, 3, False)
    assert plain[plain.index("--strategy") + 1] == "auction"
    assert "--rule-artifact" not in plain


# -- end to end: real headless runs, paired with a teacher ---------------------


def experiment(tmp_path, name, *extra):
    env = {k: v for k, v in os.environ.items() if k != "AMENT_PREFIX_PATH"}
    return subprocess.run(
        [
            sys.executable, str(SCRIPTS / "run_experiment.py"),
            "--out", str(tmp_path), "--name", name, "--strategies", "nearest_astar",
            "--robots", "4", "--orders", "6", "--seeds", "1", "2", "--quantum", "3",
            "--jobs", "3", *extra,
        ],
        capture_output=True, text=True, cwd=REPO, env=env, timeout=600,
    )


def test_rule_arms_run_paired_with_the_teacher_and_report_fallback(tmp_path):
    rules = artifact(tmp_path / "ro.json", [R_AND_O], seeds=(104,))
    everything = artifact(tmp_path / "all.json", [TAUTOLOGY])
    empty = artifact(tmp_path / "empty.json", [])

    done = experiment(
        tmp_path, "arms", "--rules", f"ro={rules}", f"all={everything}", f"empty={empty}",
    )
    assert done.returncode == 0, done.stdout + done.stderr

    base = tmp_path / "arms"
    with open(base / "summary.csv") as handle:
        rows = list(csv.DictReader(handle))

    by_arm = {}
    for row in rows:
        by_arm.setdefault(row["strategy"], []).append(row)

    assert sorted(by_arm) == ["ilp_all", "ilp_empty", "ilp_ro", "nearest_astar"]
    assert all(len(runs) == 2 for runs in by_arm.values())
    assert len({runs[0]["strategy_sha256"] for runs in by_arm.values()}) == 4
    assert len({row["settings_sha256"] for row in rows}) == 1

    # The teacher has no rule trace; every learned run says who made its picks.
    assert all(row["fallback_rate"] == "" for row in by_arm["nearest_astar"])
    for row in by_arm["ilp_all"]:
        assert int(row["fallback_picks"]) == 0 and int(row["rule_picks"]) > 0
    for row in by_arm["ilp_empty"]:
        assert int(row["rule_picks"]) == 0 and float(row["fallback_rate"]) == 1.0

    for run_dir in base.glob("ilp_*/r4_s*"):
        manifest = json.loads((run_dir / "run.json").read_text())
        assert manifest["strategy"] == "ilp"
        assert (run_dir / "rule_trace.parquet").exists()

    spec = json.loads((base / "experiment.json").read_text())["spec"]
    assert spec["rules"]["ilp_ro"]["training_seeds"] == [104]
    assert set(spec["strategy_sha256"]) == set(by_arm)

    paired = subprocess.run(
        [sys.executable, str(SCRIPTS / "compare_paired.py"), "--out", str(tmp_path), "--name", "arms"],
        capture_output=True, text=True, cwd=REPO, timeout=120,
    )
    assert paired.returncode == 0, paired.stdout + paired.stderr
    with open(base / "paired.csv") as handle:
        compared = {row["strategy"] for row in csv.DictReader(handle)}
    assert compared == {"ilp_all", "ilp_empty", "ilp_ro"}

    # A changed rule set is another arm identity: --resume refuses it.
    artifact(tmp_path / "ro.json", [TAUTOLOGY], seeds=(104,))
    again = experiment(
        tmp_path, "arms", "--rules", f"ro={rules}", f"all={everything}", f"empty={empty}", "--resume",
    )
    assert again.returncode == 2 and "strategy_sha256 changed" in again.stdout


@pytest.mark.parametrize(
    "extra, message",
    [
        (["--rules", "x=SEED1"], "trained on seed(s) [1]"),
        (["--strategies", "ilp"], "runs through --rules"),
    ],
)
def test_leakage_and_a_bare_ilp_strategy_are_refused_before_anything_runs(tmp_path, extra, message):
    leaky = artifact(tmp_path / "leaky.json", [R_AND_O], seeds=(1,))
    extra = [str(leaky) if e == "SEED1" else e.replace("SEED1", str(leaky)) for e in extra]

    refused = experiment(tmp_path, "bad", *extra)

    assert refused.returncode == 2
    assert message in refused.stderr
    assert not (tmp_path / "bad").exists()
