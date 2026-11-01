"""Online 1-to-1 batch matching for quantum-3 allocation with anti-circularity fallback.

Enforces (docs/DECISIONS.md, "M3 Execution Semantics Amendment (2026-10-31)"):
1. Hard feasibility: each robot <= 1 order, each order <= 1 robot, ineligible pairs strictly excluded.
2. Rule priority: a rule-covered pair is never given up for cardinality, and fallback (tier 1)
   only fills robots and orders left over, so a matching may be maximal rather than maximum.
3. Anti-circularity tie-breaking: queue-first fallback (tier, rule_rank, fifo_position, c_static_m, est_finish_s, robot_id).
   An empty or tautological rule collapses to naive FIFO rather than secretly executing nearest_astar.
4. Primary iterative mode: rules are re-evaluated on the remaining pairs before every pick.
   Comparison M-Card (linear_sum_assignment in two stages) and ablation M-Greedy (lexicographic greedy).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from typing import Callable, Iterable, Mapping, Protocol, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import maximum_bipartite_matching

from ilp_learning.predicates import CandidatePair


FALLBACK_RULE_RANK = 999  # above any clause_rank; the fallback tier decides first
FORCED = "forced"         # tie_level of a pick with no alternative pair left
BY_MATCHING = "matching"  # tie_level of a one-shot pick: no single key field decided it
PickKey = Callable[[CandidatePair], tuple]
KEY_FIELDS = ("tier", "clause_rank", "fifo_position", "c_static_m", "est_finish_s", "robot_id")


@dataclass(frozen=True)
class Assignment:
    robot_id: str
    order_id: str
    tier: int
    rule_rank: int
    fifo_position: int
    c_static_m: float
    est_finish_s: float
    round: int = 0  # pick number (iterative) or stage, 0 rule then 1 fallback (M-Card)
    tie_level: str = BY_MATCHING  # the key field that separated this pick from the next pair

    @property
    def matched_by_rule(self) -> bool:
        return self.tier == 0


class RuleCoverage(Protocol):
    """What a matcher needs of a learned program: its coverage of any pair set."""

    def coverage(self, pairs: Sequence[CandidatePair]) -> Mapping[tuple[str, str], int]:
        ...


REQUIRED_COLUMNS = ("robot_id", "order_id", "c_static_m", "est_finish_s", "fifo_position")


class MissingDecisionDataError(ValueError):
    """A tie-break input is absent; a fabricated default would change the matching."""


def _require_tie_break_data(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise MissingDecisionDataError(f"candidate batch lacks required columns {missing}")
    blank = [c for c in REQUIRED_COLUMNS if df[c].isna().any()]
    if blank:
        raise MissingDecisionDataError(f"candidate batch has missing values in {blank}")


def maximum_cardinality(pairs: Iterable[tuple[str, str]]) -> int:
    """Hopcroft-Karp size of a maximum matching over (robot, order) pairs."""
    edges = sorted(set(pairs))
    if not edges:
        return 0
    robots = {r: i for i, r in enumerate(sorted({r for r, _ in edges}))}
    orders = {o: j for j, o in enumerate(sorted({o for _, o in edges}))}
    adjacency = np.zeros((len(robots), len(orders)), dtype=bool)
    for r, o in edges:
        adjacency[robots[r], orders[o]] = True
    return int(np.sum(maximum_bipartite_matching(csr_matrix(adjacency), perm_type="column") >= 0))


def _tie_level(chosen: tuple, runner_up: tuple | None) -> str:
    """The first key field on which the pick beat the next pair."""
    if runner_up is None:
        return FORCED
    return next(field for field, a, b in zip(KEY_FIELDS, chosen, runner_up) if a != b)


class BatchMatcher:
    """Solves 1-to-1 matching from candidate scores and learned rule coverage."""

    MODES = ("iterative", "cardinality", "greedy")

    def __init__(self, mode: str = "cardinality", pick_key: PickKey | None = None) -> None:
        """pick_key replaces the four key fields after clause_rank. Diagnostics only."""
        if mode not in self.MODES:
            raise ValueError(f"Unknown matching mode '{mode}'; expected one of {self.MODES}")
        self.mode = mode
        self.pick_key = pick_key

    def match_pairs(
        self, pairs: Sequence[CandidatePair], rule_program: RuleCoverage | None = None
    ) -> list[Assignment]:
        """Match pairs that are already eligible, the form online execution uses."""
        if self.mode == "iterative":
            return self._match_iterative(pairs, rule_program)
        return self.match(pd.DataFrame(pairs, columns=REQUIRED_COLUMNS), rule_program=rule_program)

    def _match_iterative(
        self, pairs: Sequence[CandidatePair], rule_program: RuleCoverage | None
    ) -> list[Assignment]:
        """One pick at a time, re-evaluating the rules on the remaining pairs.

        A pick is the smallest (tier, clause_rank, fifo_position, c_static_m,
        est_finish_s, robot_id), so a fallback pick happens only when no
        rule-covered pair remains, and rules fire again on what it leaves.
        """
        remaining = list(pairs)
        assignments: list[Assignment] = []

        while remaining:
            ranks = rule_program.coverage(remaining) if rule_program is not None else {}
            keyed = []
            for pair in remaining:
                rank = ranks.get((pair.robot_id, pair.order_id))
                tier, clause_rank = (1, FALLBACK_RULE_RANK) if rank is None else (0, rank)
                order = (
                    self.pick_key(pair)
                    if self.pick_key is not None
                    else (pair.fifo_position, pair.c_static_m, pair.est_finish_s, pair.robot_id)
                )
                keyed.append((tier, clause_rank, *order))

            first = min(range(len(keyed)), key=keyed.__getitem__)
            runner_up = min((k for i, k in enumerate(keyed) if i != first), default=None)
            pick = remaining[first]

            assignments.append(
                Assignment(
                    robot_id=pick.robot_id,
                    order_id=pick.order_id,
                    tier=keyed[first][0],
                    rule_rank=keyed[first][1],
                    fifo_position=pick.fifo_position,
                    c_static_m=pick.c_static_m,
                    est_finish_s=pick.est_finish_s,
                    round=len(assignments),
                    tie_level=_tie_level(keyed[first], runner_up),
                )
            )
            remaining = [
                p for p in remaining if p.robot_id != pick.robot_id and p.order_id != pick.order_id
            ]

        return assignments

    def match(
        self,
        candidates_df: pd.DataFrame,
        rule_coverage_fn=None,
        rule_program: RuleCoverage | None = None,
    ) -> list[Assignment]:
        """Perform 1-to-1 batch matching on candidate pairs.

        candidates_df must contain: robot_id, order_id, eligible, c_static_m, est_finish_s, fifo_position.
        rule_coverage_fn: optional callable (row -> tuple[bool, int]) returning (is_covered, rule_rank).
        rule_program: a learned program. Iterative mode needs it, not a per-row callable,
        because it re-evaluates on the residual pairs; one-shot modes evaluate it once.
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

        _require_tie_break_data(valid_df)

        pairs = [
            CandidatePair(*row)
            for row in valid_df[list(REQUIRED_COLUMNS)].itertuples(index=False, name=None)
        ]
        if self.mode == "iterative":
            if rule_coverage_fn is not None:
                raise ValueError("iterative matching re-evaluates rules: pass rule_program")
            return self._match_iterative(pairs, rule_program)

        if rule_program is not None:
            if rule_coverage_fn is not None:
                raise ValueError("pass rule_coverage_fn or rule_program, not both")
            ranks = rule_program.coverage(pairs)

            def rule_coverage_fn(row):
                rank = ranks.get((row["robot_id"], row["order_id"]))
                return (rank is not None, FALLBACK_RULE_RANK if rank is None else rank)

        # Compute tier and rule_rank for each candidate pair
        tiers = []
        rule_ranks = []
        for _, row in valid_df.iterrows():
            if rule_coverage_fn is not None:
                covered, rank = rule_coverage_fn(row)
                tiers.append(0 if covered else 1)
                rule_ranks.append(rank if covered else FALLBACK_RULE_RANK)
            else:
                # Default fallback (uncovered)
                tiers.append(1)
                rule_ranks.append(FALLBACK_RULE_RANK)

        valid_df["tier"] = tiers
        valid_df["rule_rank"] = rule_ranks

        if self.mode == "cardinality":
            return self._match_rule_priority(valid_df)
        else:
            return self._match_greedy(valid_df)

    def _match_greedy(self, df: pd.DataFrame) -> list[Assignment]:
        """Ablation: deterministic greedy matching sorted by anti-circularity tie-break."""
        sort_cols = ["tier", "rule_rank", "fifo_position", "c_static_m", "est_finish_s", "robot_id"]
        sorted_df = df.sort_values(by=sort_cols)

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
                        fifo_position=int(row["fifo_position"]),
                        c_static_m=float(row["c_static_m"]),
                        est_finish_s=float(row["est_finish_s"]),
                    )
                )

        return assignments

    def _match_rule_priority(self, df: pd.DataFrame) -> list[Assignment]:
        """M-Card: maximum cardinality over rule-covered pairs, then fallback on the leftovers.

        Two stages rather than one matching over all pairs, so a covered pair
        is never traded for one more assignment (Amendment item 3).
        """
        covered = df[df["tier"] == 0]
        ruled = self._match_cardinality(covered) if len(covered) else []

        taken_robots = {a.robot_id for a in ruled}
        taken_orders = {a.order_id for a in ruled}
        leftover = df[
            (df["tier"] != 0)
            & ~df["robot_id"].isin(taken_robots)
            & ~df["order_id"].isin(taken_orders)
        ]
        fallback = self._match_cardinality(leftover) if len(leftover) else []
        return ruled + [replace(a, round=1) for a in fallback]

    def _match_cardinality(self, df: pd.DataFrame) -> list[Assignment]:
        """One stage: global min-cost max-cardinality matching via linear_sum_assignment."""
        robots = sorted(list(df["robot_id"].unique()))
        orders = sorted(list(df["order_id"].unique()))

        r_to_idx = {r: i for i, r in enumerate(robots)}
        o_to_idx = {o: j for j, o in enumerate(orders)}

        n_r = len(robots)
        n_o = len(orders)

        d_max = max(float(df["c_static_m"].max()), 0.0)
        f_max = max(float(df["est_finish_s"].max()), 0.0)
        fifo_max = max(float(df["fifo_position"].max()), 0.0)

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
            fifo = int(row["fifo_position"])
            c_static = float(row["c_static_m"])
            est_finish = float(row["est_finish_s"])

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
        k_star = maximum_cardinality(zip(df["robot_id"], df["order_id"]))
        if len(assignments) != k_star:
            raise RuntimeError(
                f"M-Card cardinality failure: matched {len(assignments)} edges, "
                f"but bipartite graph maximum feasible cardinality is {k_star}"
            )

        return assignments

