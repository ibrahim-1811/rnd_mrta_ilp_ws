"""Train a single-teacher rule artifact from recorded induction runs (M3, arm A).

    python -m ilp_learning.train_rules runs/m3_dev/q3/nearest_astar/r30_s10* --out DIR

Arm A only: every teacher positive of the sampled decisions is an example
(.scratch/m3-popper-learner/preregistration.md). Each run must prove from its own
manifest that it is induction data before anything is read from it; seeds are taken
from those manifests, never declared here. Reads run.json and decisions.parquet only,
so nothing realized can reach a rule (M2 spec 3.1). Tier A bias only.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from ilp_learning.atoms import ProvenanceTracker
from ilp_learning.bias_templates import generate_bias_pl
from ilp_learning.bk_builder import BKBuilder, candidate_pairs, derive_est_finish_s
from ilp_learning.popper_runner import (
    DEFAULT_EVAL_TIMEOUT_S,
    DEFAULT_MAX_LITERALS,
    DEFAULT_TIMEOUT_S,
    PopperResult,
    popper_version,
    run_popper,
)
from ilp_learning.rule_artifact import SCHEMA_VERSION, write_rule_artifact
from ilp_learning.rule_compiler import K_MAX, CompiledRules, DecisionExamples, compile_rules
from ilp_learning.sampler import (
    DEFAULT_CORPUS_SEED,
    DEFAULT_DECISION_CAP,
    GroundAtomExample,
    labelled_decisions,
    stratified_decisions,
    write_exs_pl,
)
from ilp_learning.supervision.single_teacher import build_single_teacher_supervision
from ilp_learning.thresholds import (
    DECISION_DISCRETIZATIONS,
    fit_thresholds_from_frames,
    induction_seeds_of,
)
from simulation_env.dataset import git_state
from simulation_env.failures import FAILURE_FILE
from simulation_env.seed_split import check_run_is_induction, workload_seed_of


ARTIFACT_FILE = "rule_artifact.json"
ARM = "A"
TERCILES = 3


class TrainingError(RuntimeError):
    """The inputs or the learner's output cannot give a trustworthy artifact."""


@dataclass(frozen=True)
class InductionRun:
    manifest: Mapping[str, Any]
    decisions: pd.DataFrame

    @property
    def run_id(self) -> str:
        return self.manifest["run_id"]


def load_induction_run(run_dir: Path | str) -> InductionRun:
    """A recorded run, after it has proved it is induction data and finished."""
    path = Path(run_dir)
    manifest = json.loads((path / "run.json").read_text(encoding="utf-8"))
    check_run_is_induction(manifest)
    if (path / FAILURE_FILE).exists():
        raise TrainingError(f"{path} records a failed run")
    return InductionRun(manifest, pd.read_parquet(path / "decisions.parquet"))


def _check_corpus(runs: Sequence[InductionRun], teacher: str) -> None:
    if not runs:
        raise TrainingError("no runs given")
    wrong = sorted({r.manifest["strategy"] for r in runs} - {teacher})
    if wrong:
        raise TrainingError(f"teacher is {teacher}, but runs were driven by {wrong}")
    ids = [r.run_id for r in runs]
    if len(set(ids)) != len(ids):
        raise TrainingError("the same run was given twice")


def _strata(runs: Sequence[InductionRun]) -> dict[tuple, list[tuple[str, int]]]:
    """(fleet_size, quantum, seed, sim_time tercile) -> labelled decisions."""
    strata: dict[tuple, list[tuple[str, int]]] = {}
    for run in runs:
        times = run.decisions.groupby("decision_id")["sim_time"].first()
        horizon = float(times.max())
        for decision_id in labelled_decisions(run.decisions):
            third = int(TERCILES * float(times[decision_id]) / horizon) if horizon > 0 else 0
            key = (
                int(run.manifest["robot_count"]),
                float(run.manifest["dispatch_quantum_s"]),
                int(workload_seed_of(run.manifest["orders_path"])),
                min(third, TERCILES - 1),
            )
            strata.setdefault(key, []).append((run.run_id, decision_id))
    return strata


