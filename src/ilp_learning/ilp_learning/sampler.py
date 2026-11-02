"""Deterministic stratified decision and negative example sampler.

Enforces:
0. Stratified decisions: up to N_dec choice-bearing decisions, spread evenly over
   (fleet_size, dispatch_quantum_s, seed, sim_time tercile) (M2 spec 5.5 item 2).
1. Choice-bearing decisions: a decision with no pair facing an alternative is dropped,
   and a forced pair (no alternative robot or order) is no example (M2 spec 4.3, 5.5).
2. Balanced negative sampling: m- = min(|E-|, k_neg * m+) per decision over all
   unselected eligible pairs, ceil(m-/2) hard and the rest uniform (M2 spec 5.5).
3. Disjointness invariant: E+ and E- partition the choice-bearing pairs.
4. Determinism: one RNG per decision, so no draw depends on what was sampled before.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from ilp_learning.atoms import ProvenanceTracker


DEFAULT_CORPUS_SEED = 20261029  # pinned, M2 spec 5.5
DEFAULT_DECISION_CAP = 800      # N_dec, M2 spec 5.5 item 4


def choice_pairs(eligible_rows: pd.DataFrame) -> pd.DataFrame:
    """Eligible pairs of one decision that faced an alternative robot or order."""
    orders_of_robot = eligible_rows.groupby("robot_id")["order_id"].transform("size")
    robots_of_order = eligible_rows.groupby("order_id")["robot_id"].transform("size")
    return eligible_rows[(orders_of_robot >= 2) | (robots_of_order >= 2)]


def labelled_decisions(decisions_df: pd.DataFrame, selected_col: str = "selected") -> list[int]:
    """Decisions that can yield examples: a choice pair the teacher selected."""
    eligible = decisions_df[decisions_df["eligible"]]
    return [
        int(decision_id)
        for decision_id, rows in eligible.groupby("decision_id")
        if choice_pairs(rows)[selected_col].any()
    ]


def stratified_decisions(
    strata: Mapping[tuple, Sequence[tuple[str, int]]],
    cap: int = DEFAULT_DECISION_CAP,
    seed: int = DEFAULT_CORPUS_SEED,
) -> list[tuple[str, int]]:
    """Up to cap (run_id, decision_id) keys, one per stratum in turn.

    A stratum that runs out leaves its share to the others. Each stratum and
    the turn order draw from the corpus seed and the stratum's key alone, so
    adding a run does not reshuffle what was picked from the others.
    """

    def digest(key: tuple) -> bytes:
        return hashlib.sha256(f"{seed}:{key}".encode()).digest()

    shuffled = []
    for key in sorted(strata, key=digest):
        rng = np.random.default_rng(np.random.PCG64(int.from_bytes(digest(key)[:8], "big")))
        members = sorted(strata[key])
        shuffled.append([members[i] for i in rng.permutation(len(members))])

    picked: list[tuple[str, int]] = []
    for turn in range(max(map(len, shuffled), default=0)):
        for members in shuffled:
            if len(picked) == cap:
                return picked
            if turn < len(members):
                picked.append(members[turn])
    return picked


@dataclass(frozen=True)
class GroundAtomExample:
    decision_atom: str
    robot_atom: str
    order_atom: str
    is_positive: bool
    weight: int = 1
    origin: str = "teacher"

    def to_prolog(self) -> str:
        wrapper = "pos" if self.is_positive else "neg"
        return f"{wrapper}(assign({self.decision_atom},{self.robot_atom},{self.order_atom}))."


class StratifiedExampleSampler:
    """Samples positive and negative assignment examples from decision-time logs."""

    def __init__(self, k_neg: int = 3, seed: int = DEFAULT_CORPUS_SEED) -> None:
        self.k_neg = k_neg
        self.seed = seed

    def _decision_rng(self, run_id: str, decision_id: int) -> np.random.Generator:
        digest = hashlib.sha256(f"{self.seed}:{run_id}:{decision_id}".encode()).digest()
        return np.random.default_rng(int.from_bytes(digest[:8], "big"))

    def _sample_negatives(
        self, run_id: str, decision_id: int, positives: pd.DataFrame, negatives: pd.DataFrame
    ) -> pd.DataFrame:
        """Pick m- negatives: closest competitors of the matched pairs, then uniform."""
        canonical = negatives.sort_values(by=["c_static_m", "robot_id", "order_id"])
        m_neg = min(len(canonical), self.k_neg * len(positives))

        competes = canonical["robot_id"].isin(positives["robot_id"]) | canonical["order_id"].isin(
            positives["order_id"]
        )
        hard = canonical[competes].head(math.ceil(m_neg / 2))
        remaining = canonical.drop(index=hard.index)

        rng = self._decision_rng(run_id, decision_id)
        picked = rng.choice(len(remaining), size=m_neg - len(hard), replace=False)
        return pd.concat([hard, remaining.iloc[np.sort(picked)]])

    def sample_examples_from_decisions(
        self,
        run_id: str,
        decisions_df: pd.DataFrame,
        tracker: ProvenanceTracker,
        selected_col: str = "selected",
    ) -> list[GroundAtomExample]:
        """Extract positive teacher assignments and sampled negative alternatives."""
        if selected_col not in decisions_df.columns:
            raise KeyError(f"Column '{selected_col}' not found in decisions DataFrame")

        df = decisions_df[decisions_df["eligible"]]

        examples: list[GroundAtomExample] = []

        for dec_id, g in df.groupby("decision_id"):
            contested = choice_pairs(g)

            positives = contested[contested[selected_col]]
            if len(positives) == 0:
                continue

            decision_id = int(dec_id)
            d_atom = tracker.get_or_create_decision(run_id, decision_id, g["sim_time"].iloc[0])
            # An abstained pair (M5) is neither: the teachers disagreed about it.
            unselected = ~contested[selected_col]
            if "abstain" in contested.columns:
                unselected &= ~contested["abstain"]
            negatives = self._sample_negatives(
                run_id, decision_id, positives, contested[unselected]
            )

            for rows, is_positive, origin in (
                (positives, True, "teacher_selected"),
                (negatives, False, "teacher_unselected"),
            ):
                for r_id, o_id in zip(rows["robot_id"], rows["order_id"]):
                    examples.append(
                        GroundAtomExample(
                            decision_atom=d_atom,
                            robot_atom=tracker.get_or_create_robot(run_id, decision_id, str(r_id)),
                            order_atom=tracker.get_or_create_order(run_id, decision_id, str(o_id)),
                            is_positive=is_positive,
                            weight=1,
                            origin=origin,
                        )
                    )

        return examples


def write_exs_pl(examples: Sequence[GroundAtomExample], path: str | Path) -> None:
    """Write ground example atoms to exs.pl, grouping pos/1 and neg/1."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pos_exs = [e for e in examples if e.is_positive]
    neg_exs = [e for e in examples if not e.is_positive]
    with open(out_path, "w", encoding="utf-8") as f:
        for ex in pos_exs:
            f.write(ex.to_prolog() + "\n")
        for ex in neg_exs:
            f.write(ex.to_prolog() + "\n")
