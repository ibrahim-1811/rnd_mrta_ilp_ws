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
import random
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
from ilp_learning.predicates import TIER_B_FEATURES
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
from ilp_learning.supervision.multi_teacher import POLICIES, consensus_labelled
from ilp_learning.supervision.outcome_aware import (
    FROZEN as OUTCOME_CONFIG,
    MODES as OUTCOME_MODES,
    PRIMARY_MODE as OUTCOME_PRIMARY_MODE,
    outcome_examples,
    outcome_labels,
    summarize as summarize_outcome_labels,
    write_labels as write_outcome_labels,
)
from ilp_learning.supervision.outcome_common import (
    ARM as MP1_ARM,
    JW1,
    JW2,
    OBJECTIVES as COMMON_OBJECTIVES,
    common_examples,
    common_labels,
    cross_policy_labels,
    cross_policy_report,
    summarize_common,
    write_common_labels,
)
from ilp_learning.supervision.outcome_corrected import (
    corrected_examples,
    corrections,
    summarize_corrections,
)
from ilp_learning.supervision.single_teacher import build_single_teacher_supervision
from ilp_learning.teachers import TEACHERS
from ilp_learning.thresholds import (
    DECISION_DISCRETIZATIONS,
    DEFAULT_DISCRETIZATIONS,
    BATTERY_DISCRETIZATIONS,
    EXTENDED_DISCRETIZATIONS,
    add_battery_columns,
    add_load_columns,
    fit_thresholds_from_frames,
    induction_seeds_of,
)
from simulation_env.dataset import git_state
from simulation_env.failures import FAILURE_FILE
from simulation_env.seed_split import check_run_is_induction, workload_seed_of


ARTIFACT_FILE = "rule_artifact.json"
FEATURES_FILE = "features.parquet"
LABELS_FILE = "teacher_labels.parquet"
OUTCOME_LABELS_FILE = "outcome_labels.csv"
PAIR_KEYS = ["decision_id", "robot_id", "order_id"]
ARM = "A"
TERCILES = 3
# --continuation value meaning "each run's own strategy continued its branches".
BEHAVIOUR = "behaviour"


class TrainingError(RuntimeError):
    """The inputs or the learner's output cannot give a trustworthy artifact."""


@dataclass(frozen=True)
class InductionRun:
    manifest: Mapping[str, Any]
    decisions: pd.DataFrame
    features: pd.DataFrame | None = None
    teacher_labels: pd.DataFrame | None = None

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
    features, labels = path / FEATURES_FILE, path / LABELS_FILE
    return InductionRun(
        manifest,
        pd.read_parquet(path / "decisions.parquet"),
        pd.read_parquet(features) if features.exists() else None,
        pd.read_parquet(labels) if labels.exists() else None,
    )


def _check_corpus(runs: Sequence[InductionRun], teacher: str, pooled: bool = False) -> None:
    if not runs:
        raise TrainingError("no runs given")
    if pooled:
        strategies = sorted({r.manifest["strategy"] for r in runs})
        unknown = [s for s in strategies if s not in TEACHERS]
        if unknown:
            raise TrainingError(f"pooled training takes conventional teachers only, got {unknown}")
    else:
        wrong = sorted({r.manifest["strategy"] for r in runs} - {teacher})
        if wrong:
            raise TrainingError(f"teacher is {teacher}, but runs were driven by {wrong}")
    ids = [r.run_id for r in runs]
    if len(set(ids)) != len(ids):
        raise TrainingError("the same run was given twice")


def _strata(runs: Sequence[InductionRun], by_strategy: bool = False) -> dict[tuple, list[tuple[str, int]]]:
    """(fleet_size, quantum, seed, sim_time tercile) -> labelled decisions.

    by_strategy (pooled training) puts the driving strategy first in the key, so the
    decision cap is shared evenly between teachers instead of following run counts.
    """
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
            if by_strategy:
                key = (run.manifest["strategy"], *key)
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


