"""Decision-time Tier A body predicates: the one implementation.

bk.pl (offline, on all eligible pairs of a decision) and rule execution
(online, on the pairs still remaining in an iterative matching) both read
these definitions, so the facts Popper learned from and the facts a rule
fires on cannot drift apart (M2 spec 3.2, docs/DECISIONS.md, "M3 Execution
Semantics Amendment (2026-10-31)", item 1).

A relation is defined over whichever pair set it is given. Nothing here reads
an outcome, a selection, or anything but the five decision-time values of
CandidatePair. Ids are carried for joins and traceability; no relation
compares them (order_id breaks no tie that could change a fact).
"""

from __future__ import annotations

from typing import Iterable, NamedTuple, Sequence

from ilp_learning.bias_templates import BASE_PREDICATES


TIE_TOLERANCE = 1e-9  # exact-tie width of the rank predicates, M2 spec 3.2
COMPARE_LEVELS = 3    # cost levels with pairwise facts (the bound on closer_static)
COMPARE_REACH = 2     # later levels each is compared with
PRIOR_FIFO_REACH = 3  # following orders each order is compared with

_DEEPEST_LEVEL = COMPARE_LEVELS + COMPARE_REACH - 1


def _argument_kinds(types: str) -> tuple[str, ...]:
    """Argument kinds after the decision, from a bias type tuple."""
    return tuple(types.strip("()").split(","))[1:]


# predicate -> kinds of its arguments after D, read from the bias itself so the
# vocabulary Popper searches and the vocabulary a rule may use are one list.
TIER_A_SIGNATURES: dict[str, tuple[str, ...]] = {
    name: _argument_kinds(types) for name, _arity, types, _directions in BASE_PREDICATES
}


class CandidatePair(NamedTuple):
    """One eligible (robot, order) pair at a decision."""

    robot_id: str
    order_id: str
    c_static_m: float
    est_finish_s: float
    fifo_position: int


def _levels(values: Iterable[tuple[float, str]]) -> list[list[str]]:
    """Orders by rank level, with values within TIE_TOLERANCE of a level's start equal.

    Levels start from their lowest value rather than chaining, so a run of
    near ties cannot walk past the tolerance. A blank value has no rank.
    """
    levels: list[list[str]] = []
    start = None
    for value, order_id in sorted(v for v in values if v[0] == v[0]):
        if start is None or value > start + TIE_TOLERANCE:
            if len(levels) > _DEEPEST_LEVEL:
                break
            levels.append([])
            start = value
        levels[-1].append(order_id)
    return levels


class TierAFacts:
    """Tier A relations of one pair set, computed on first use.

    relation(name) returns the predicate's tuples without the decision
    argument, so nearest_static(D,R,O) is the set of (R, O).
    """

    def __init__(self, pairs: Sequence[CandidatePair]) -> None:
        self.pairs = tuple(CandidatePair(*pair) for pair in pairs)
        self._relations: dict[str, frozenset[tuple[str, ...]]] = {}
        self._by_robot: dict[str, list[CandidatePair]] = {}
        self._by_order: dict[str, list[CandidatePair]] = {}
        for pair in self.pairs:
            self._by_robot.setdefault(pair.robot_id, []).append(pair)
            self._by_order.setdefault(pair.order_id, []).append(pair)

    def relation(self, name: str) -> frozenset[tuple[str, ...]]:
        if name not in TIER_A_SIGNATURES:
            raise KeyError(f"'{name}' is not a Tier A predicate")
        if name not in self._relations:
            self._build(name)
        return self._relations[name]

    def _build(self, name: str) -> None:
        if name == "candidate_pair":
            self._relations[name] = frozenset((p.robot_id, p.order_id) for p in self.pairs)
        elif name == "candidate_robot":
            self._relations[name] = frozenset((r,) for r in self._by_robot)
        elif name == "candidate_order":
            self._relations[name] = frozenset((o,) for o in self._by_order)
        elif name == "best_cost_for_order":
            self._relations[name] = self._best_cost_for_order()
        elif name in ("oldest_fifo", "prior_fifo"):
            self._build_fifo()
        elif name in ("nearest_static", "second_nearest_static", "closer_static"):
            self._build_ranks(
                "c_static_m", "nearest_static", "second_nearest_static", "closer_static"
            )
        else:
            self._build_ranks(
                "est_finish_s",
                "earliest_static_finish",
                "second_earliest_static_finish",
                "earlier_static_finish",
            )

    def _build_ranks(self, field: str, best: str, second: str, compare: str) -> None:
        """Rank each robot's orders by field, level by level so tied orders stay equal."""
        best_facts: set[tuple[str, ...]] = set()
        second_facts: set[tuple[str, ...]] = set()
        compare_facts: set[tuple[str, ...]] = set()
        for robot_id, pairs in self._by_robot.items():
            levels = _levels((getattr(p, field), p.order_id) for p in pairs)
            if levels:
                best_facts.update((robot_id, order_id) for order_id in levels[0])
            if len(levels) > 1:
                second_facts.update((robot_id, order_id) for order_id in levels[1])
            for near in range(min(COMPARE_LEVELS, len(levels))):
                for far in range(near + 1, min(near + 1 + COMPARE_REACH, len(levels))):
                    compare_facts.update(
                        (robot_id, o_near, o_far)
                        for o_near in levels[near]
                        for o_far in levels[far]
                    )
        self._relations[best] = frozenset(best_facts)
        self._relations[second] = frozenset(second_facts)
        self._relations[compare] = frozenset(compare_facts)

    def _best_cost_for_order(self) -> frozenset[tuple[str, ...]]:
        """Every robot at the order's minimum cost: a tie is fact, not a choice."""
        facts: set[tuple[str, ...]] = set()
        for order_id, pairs in self._by_order.items():
            known = [p for p in pairs if p.c_static_m == p.c_static_m]
            if known:
                lowest = min(p.c_static_m for p in known) + TIE_TOLERANCE
                facts.update((p.robot_id, order_id) for p in known if p.c_static_m <= lowest)
        return frozenset(facts)

    def _build_fifo(self) -> None:
        position = {
            order_id: min(p.fifo_position for p in pairs)
            for order_id, pairs in self._by_order.items()
        }
        queue = sorted(position, key=lambda order_id: (position[order_id], order_id))
        self._relations["oldest_fifo"] = frozenset((o,) for o in queue[:1])
        self._relations["prior_fifo"] = frozenset(
            (older, later)
            for i, older in enumerate(queue)
            for later in queue[i + 1 : i + 1 + PRIOR_FIFO_REACH]
        )
