"""Multi-teacher supervision for M5: votes of the panel on one decision.

Pooling each teacher's picks as positives and the rest as negatives would state
the same atom both ways, so a pair is a positive, a negative, or left out
(M2 spec 6.4). Left-out pairs stay in bk.pl; they are only not examples.

    majority    positive with more than half the votes, negative with none (C1)
    plurality   positive in the maximum-vote matching with 2 or more votes,
                negative with none (C3)

Votes are all this reads: picking the teacher that worked best at a decision
would make M5 an outcome-aware method, and M5 is what M6 is compared against.
"""

from __future__ import annotations

import pandas as pd
from scipy.optimize import linear_sum_assignment


POLICIES = ("majority", "plurality")
PAIR_KEYS = ["decision_id", "robot_id", "order_id"]
MIN_PLURALITY_VOTES = 2


def _with_votes(frame: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    votes = labels.groupby(PAIR_KEYS).size().rename("votes").reset_index()
    joined = frame.merge(votes, on=PAIR_KEYS, how="left")
    return joined.assign(votes=joined["votes"].fillna(0).astype(int))


def _plurality_picks(rows: pd.DataFrame) -> set[tuple[str, str]]:
    """The maximum-vote matching of one decision, on sorted ids so it is repeatable."""
    table = rows.pivot_table(index="robot_id", columns="order_id", values="votes", fill_value=0)
    table = table.sort_index().sort_index(axis=1)
    picked_robots, picked_orders = linear_sum_assignment(table.to_numpy(), maximize=True)
    return {
        (table.index[r], table.columns[o])
        for r, o in zip(picked_robots, picked_orders)
        if table.iat[r, o] >= MIN_PLURALITY_VOTES
    }


def consensus_labelled(frame: pd.DataFrame, labels: pd.DataFrame, policy: str) -> pd.DataFrame:
    """The same decisions, with selected and abstain set by this policy."""
    if policy not in POLICIES:
        raise ValueError(f"unknown label policy {policy!r}; known: {POLICIES}")
    teachers = labels["teacher"].nunique()
    voted = _with_votes(frame, labels)

    if policy == "majority":
        selected = voted["votes"] >= teachers // 2 + 1
    else:
        picks = set()
        for decision_id, rows in voted[voted["eligible"]].groupby("decision_id"):
            picks |= {(decision_id, *pair) for pair in _plurality_picks(rows)}
        selected = pd.Series(
            [key in picks for key in zip(voted["decision_id"], voted["robot_id"], voted["order_id"])],
            index=voted.index,
        )

    return voted.drop(columns="votes").assign(
        selected=selected, abstain=~selected & (voted["votes"] > 0)
    )
