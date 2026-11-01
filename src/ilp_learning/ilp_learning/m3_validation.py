"""The single M3 validation campaign, and its gate report.

    pixi run m3-validation

Runs teachers and pre-registered arms on seeds 111-115, scores G1-G9 and G11-G12
(.scratch/m3-popper-learner/preregistration.md), and writes gates.json and gates.md.
It refuses to start unless the tree is clean at tag m3-evidence-1 and the induction
evidence is complete and from that same commit, so no seed is spent on a campaign
that cannot finish. It writes only to runs/m3_validation, and a campaign from
another commit is refused. A failed run is recorded and scored as failed; it
cannot stop the report or count as a pass. --dry-run does the same work on two
induction seeds, in another folder, with no tag.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import itertools
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
from typing import Sequence

import pandas as pd

from ilp_learning import m3_induction as m3
from ilp_learning.bk_builder import derive_est_finish_s
from ilp_learning.fidelity import (
    evaluate_runs,
    key_agreement,
    load_validation_run,
    random_key,
)
from ilp_learning.rule_artifact import load_rule_artifact, write_rule_artifact
from ilp_learning.rule_compiler import RuleProgram, parse_clause
from ilp_learning.train_rules import load_induction_run
from simulation_env.seed_split import VALIDATION


TAG = "m3-evidence-1"
RANDOM_REPEATS = 20
G1_LINE = {"q3": 0.90, "q0": 0.98}
G2_MARGIN = 0.05
G2_FLEETS = (70, 100)
G3_LINE = 0.98
G5_FLOOR_CONDITION = "q0/30"  # amendment item 4
G4_LINE = 0.02
TEACHERS = ("nearest_astar", "auction")
TARGET_R, TARGET_O = "nearest_static", m3.TARGET_O
THREE_ARGUMENT = (
    TARGET_R, TARGET_O, "second_nearest_static", "earliest_static_finish", "second_earliest_static_finish",
)
HAND_RULES = {
    "empty": (),
    "r_only": (f"assign(A,B,C):- {TARGET_R}(A,B,C).",),
    "o_only": (f"assign(A,B,C):- {TARGET_O}(A,B,C).",),
    "r_and_o": (m3.HAND_WRITTEN_R_AND_O,),
}
ARMS = {  # arm -> (rule, matching)
    "learned": ("learned", "iterative"),
    "empty": ("empty", "iterative"),
    "r_only": ("r_only", "iterative"),
    "o_only": ("o_only", "iterative"),
    "r_and_o": ("r_and_o", "iterative"),
    "mcard_r_and_o": ("r_and_o", "cardinality"),
    "mgreedy_r_and_o": ("r_and_o", "greedy"),
}
REPO = m3.REPO
DEFAULT_OUT = "runs/m3_validation"
DONE = "finished.ok"
ALLOWLIST_TEST = "src/ilp_learning/test/test_bk_builder_firewall.py"


@dataclass(frozen=True)
class Campaign:
    induction: Path
    out: Path
    seeds: tuple[int, ...]
    purpose: str
    jobs: int

    @property
    def load(self):
        return load_validation_run if self.purpose == "validation" else load_induction_run

    def teacher_run(self, q: str, robots: int, seed: int, teacher: str = m3.TEACHER) -> Path:
        return self.out / q / teacher / f"r{robots}_s{seed}"

    def arm_run(self, arm: str, q: str, robots: int, seed: int) -> Path:
        return self.out / "arms" / arm / q / f"r{robots}_s{seed}"

    def rule(self, name: str, q: str, robots: int) -> Path:
        if name == "learned":
            return m3.rule_dir(self.induction, q, robots, m3.FULL) / "rule_artifact.json"
        return self.out / "hand" / name / "rule_artifact.json"


def git_state() -> tuple[str, bool, list[str]]:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True).stdout.strip()

    return git("rev-parse", "HEAD"), bool(git("status", "--porcelain")), git("tag", "--points-at", "HEAD").split()


def check_induction(induction: Path, commit: str) -> None:
    """Every input of the campaign exists, is clean, and is from this commit."""
    summary_path = induction / "summary.json"
    if not summary_path.exists():
        raise SystemExit(f"refused: {summary_path} does not exist")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    missing = [key for key in ("rules", "frozen_key", "g5", "g8", "g10", "battery_probe") if key not in summary]
    if missing:
        raise SystemExit(f"refused: the induction summary lacks {missing}")

    for q, robots in m3.conditions():
        for fold in (m3.FULL, *(f"sample_{s}" for s in m3.SAMPLING_SEEDS[1:]), "relabel_finish"):
            path = m3.rule_dir(induction, q, robots, fold) / "rule_artifact.json"
            if not path.exists():
                raise SystemExit(f"refused: {path} does not exist")
            state = load_rule_artifact(path)[1]["git"]
            if state.get("commit") != commit or state.get("dirty"):
                raise SystemExit(f"refused: {path} is not from clean commit {commit[:12]}")


def check_may_start(out: Path, induction: Path) -> None:
    """A clean tree at the tag, sound inputs, and one campaign."""
    commit, dirty, tags = git_state()
    if dirty:
        raise SystemExit("refused: the tree is not clean")
    if TAG not in tags:
        raise SystemExit(f"refused: HEAD is not tagged {TAG}")
    if out != (REPO / DEFAULT_OUT).resolve():
        raise SystemExit(f"refused: the campaign writes only to {DEFAULT_OUT}")
    check_induction(induction, commit)
    started = out / "started.json"
    if started.exists() and json.loads(started.read_text())["commit"] != commit:
        raise SystemExit("refused: a campaign already ran from another commit")
    out.mkdir(parents=True, exist_ok=True)
    started.write_text(json.dumps({"commit": commit, "tag": TAG}) + "\n", encoding="utf-8")
    with (out / "attempts.log").open("a", encoding="utf-8") as log:
        log.write(f"{datetime.now(timezone.utc).isoformat()} {commit}\n")


def write_hand_rules(out: Path) -> None:
    for name, clauses in HAND_RULES.items():
        write_rule_artifact(
            out / "hand" / name / "rule_artifact.json",
            {
                "schema_version": "mrta-rule-artifact/1", "teacher": "hand-written", "arm": "baseline",
                "raw_clauses": list(clauses),
                "clauses": [
                    {"rank": rank, "clause": parse_clause(text).text, "metrics": {}}
                    for rank, text in enumerate(clauses)
                ],
                "pruned_clauses": [], "k_max": len(clauses), "thresholds_sha256": "",
                "bias_sha256": "", "source_runs": [], "git": {}, "popper": {},
                "solver_settings": {}, "sampling": {},
            },
        )


def run_teachers(c: Campaign) -> list[str]:
    """Both quanta are attempted; a failure is returned, never raised."""
    failed = []
    for q, quantum in m3.QUANTA.items():
        command = [
            sys.executable, "src/simulation_env/scripts/run_experiment.py",
            "--name", q, "--out", str(c.out), "--strategies", *TEACHERS,
            "--robots", *map(str, m3.FLEETS), "--seeds", *map(str, c.seeds),
            "--orders", str(m3.ORDERS), "--workloads", "seeded",
            "--workload-orders", str(m3.ORDERS), "--quantum", str(quantum),
            "--window", m3.WINDOW, "--purpose", c.purpose, "--jobs", str(c.jobs), "--resume",
        ]
        try:
            m3._call(command, c.out / "logs" / f"teachers_{q}.log")
        except RuntimeError as error:
            failed.append(f"{q}: {error}")
    return failed


def run_arm(c: Campaign, arm: str, q: str, robots: int, seed: int) -> None:
    out = c.arm_run(arm, q, robots, seed)
    if (out / DONE).exists():
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    rule, matching = ARMS[arm]
    m3._call(
        [
            sys.executable, "src/simulation_env/scripts/run_workload.py",
            "--strategy", "ilp", "--rule-artifact", str(c.rule(rule, q, robots)),
            "--rule-matching", matching, "--robots", str(robots), "--seed", str(seed),
            "--orders", str(m3.ORDERS), "--quiet", "--progress", "0", "--dataset", str(out),
            "--quantum", str(m3.QUANTA[q]), "--window", m3.WINDOW, "--purpose", c.purpose,
            "--orders-file", str(c.out / q / "workloads" / f"orders_s{seed}.csv"),
        ],
        c.out / "logs" / f"{arm}_{q}_r{robots}_s{seed}.log",
    )
    (out / DONE).write_text("ok\n", encoding="utf-8")


def run_arms(c: Campaign) -> list[str]:
    """Every arm is attempted; a failure is returned, never raised."""
    with ThreadPoolExecutor(c.jobs) as pool:
        work = {
            (arm, q, robots, seed): pool.submit(run_arm, c, arm, q, robots, seed)
            for arm in ARMS for q, robots in m3.conditions() for seed in c.seeds
        }
    failed = []
    for job, future in work.items():
        if future.exception() is not None:
            failed.append("/".join(map(str, job)))
    return failed


def arm_finished(c: Campaign, arm: str, q: str, robots: int, seed: int) -> bool:
    return (c.arm_run(arm, q, robots, seed) / DONE).exists()


def distractor_programs() -> dict[str, RuleProgram]:
    """Every single literal, and every pair but the target R and O."""
    programs = {
        name: RuleProgram((parse_clause(f"assign(A,B,C):- {name}(A,B,C)."),))
        for name in THREE_ARGUMENT
    }
    for first, second in itertools.combinations(THREE_ARGUMENT, 2):
        if {first, second} != {TARGET_R, TARGET_O}:
            clause = f"assign(A,B,C):- {first}(A,B,C),{second}(A,B,C)."
            programs[f"{first}+{second}"] = RuleProgram((parse_clause(clause),))
    return programs


def _frames(c: Campaign, q: str, robots: int) -> list[pd.DataFrame]:
    frames = []
    for seed in c.seeds:
        run = c.load(c.teacher_run(q, robots, seed))
        frames.append(run.decisions.assign(est_finish_s=derive_est_finish_s(run.decisions, run.manifest)))
    return frames


def _key_rate(frames: Sequence[pd.DataFrame], program: RuleProgram, keys=()) -> list[float]:
    results = [key_agreement(frame, program, keys) for frame in frames]
    total = sum(r["teacher_pairs"] for r in results)
    return [sum(r["agreed"][i] for r in results) / total for i in range(len(results[0]["agreed"]))]


def _score_condition(job: tuple[Campaign, str, int]) -> tuple[str, dict]:
    c, q, robots = job
    program, _ = load_rule_artifact(c.rule("learned", q, robots))
    runs = [str(c.teacher_run(q, robots, seed)) for seed in c.seeds]
    frames = _frames(c, q, robots)
    keys = [random_key(i) for i in range(RANDOM_REPEATS)]
    learned_random = _key_rate(frames, program, keys)

    scored = {
        "learned": evaluate_runs(runs, program, load=c.load),
        "teacher_key": _key_rate(frames, program)[0],
        "random_key": statistics.mean(learned_random),
        "random_key_range": [min(learned_random), max(learned_random)],
        "samples": {},
    }
    for seed in m3.SAMPLING_SEEDS[1:]:
        fold = f"sample_{seed}"
        sampled, _ = load_rule_artifact(m3.rule_dir(c.induction, q, robots, fold) / "rule_artifact.json")
        scored["samples"][fold] = evaluate_runs(runs, sampled, load=c.load)["pooled"]["scopes"][
            "choice_bearing"
        ]["rates"]
    if robots in G2_FLEETS:
        scored["distractors"] = {
            name: statistics.mean(_key_rate(frames, candidate, keys))
            for name, candidate in distractor_programs().items()
        }
    return f"{q}/{robots}", scored


def _metrics(run: Path) -> dict:
    return json.loads((run / "metrics.json").read_text(encoding="utf-8"))


def equivalence(ratios: Sequence[float], margin: float) -> dict:
    """G8: the 90% CI of the mean paired log ratio against +-margin."""
    mean = statistics.mean(ratios)
    half = m3.T_90_4DF_OVER_ROOT_5 * statistics.stdev(ratios)
    if all(r == 0 for r in ratios):
        verdict = "identical"
    elif mean - half >= -margin and mean + half <= margin:
        verdict = "equivalent"
    elif mean - half > margin or mean + half < -margin:
        verdict = "non-equivalent"
    else:
        verdict = "inconclusive"
    return {"mean": mean, "low": mean - half, "high": mean + half, "margin": margin, "verdict": verdict}


def overlapping_missions(outcomes: pd.DataFrame) -> int:
    """Missions a robot started before finishing its previous one."""
    ordered = outcomes.sort_values(["robot_id", "launched_at"])
    previous = ordered.groupby("robot_id")["completed_at"].shift()
    return int((ordered["launched_at"] < previous).sum())


def g7_run(run: Path) -> dict:
    metrics = _metrics(run)
    decisions = pd.read_parquet(run / "decisions.parquet")
    outcomes = pd.read_parquet(run / "outcomes.parquet")
    chosen = decisions[decisions["selected"]]
    return {
        "completed": metrics["orders_completed"] == m3.ORDERS,
        "ineligible_assignments": int((~chosen["eligible"]).sum()),
        "reservation_violations": metrics["reservation_violations"],
        "double_assignments": int(chosen["order_id"].duplicated().sum())
        + int(outcomes["order_id"].duplicated().sum())
        + int(chosen.duplicated(["decision_id", "robot_id"]).sum())
        + overlapping_missions(outcomes),
    }


def condition_gates(c: Campaign, q: str, robots: int, scored: dict, summary: dict) -> dict:
    """Raises if a learned run did not finish: that condition is not scored."""
    unfinished = [seed for seed in c.seeds if not arm_finished(c, "learned", q, robots, seed)]
    if unfinished:
        raise RuntimeError(f"learned runs did not finish on seeds {unfinished}")
    condition = f"{q}/{robots}"
    rates = scored["learned"]["pooled"]["scopes"]["choice_bearing"]["rates"]
    pairs = scored["learned"]["pooled"]["scopes"]["choice_bearing"]["counts"]["teacher_pairs"]
    frozen = rates["frozen_key"]
    gates: dict = {
        "G1": {
            "frozen_key": frozen, "line": G1_LINE[q], "distinct_cost": rates["distinct_cost"],
            "tie_neutral": rates["tie_neutral"], "exact_decision": rates["exact_decision"],
            "pass": frozen >= G1_LINE[q] and rates["distinct_cost"] == 1.0,
        },
        "G1b": {
            "clauses": scored["learned"]["pooled"]["static"],
            "clause_picks": scored["learned"]["pooled"]["clause_picks"],
        },
        "G3": {"rule_coverage": rates["rule_coverage"], "pass": rates["rule_coverage"] >= G3_LINE},
        "G4": {"fallback": rates["fallback"], "pass": rates["fallback"] <= G4_LINE},
        "keys": {
            "random_key": scored["random_key"], "random_key_range": scored["random_key_range"],
            "teacher_key": scored["teacher_key"],
        },
    }

    if "distractors" in scored:
        best = max(scored["distractors"], key=scored["distractors"].get)
        lift = scored["random_key"] - scored["distractors"][best]
        gates["G2"] = {"best_distractor": best, "lift": lift, "pass": lift >= G2_MARGIN}

    induction = summary["frozen_key"][f"{condition}/{m3.FULL}"]
    drop = induction - frozen
    limit = summary["g5"][condition]["limit"]
    floor = 1 / pairs if condition == G5_FLOOR_CONDITION else 0.0
    best_single = max(
        (value for name, value in scored.get("distractors", {}).items() if "+" not in name),
        default=None,
    )
    static = scored["learned"]["pooled"]["static"]["program"]
    gates["shares"] = {
        "tie_share": rates["tie_neutral"] - frozen,
        "unexplained_positive_share": 1 - static["recall"],
        "lift_over_best_single_literal": None if best_single is None else scored["random_key"] - best_single,
    }
    gates["G5"] = {
        "drop": drop, "limit": limit, "pass_original": drop <= limit,
        "limit_amended": max(limit, floor), "pass": drop <= max(limit, floor),
    }

    texts = [summary["rules"][f"{condition}/{m3.FULL}"]]
    texts += [summary["rules"][f"{condition}/sample_{s}"] for s in m3.SAMPLING_SEEDS[1:]]
    matches = max(sum(text == other for other in texts) for text in texts)
    sampled = [
        (frozen, rates["distinct_cost"]),
        *((s["frozen_key"], s["distinct_cost"]) for s in scored["samples"].values()),
    ]
    gates["G6"] = {
        "same_text": matches, "of": len(texts), "pass": matches >= 2,
        "g1_met_on": sum(key >= G1_LINE[q] and cost == 1.0 for key, cost in sampled),
        "g1_of": len(sampled),
        "junk_clauses": [
            rank for rank in range(len(texts[0])) if scored["learned"]["pooled"]["clause_picks"].get(str(rank), 0) == 0
        ],
    }

    runs = [c.arm_run("learned", q, robots, seed) for seed in c.seeds]
    teachers = [c.teacher_run(q, robots, seed) for seed in c.seeds]
    checks = [g7_run(run) for run in runs]
    gates["G7"] = {
        "runs": checks,
        "pass": all(
            check["completed"]
            and not (check["ineligible_assignments"] or check["reservation_violations"] or check["double_assignments"])
            for check in checks
        ),
    }

    gates["G8"] = {}
    for metric in m3.METRICS:
        ratios = [
            math.log(_metrics(run)[metric] / _metrics(teacher)[metric])
            for run, teacher in zip(runs, teachers)
        ]
        margin = summary["g8"][f"{condition}/{metric}"]["margin"]
        gates["G8"][metric] = equivalence(ratios, margin)

    worst = [_metrics(run)["allocator_decision_worst_s"] for run in runs]
    median = [_metrics(run)["allocator_decision_median_s"] for run in runs]
    teacher_worst = max(_metrics(run)["allocator_decision_worst_s"] for run in teachers)
    teacher_median = statistics.median(_metrics(run)["allocator_decision_median_s"] for run in teachers)
    gates["G9"] = {
        "worst_s": max(worst), "median_s": statistics.median(median),
        "teacher_worst_s": teacher_worst, "teacher_median_s": teacher_median,
        "pass": max(worst) <= max(1.0, 1.5 * teacher_worst)
        and statistics.median(median) <= 5 * teacher_median,
    }

    gates["baselines"] = {
        arm: {
            metric: statistics.mean(_metrics(c.arm_run(arm, q, robots, seed))[metric] for seed in c.seeds)
            for metric in m3.METRICS
        }
        if all(arm_finished(c, arm, q, robots, seed) for seed in c.seeds)
        else {"failed": True}
        for arm in ARMS
    }
    gates["baselines"].update({
        teacher: {
            metric: statistics.mean(_metrics(c.teacher_run(q, robots, seed, teacher))[metric] for seed in c.seeds)
            for metric in m3.METRICS
        }
        for teacher in TEACHERS
    })
    return gates


def g11(c: Campaign) -> dict:
    """Integrity: same BK across teachers, rule hash in every learned run's identity."""
    same_bk = all(
        (m3.rule_dir(c.induction, q, r, m3.FULL) / "kb" / "bk.pl").read_bytes()
        == (m3.rule_dir(c.induction, q, r, "relabel_finish") / "kb" / "bk.pl").read_bytes()
        for q, r in m3.conditions()
    )
    identities = True
    for q, robots in m3.conditions():
        _, artifact = load_rule_artifact(c.rule("learned", q, robots))
        file_hash = sha256(c.rule("learned", q, robots).read_bytes()).hexdigest()
        for seed in c.seeds:
            path = c.arm_run("learned", q, robots, seed) / "run.json"
            if not path.exists():
                identities = False
                continue
            identity = json.dumps(json.loads(path.read_text())["strategy_identity"])
            identities = identities and (file_hash in identity or artifact["content_sha256"] in identity)
    commit, dirty, tags = git_state()
    allowlist = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", ALLOWLIST_TEST],
        cwd=REPO, capture_output=True,
    ).returncode == 0
    return {
        "commit": commit, "clean": not dirty, "tagged": TAG in tags,
        "bk_identical_across_teachers": same_bk, "rule_hash_in_identity": identities,
        "allowlist_test": allowlist,
        "pass": not dirty and TAG in tags and same_bk and identities and allowlist,
    }