def permute_labels(frame: pd.DataFrame, seed: int) -> pd.DataFrame:
    """G10 control: each decision's selection moved to random disjoint eligible pairs."""
    rng = random.Random(seed)
    selected = pd.Series(False, index=frame.index)
    for _, rows in frame[frame["eligible"]].groupby("decision_id", sort=True):
        wanted = int(rows["selected"].sum())
        taken_robots, taken_orders = set(), set()
        for index in rng.sample(list(rows.index), len(rows)):
            if wanted == 0:
                break
            robot, order = rows.at[index, "robot_id"], rows.at[index, "order_id"]
            if robot not in taken_robots and order not in taken_orders:
                selected.at[index] = True
                taken_robots.add(robot)
                taken_orders.add(order)
                wanted -= 1
    return frame.assign(selected=selected)


def relabel_earliest_finish(frame: pd.DataFrame) -> pd.DataFrame:
    """G10 control: the same states, labelled by earliest_finish's own greedy sweep."""
    selected = pd.Series(False, index=frame.index)
    for _, rows in frame[frame["eligible"]].groupby("decision_id", sort=True):
        wanted = int(rows["selected"].sum())
        ordered = rows.sort_values(
            ["est_finish_s", "c_static_m", "fifo_position", "robot_id"], kind="stable"
        )
        taken_robots, taken_orders = set(), set()
        for index, row in ordered.iterrows():
            if wanted == 0:
                break
            if row["robot_id"] not in taken_robots and row["order_id"] not in taken_orders:
                selected.at[index] = True
                taken_robots.add(row["robot_id"])
                taken_orders.add(row["order_id"])
                wanted -= 1
    return frame.assign(selected=selected)


def panel_labelled(frame: pd.DataFrame, labels: pd.DataFrame, teacher: str) -> pd.DataFrame:
    """The same decisions, selected as this panel teacher would have (M2 spec 6.3)."""
    picks = labels[labels["teacher"] == teacher]
    if picks.empty:
        raise TrainingError(f"the panel has no label from {teacher}")
    chosen = set(zip(picks["decision_id"], picks["robot_id"], picks["order_id"]))
    keys = zip(frame["decision_id"], frame["robot_id"], frame["order_id"])
    return frame.assign(selected=[key in chosen for key in keys])


