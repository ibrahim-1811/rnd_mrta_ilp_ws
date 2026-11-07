"""Candidate set "mp1": which pairs to try at one decision state, for the M6 multi-pair arm.

M6-A tries one robot with up to four orders. mp1 tries pairs across robots and orders,
all at the same recorded state, around the behaviour policy's pivot: the pair it gave
its oldest order (lowest fifo_position).

    behaviour    the behaviour policy's pivot pair; the reference branch, always first
    panel        what each other teacher in the panel assigns at this state to the pivot
                 order (another robot) and to the pivot robot (another order)
    alt_robot    the eligible robot with the lowest static cost for the pivot order
    alt_order    the eligible order with the earliest finish for the pivot robot
    random       one eligible choice pair, drawn uniformly with a seed derived from
                 (seed, run_id, decision_id): a control against picking only pairs
                 that some heuristic already likes

Each pair is listed once, with every source that proposed it. The non-random pairs are
truncated to max_actions - 1 in the order above; one slot is always kept for the random
control, drawn from the choice pairs not already listed, and its selection probability
(1 / pairs it was drawn from) is recorded. Teacher names are provenance: nothing here
decides that a pair is good.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib

import numpy as np
import pandas as pd

from ilp_learning.sampler import choice_pairs


CANDIDATE_SET = "mp1"
DEFAULT_MAX_ACTIONS = 6


@dataclass
class CandidateAction:
    robot_id: str
    order_id: str
    sources: list[str] = field(default_factory=list)
    p_select: float = 1.0  # 1 for a deterministic source; 1/n for the random draw

    def extra(self, rank: int) -> dict:
        return {
            "candidate_set": CANDIDATE_SET,
            "candidate_rank": rank,
            "candidate_sources": "+".join(self.sources),
            "candidate_p_select": self.p_select,
            "branch": f"c{rank}",
        }


def pivot_of(decision: pd.DataFrame) -> tuple[str, str] | None:
    """The behaviour policy's pair of its oldest assigned order at this decision."""
    picked = decision[decision["selected"]]
    if picked.empty:
        return None
    row = picked.sort_values(["fifo_position", "robot_id"], kind="stable").iloc[0]
    return str(row["robot_id"]), str(row["order_id"])


def _draw(seed: int, run_id: str, decision_id: int, n: int) -> int:
    digest = hashlib.sha256(f"{seed}:{run_id}:{decision_id}".encode()).digest()
    return int(np.random.default_rng(int.from_bytes(digest[:8], "big")).integers(n))


def select_candidates(
    decision: pd.DataFrame,
    panel: pd.DataFrame | None,
    *,
    run_id: str,
    seed: int,
    behaviour: str = "nearest_astar",
    max_actions: int = DEFAULT_MAX_ACTIONS,
) -> list[CandidateAction]:
    """Up to max_actions distinct eligible pairs of one decision, behaviour pair first."""
    eligible = decision[decision["eligible"]]
    pivot = pivot_of(eligible)
    if pivot is None:
        return []
    robot, order = pivot
    allowed = {(str(r), str(o)) for r, o in zip(eligible["robot_id"], eligible["order_id"])}

    actions: dict[tuple[str, str], CandidateAction] = {}

    def add(pair, source):
        if pair in allowed:
            actions.setdefault(pair, CandidateAction(*pair)).sources.append(source)

    add(pivot, "behaviour")

    if panel is not None and not panel.empty:
        for teacher, rows in sorted(panel.groupby("teacher"), key=lambda item: item[0]):
            if teacher == behaviour:
                continue
            for r, o in zip(rows["robot_id"].astype(str), rows["order_id"].astype(str)):
                if o == order or r == robot:
                    add((r, o), f"panel:{teacher}")

    for_order = eligible[(eligible["order_id"] == order) & (eligible["robot_id"] != robot)]
    if len(for_order):
        best = for_order.sort_values(["c_static_m", "robot_id"], kind="stable").iloc[0]
        add((str(best["robot_id"]), order), "alt_robot")

    for_robot = eligible[(eligible["robot_id"] == robot) & (eligible["order_id"] != order)]
    if len(for_robot):
        key = "est_finish_s" if "est_finish_s" in for_robot.columns else "c_static_m"
        best = for_robot.sort_values([key, "fifo_position", "order_id"], kind="stable").iloc[0]
        add((robot, str(best["order_id"])), "alt_order")

    kept = list(actions.values())[: max(1, max_actions - 1)]
    listed = {(a.robot_id, a.order_id) for a in kept}
    contested = choice_pairs(eligible).sort_values(["robot_id", "order_id"], kind="stable")
    rest = [
        (str(r), str(o)) for r, o in zip(contested["robot_id"], contested["order_id"])
        if (str(r), str(o)) not in listed
    ]
    if rest and len(kept) < max_actions:
        pair = rest[_draw(seed, run_id, int(decision["decision_id"].iloc[0]), len(rest))]
        kept.append(CandidateAction(*pair, sources=["random"], p_select=1.0 / len(rest)))
    return kept