def g12(c: Campaign) -> dict:
    out = {}
    for q, robots in m3.conditions():
        _, artifact = load_rule_artifact(c.rule("learned", q, robots))
        out[f"{q}/{robots}"] = {**artifact["sampling"], "timed_out": artifact["popper"]["timed_out"]}
    return out


def stop_rule(gates: dict) -> dict:
    """Amendment item 3: the primary rule reads quantum 3 only."""
    primary = {k: v for k, v in gates["conditions"].items() if k.startswith("q3/")}
    arm_c_question = [
        condition for condition, row in primary.items()
        if "error" in row or row["G6"]["g1_met_on"] < 2 or not row["G6"]["pass"]
    ]
    limit = [
        condition for condition, row in primary.items()
        if "error" in row or not row["G7"]["pass"]
    ]
    return {
        "ask_about_arm_c": arm_c_question,
        "g7_failures": limit,
        "g11_failed": not gates["G11"]["pass"],
        "stop": bool(arm_c_question or limit or not gates["G11"]["pass"]),
    }


def report(gates: dict) -> str:
    lines = [f"# M3 gates, commit {gates['G11']['commit'][:12]}", ""]
    stop = gates["stop_rule"]
    lines += [f"Primary stop rule (quantum 3): {'STOP' if stop['stop'] else 'not triggered'}. {stop}", ""]
    if gates["failed_arms"]:
        lines += [f"Failed arm runs: {gates['failed_arms']}", ""]
    names = ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G9")
    lines += ["| Condition | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for condition, row in gates["conditions"].items():
        if "error" in row:
            lines.append(f"| {condition} | NOT SCORED: {row['error']} |")
            continue
        cells = ["n/a" if name not in row else ("pass" if row[name]["pass"] else "FAIL") for name in names]
        lines.append(f"| {condition} | " + " | ".join(cells) + " |")
    lines += ["", "| Condition | Metric | Mean log ratio | 90% CI | Margin | Verdict |", "|---|---|---:|---|---:|---|"]
    for condition, row in gates["conditions"].items():
        for metric, test in row.get("G8", {}).items():
            lines.append(
                f"| {condition} | {metric} | {test['mean']:+.4f} | "
                f"[{test['low']:+.4f}, {test['high']:+.4f}] | {test['margin']:.4f} | {test['verdict']} |"
            )
    lines += ["", "| Condition | Frozen key | Tie-neutral | Distinct-cost | Exact | G5 drop | Limit | Original G5 | Random-key | Teacher-key | Tie share | Unexplained |", "|---|" + "---:|" * 11]
    for condition, row in gates["conditions"].items():
        if "error" in row:
            continue
        g1, g5, keys, shares = row["G1"], row["G5"], row["keys"], row["shares"]
        lines.append(
            f"| {condition} | {g1['frozen_key']:.4f} | {g1['tie_neutral']:.4f} | {g1['distinct_cost']:.4f} | "
            f"{g1['exact_decision']:.4f} | {g5['drop']:+.4f} | {g5['limit_amended']:.4f} | "
            f"{'pass' if g5['pass_original'] else 'FAIL'} | {keys['random_key']:.4f} | {keys['teacher_key']:.4f} | "
            f"{shares['tie_share']:.4f} | {shares['unexplained_positive_share']:.4f} |"
        )
    lines += ["", "| Condition | Relabel differs | Permuted | Removing O drop | Removing O |", "|---|---|---:|---:|---|"]
    for condition, row in gates["G10"].items():
        lines.append(
            f"| {condition} | {row['relabel_differs']} | {row['permuted_fidelity']:.4f} ({row['permuted_clauses']} clauses) | "
            f"{row['without_o_drop']:.4f} | {'pass' if row['without_o_pass'] else 'FAIL'} |"
        )
    lines += ["", f"G11: {'pass' if gates['G11']['pass'] else 'FAIL'}. {gates['G11']}", ""]
    lines += [f"Battery probe: {gates['battery_probe']}", "", f"G12: {gates['G12']}", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--induction", default="runs/m3_evidence_1")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--jobs", type=int, default=m3.MAX_JOBS)
    parser.add_argument("--dry-run", action="store_true", help="two induction seeds, no tag needed")
    args = parser.parse_args()

    induction = (REPO / args.induction).resolve()
    out = (REPO / args.out).resolve()
    if args.dry_run:
        if out == (REPO / DEFAULT_OUT).resolve():
            raise SystemExit(f"refused: a dry run may not write to {DEFAULT_OUT}; give --out")
        c = Campaign(induction, out, m3.SEEDS[:2], "induction", args.jobs)
    else:
        check_may_start(out, induction)
        c = Campaign(induction, out, tuple(VALIDATION), "validation", args.jobs)

    out.mkdir(parents=True, exist_ok=True)
    write_hand_rules(out)
    print("teachers", flush=True)
    failed_teachers = run_teachers(c)

    print("arms", flush=True)
    failed_arms = run_arms(c)

    print("scoring", flush=True)
    summary = json.loads((induction / "summary.json").read_text(encoding="utf-8"))
    scored, conditions = {}, {}
    with ProcessPoolExecutor(c.jobs) as pool:
        work = {(q, r): pool.submit(_score_condition, (c, q, r)) for q, r in m3.conditions()}
    for (q, r), future in work.items():
        condition = f"{q}/{r}"
        try:
            scored[condition] = future.result()[1]
            conditions[condition] = condition_gates(c, q, r, scored[condition], summary)
        except Exception as error:  # a condition that cannot be scored is reported, not lost
            conditions[condition] = {"error": repr(error)}

    gates = {
        "conditions": conditions,
        "failed_arms": failed_teachers + failed_arms,
        "G10": summary["g10"],
        "G11": g11(c),
        "G12": g12(c),
        "battery_probe": summary["battery_probe"],
        "scored": scored,
    }
    gates["stop_rule"] = stop_rule(gates)
    (out / "gates.json").write_text(json.dumps(gates, indent=1) + "\n", encoding="utf-8")
    (out / "gates.md").write_text(report(gates), encoding="utf-8")
    print(report(gates))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