def _training_decisions(
    frames: Mapping[str, pd.DataFrame],
    examples: Sequence[GroundAtomExample],
    tracker: ProvenanceTracker,
) -> list[DecisionExamples]:
    """Join examples back to raw pairs, so clause metrics use the executed interpreter."""
    labelled: dict[tuple[str, int], tuple[set, set]] = {}
    for example in examples:
        origin = tracker.decisions[example.decision_atom]
        pair = (
            tracker.robots[example.robot_atom].robot_id,
            tracker.orders[example.order_atom].order_id,
        )
        positives, negatives = labelled.setdefault((origin.run_id, origin.decision_id), (set(), set()))
        (positives if example.is_positive else negatives).add(pair)

    decisions = []
    for (run_id, decision_id), (positives, negatives) in sorted(labelled.items()):
        frame = frames[run_id]
        rows = frame[(frame["decision_id"] == decision_id) & frame["eligible"]]
        decisions.append(
            DecisionExamples(
                tuple(candidate_pairs(rows)), frozenset(positives), frozenset(negatives)
            )
        )
    return decisions


def _check_against_popper(result: PopperResult, compiled: CompiledRules) -> None:
    """The interpreter must see what Popper saw, or the artifact is not this rule."""
    if result.score is None or compiled.pruned or not compiled.program.clauses:
        return
    tp, fn, tn, fp, _size = result.score
    ours = compiled.program_metrics
    if (ours.tp, ours.fn, ours.tn, ours.fp) != (tp, fn, tn, fp):
        raise TrainingError(
            f"interpreter disagrees with Popper: (tp, fn, tn, fp) "
            f"{(ours.tp, ours.fn, ours.tn, ours.fp)} against {(tp, fn, tn, fp)}"
        )


