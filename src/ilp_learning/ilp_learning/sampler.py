"""Deterministic stratified decision and negative example sampler.

Enforces:
1. Choice-bearing decisions: filters out non-choice 1-robot x 1-order decisions.
2. Balanced negative sampling: k_neg = 3 (half hard negatives, half uniform negatives).
3. Disjointness invariant: ensures E+ ∩ E- == ∅.
4. Determinism: seeded RNG with stable sorting.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from ilp_learning.atoms import ProvenanceTracker


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

    def __init__(self, k_neg: int = 3, seed: int = 42) -> None:
        self.k_neg = k_neg
        self.seed = seed
        self.rng = np.random.default_rng(seed)

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

        # Hard eligibility gate
        if "eligible" in decisions_df.columns:
            df = decisions_df[decisions_df["eligible"] == True].copy()  # noqa: E712
        else:
            df = decisions_df.copy()

        examples: list[GroundAtomExample] = []
        grouped = df.groupby("decision_id")

        for dec_id, g in grouped:
            # Check if this is a choice-bearing decision
            n_pairs = len(g)
            if n_pairs < 2:
                continue

            tick = g["t"].iloc[0] if "t" in g.columns else 0.0
            d_atom = tracker.get_or_create_decision(run_id, int(dec_id), tick)

            # Selected (positive) pairs
            pos_rows = g[g[selected_col]]
            if len(pos_rows) == 0:
                continue

            for _, pos_row in pos_rows.iterrows():
                r_id = pos_row["robot_id"]
                pos_o_id = pos_row["order_id"]

                r_atom = tracker.get_or_create_robot(run_id, int(dec_id), str(r_id))
                pos_o_atom = tracker.get_or_create_order(run_id, int(dec_id), str(pos_o_id))

                examples.append(
                    GroundAtomExample(
                        decision_atom=d_atom,
                        robot_atom=r_atom,
                        order_atom=pos_o_atom,
                        is_positive=True,
                        weight=1,
                        origin="teacher_selected",
                    )
                )

                # Available alternative orders for this same robot
                r_group = g[(g["robot_id"] == r_id) & (g["order_id"] != pos_o_id)]
                if len(r_group) == 0:
                    continue

                # Sort by static cost to identify hard negatives
                if "c_static_m" in r_group.columns:
                    sorted_alts = r_group.sort_values(by="c_static_m")
                elif "est_finish_s" in r_group.columns:
                    sorted_alts = r_group.sort_values(by="est_finish_s")
                else:
                    sorted_alts = r_group

                alt_order_ids = list(sorted_alts["order_id"])

                # Select up to k_neg alternatives: half hard, half uniform
                n_hard = (self.k_neg + 1) // 2
                hard_negs = alt_order_ids[:n_hard]
                remaining = alt_order_ids[n_hard:]

                n_uniform = self.k_neg - len(hard_negs)
                uniform_negs: list[str] = []
                if n_uniform > 0 and len(remaining) > 0:
                    sampled_idx = self.rng.choice(len(remaining), size=min(n_uniform, len(remaining)), replace=False)
                    uniform_negs = [remaining[i] for i in sampled_idx]

                selected_negs = hard_negs + uniform_negs

                for neg_o_id in selected_negs:
                    neg_o_atom = tracker.get_or_create_order(run_id, int(dec_id), str(neg_o_id))
                    examples.append(
                        GroundAtomExample(
                            decision_atom=d_atom,
                            robot_atom=r_atom,
                            order_atom=neg_o_atom,
                            is_positive=False,
                            weight=1,
                            origin="teacher_unselected",
                        )
                    )

        # Enforce disjointness invariant E+ ∩ E- == ∅
        pos_set = {(e.decision_atom, e.robot_atom, e.order_atom) for e in examples if e.is_positive}
        neg_clean = [
            e for e in examples if e.is_positive or (e.decision_atom, e.robot_atom, e.order_atom) not in pos_set
        ]
        return neg_clean


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
