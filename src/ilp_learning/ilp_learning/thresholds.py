"""Threshold fitting and serialization for continuous feature discretization.

Enforces:
1. Training-only fitting: restricts threshold estimation strictly to training seeds (104-110).
2. Deterministic serialization: sorted JSON with embedded SHA-256 content digest.
3. Quantile cutoffs: tercile and median boundaries for spatial and fleet state features.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from ilp_learning.predicates import busy_ratio, cost_regret, queue_pressure
from simulation_env.seed_split import check_induction_purpose, workload_seed_of


DEFAULT_DISCRETIZATIONS = {
    "fleet_load": ("window_orders", [33.3, 66.7]),
    "free_robots": ("free_robots", [33.3, 66.7]),
    "local_density": ("local_density", [50.0]),
    "overlap_ratio": ("overlap_ratio", [50.0]),
    "occupied_now": ("occupied_now", [66.7]),
    "st_conflict_robots": ("st_conflict_robots", [66.7]),
    "st_conflicts_30s": ("st_conflicts_30s", [66.7]),
}


# Cutoffs of the extended vocabulary, read from add_load_columns.
EXTENDED_DISCRETIZATIONS = {
    "cost_regret": ("cost_regret", [25.0]),
    "busy_ratio": ("busy_ratio", [75.0]),
    "queue_pressure": ("queue_pressure", [75.0]),
}


# The cutoffs the decision table alone can fit. Tier B cutoffs need features.parquet.
DECISION_DISCRETIZATIONS = {
    name: DEFAULT_DISCRETIZATIONS[name] for name in ("fleet_load", "free_robots")
}


def add_load_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """The columns the extended cutoffs are fitted on, by the online definitions."""
    best = frame.groupby(["decision_id", "robot_id"])["c_static_m"].transform("min")
    return frame.assign(
        cost_regret=[cost_regret(c, b) for c, b in zip(frame["c_static_m"], best)],
        busy_ratio=[busy_ratio(row) for row in frame.itertuples()],
        queue_pressure=[queue_pressure(row) for row in frame.itertuples()],
    )


class ThresholdModel:
    """Stores quantile cutoffs and produces symbolic discrete category labels."""

    def __init__(self, cutoffs: Mapping[str, Sequence[float]], metadata: Mapping[str, Any] | None = None) -> None:
        self.cutoffs = {k: list(float(v) for v in vals) for k, vals in cutoffs.items()}
        self.metadata = dict(metadata or {})

    def categorize(self, feature_name: str, value: float) -> str:
        """Return symbolic category, e.g. 'tier1', 'tier2', 'tier3' (or 'low', 'high')."""
        if feature_name not in self.cutoffs:
            raise KeyError(f"Feature '{feature_name}' has no fitted thresholds")
        
        boundaries = self.cutoffs[feature_name]
        if len(boundaries) == 1:
            return "high" if value >= boundaries[0] else "low"
        elif len(boundaries) == 2:
            if value < boundaries[0]:
                return "low"
            elif value < boundaries[1]:
                return "medium"
            else:
                return "high"
        else:
            for i, b in enumerate(boundaries):
                if value < b:
                    return f"tier{i+1}"
            return f"tier{len(boundaries)+1}"

    def to_dict(self) -> dict[str, Any]:
        data = {
            "metadata": self.metadata,
            "cutoffs": self.cutoffs,
        }
        content_bytes = json.dumps(data, sort_keys=True).encode("utf-8")
        data["sha256"] = hashlib.sha256(content_bytes).hexdigest()
        return data

    @property
    def sha256(self) -> str:
        return self.to_dict()["sha256"]

    def save_json(self, path: Path | str) -> None:
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, sort_keys=True)

    @classmethod
    def load_json(cls, path: Path | str) -> ThresholdModel:
        in_path = Path(path)
        with open(in_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cutoffs = data.get("cutoffs", {})
        metadata = data.get("metadata", {})
        return cls(cutoffs=cutoffs, metadata=metadata)


def induction_seeds_of(manifests: Sequence[Mapping[str, Any]]) -> list[int]:
    """Every seed the runs used, read from their manifests rather than declared."""
    seeds: set[int] = set()
    for manifest in manifests:
        seeds.add(int(manifest["seed"]))
        seeds.add(int(workload_seed_of(manifest["orders_path"])))
    return sorted(seeds)


def fit_thresholds_from_frames(
    training_frames: Sequence[pd.DataFrame],
    seeds_used: Sequence[int],
    spec: Mapping[str, tuple[str, Sequence[float]]] | None = None,
) -> ThresholdModel:
    """Fit quantile cutoffs from training DataFrames, enforcing seed role discipline."""
    check_induction_purpose(seeds_used)
    
    active_spec = spec or DEFAULT_DISCRETIZATIONS
    combined_df = pd.concat(training_frames, ignore_index=True)
    
    cutoffs: dict[str, list[float]] = {}
    for target_name, (col_name, percentiles) in active_spec.items():
        if col_name not in combined_df.columns:
            raise ValueError(f"cannot fit '{target_name}': column '{col_name}' is absent")
        vals = combined_df[col_name].dropna().to_numpy()
        if len(vals) == 0:
            raise ValueError(f"cannot fit '{target_name}': column '{col_name}' has no values")
        cutoffs[target_name] = [float(np.percentile(vals, p)) for p in percentiles]

    metadata = {
        "seeds_used": sorted(list(seeds_used)),
        "n_samples": len(combined_df),
    }
    return ThresholdModel(cutoffs=cutoffs, metadata=metadata)