def train(
    run_dirs: Sequence[Path | str],
    out_dir: Path | str,
    *,
    teacher: str = "nearest_astar",
    decision_cap: int = DEFAULT_DECISION_CAP,
    corpus_seed: int = DEFAULT_CORPUS_SEED,
    k_neg: int = 3,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    eval_timeout_s: float = DEFAULT_EVAL_TIMEOUT_S,
    max_literals: int = DEFAULT_MAX_LITERALS,
    max_vars: int = 4,
    max_body: int = 4,
    k_max: int = K_MAX,
    repo: Path | str | None = None,
) -> dict[str, Any]:
    """Runs -> thresholds, BK, Tier A bias, examples, Popper, compile, artifact."""
    runs = [load_induction_run(path) for path in run_dirs]
    _check_corpus(runs, teacher)
    manifests = [run.manifest for run in runs]

    frames = {
        run.run_id: run.decisions.assign(
            est_finish_s=derive_est_finish_s(run.decisions, run.manifest)
        )
        for run in runs
    }

    # Thresholds over the eligible rows of every choice-bearing decision.
    thresholds = fit_thresholds_from_frames(
        [
            frame[frame["eligible"] & frame["choice_bearing"]]
            for frame in frames.values()
        ],
        seeds_used=induction_seeds_of(manifests),
        spec=DECISION_DISCRETIZATIONS,
    )

    chosen = stratified_decisions(_strata(runs), cap=decision_cap, seed=corpus_seed)
    if not chosen:
        raise TrainingError("no labelled choice-bearing decision in these runs")
    chosen_by_run: dict[str, set[int]] = {}
    for run_id, decision_id in chosen:
        chosen_by_run.setdefault(run_id, set()).add(decision_id)

    out_path = Path(out_dir)
    kb_dir = out_path / "kb"
    kb_dir.mkdir(parents=True, exist_ok=True)

    tracker = ProvenanceTracker()
    builder = BKBuilder(thresholds=thresholds, tracker=tracker)
    examples: list[GroundAtomExample] = []
    for run in runs:
        wanted = chosen_by_run.get(run.run_id)
        if not wanted:
            continue
        rows = frames[run.run_id]
        rows = rows[rows["decision_id"].isin(wanted)]
        builder.build_from_frames(run.run_id, rows, run_manifest=run.manifest)
        examples.extend(
            build_single_teacher_supervision(
                run.run_id, rows, tracker, run_manifest=run.manifest, k_neg=k_neg, seed=corpus_seed
            )
        )

    builder.write_bk_pl(kb_dir / "bk.pl")
    write_exs_pl(examples, kb_dir / "exs.pl")
    bias = generate_bias_pl(
        include_tier_b=False, max_vars=max_vars, max_body=max_body, max_clauses=k_max
    )
    (kb_dir / "bias.pl").write_text(bias, encoding="utf-8")
    thresholds.save_json(out_path / "thresholds.json")
    tracker.save_parquet(out_path)

    result = run_popper(
        kb_dir, timeout=timeout_s, eval_timeout=eval_timeout_s, max_literals=max_literals
    )
    if not result.success:
        raise TrainingError(f"Popper failed: {result.raw_output[-2000:]}")

    compiled = compile_rules(result.rules, _training_decisions(frames, examples, tracker), k_max)
    _check_against_popper(result, compiled)

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "teacher": teacher,
        "arm": ARM,
        "raw_clauses": list(compiled.raw),
        "clauses": [
            {"rank": rank, "clause": clause.text, "metrics": metrics.as_dict()}
            for rank, (clause, metrics) in enumerate(
                zip(compiled.program.clauses, compiled.clause_metrics)
            )
        ],
        "pruned_clauses": list(compiled.pruned),
        "program_metrics": compiled.program_metrics.as_dict(),
        "k_max": k_max,
        "thresholds_sha256": thresholds.sha256,
        "bias_sha256": sha256(bias.encode()).hexdigest(),
        "source_runs": sorted(
            (
                {
                    "run_id": m["run_id"],
                    "orders_sha256": m["orders_sha256"],
                    "seed": m["seed"],
                    "workload_seed": workload_seed_of(m["orders_path"]),
                    "robot_count": m["robot_count"],
                    "dispatch_quantum_s": m["dispatch_quantum_s"],
                    "git": m["git"],
                }
                for m in manifests
            ),
            key=lambda entry: entry["run_id"],
        ),
        "git": git_state(repo),
        "popper": {
            "version": popper_version(),
            "score": list(result.score) if result.score else None,
            "datalog": result.datalog,
            "timed_out": result.timed_out,
        },
        "solver_settings": {
            "noisy": True,
            "solver": "rc2",
            "timeout_s": timeout_s,
            "eval_timeout_s": eval_timeout_s,
            "max_literals": max_literals,
            "max_vars": max_vars,
            "max_body": max_body,
            "max_clauses": k_max,
            "tier_b": False,
        },
        "sampling": {
            "corpus_seed": corpus_seed,
            "k_neg": k_neg,
            "decision_cap": decision_cap,
            "decisions": len(chosen),
            "positives": sum(e.is_positive for e in examples),
            "negatives": sum(not e.is_positive for e in examples),
        },
    }
    sealed = write_rule_artifact(out_path / ARTIFACT_FILE, artifact)

    # Wall time varies between identical runs, so it stays out of the artifact.
    (out_path / "training_log.json").write_text(
        json.dumps({"popper_total_time_s": result.total_time_s, "raw_output": result.raw_output}, indent=2),
        encoding="utf-8",
    )
    return sealed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", help="recorded run directories")
    parser.add_argument("--out", required=True, help="directory for the artifact and its KB")
    parser.add_argument("--teacher", default="nearest_astar")
    parser.add_argument("--decisions", type=int, default=DEFAULT_DECISION_CAP)
    parser.add_argument("--corpus-seed", type=int, default=DEFAULT_CORPUS_SEED)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    args = parser.parse_args()

    artifact = train(
        args.runs,
        args.out,
        teacher=args.teacher,
        decision_cap=args.decisions,
        corpus_seed=args.corpus_seed,
        timeout_s=args.timeout,
        repo=Path(__file__).resolve().parents[3],
    )
    print(f"clauses  : {len(artifact['clauses'])} kept, {len(artifact['pruned_clauses'])} pruned")
    for entry in artifact["clauses"]:
        print(f"  [{entry['rank']}] {entry['clause']}  {entry['metrics']}")
    print(f"program  : {artifact['program_metrics']}")
    print(f"timed out: {artifact['popper']['timed_out']}")
    print(f"artifact : {Path(args.out) / ARTIFACT_FILE}")
    print(f"sha256   : {artifact['content_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
