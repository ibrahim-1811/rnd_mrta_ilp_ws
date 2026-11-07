"""Stratified decision sampling for expensive rollout labels (M6-MP).

Rollouts at 2000 orders cost about a minute per branch, so only a sample of decisions
is probed. Which decisions is decided here, from decision-time columns only (no outcome,
no teacher pick beyond the pool definition), reproducibly, and with the probabilities
written down so no stratum can be quietly over- or under-represented.

Strata of a decision, computed over its eligible pairs (a decision can be in several):

    charging     robots_charging > 0 (the corrected count, 2026-11-06)
    low_battery  a reachable pair refused by battery eligibility, or the decision's
                 lowest eligible energy margin in the run's lowest quartile
    congestion   mean st_conflicts_30s of its pairs in the run's top quartile
                 (features.parquet; empty without it, and recorded as such)
    waiting      queue pressure, pending orders per robot, in the run's top quartile:
                 a decision-time proxy for orders waiting; realized waiting is an
                 outcome and is never read
    normal       none of the above

Draw: quota decisions from each stratum in the fixed order above, uniformly without
replacement among members not already drawn, then quota random-control decisions
uniformly from the whole pool not yet drawn. Each draw uses its own seed, derived from
(seed, run_id, stratum). Quartile cutoffs are taken over the pool of the run itself.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

import numpy as np
import pandas as pd

from ilp_learning.predicates import queue_pressure


STRATA = ("charging", "low_battery", "congestion", "waiting", "normal")
CONTROL = "random_control"


@dataclass(frozen=True)
class SampledDecision:
    decision_id: int
    drawn_from: str
    p_select: float     # quota / members available when this stratum was drawn
    strata: tuple[str, ...]


def decision_strata(decisions: pd.DataFrame, features: pd.DataFrame | None,
                    pool: list[int]) -> pd.DataFrame:
    """One row per pool decision: the aggregates and the stratum flags."""
    rows = decisions[decisions["decision_id"].isin(pool)]
    eligible = rows[rows["eligible"]]
    by = eligible.groupby("decision_id")
    table = pd.DataFrame({
        "robots_charging": rows.groupby("decision_id")["robots_charging"].first(),
        "battery_refused": rows[rows["route_reachable"] & ~rows["battery_eligible"]]
        .groupby("decision_id").size(),
        "min_margin": by["energy_margin_percent"].min(),
        "queue_pressure": by.first().apply(queue_pressure, axis=1),
    }).reindex(sorted(pool))
    table["battery_refused"] = table["battery_refused"].fillna(0).astype(int)
    if features is not None and "st_conflicts_30s" in features.columns:
        merged = eligible.merge(features[["decision_id", "robot_id", "order_id", "st_conflicts_30s"]],
                                on=["decision_id", "robot_id", "order_id"], how="left")
        table["conflicts"] = merged.groupby("decision_id")["st_conflicts_30s"].mean()
    else:
        table["conflicts"] = np.nan

    def top(column):
        values = table[column].dropna()
        return table[column] >= values.quantile(0.75) if len(values) else pd.Series(False, index=table.index)

    table["charging"] = table["robots_charging"] > 0
    margins = table["min_margin"].dropna()
    low_margin = table["min_margin"] <= margins.quantile(0.25) if len(margins) else False
    table["low_battery"] = (table["battery_refused"] > 0) | low_margin
    table["congestion"] = top("conflicts").fillna(False)
    table["waiting"] = top("queue_pressure").fillna(False)
    table["normal"] = ~table[["charging", "low_battery", "congestion", "waiting"]].any(axis=1)
    return table


def _rng(seed: int, run_id: str, stratum: str) -> np.random.Generator:
    digest = hashlib.sha256(f"{seed}:{run_id}:{stratum}".encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def sample_decisions(table: pd.DataFrame, *, quota: int, seed: int, run_id: str) -> list[SampledDecision]:
    """quota per stratum plus quota random controls, deterministic, in draw order."""
    flags = {d: tuple(s for s in STRATA if bool(table.at[d, s])) for d in table.index}
    taken: list[SampledDecision] = []
    used: set[int] = set()
    for stratum in (*STRATA, CONTROL):
        members = sorted(
            d for d in table.index
            if d not in used and (stratum == CONTROL or bool(table.at[d, stratum]))
        )
        if not members:
            continue
        k = min(quota, len(members))
        for index in sorted(_rng(seed, run_id, stratum).choice(len(members), size=k, replace=False)):
            decision = int(members[index])
            used.add(decision)
            taken.append(SampledDecision(decision, stratum, k / len(members), flags[decision]))
    return taken


def coverage(table: pd.DataFrame, sample: list[SampledDecision]) -> dict:
    """Pool size and sampled count per stratum (by membership, not only by draw)."""
    chosen = {s.decision_id for s in sample}
    return {
        stratum: {"pool": int(table[stratum].sum()),
                  "sampled": int(table.loc[sorted(chosen), stratum].sum()) if chosen else 0}
        for stratum in STRATA
    } | {"pool_decisions": int(len(table)), "sampled_decisions": len(chosen)}
