"""Outcome-corrected demonstrations (group D, decision of 2026-11-07).

The plain outcome labels ask "is this pair the single best of about five?". About one
candidate in five wins, no decision-time fact picks the winner more than half the time,
and Popper's noisy search rightly keeps no clause. These labels start from the
teacher's own picks instead, which are learnable, and change them only where the
teacher's run continued from the same state shows the teacher's own pair clearly lost:

    teacher's branch within delta of the best, or no decisive best   keep the teacher's picks
    teacher's branch worse by at least delta ("neg"), a "pos" best    correct the decision:
        the teacher's pair (and its swap partner)  -> negative ("outcome_demoted")
        the best branch's pairs                    -> positive ("outcome_promoted")
        another teacher pick sharing a robot or an
        order with a promoted pair                 -> neither: one robot, one order

Every other pick at the decision stays a teacher positive and the negatives are sampled
as for any demonstration. The verdicts are those of outcome_common.common_labels with
the run's own strategy continuing (continuation "behaviour"), so the same delta and
conservative mode decide; nothing about the realized future enters a decision-time
fact, only which pair is labelled.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Sequence

import pandas as pd

from ilp_learning.atoms import ProvenanceTracker
from ilp_learning.sampler import DEFAULT_CORPUS_SEED, GroundAtomExample, StratifiedExampleSampler

BEHAVIOUR_SOURCE = "behaviour"
PROMOTED = "outcome_promoted"
DEMOTED = "outcome_demoted"


class CorrectionError(ValueError):
    """The rollout labels and the recorded decisions do not describe the same picks."""


@dataclass(frozen=True)
class Correction:
    run_id: str
    decision_id: int
    demoted: tuple[tuple[str, str], ...]
    promoted: tuple[tuple[str, str], ...]
    margin_s: float
    best_j_w_s: float


def _pairs(atoms: str) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(tuple(atom.split(":", 1)) for atom in str(atoms).split()))


def _is_behaviour(sources: Any) -> bool:
    return BEHAVIOUR_SOURCE in str(sources).split("+")


def corrections(labels: pd.DataFrame) -> list[Correction]:
    """Decisions whose teacher branch lost by delta to a decisive best branch."""
    found = []
    for (run_id, decision_id), d in labels.groupby(["run_id", "decision_id"], sort=True):
        teacher = d[d["candidate_sources"].map(_is_behaviour)]
        best = d[d["label"] == "pos"]
        if teacher.empty or best.empty or teacher["label"].iloc[0] != "neg":
            continue
        promoted = _pairs(best["atoms"].iloc[0])
        found.append(Correction(
            run_id=str(run_id), decision_id=int(decision_id),
            demoted=tuple(p for p in _pairs(teacher["atoms"].iloc[0]) if p not in promoted),
            promoted=promoted,
            margin_s=float(teacher["margin_s"].iloc[0]),
            best_j_w_s=float(best["j_w_s"].iloc[0]),
        ))
    return found


def corrected_frame(rows: pd.DataFrame, fixes: Sequence[Correction]) -> pd.DataFrame:
    """The decision rows with 'selected' corrected and displaced picks set to abstain."""
    frame = rows.copy()
    if "abstain" not in frame.columns:
        frame["abstain"] = False
    robot, order = frame["robot_id"].astype(str), frame["order_id"].astype(str)
    for fix in fixes:
        at = frame["decision_id"] == fix.decision_id
        for r, o in fix.demoted:
            hit = at & (robot == r) & (order == o)
            if not (frame.loc[hit, "selected"].any()):
                raise CorrectionError(
                    f"decision {fix.decision_id}: the teacher branch's pair {r}:{o} is not a recorded pick")
            frame.loc[hit, "selected"] = False
        promoted = pd.Series(False, index=frame.index)
        for r, o in fix.promoted:
            hit = at & (robot == r) & (order == o) & frame["eligible"]
            if not hit.any():
                raise CorrectionError(f"decision {fix.decision_id}: promoted pair {r}:{o} is not eligible")
            promoted |= hit
        taken_r = {r for r, _ in fix.promoted}
        taken_o = {o for _, o in fix.promoted}
        clash = at & frame["selected"] & ~promoted & (robot.isin(taken_r) | order.isin(taken_o))
        frame.loc[clash, "selected"] = False
        frame.loc[clash, "abstain"] = True
        frame.loc[promoted, "selected"] = True
        frame.loc[promoted, "abstain"] = False
    return frame


def corrected_examples(
    run_id: str,
    rows: pd.DataFrame,
    fixes: Sequence[Correction],
    tracker: ProvenanceTracker,
    *,
    k_neg: int = 3,
    seed: int = DEFAULT_CORPUS_SEED,
) -> list[GroundAtomExample]:
    """Teacher demonstrations of these decisions, with the corrections applied."""
    fixes = [f for f in fixes if f.run_id == run_id]
    frame = corrected_frame(rows, fixes)
    examples = StratifiedExampleSampler(k_neg=k_neg, seed=seed).sample_examples_from_decisions(
        run_id=run_id, decisions_df=frame, tracker=tracker
    )

    def key(decision_id, r, o):
        return (tracker.get_or_create_decision(run_id, decision_id),
                tracker.get_or_create_robot(run_id, decision_id, r),
                tracker.get_or_create_order(run_id, decision_id, o))

    promoted = {key(f.decision_id, r, o) for f in fixes for r, o in f.promoted}
    demoted = {key(f.decision_id, r, o) for f in fixes for r, o in f.demoted}
    out, negatives = [], set()
    for e in examples:
        k = (e.decision_atom, e.robot_atom, e.order_atom)
        if e.is_positive and k in promoted:
            e = replace(e, origin=PROMOTED)
        elif not e.is_positive and k in demoted:
            e = replace(e, origin=DEMOTED)
        if not e.is_positive:
            negatives.add(k)
        out.append(e)
    for k in sorted(demoted - negatives):
        out.append(GroundAtomExample(*k, is_positive=False, weight=1, origin=DEMOTED))
    return out


def summarize_corrections(fixes: Sequence[Correction], probed_decisions: int) -> dict[str, Any]:
    margins = pd.Series([f.margin_s for f in fixes], dtype=float)
    relative = pd.Series([f.margin_s / f.best_j_w_s for f in fixes if f.best_j_w_s > 0], dtype=float)
    return {
        "probed_decisions": int(probed_decisions),
        "corrected_decisions": len(fixes),
        "promoted_pairs": sum(len(f.promoted) for f in fixes),
        "demoted_pairs": sum(len(f.demoted) for f in fixes),
        "margin_s_quantiles": {q: round(float(margins.quantile(q)), 3) for q in (0.1, 0.5, 0.9)}
        if len(margins) else {},
        "margin_relative_median": round(float(relative.median()), 5) if len(relative) else None,
    }
