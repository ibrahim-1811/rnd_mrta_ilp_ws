"""Background knowledge (bk.pl) compiler enforcing State_t decision-time firewall.

Enforces:
1. Strict State_t firewall: zero access to outcomes, rollouts, or future ticks.
2. Hard eligibility gate: only eligible candidate pairs enter candidate_pair/3.
3. Pure symbolic Datalog: zero raw floating point literals (rejected by Clingo 5.8.2).
4. Relative ranking & comparison predicates: Tier A1 ranks, Tier A2 order comparisons,
   and Tier B M1-screened congestion predicates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from ilp_learning.atoms import ProvenanceTracker
from ilp_learning.thresholds import ThresholdModel


FORBIDDEN_OUTCOME_COLUMNS = {
    "mission_s",
    "waiting_s",
    "realized_distance_m",
    "pivot_mission_s",
    "waiting_in_h_s",
    "completed_in_h",
    "completed_at",
    "retreat_finished_at",
    "residual_s",
    "window_flow_s",
}


def _verify_firewall(columns: Sequence[str]) -> None:
    found = set(columns).intersection(FORBIDDEN_OUTCOME_COLUMNS)
    if found:
        raise ValueError(
            f"State_t firewall violation: input contains outcome columns {sorted(list(found))}. "
            "Background knowledge must be constructed strictly from decision-time state."
        )


class BKBuilder:
    """Builds pure Datalog bk.pl facts from decision-time tables."""

    def __init__(
        self,
        thresholds: ThresholdModel | None = None,
        tracker: ProvenanceTracker | None = None,
    ) -> None:
        self.thresholds = thresholds
        self.tracker = tracker or ProvenanceTracker()
        self.facts: list[str] = []

    def add_fact(self, predicate: str, *args: str) -> None:
        args_str = ",".join(args)
        self.facts.append(f"{predicate}({args_str}).")

    def build_from_frames(
        self,
        run_id: str,
        decisions_df: pd.DataFrame,
        features_df: pd.DataFrame | None = None,
        run_manifest: Mapping[str, Any] | None = None,
    ) -> list[str]:
        """Compile a run's decision-time state into Datalog facts."""
        _verify_firewall(decisions_df.columns)
        if features_df is not None:
            _verify_firewall(features_df.columns)

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
        if "eligible" in df.columns:
            df = df[df["eligible"] == True].copy()  # noqa: E712

        grouped = df.groupby("decision_id")

        for dec_id, g in grouped:
            tick = g["t"].iloc[0] if "t" in g.columns else 0.0
            d_atom = self.tracker.get_or_create_decision(run_id, int(dec_id), tick)
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
                self.add_fact("candidate_robot", d_atom, r_atoms[r_id])

                # Robot-level local density (Tier 2 survivor)
                if self.thresholds and "local_density" in g.columns:
                    r_rows = g[g["robot_id"] == r_id]
                    if len(r_rows) > 0 and pd.notna(r_rows["local_density"].iloc[0]):
                        dens = float(r_rows["local_density"].iloc[0])
                        if self.thresholds.categorize("local_density", dens) == "low":
                            self.add_fact("low_local_density", d_atom, r_atoms[r_id])

            o_atoms: dict[str, str] = {}
            for o_id in orders:
                o_atoms[o_id] = self.tracker.get_or_create_order(run_id, int(dec_id), str(o_id))
                self.add_fact("candidate_order", d_atom, o_atoms[o_id])

            # FIFO ordering across orders in the decision
            if "fifo_position" in g.columns:
                order_fifo = g.groupby("order_id")["fifo_position"].min().sort_values()
                if len(order_fifo) > 0:
                    oldest_o = order_fifo.index[0]
                    self.add_fact("oldest_fifo", d_atom, o_atoms[oldest_o])
                    # Pairwise FIFO comparisons
                    order_list = list(order_fifo.index)
                    for i in range(len(order_list)):
                        for j in range(i + 1, min(i + 4, len(order_list))):  # limit comparison depth
                            self.add_fact("prior_fifo", d_atom, o_atoms[order_list[i]], o_atoms[order_list[j]])

            # Per-robot candidate ordering and relative ranking
            for r_id in robots:
                r_atom = r_atoms[r_id]
                r_group = g[g["robot_id"] == r_id].copy()

                if len(r_group) == 0:
                    continue

                for _, row in r_group.iterrows():
                    o_atom = o_atoms[row["order_id"]]
                    self.add_fact("candidate_pair", d_atom, r_atom, o_atom)

                # Tier A1 & A2: Static cost ranking (c_static_m)
                if "c_static_m" in r_group.columns:
                    sorted_by_c = r_group.sort_values(by=["c_static_m", "fifo_position"] if "fifo_position" in r_group.columns else ["c_static_m"])
                    orders_by_c = list(sorted_by_c["order_id"])

                    if len(orders_by_c) >= 1:
                        self.add_fact("nearest_static", d_atom, r_atom, o_atoms[orders_by_c[0]])
                    if len(orders_by_c) >= 2:
                        self.add_fact("second_nearest_static", d_atom, r_atom, o_atoms[orders_by_c[1]])

                    # Closer static pairwise comparisons
                    for i in range(min(3, len(orders_by_c))):
                        for j in range(i + 1, min(i + 3, len(orders_by_c))):
                            self.add_fact("closer_static", d_atom, r_atom, o_atoms[orders_by_c[i]], o_atoms[orders_by_c[j]])

                # Static finish ranking (est_finish_s)
                if "est_finish_s" in r_group.columns:
                    sorted_by_est = r_group.sort_values(by=["est_finish_s", "fifo_position"] if "fifo_position" in r_group.columns else ["est_finish_s"])
                    orders_by_est = list(sorted_by_est["order_id"])

                    if len(orders_by_est) >= 1:
                        self.add_fact("earliest_static_finish", d_atom, r_atom, o_atoms[orders_by_est[0]])
                    if len(orders_by_est) >= 2:
                        self.add_fact("second_earliest_static_finish", d_atom, r_atom, o_atoms[orders_by_est[1]])

                    for i in range(min(3, len(orders_by_est))):
                        for j in range(i + 1, min(i + 3, len(orders_by_est))):
                            self.add_fact("earlier_static_finish", d_atom, r_atom, o_atoms[orders_by_est[i]], o_atoms[orders_by_est[j]])

                # Tier B M1-screened congestion predicates
                if "occupied_now" in r_group.columns and r_group["occupied_now"].notna().any():
                    min_occ_idx = r_group["occupied_now"].idxmin()
                    min_occ_o = r_group.loc[min_occ_idx, "order_id"]
                    self.add_fact("lowest_occupied_for_robot", d_atom, r_atom, o_atoms[min_occ_o])

                    if self.thresholds:
                        for _, row in r_group.iterrows():
                            if pd.notna(row["occupied_now"]):
                                if self.thresholds.categorize("occupied_now", float(row["occupied_now"])) == "high":
                                    self.add_fact("high_occupied_now", d_atom, r_atom, o_atoms[row["order_id"]])

                if "st_conflict_robots" in r_group.columns and r_group["st_conflict_robots"].notna().any():
                    min_cr_idx = r_group["st_conflict_robots"].idxmin()
                    min_cr_o = r_group.loc[min_cr_idx, "order_id"]
                    self.add_fact("fewest_conflict_robots_for_robot", d_atom, r_atom, o_atoms[min_cr_o])

                    if self.thresholds:
                        for _, row in r_group.iterrows():
                            if pd.notna(row["st_conflict_robots"]):
                                if self.thresholds.categorize("st_conflict_robots", float(row["st_conflict_robots"])) == "high":
                                    self.add_fact("high_conflict_robots", d_atom, r_atom, o_atoms[row["order_id"]])

                if "st_conflicts_30s" in r_group.columns and r_group["st_conflicts_30s"].notna().any():
                    min_c30_idx = r_group["st_conflicts_30s"].idxmin()
                    min_c30_o = r_group.loc[min_c30_idx, "order_id"]
                    self.add_fact("fewest_conflicts_for_robot", d_atom, r_atom, o_atoms[min_c30_o])

                if self.thresholds and "overlap_ratio" in r_group.columns:
                    for _, row in r_group.iterrows():
                        if pd.notna(row["overlap_ratio"]):
                            if self.thresholds.categorize("overlap_ratio", float(row["overlap_ratio"])) == "low":
                                self.add_fact("low_route_overlap", d_atom, r_atom, o_atoms[row["order_id"]])

        return self.facts

    def write_bk_pl(self, path: Path | str) -> None:
        """Write sorted, deduplicated Prolog facts to bk.pl."""
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        unique_facts = sorted(list(set(self.facts)))
        with open(out_path, "w", encoding="utf-8") as f:
            for fact in unique_facts:
                f.write(fact + "\n")

