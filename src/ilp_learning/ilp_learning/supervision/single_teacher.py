"""Single-teacher supervision builder for M3 (nearest_astar) and M4 (strong baselines)."""

from __future__ import annotations

from pathlib import Path
import pandas as pd

from ilp_learning.atoms import ProvenanceTracker
from ilp_learning.sampler import GroundAtomExample, StratifiedExampleSampler, write_exs_pl
from simulation_env.seed_split import check_induction_purpose


def build_single_teacher_supervision(
    run_id: str,
    decisions_df: pd.DataFrame,
    tracker: ProvenanceTracker,
    k_neg: int = 3,
    seed: int = 42,
    workload_seed: int | None = None,
    out_path: Path | str | None = None,
) -> list[GroundAtomExample]:
    """Generate ground positive and negative assignment examples from teacher selections."""
    if workload_seed is not None:
        check_induction_purpose([workload_seed])

    sampler = StratifiedExampleSampler(k_neg=k_neg, seed=seed)
    examples = sampler.sample_examples_from_decisions(
        run_id=run_id,
        decisions_df=decisions_df,
        tracker=tracker,
        selected_col="selected",
    )
    if out_path is not None:
        write_exs_pl(examples, out_path)
    return examples

