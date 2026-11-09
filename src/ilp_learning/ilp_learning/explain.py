"""Plain-language explanations of a learned policy's picks (M7 explanations, prototype).

A pick is explained by the clause that made it: every body literal becomes one short
statement with the decision-time numbers behind it. The facts are re-checked against
the pairs that were left when the pick was made (the matcher re-evaluates the rules on
the remaining pairs before every pick), so an explanation can never cite a condition
that did not hold. A fallback pick says so.

Display only: nothing here changes a decision.
"""

from __future__ import annotations

from typing import Sequence

from ilp_learning.predicates import CandidatePair, DecisionFacts, busy_ratio, queue_pressure

FALLBACK_TEXT = ("no learned rule covered any remaining pair, so the fixed fallback took the oldest "
                 "waiting order and the nearest free robot")


def _next_best(pairs, pick, field, by):
    """The best value of field among the other robots (by='order_id') or orders (by='robot_id')."""
    other = "robot_id" if by == "order_id" else "order_id"
    values = [getattr(p, field) for p in pairs
              if getattr(p, by) == getattr(pick, by) and getattr(p, other) != getattr(pick, other)]
    return min(values) if values else None


def statement(name: str, p: CandidatePair, pairs: Sequence[CandidatePair]) -> str | None:
    """One literal of a clause, as a sentence about this pair; None for helper comparisons."""
    R, O = p.robot_id, p.order_id  # noqa: E741 (robot, order, as in the clauses)
    if name == "best_finish_for_order":
        nxt = _next_best(pairs, p, "est_finish_s", "order_id")
        tail = f", next best {nxt:.0f} s" if nxt is not None else ", no other robot could take it"
        return f"{R} finishes {O} soonest ({p.est_finish_s:.0f} s{tail})"
    if name == "nearest_static":
        return f"{O} is {R}'s closest order ({p.c_static_m:.0f} m on the one-way lanes)"
    if name == "earliest_static_finish":
        return f"{O} is the order {R} finishes soonest ({p.est_finish_s:.0f} s)"
    if name == "second_earliest_static_finish":
        return f"{O} is {R}'s second-quickest order ({p.est_finish_s:.0f} s)"
    if name == "second_nearest_static":
        return f"{O} is {R}'s second-closest order ({p.c_static_m:.0f} m)"
    if name == "best_cost_for_order":
        return f"{R} is the closest robot to {O} ({p.c_static_m:.0f} m)"
    if name == "oldest_fifo":
        return f"{O} has waited longest in the queue"
    if name == "prior_fifo":
        return f"{O} has waited longer than another candidate order"
    if name == "aging_window_order":
        return f"{O} has been waiting a long time"
    if name == "oldest_eligible_for_robot":
        return f"{O} is the oldest order {R} may take"
    if name == "near_best_cost_for_robot":
        return f"{O} is almost as close to {R} as its closest order"
    if name == "batch_contention":
        return f"{p.free_robots:.0f} free robots compete for {p.window_orders:.0f} waiting orders"
    if name == "high_fleet_busy_ratio":
        return f"most of the fleet is busy ({busy_ratio(p):.0%} of robots)"
    if name == "high_queue_pressure":
        return f"the queue is long ({queue_pressure(p):.1f} pending orders per robot)"
    if name == "low_route_overlap":
        return f"{R}'s route to {O} shares little road with other robots' routes ({p.overlap_ratio:.0%} overlap)"
    if name == "lowest_occupied_for_robot":
        return f"of {R}'s options, the route to {O} passes the fewest occupied cells ({p.occupied_now:.0f})"
    if name == "high_occupied_now":
        return f"the route to {O} is crowded right now ({p.occupied_now:.0f} occupied cells)"
    if name == "fewest_conflict_robots_for_robot":
        return f"of {R}'s options, the route to {O} crosses the fewest other robots ({p.st_conflict_robots:.0f})"
    if name == "fewest_conflicts_for_robot":
        return f"of {R}'s options, the route to {O} has the fewest predicted conflicts in 30 s ({p.st_conflicts_30s:.0f})"
    if name == "high_conflict_robots":
        return f"the route to {O} crosses many other robots ({p.st_conflict_robots:.0f})"
    if name == "low_local_density":
        return f"few robots are near {R} right now (density {p.local_density:.2f})"
    if name == "best_margin_for_order":
        return f"{R} has the most battery to spare for {O} ({p.energy_margin_percent:.0f}% above what the trip needs)"
    if name == "low_energy_margin":
        return f"{R} would have little battery to spare after {O} ({p.energy_margin_percent:.0f}%)"
    if name == "near_charger_after_order":
        return f"after {O}, {R} ends close to a charger ({p.predicted_charger_distance_m:.0f} m)"
    if name == "high_charging_pressure":
        return "many robots are charging or waiting to charge"
    if name == "least_driven_robot":
        return f"{R} has driven the least of the free robots so far ({p.robot_distance_m:.0f} m), spreading the work"
    if name in ("closer_static", "earlier_static_finish", "candidate_pair", "candidate_robot", "candidate_order"):
        return None
    return f"{name} holds"


def explain_decision(program, pairs: Sequence[CandidatePair], picks) -> list[dict]:
    """One entry per pick, in pick order: robot, order, rule (1-based or None), text.

    picks are matcher Assignments (robot_id, order_id, round, matched_by_rule, rule_rank).
    """
    pairs = list(pairs)
    used_r, used_o, out = set(), set(), []
    for pick in sorted(picks, key=lambda a: a.round):
        left = [p for p in pairs if p.robot_id not in used_r and p.order_id not in used_o]
        p = next(x for x in left if x.robot_id == pick.robot_id and x.order_id == pick.order_id)
        if pick.matched_by_rule:
            clause = program.clauses[int(pick.rule_rank)]
            facts = DecisionFacts(left, program.thresholds)
            assert (p.robot_id, p.order_id) in clause.covers(facts), "a clause must cover its own pick"
            parts = [s for s in (statement(lit.predicate, p, left) for lit in clause.body) if s]
            out.append({"robot": p.robot_id, "order": p.order_id, "rule": int(pick.rule_rank) + 1,
                        "text": "; ".join(parts)})
        else:
            out.append({"robot": p.robot_id, "order": p.order_id, "rule": None, "text": FALLBACK_TEXT})
        used_r.add(p.robot_id)
        used_o.add(p.order_id)
    return out
