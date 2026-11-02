"""Background knowledge (bk.pl) compiler enforcing State_t decision-time firewall.

Tier A facts come from ilp_learning.predicates, the implementation rule execution
also uses, so bk.pl and an executed rule read one set of definitions.

Enforces:
1. Strict State_t firewall: an explicit permitted-column schema, so outcomes, rollouts,
   and the behaviour policy's own selection cannot reach a fact.
2. Hard eligibility gate: only eligible candidate pairs enter candidate_pair/3.
3. Pure symbolic Datalog: zero raw floating point literals (rejected by Clingo 5.8.2).
4. Relative ranking & comparison predicates: Tier A1 ranks, Tier A2 order comparisons,
   and Tier B M1-screened congestion predicates. Ranks are tie-inclusive and comparisons
   strict, so no fact depends on the arbitrary order of equal values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from ilp_learning.atoms import ProvenanceTracker
from ilp_learning.predicates import (
    TIE_TOLERANCE,
    EXTENDED_SIGNATURES,
    LOAD_FIELDS,
    TIER_A_SIGNATURES,
    TIER_B_FEATURES,
    TIER_B_SIGNATURES,
    TIER_B_SOURCE,
    TIER_B_THRESHOLDED,
    DecisionFacts,
    CandidatePair,
    TierAFacts,
)
from ilp_learning.thresholds import ThresholdModel


# Every column the builder may read: what a run knows at the decision, from
# decisions.parquet and features.parquet (M2 spec 3.1). A permitted list, so a
# new or renamed outcome column is refused instead of slipping past a blocklist.
ALLOWED_DECISION_COLUMNS = frozenset({
    "run_id", "decision_id", "sim_time", "sim_time_exact",
    "window", "window_width", "dispatch_quantum_s",
    "free_robots", "window_orders", "choice_bearing",
    "pending_orders", "robots_busy", "robots_charging",
    "robot_id", "order_id", "robot_x", "robot_y",
    "robot_soc_percent", "robot_soc_exact",
    "fifo_position", "order_shelf_count", "order_dropoff_id",
    "c_static_m", "route_reachable", "route_cells",
    "predicted_travel_energy_percent", "predicted_service_energy_percent",
    "predicted_charger_distance_m", "predicted_charger_energy_percent",
    "reserve_percent", "required_soc_percent", "required_soc_exact",
    "energy_margin_percent", "energy_margin_exact",
    "nearest_charger_id", "battery_eligible", "eligible",
    "est_finish_s",
})
ALLOWED_FEATURE_COLUMNS = frozenset({
    "decision_id", "robot_id", "order_id", "route_cells",
    "overlap_cells", "overlap_ratio", "occupied_now",
    "st_conflicts", "st_conflict_robots", "st_conflicts_30s",
    "dropoff_load", "shelf_load", "local_density",
    "robot_soc_percent",
})
# Recorded at the decision, but they are the behaviour policy's own action and
# identity: legal input to drop, never to read, or the teacher leaks into bk.pl.
POLICY_COLUMNS = frozenset({"selected", "strategy", "abstain"})

REQUIRED_DECISION_COLUMNS = (
    "decision_id", "robot_id", "order_id", "eligible", "sim_time", "c_static_m", "fifo_position",
)


def _project_decision_time(df: pd.DataFrame, allowed: frozenset[str]) -> pd.DataFrame:
    """Keep the permitted columns; refuse any column not known to be decision-time."""
    unknown = set(df.columns) - allowed - POLICY_COLUMNS
    if unknown:
        raise ValueError(
            f"State_t firewall violation: input contains columns outside the permitted schema "
            f"{sorted(unknown)}. Background knowledge must be constructed strictly from "
            "decision-time state."
        )
    return df[[c for c in df.columns if c in allowed]]


def derive_est_finish_s(df: pd.DataFrame, run_manifest: Mapping[str, Any] | None) -> pd.Series:
    """Nominal mission time, the offline form of DecisionTiming.finish_s.

    Route length over speed, one pick per shelf, one drop-off, and no waiting:
    decisions.parquet does not record this estimate, so it is rebuilt from what
    that decision knew rather than being treated as absent.
    """
    needed = ("speed_mps", "shelf_pick_s", "dropoff_s")
    absent = [k for k in needed if run_manifest is None or k not in run_manifest]
    if absent or "order_shelf_count" not in df.columns:
        raise ValueError(
            f"cannot derive est_finish_s: manifest lacks {absent}, "
            f"order_shelf_count present: {'order_shelf_count' in df.columns}"
        )
    return (
        df["c_static_m"] / run_manifest["speed_mps"]
        + df["order_shelf_count"] * run_manifest["shelf_pick_s"]
        + run_manifest["dropoff_s"]
    )


def candidate_pairs(rows: pd.DataFrame) -> list[CandidatePair]:
    """One decision's rows as the pair set the predicates are defined over."""
    columns = ["robot_id", "order_id", "c_static_m", "est_finish_s", "fifo_position"]
    features = [c for c in (*TIER_B_FEATURES, *LOAD_FIELDS) if c in rows.columns]
    return [
        CandidatePair(*row[:5], **dict(zip(features, row[5:])))
        for row in rows[columns + features].itertuples(index=False, name=None)
    ]


