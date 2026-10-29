"""Online 1-to-1 batch matching for quantum-3 allocation with anti-circularity fallback.

Enforces:
1. Hard feasibility: each robot <= 1 order, each order <= 1 robot, ineligible pairs strictly excluded.
2. Learned-rule preference influence: rule-covered pairs (tier 0) are prioritized over fallback (tier 1).
3. Anti-circularity tie-breaking: queue-first fallback (tier, rule_rank, fifo_position, c_static_m, est_finish_s, robot_id).
   An empty or tautological rule collapses to naive FIFO rather than secretly executing nearest_astar.
4. Primary M-Card (linear_sum_assignment) and ablation M-Greedy (lexicographic greedy).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import maximum_bipartite_matching


@dataclass(frozen=True)
class Assignment:
    robot_id: str
    order_id: str
    tier: int
    rule_rank: int
    fifo_position: int
    c_static_m: float
    est_finish_s: float


class BatchMatcher:
    """Solves 1-to-1 matching from candidate scores and learned rule coverage."""

    def __init__(self, mode: str = "cardinality") -> None:
        if mode not in ("cardinality", "greedy"):
            raise ValueError(f"Unknown matching mode '{mode}'; expected 'cardinality' or 'greedy'")
        self.mode = mode

    def match(
        self,
        candidates_df: pd.DataFrame,
        rule_coverage_fn=None,
    ) -> list[Assignment]:
        """Perform 1-to-1 batch matching on candidate pairs.

        candidates_df must contain: robot_id, order_id, eligible, c_static_m, est_finish_s, fifo_position.
        rule_coverage_fn: optional callable (row -> tuple[bool, int]) returning (is_covered, rule_rank).
        """
        if len(candidates_df) == 0:
            return []

        # Hard eligibility gate: drop ineligible pairs
        if "eligible" in candidates_df.columns:
            valid_df = candidates_df[candidates_df["eligible"] == True].copy()  # noqa: E712
        else:
            valid_df = candidates_df.copy()

        if len(valid_df) == 0:
            return []

        # Compute tier and rule_rank for each candidate pair
        tiers = []
        rule_ranks = []
        for _, row in valid_df.iterrows():
            if rule_coverage_fn is not None:
                covered, rank = rule_coverage_fn(row)
                tiers.append(0 if covered else 1)
                rule_ranks.append(rank if covered else 999)
            else:
                # Default fallback (uncovered)
                tiers.append(1)
                rule_ranks.append(999)

        valid_df["tier"] = tiers
        valid_df["rule_rank"] = rule_ranks

        if self.mode == "cardinality":
            return self._match_cardinality(valid_df)
        else:
            return self._match_greedy(valid_df)

    def _match_greedy(self, df: pd.DataFrame) -> list[Assignment]:
        """Ablation: deterministic greedy matching sorted by anti-circularity tie-break."""
        sort_cols = ["tier", "rule_rank", "fifo_position", "c_static_m", "est_finish_s", "robot_id"]
        clean_df = df.copy()
        if "fifo_position" not in clean_df.columns:
            clean_df["fifo_position"] = 0
        if "c_static_m" not in clean_df.columns:
            clean_df["c_static_m"] = 0.0
        if "est_finish_s" not in clean_df.columns:
            clean_df["est_finish_s"] = 0.0

        sorted_df = clean_df.sort_values(by=sort_cols)

        assigned_robots: set[str] = set()
        assigned_orders: set[str] = set()
        assignments: list[Assignment] = []

        for _, row in sorted_df.iterrows():
            r = str(row["robot_id"])
            o = str(row["order_id"])
            if r not in assigned_robots and o not in assigned_orders:
                assigned_robots.add(r)
                assigned_orders.add(o)
                assignments.append(
                    Assignment(
                        robot_id=r,
                        order_id=o,
                        tier=int(row["tier"]),
                        rule_rank=int(row["rule_rank"]),
                        fifo_position=int(row.get("fifo_position", 0)),
                        c_static_m=float(row.get("c_static_m", 0.0)),
                        est_finish_s=float(row.get("est_finish_s", 0.0)),
                    )
                )

        return assignments

    def _match_cardinality(self, df: pd.DataFrame) -> list[Assignment]:
        """Primary: global min-cost max-cardinality matching via linear_sum_assignment."""
        robots = sorted(list(df["robot_id"].unique()))
        orders = sorted(list(df["order_id"].unique()))

        r_to_idx = {r: i for i, r in enumerate(robots)}
        o_to_idx = {o: j for j, o in enumerate(orders)}

        n_r = len(robots)
        n_o = len(orders)

        d_max = max(float(df["c_static_m"].max()), 0.0) if "c_static_m" in df.columns and len(df) > 0 else 0.0
        f_max = max(float(df["est_finish_s"].max()), 0.0) if "est_finish_s" in df.columns and len(df) > 0 else 0.0
        fifo_max = max(float(df["fifo_position"].max()), 0.0) if "fifo_position" in df.columns and len(df) > 0 else 0.0

        # Infeasible edge penalty M:
        # Feasible edge costs are guaranteed to be in [0.0, 2.0).
        # Any matching with k feasible edges has total cost <= (min(n_r, n_o) - k)*M + k*2.0.
        # Any matching with k-1 feasible edges has total cost >= (min(n_r, n_o) - k + 1)*M.
        # Setting M > min(n_r, n_o)*2.0 guarantees that any matching with k feasible edges strictly
        # beats any matching with < k feasible edges, proving max-cardinality matching.
        max_feasible_k = min(n_r, n_o)
        M = float((max_feasible_k + 1) * 2.0 + 10.0)
        cost_matrix = np.full((n_r, n_o), fill_value=M, dtype=np.float64)

        pair_dict = {}
        for _, row in df.iterrows():
            r = row["robot_id"]
            o = row["order_id"]
            i = r_to_idx[r]
            j = o_to_idx[o]

            tier = int(row["tier"])
            rule_rank = int(row["rule_rank"])
            fifo = int(row.get("fifo_position", 0))
            c_static = float(row.get("c_static_m", 0.0))
            est_finish = float(row.get("est_finish_s", 0.0))

            # Exact anti-circularity nested lexicographic cost:
            # Level 6: robot_id deterministic tie-break
            t_robot = i / (n_r + 1.0)
            # Level 5: est_finish_s
            t_finish = (est_finish + t_robot) / (f_max + 2.0)
            # Level 4: c_static_m
            t_dist = (c_static + t_finish) / (d_max + 2.0)
            # Level 3: fifo_position
            m_fifo = fifo_max + 2.0
            t_fifo = (fifo + t_dist) / m_fifo
            # Level 2: rule_rank
            m_rule = 1005.0
            t_rule = (rule_rank + t_fifo) / m_rule
            # Level 1: tier (0 for rule-covered, 1 for fallback)
            cost = tier + t_rule

            cost_matrix[i, j] = cost
            pair_dict[(i, j)] = (r, o, tier, rule_rank, fifo, c_static, est_finish)

        row_ind, col_ind = linear_sum_assignment(cost_matrix)

        assignments: list[Assignment] = []
        for r_i, o_j in zip(row_ind, col_ind):
            # Feasible candidate pairs have cost < 2.0 (well below M / 2.0)
            if cost_matrix[r_i, o_j] < 2.0:
                r, o, tier, rule_rank, fifo, c_static, est_finish = pair_dict[(r_i, o_j)]
                assignments.append(
                    Assignment(
                        robot_id=str(r),
                        order_id=str(o),
                        tier=tier,
                        rule_rank=rule_rank,
                        fifo_position=fifo,
                        c_static_m=c_static,
                        est_finish_s=est_finish,
                    )
                )

        # Mathematical verification: Hopcroft-Karp maximum bipartite matching
        adj_matrix = np.zeros((n_r, n_o), dtype=bool)
        for i, j in pair_dict.keys():
            adj_matrix[i, j] = True
        k_star = int(np.sum(maximum_bipartite_matching(csr_matrix(adj_matrix), perm_type="column") >= 0))
        if len(assignments) != k_star:
            raise RuntimeError(
                f"M-Card cardinality failure: matched {len(assignments)} edges, "
                f"but bipartite graph maximum feasible cardinality is {k_star}"
            )

        return assignments

