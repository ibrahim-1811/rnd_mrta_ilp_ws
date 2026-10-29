"""Declarative protocol for future M6 Outcome-Aware supervision.

CONTRACT ONLY — NO LABEL GENERATION CODE IMPLEMENTED.
Per DECISIONS.md (2026-10-29), outcome parameters are frozen at:
- Horizon H = 120 s
- Objective J = pivot_mission_s + lambda * waiting_in_h_s
- Weight lambda = 0.50 (sensitivity 0.25)
- Margin delta = 2.0 s (sensitivity 5.0 s)
- Censoring rule: incomplete rollout branches or failed rollouts never generate labels.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable
import pandas as pd

from ilp_learning.sampler import GroundAtomExample


@runtime_checkable
class OutcomeSupervisionProvider(Protocol):
    """Protocol for outcome-aware supervision generation (M6 only)."""

    def generate_outcome_examples(
        self,
        decisions_df: pd.DataFrame,
        rollouts_df: pd.DataFrame,
        horizon_s: float = 120.0,
        lambda_wait: float = 0.50,
        delta_margin_s: float = 2.0,
    ) -> Sequence[GroundAtomExample]:
        """Generate outcome-supervised examples. Must raise NotImplementedError until M6."""
        ...

