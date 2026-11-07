"""M6 outcome-aware supervision: counterfactual rollout scores -> pos / neg / abstain.

A rollout (simulation_env/rollout.py, M1) clones the simulator just before a
decision, keeps every pair but the pivot's, and gives the pivot robot either the
teacher's order or one of up to three alternative orders. Each branch runs on until
H has passed and the decision's window is done. A branch is one tested pair.

Frozen scoring contract (DECISIONS.md, "M1 Acceptance Record", item 2):

    J      = pivot_mission_s + lambda * waiting_in_h_s     lower is better
    H      = 120 s, lambda = 0.50, delta = 2.0 s

Branch status:

    valid      completed: window done, pivot mission done inside H, finite J
    censored   no stall, but the window or the pivot mission did not finish in H, or
               J is not a number. Not a bad action: its outcome is unknown.
    failed     the branch stalled
    Neither censored nor failed is ever an example (M1 record item 2).

Labels, per probed decision, over its valid branches only. The teacher's own pair
is one tested pair like any other; which branch was the teacher's never enters
the calculation, and teacher picks and votes are not read here at all.

    pos        the unique best branch, at least delta better than the second best
    neg        a branch at least delta worse than the best (also when there is no
               unique winner)
    abstain    everything else: a best that does not win by delta, a branch within
               delta of the best, a decision with fewer than two valid branches, and
               every branch of a decision whose rows disagree about the same pair

Modes:

    conservative   primary (M6-2): no positive at a decision where any branch is
                   censored or failed; the unseen branch might have been the best.
                   Negatives between completed branches stand.
    m6_1           sensitivity: the M6-1 rule, a positive among the valid branches
                   whatever happened to the others.

Pairs that were not tested are not examples at all; they stay in bk.pl like every
other candidate. Nothing here builds bk.pl, bias.pl or thresholds, and nothing
here is read by a rule at run time: outcomes reach the learner as labels only.

Only induction runs (seeds 104-110) whose clone check passed are read.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from ilp_learning.atoms import ProvenanceTracker
from ilp_learning.sampler import GroundAtomExample
from simulation_env.seed_split import check_run_is_induction, workload_seed_of


ROLLOUTS_FILE = "rollouts.csv"
PROBE_FILE = "probe.json"
MODES = ("conservative", "m6_1")
PRIMARY_MODE = "conservative"
LABELS_COLUMNS = [
    "run_id", "workload_seed", "robot_count", "quantum_s", "decision_id", "state_sha256",
    "branch", "robot_id", "order_id", "baseline_order", "rollout_status", "j_s", "margin_s",
    "status", "label", "reason", "mode", "config_sha256", "rollouts_sha256",
]
REQUIRED = (
    "decision_id", "branch", "pivot_robot", "teacher_order", "order", "pivot_mission_s",
    "waiting_in_h_s", "window_complete", "pivot_done_s", "rollout_status",
)
# Decision outputs, not state: the teacher's pick and the run's names.
NOT_STATE = ("run_id", "selected", "strategy")
# Scores are float images of exact rational clocks; a 2.0 s gap must not read 1.9999.
EPS = 1e-9


class OutcomeLabelError(RuntimeError):
    """The rollouts cannot give trustworthy outcome labels."""


@dataclass(frozen=True)
class OutcomeConfig:
    horizon_s: float = 120.0
    lambda_wait: float = 0.50
    delta_s: float = 2.0

    @property
    def sha256(self) -> str:
        return sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]


FROZEN = OutcomeConfig()


def j_score(pivot_mission_s: float, waiting_in_h_s: float, config: OutcomeConfig = FROZEN) -> float:
    return float(pivot_mission_s) + config.lambda_wait * float(waiting_in_h_s)


def branch_status(row: Mapping[str, Any], config: OutcomeConfig = FROZEN) -> str:
    """'valid', 'failed' or 'censored' for one rollout row."""
    if row["rollout_status"] != "complete":
        return "failed"
    numbers = (row["pivot_mission_s"], row["waiting_in_h_s"], row["pivot_done_s"])
    if any(pd.isna(x) or not math.isfinite(float(x)) for x in numbers):
        return "censored"
    if not _true(row["window_complete"]):
        return "censored"
    if float(row["pivot_done_s"]) > config.horizon_s + EPS:
        return "censored"
    return "valid"


def _true(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def label_decision(
    branches: pd.DataFrame, config: OutcomeConfig = FROZEN, mode: str = PRIMARY_MODE
) -> pd.DataFrame:
    """Labels for the branches of one decision, sorted by (order_id, branch)."""
    if mode not in MODES:
        raise ValueError(f"unknown outcome mode {mode!r}; known: {MODES}")
    # Sorted by the tested pair, not by branch name, so naming a branch "teacher"
    # cannot decide anything; the branch name only breaks exact repeats of a pair.
    rows = branches.sort_values(["order", "branch"], kind="stable").reset_index(drop=True)
    out = pd.DataFrame(
        {
            "decision_id": rows["decision_id"].astype(int),
            "branch": rows["branch"].astype(str),
            "robot_id": rows["pivot_robot"].astype(str),
            "order_id": rows["order"].astype(str),
            "baseline_order": rows["teacher_order"].astype(str),
            "rollout_status": rows["rollout_status"].astype(str),
        }
    )
    out["status"] = [branch_status(row, config) for _, row in rows.iterrows()]
    out["j_s"] = [
        j_score(m, w, config) if s == "valid" else float("nan")
        for m, w, s in zip(rows["pivot_mission_s"], rows["waiting_in_h_s"], out["status"])
    ]
    out["margin_s"] = float("nan")
    out["label"] = "excluded"
    out["reason"] = out["status"]

    valid = out[out["status"] == "valid"]

    def abstain_all(reason: str) -> pd.DataFrame:
        out.loc[valid.index, ["label", "reason"]] = ["abstain", reason]
        return out

    if out["robot_id"].nunique() > 1:
        return abstain_all("conflict")
    # The same pair must not carry two different scores; identical repeats count once.
    spread = valid.groupby("order_id")["j_s"].agg(lambda j: j.max() - j.min())
    if (spread > EPS).any():
        return abstain_all("conflict")
    unique = valid.drop_duplicates("order_id", keep="first")
    if len(unique) < 2:
        return abstain_all("no_comparison")

    ranked = unique.sort_values(["j_s", "order_id"], kind="stable")
    best = float(ranked["j_s"].iloc[0])
    best_order = ranked["order_id"].iloc[0]
    gap = float(ranked["j_s"].iloc[1]) - best
    unseen = bool((out["status"] != "valid").any())
    out.loc[valid.index, "margin_s"] = out.loc[valid.index, "j_s"] - best

    for index in valid.index:
        margin = float(out.at[index, "margin_s"])
        if out.at[index, "order_id"] == best_order:
            if gap < config.delta_s - EPS:
                verdict = ["abstain", "best_not_by_delta"]
            elif unseen and mode == "conservative":
                verdict = ["abstain", "best_but_censored_decision"]
            else:
                verdict = ["pos", "best_by_delta"]
        elif margin >= config.delta_s - EPS:
            verdict = ["neg", "worse_by_delta"]
        else:
            verdict = ["abstain", "within_delta"]
        out.loc[index, ["label", "reason"]] = verdict
    return out


def state_hashes(decisions: pd.DataFrame) -> dict[int, str]:
    """A hash of each decision's candidate state: every column but the decision's outputs."""
    columns = [c for c in decisions.columns if c not in NOT_STATE]
    hashes = {}
    for decision_id, rows in decisions.groupby("decision_id", sort=True):
        canonical = rows[columns].sort_values(["robot_id", "order_id"], kind="stable")
        text = canonical.to_csv(index=False, float_format="%.12g", lineterminator="\n")
        hashes[int(decision_id)] = sha256(text.encode()).hexdigest()[:16]
    return hashes


def load_rollout_run(
    run_dir: Path | str, rollout_dir: Path | str | None = None, config: OutcomeConfig = FROZEN
):
    """(manifest, decisions, rollouts, rollouts_sha256) of one probed run, after every check.

    rollout_dir holds probe.json and rollouts.csv when they live apart from the run
    (M6-2 corpus rollouts); by default they sit beside it (M6-1 profile).
    """
    path = Path(run_dir)
    source = Path(rollout_dir) if rollout_dir is not None else path
    manifest = json.loads((path / "run.json").read_text(encoding="utf-8"))
    check_run_is_induction(manifest)

    probe = json.loads((source / PROBE_FILE).read_text(encoding="utf-8"))
    if probe.get("arm", "m6a") != "m6a":
        # The pivot-mission J is not comparable across different orders and robots;
        # the multi-pair arm has no frozen label rule yet.
        raise OutcomeLabelError(f"{source}: arm {probe['arm']!r} has no frozen label rule")
    if probe.get("clone_verified") is not True:
        raise OutcomeLabelError(f"{source}: clone check did not pass; rollouts are not evidence")
    if probe.get("replay_identical") is False:
        raise OutcomeLabelError(f"{source}: replay differs from the run it names")
    horizon = float(probe.get("args", {}).get("horizon", "nan"))
    if not math.isclose(horizon, config.horizon_s):
        raise OutcomeLabelError(f"{source}: rollouts ran with H={horizon}, frozen H={config.horizon_s}")
    if probe.get("run_id") != manifest["run_id"]:
        raise OutcomeLabelError(f"{source}: probe.json and run.json name different runs")

    rollouts = pd.read_csv(
        source / ROLLOUTS_FILE, dtype={"pivot_robot": str, "order": str, "teacher_order": str}
    )
    missing = [c for c in REQUIRED if c not in rollouts.columns]
    if missing:
        raise OutcomeLabelError(f"{source}: rollouts.csv lacks {missing}; re-run the probe")
    # Content, not file bytes: the same scored branches in another row order are the same.
    canonical = rollouts[list(REQUIRED)].sort_values(["decision_id", "branch"], kind="stable")
    content = canonical.to_csv(index=False, float_format="%.12g", lineterminator="\n")
    return (
        manifest, pd.read_parquet(path / "decisions.parquet"), rollouts,
        sha256(content.encode()).hexdigest()[:16],
    )


def outcome_labels(
    run_dir: Path | str,
    rollout_dir: Path | str | None = None,
    *,
    mode: str = PRIMARY_MODE,
    config: OutcomeConfig = FROZEN,
) -> pd.DataFrame:
    """Every rollout branch of one run with its label and provenance, in a fixed order."""
    manifest, decisions, rollouts, rollouts_sha = load_rollout_run(run_dir, rollout_dir, config)
    if rollouts.empty:
        return pd.DataFrame(columns=LABELS_COLUMNS)

    frames = [
        label_decision(rows, config, mode)
        for _, rows in sorted(rollouts.groupby("decision_id"), key=lambda item: int(item[0]))
    ]
    labels = pd.concat(frames, ignore_index=True)

    hashes = state_hashes(decisions)
    unknown = sorted(set(labels["decision_id"]) - set(hashes))
    if unknown:
        raise OutcomeLabelError(f"rollouts name decisions the run does not have: {unknown[:3]}")
    labels = labels.assign(
        run_id=manifest["run_id"],
        workload_seed=workload_seed_of(manifest["orders_path"]),
        robot_count=int(manifest["robot_count"]),
        quantum_s=float(manifest["dispatch_quantum_s"]),
        state_sha256=labels["decision_id"].map(hashes),
        mode=mode,
        config_sha256=config.sha256,
        rollouts_sha256=rollouts_sha,
    )

    # A labelled pair must be an eligible candidate of that very decision.
    eligible = decisions[decisions["eligible"]]
    allowed = set(
        zip(eligible["decision_id"].astype(int), eligible["robot_id"].astype(str),
            eligible["order_id"].astype(str))
    )
    tested = labels[labels["label"].isin(["pos", "neg"])]
    stray = [
        key for key in zip(tested["decision_id"], tested["robot_id"], tested["order_id"])
        if key not in allowed
    ]
    if stray:
        raise OutcomeLabelError(f"labelled pairs not eligible in decisions.parquet: {stray[:3]}")
    return labels[LABELS_COLUMNS].reset_index(drop=True)


def outcome_examples(
    labels: pd.DataFrame, decisions: pd.DataFrame, tracker: ProvenanceTracker
) -> list[GroundAtomExample]:
    """pos and neg rows as ground examples; abstained and excluded rows give none."""
    times = decisions.groupby("decision_id")["sim_time"].first()
    examples = []
    used = labels[labels["label"].isin(["pos", "neg"])].sort_values(
        ["run_id", "decision_id", "order_id", "branch"], kind="stable"
    )
    seen: dict[tuple, bool] = {}
    for row in used.itertuples(index=False):
        key = (row.run_id, int(row.decision_id), row.robot_id, row.order_id)
        positive = row.label == "pos"
        if key in seen:
            if seen[key] != positive:
                raise OutcomeLabelError(f"pair labelled both ways: {key}")
            continue
        seen[key] = positive
        decision_id = int(row.decision_id)
        examples.append(
            GroundAtomExample(
                decision_atom=tracker.get_or_create_decision(
                    row.run_id, decision_id, times[decision_id]
                ),
                robot_atom=tracker.get_or_create_robot(row.run_id, decision_id, row.robot_id),
                order_atom=tracker.get_or_create_order(row.run_id, decision_id, row.order_id),
                is_positive=positive,
                weight=1,
                origin="outcome_better" if positive else "outcome_worse",
            )
        )
    return examples


def write_labels(labels: pd.DataFrame, path: Path | str) -> str:
    """Write the labels as CSV; returns its SHA-256, the same for the same rollouts."""
    text = labels[LABELS_COLUMNS].to_csv(index=False, float_format="%.9f", lineterminator="\n")
    Path(path).write_text(text, encoding="utf-8")
    return sha256(text.encode()).hexdigest()


def summarize(labels: pd.DataFrame) -> dict[str, Any]:
    """How much supervision the rollouts gave, kept apart by kind."""
    counts = labels["label"].value_counts()
    status = labels["status"].value_counts()
    by_decision = labels.groupby("decision_id")["label"]
    return {
        "branches": int(len(labels)),
        "pos": int(counts.get("pos", 0)),
        "neg": int(counts.get("neg", 0)),
        "usable": int(counts.get("pos", 0) + counts.get("neg", 0)),
        "ambiguous": int(counts.get("abstain", 0)),
        "abstain": int(counts.get("abstain", 0)),
        "excluded": int(counts.get("excluded", 0)),
        "censored": int(status.get("censored", 0)),
        "failed": int(status.get("failed", 0)),
        "probed_decisions": int(labels["decision_id"].nunique()),
        "decisions_with_pos": int(by_decision.apply(lambda s: (s == "pos").any()).sum()),
        "decisions_with_example": int(
            by_decision.apply(lambda s: s.isin(["pos", "neg"]).any()).sum()
        ),
        "reasons": {k: int(v) for k, v in labels["reason"].value_counts().sort_index().items()},
    }