class BKBuilder:
    """Builds pure Datalog bk.pl facts from decision-time tables."""

    def __init__(
        self,
        thresholds: ThresholdModel | None = None,
        tracker: ProvenanceTracker | None = None,
        extended: bool = False,
    ) -> None:
        self.thresholds = thresholds
        self.extended = extended
        self.tracker = tracker or ProvenanceTracker()
        self.facts: list[str] = []

    def add_fact(self, predicate: str, *args: str) -> None:
        args_str = ",".join(args)
        self.facts.append(f"{predicate}({args_str}).")

    @staticmethod
    def _lowest(rows: pd.DataFrame, column: str, entity: str) -> list[str]:
        """Every entity at the column's minimum: a tie is fact, not a choice to make."""
        known = rows[rows[column].notna()]
        return list(known.loc[known[column] <= known[column].min() + TIE_TOLERANCE, entity])

    def _add_tier_a_facts(
        self,
        d_atom: str,
        r_atoms: Mapping[str, str],
        o_atoms: Mapping[str, str],
        rows: pd.DataFrame,
    ) -> None:
        """Emit the shared Tier A relations of one decision as ground facts."""
        atoms = {"robot": r_atoms, "order": o_atoms}
        facts = TierAFacts(candidate_pairs(rows))
        for name, kinds in TIER_A_SIGNATURES.items():
            for values in facts.relation(name):
                self.add_fact(
                    name, d_atom, *(atoms[kind][v] for kind, v in zip(kinds, values))
                )

    def _add_tier_b_facts(
        self,
        d_atom: str,
        r_atoms: Mapping[str, str],
        o_atoms: Mapping[str, str],
        rows: pd.DataFrame,
    ) -> None:
        """Emit the shared Tier B relations a decision has data for: M1-screened congestion."""
        atoms = {"robot": r_atoms, "order": o_atoms}
        facts = DecisionFacts(candidate_pairs(rows), self.thresholds)
        for name, kinds in TIER_B_SIGNATURES.items():
            source = TIER_B_SOURCE[name]
            if source not in rows.columns or rows[source].isna().all():
                continue
            if name in TIER_B_THRESHOLDED and self.thresholds is None:
                continue
            for values in facts.relation(name):
                self.add_fact(
                    name, d_atom, *(atoms[kind][v] for kind, v in zip(kinds, values))
                )

    def _add_extended_facts(
        self,
        d_atom: str,
        r_atoms: Mapping[str, str],
        o_atoms: Mapping[str, str],
        rows: pd.DataFrame,
    ) -> None:
        """Emit the shared extended relations of one decision as ground facts."""
        atoms = {"robot": r_atoms, "order": o_atoms}
        facts = DecisionFacts(candidate_pairs(rows), self.thresholds)
        for name, kinds in EXTENDED_SIGNATURES.items():
            for values in facts.relation(name):
                self.add_fact(
                    name, d_atom, *(atoms[kind][v] for kind, v in zip(kinds, values))
                )

    def build_from_frames(
        self,
        run_id: str,
        decisions_df: pd.DataFrame,
        features_df: pd.DataFrame | None = None,
        run_manifest: Mapping[str, Any] | None = None,
    ) -> list[str]:
        """Compile a run's decision-time state into Datalog facts."""
        decisions_df = _project_decision_time(decisions_df, ALLOWED_DECISION_COLUMNS)
        missing = [c for c in REQUIRED_DECISION_COLUMNS if c not in decisions_df.columns]
        if missing:
            raise ValueError(f"decision table lacks required columns {missing}")
        if "est_finish_s" not in decisions_df.columns:
            decisions_df = decisions_df.assign(
                est_finish_s=derive_est_finish_s(decisions_df, run_manifest)
            )
        if features_df is not None:
            features_df = _project_decision_time(features_df, ALLOWED_FEATURE_COLUMNS)

        # Merge decision-time features if present
        if features_df is not None:
            # Drop overlapping columns to avoid suffix collisions
            feat_cols = [c for c in features_df.columns if c not in decisions_df.columns or c in ["decision_id", "robot_id", "order_id"]]
            df = decisions_df.merge(
                features_df[feat_cols],
                on=["decision_id", "robot_id", "order_id"],
                how="left",
            )
        else:
            df = decisions_df.copy()

        # Hard eligibility gate: drop ineligible rows
        df = df[df["eligible"]].copy()

        grouped = df.groupby("decision_id")

        for dec_id, g in grouped:
            d_atom = self.tracker.get_or_create_decision(run_id, int(dec_id), g["sim_time"].iloc[0])
            self.add_fact("decision", d_atom)

            # Global decision-level properties
            if self.thresholds and "window_orders" in g.columns:
                load_cat = self.thresholds.categorize("fleet_load", float(g["window_orders"].iloc[0]))
                self.add_fact("fleet_load", d_atom, load_cat)

            if self.thresholds and "free_robots" in g.columns:
                rob_cat = self.thresholds.categorize("free_robots", float(g["free_robots"].iloc[0]))
                self.add_fact("free_robots_bin", d_atom, rob_cat)

            # Robots and Orders present in this decision
            robots = g["robot_id"].unique()
            orders = g["order_id"].unique()

            r_atoms: dict[str, str] = {}
            for r_id in robots:
                r_atoms[r_id] = self.tracker.get_or_create_robot(run_id, int(dec_id), str(r_id))

            o_atoms: dict[str, str] = {}
            for o_id in orders:
                o_atoms[o_id] = self.tracker.get_or_create_order(run_id, int(dec_id), str(o_id))

            self._add_tier_a_facts(d_atom, r_atoms, o_atoms, g)

            self._add_tier_b_facts(d_atom, r_atoms, o_atoms, g)
            if self.extended:
                self._add_extended_facts(d_atom, r_atoms, o_atoms, g)

        return self.facts

    def write_bk_pl(self, path: Path | str) -> None:
        """Write sorted, deduplicated Prolog facts to bk.pl."""
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        unique_facts = sorted(list(set(self.facts)))
        with open(out_path, "w", encoding="utf-8") as f:
            for fact in unique_facts:
                f.write(fact + "\n")