def _pool_provenance(runs, chosen, examples, tracker) -> dict[str, dict[str, int]]:
    """Per teacher: runs, sampled decisions and examples it contributed."""
    strategy_of = {run.run_id: run.manifest["strategy"] for run in runs}
    pool: dict[str, dict[str, int]] = {}
    for run in runs:
        entry = pool.setdefault(strategy_of[run.run_id], {"runs": 0, "decisions": 0, "positives": 0, "negatives": 0})
        entry["runs"] += 1
    for run_id, _decision in chosen:
        pool[strategy_of[run_id]]["decisions"] += 1
    for example in examples:
        strategy = strategy_of[tracker.decisions[example.decision_atom].run_id]
        pool[strategy]["positives" if example.is_positive else "negatives"] += 1
    return dict(sorted(pool.items()))


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
    exclude_predicates: Sequence[str] = (),
    permute_seed: int | None = None,
    relabel_finish: bool = False,
    tier_b: bool = False,
    label_teacher: str | None = None,
    extended: bool = False,
    label_policy: str | None = None,
    outcome_rollouts: Mapping[str, Path | str | Sequence[Path | str]] | None = None,
    outcome_mode: str = OUTCOME_PRIMARY_MODE,
    battery: bool = False,
    only_decisions: Mapping[str, set[int]] | None = None,
    outcome_objective: str = "jw2",
    pooled: bool = False,
    continuation: str | None = None,
    outcome_correct: bool = False,
) -> dict[str, Any]:
    """Runs -> thresholds, BK, bias, examples, Popper, compile, artifact.

    tier_b adds the congestion predicates; it needs each run's features.parquet.
    label_teacher takes labels from that teacher in the run's panel, keeping the
    decisions, and with them bk.pl, those of the run's own strategy (``teacher``).
    extended adds the relative cost, order age and load predicates. label_policy
    takes labels from the votes of the whole panel instead. outcome_rollouts (M6) maps
    a run_id to its rollout directory (ilp_learning.m6_rollouts) and takes every label
    from measured rollout outcomes instead: no teacher pick or vote becomes an example.
    A chosen decision without rollouts gives no example but stays in bk.pl, so bk.pl and
    bias.pl are those of M4 and M5 on the same runs and switches. A run whose rollouts
    are the multi-pair arm (mp1, one directory or several shards) is labelled by the
    common-workload objective jw1 instead of the frozen J. battery adds the battery
    predicates. only_decisions restricts every supervision to these decisions of each
    run (the probed ones), so M4, M5 and M6 can share one sampled state set.
    pooled (multi-teacher demonstrations) takes runs driven by several conventional
    strategies, each labelled by its own picks: no teacher has to agree with another,
    and the states are those each teacher reached, never compared as counterfactuals.
    continuation (mp1 rollouts only) takes labels from the branches one continuation
    policy continued, instead of requiring every policy to agree (cross_policy_labels,
    the sensitivity experiment). "behaviour" means each run's own strategy: per-teacher
    outcome supervision, and with pooled, outcome labels pooled over teachers. Every
    label is relative within one recorded state; no score is compared across states.
    outcome_correct (group D) keeps the teacher's own picks of the probed decisions and
    corrects them where the run's own continuation shows the teacher's pair lost by delta
    (supervision.outcome_corrected); it needs mp1 rollouts and continuation "behaviour".
    """
    runs = [load_induction_run(path) for path in run_dirs]
    if outcome_correct and (outcome_rollouts is None or continuation != BEHAVIOUR):
        raise TrainingError(
            f"--outcome-correct needs --outcome-rollouts and --continuation {BEHAVIOUR}: "
            "each teacher's picks are corrected by its own continuation")
    pooled_outcome = pooled and outcome_rollouts is not None
    if pooled_outcome and continuation != BEHAVIOUR:
        raise TrainingError(
            f"pooled outcome labels need --continuation {BEHAVIOUR}: each teacher continues its own states")
    if pooled and (label_teacher or label_policy or relabel_finish or permute_seed is not None):
        raise TrainingError("pooled demonstrations use each run's own picks; no other labels")
    if continuation is not None and outcome_rollouts is None:
        raise TrainingError("--continuation selects outcome labels; it needs --outcome-rollouts")
    _check_corpus(runs, teacher, pooled)
    if tier_b and any(run.features is None for run in runs):
        raise TrainingError(f"--tier-b needs {FEATURES_FILE} beside every run")
    if label_teacher and label_policy:
        raise TrainingError("labels come from one teacher or from the panel, not both")
    if (label_teacher or label_policy) and any(run.teacher_labels is None for run in runs):
        raise TrainingError(f"panel labels need {LABELS_FILE} beside every run")
    outcome = outcome_rollouts is not None
    if outcome and (label_teacher or label_policy or relabel_finish or permute_seed is not None):
        raise TrainingError("outcome labels exclude teacher, panel and control labels")
    if outcome and outcome_objective not in COMMON_OBJECTIVES:
        raise TrainingError(f"unknown outcome objective {outcome_objective!r}")
    common = JW2 if outcome_objective == "jw2" else JW1
    if outcome and outcome_mode not in OUTCOME_MODES:
        raise TrainingError(f"unknown outcome mode {outcome_mode!r}; known: {OUTCOME_MODES}")
    if outcome:
        unknown = sorted(set(outcome_rollouts) - {run.run_id for run in runs})
        if unknown:
            raise TrainingError(f"rollouts given for runs not in the corpus: {unknown}")
    run_paths = {run.run_id: Path(path) for run, path in zip(runs, run_dirs)}
    manifests = [run.manifest for run in runs]

    frames = {
        run.run_id: run.decisions.assign(
            est_finish_s=derive_est_finish_s(run.decisions, run.manifest)
        )
        for run in runs
    }
    if label_teacher:
        frames = {
            run.run_id: panel_labelled(frames[run.run_id], run.teacher_labels, label_teacher)
            for run in runs
        }
    if label_policy:
        frames = {
            run.run_id: consensus_labelled(frames[run.run_id], run.teacher_labels, label_policy)
            for run in runs
        }
    if relabel_finish:
        frames = {run_id: relabel_earliest_finish(frame) for run_id, frame in frames.items()}
    if permute_seed is not None:
        frames = {run_id: permute_labels(frame, permute_seed) for run_id, frame in frames.items()}

    # The frames bk.pl is built from stay decision tables; the pairs that metrics
    # and thresholds read also carry the features.
    paired = frames
    if tier_b:
        paired = {
            run.run_id: frames[run.run_id].merge(
                run.features[[*PAIR_KEYS, *TIER_B_FEATURES]], on=PAIR_KEYS, how="left"
            )
            for run in runs
        }

    spec = dict(DEFAULT_DISCRETIZATIONS if tier_b else DECISION_DISCRETIZATIONS)
    if extended:
        spec.update(EXTENDED_DISCRETIZATIONS)
        paired = {
            run_id: add_load_columns(frame[frame["eligible"]]) for run_id, frame in paired.items()
        }
    if battery:
        spec.update(BATTERY_DISCRETIZATIONS)
        paired = {
            run_id: add_battery_columns(frame[frame["eligible"]]) for run_id, frame in paired.items()
        }

    # Thresholds over the eligible rows of every choice-bearing decision.
    thresholds = fit_thresholds_from_frames(
        [
            frame[frame["eligible"] & frame["choice_bearing"]]
            for frame in paired.values()
        ],
        seeds_used=induction_seeds_of(manifests),
        spec=spec,
    )

    strata = _strata(runs, by_strategy=pooled)
    if only_decisions is not None:
        strata = {
            key: [m for m in members if m[1] in only_decisions.get(m[0], set())]
            for key, members in strata.items()
        }
        strata = {key: members for key, members in strata.items() if members}
    chosen = stratified_decisions(strata, cap=decision_cap, seed=corpus_seed)
    if not chosen:
        raise TrainingError("no labelled choice-bearing decision in these runs")
    chosen_by_run: dict[str, set[int]] = {}
    for run_id, decision_id in chosen:
        chosen_by_run.setdefault(run_id, set()).add(decision_id)

    out_path = Path(out_dir)
    kb_dir = out_path / "kb"
    kb_dir.mkdir(parents=True, exist_ok=True)

    tracker = ProvenanceTracker()
    builder = BKBuilder(thresholds=thresholds, tracker=tracker, extended=extended, battery=battery)
    objectives: set[str] = set()
    examples: list[GroundAtomExample] = []
    outcome_frames: list[pd.DataFrame] = []
    fixes_all: list = []
    for run in runs:
        wanted = chosen_by_run.get(run.run_id)
        if not wanted:
            continue
        rows = frames[run.run_id]
        rows = rows[rows["decision_id"].isin(wanted)]
        features = None
        if tier_b:
            features = run.features[run.features["decision_id"].isin(wanted)]
        builder.build_from_frames(
            run.run_id, rows, features_df=features, run_manifest=run.manifest
        )
        if outcome:
            # M6: labels from rollouts only; the teacher's 'selected' is never read here.
            if run.run_id not in outcome_rollouts:
                continue
            sources = outcome_rollouts[run.run_id]
            sources = [sources] if isinstance(sources, (str, Path)) else list(sources)
            arms = {
                json.loads((Path(src) / "probe.json").read_text(encoding="utf-8")).get("arm", "m6a")
                for src in sources
            }
            continued = {
                len(json.loads((Path(src) / "probe.json").read_text(encoding="utf-8")).get("continuations") or [1])
                for src in sources
            }
            if outcome_correct:
                if arms != {MP1_ARM}:
                    raise TrainingError(f"run {run.run_id}: --outcome-correct needs mp1 rollouts")
                labels = common_labels(
                    run_paths[run.run_id], sources, mode=outcome_mode, config=common,
                    continuation=run.manifest["strategy"],
                )
                labels = labels[labels["decision_id"].isin(wanted)]
                fixes = corrections(labels)
                fixes_all.extend(fixes)
                probed_rows = rows[rows["decision_id"].isin(set(labels["decision_id"]))]
                examples.extend(corrected_examples(
                    run.run_id, probed_rows, fixes, tracker, k_neg=k_neg, seed=corpus_seed))
                objectives.add(common.objective)
            elif arms == {MP1_ARM} and continuation is not None:
                # One continuation policy's labels; "behaviour" is the run's own strategy.
                policy = run.manifest["strategy"] if continuation == BEHAVIOUR else continuation
                labels = common_labels(
                    run_paths[run.run_id], sources, mode=outcome_mode, config=common,
                    continuation=policy,
                )
                labels = labels[labels["decision_id"].isin(wanted)]
                examples.extend(common_examples(labels, rows, tracker))
                objectives.add(common.objective)
            elif arms == {MP1_ARM} and max(continued) > 1:
                # Several continuation policies: a candidate is labelled only when every
                # policy agrees (outcome_common.cross_policy_labels).
                labels = cross_policy_labels(
                    run_paths[run.run_id], sources, mode=outcome_mode, config=common
                )
                labels = labels[labels["decision_id"].isin(wanted)]
                examples.extend(common_examples(labels, rows, tracker))
                objectives.add(f"{common.objective}_cross_policy")
            elif arms == {MP1_ARM}:
                labels = common_labels(
                    run_paths[run.run_id], sources, mode=outcome_mode, config=common
                )
                labels = labels[labels["decision_id"].isin(wanted)]
                examples.extend(common_examples(labels, rows, tracker))
                objectives.add(common.objective)
            elif continuation is not None:
                raise TrainingError(f"run {run.run_id}: --continuation is for mp1 rollouts only")
            elif arms == {"m6a"} and len(sources) == 1:
                labels = outcome_labels(run_paths[run.run_id], sources[0], mode=outcome_mode)
                labels = labels[labels["decision_id"].isin(wanted)]
                examples.extend(outcome_examples(labels, rows, tracker))
                objectives.add("m1_frozen_J")
            else:
                raise TrainingError(f"run {run.run_id}: rollouts of arms {sorted(arms)} cannot be mixed")
            outcome_frames.append(labels)
            continue
        examples.extend(
            build_single_teacher_supervision(
                run.run_id, rows, tracker, run_manifest=run.manifest, k_neg=k_neg, seed=corpus_seed
            )
        )

    builder.write_bk_pl(kb_dir / "bk.pl")
    supervision = None
    if outcome:
        all_labels = pd.concat(outcome_frames, ignore_index=True) if outcome_frames else None
        if all_labels is None or not any(e.is_positive for e in examples):
            raise TrainingError("outcome labels gave no positive example; nothing to learn from")
        if len(objectives) != 1:
            raise TrainingError(f"one objective per artifact, got {sorted(objectives)}")
        objective = objectives.pop()
        if objective.endswith("_cross_policy"):
            text = all_labels.to_csv(index=False, float_format="%.9f", lineterminator="\n")
            (out_path / OUTCOME_LABELS_FILE).write_text(text, encoding="utf-8")
            supervision = {
                "kind": "outcome_aware",
                "objective": objective,
                "arm": MP1_ARM,
                "mode": outcome_mode,
                "contract": {"horizon_s": common.horizon_s, "lambda_wait": common.lambda_wait,
                             "delta_s": common.delta_s, "completion": common.completion,
                             "config_sha256": common.sha256},
                "labels_sha256": sha256(text.encode()).hexdigest(),
                "labels": cross_policy_report(all_labels),
                "chosen_decisions_without_rollouts": len(chosen)
                - int(all_labels[["run_id", "decision_id"]].drop_duplicates().shape[0]),
            }
        elif objective in COMMON_OBJECTIVES:
            supervision = {
                "kind": "outcome_aware",
                "objective": objective,
                "arm": MP1_ARM,
                "mode": outcome_mode,
                "contract": {"horizon_s": common.horizon_s, "lambda_wait": common.lambda_wait,
                             "delta_s": common.delta_s, "completion": common.completion,
                             "config_sha256": common.sha256},
                "labels_sha256": write_common_labels(all_labels, out_path / OUTCOME_LABELS_FILE),
                "labels": summarize_common(all_labels),
                # Which policy continued the labelled branches: a name, "behaviour" (each
                # run's own strategy), or None for single-policy rollouts.
                "continuation": continuation,
                "continued_by": sorted(
                    {(r.manifest["strategy"] if continuation == BEHAVIOUR else continuation)
                     for r in runs if r.run_id in set(all_labels["run_id"])}
                ) if continuation is not None else None,
                "rollouts": [
                    {"run_id": r, "rollouts_sha256": sorted(set(g))}
                    for r, g in sorted(all_labels.groupby("run_id")["rollouts_sha256"])
                ],
                "chosen_decisions_without_rollouts": len(chosen)
                - int(all_labels[["run_id", "decision_id"]].drop_duplicates().shape[0]),
            }
            if outcome_correct:
                supervision["kind"] = "outcome_corrected"
                supervision["corrections"] = summarize_corrections(
                    fixes_all, int(all_labels[["run_id", "decision_id"]].drop_duplicates().shape[0]))
    if outcome and supervision is None:
        supervision = {
            "objective": "m1_frozen_J",
            "kind": "outcome_aware",
            "mode": outcome_mode,
            "contract": {
                "horizon_s": OUTCOME_CONFIG.horizon_s,
                "lambda_wait": OUTCOME_CONFIG.lambda_wait,
                "delta_s": OUTCOME_CONFIG.delta_s,
                "config_sha256": OUTCOME_CONFIG.sha256,
            },
            "labels_sha256": write_outcome_labels(all_labels, out_path / OUTCOME_LABELS_FILE),
            "labels": summarize_outcome_labels(all_labels),
            "rollouts": sorted(
                (
                    {"run_id": run_id, "rollouts_sha256": sha}
                    for run_id, sha in all_labels.groupby("run_id")["rollouts_sha256"].first().items()
                ),
                key=lambda entry: entry["run_id"],
            ),
            "chosen_decisions_without_rollouts": len(chosen)
            - int(all_labels[["run_id", "decision_id"]].drop_duplicates().shape[0]),
        }
    write_exs_pl(examples, kb_dir / "exs.pl")
    bias = generate_bias_pl(
        include_tier_b=tier_b,
        include_extended=extended,
        include_battery=battery,
        exclude=tuple(exclude_predicates),
        max_vars=max_vars,
        max_body=max_body,
        max_clauses=k_max,
    )
    (kb_dir / "bias.pl").write_text(bias, encoding="utf-8")
    thresholds.save_json(out_path / "thresholds.json")
    tracker.save_parquet(out_path)

    result = run_popper(
        kb_dir, timeout=timeout_s, eval_timeout=eval_timeout_s, max_literals=max_literals
    )
    if not result.success:
        raise TrainingError(f"Popper failed: {result.raw_output[-2000:]}")

    compiled = compile_rules(
        result.rules, _training_decisions(paired, examples, tracker), k_max, thresholds
    )
    _check_against_popper(result, compiled)

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "teacher": ("outcome_corrected:" + "+".join(sorted({r.manifest["strategy"] for r in runs})))
        if outcome_correct
        else "outcome_aware"
        if outcome
        else "pooled:" + "+".join(sorted({r.manifest["strategy"] for r in runs}))
        if pooled
        else label_teacher or (f"panel_{label_policy}" if label_policy else teacher),
        "behaviour_policy": "+".join(sorted({r.manifest["strategy"] for r in runs})) if pooled else teacher,
        "arm": ARM,
        "control": {
            "exclude_predicates": sorted(exclude_predicates),
            "permute_seed": permute_seed,
            "relabel_earliest_finish": relabel_finish,
        },
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
            # Stopped after its grace: the rules are Popper's last incumbent.
            "stopped_by_runner": result.stopped_by_runner,
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
            "tier_b": tier_b,
            "extended": extended,
            "battery": battery,
            "only_decisions": only_decisions is not None,
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
    if supervision is not None:
        artifact["supervision"] = supervision
    if pooled:
        artifact["pool"] = _pool_provenance(runs, chosen, examples, tracker)
    if tier_b or extended or battery:
        artifact["thresholds"] = thresholds.to_dict()
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
    parser.add_argument("--exclude", nargs="*", default=[], help="G10 control: predicates left out")
    parser.add_argument("--permute-seed", type=int, help="G10 control: permute the labels")
    parser.add_argument("--relabel-finish", action="store_true", help="G10 control: earliest_finish labels")
    parser.add_argument("--tier-b", action="store_true", help="add the congestion predicates")
    parser.add_argument("--extended", action="store_true", help="add relative cost, age and load")
    parser.add_argument("--label-policy", choices=POLICIES, help="labels from the panel's votes")
    parser.add_argument("--label-teacher", help="labels from this teacher in the run's panel")
    parser.add_argument(
        "--outcome-rollouts", type=Path,
        help="M6: labels from rollouts; a directory of m6_rollouts outputs (any depth)",
    )
    parser.add_argument("--outcome-mode", choices=OUTCOME_MODES, default=OUTCOME_PRIMARY_MODE)
    parser.add_argument("--battery", action="store_true", help="add the battery predicates")
    parser.add_argument("--pooled", action="store_true",
                        help="multi-teacher demonstrations: runs of several strategies, each its own labels")
    parser.add_argument("--outcome-objective", choices=COMMON_OBJECTIVES, default="jw2",
                        help="objective for multi-pair (mp1) rollouts; M6-A always uses the frozen J")
    parser.add_argument(
        "--continuation",
        help="mp1 outcome labels from one continuation policy (e.g. nearest_astar), or "
        f"'{BEHAVIOUR}': each run's own strategy. Omitted with several policies: "
        "labels only where every policy agrees (sensitivity)",
    )
    parser.add_argument(
        "--decisions-from", type=Path,
        help="train only on the decisions probed in these rollout directories (any depth)",
    )
    parser.add_argument(
        "--outcome-correct", action="store_true",
        help="group D: the teacher's picks, corrected where its own continuation shows its pair "
        "lost by delta (needs --outcome-rollouts and --continuation behaviour)",
    )
    args = parser.parse_args()

    given = {json.loads((Path(r) / "run.json").read_text())["run_id"] for r in args.runs}

    def probes(root):
        found: dict[str, list[Path]] = {}
        for probe in sorted(root.rglob("probe.json")):
            run_id = json.loads(probe.read_text(encoding="utf-8"))["run_id"]
            if run_id in given:
                found.setdefault(run_id, []).append(probe.parent)
        return found

    rollouts = None
    if args.outcome_rollouts is not None:
        rollouts = {
            run_id: dirs if len(dirs) > 1 else dirs[0]
            for run_id, dirs in probes(args.outcome_rollouts).items()
        }
    only = None
    if args.decisions_from is not None:
        only = {
            run_id: {
                int(d) for p in dirs
                for d in json.loads((p / "probe.json").read_text(encoding="utf-8"))["rollout_decisions"]
            }
            for run_id, dirs in probes(args.decisions_from).items()
        }

    artifact = train(
        args.runs,
        args.out,
        teacher=args.teacher,
        decision_cap=args.decisions,
        corpus_seed=args.corpus_seed,
        timeout_s=args.timeout,
        exclude_predicates=args.exclude,
        permute_seed=args.permute_seed,
        relabel_finish=args.relabel_finish,
        tier_b=args.tier_b,
        label_teacher=args.label_teacher,
        extended=args.extended,
        label_policy=args.label_policy,
        outcome_rollouts=rollouts,
        outcome_mode=args.outcome_mode,
        battery=args.battery,
        only_decisions=only,
        outcome_objective=args.outcome_objective,
        pooled=args.pooled,
        continuation=args.continuation,
        outcome_correct=args.outcome_correct,
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
