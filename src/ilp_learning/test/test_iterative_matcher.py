"""Iterative execution and rule-priority M-Card (M3 Execution Semantics Amendment)."""

import pandas as pd
import pytest

from ilp_learning.matcher import BatchMatcher, maximum_cardinality
from ilp_learning.predicates import CandidatePair
from ilp_learning.rule_compiler import RuleProgram, parse_clause

R_AND_O = RuleProgram((parse_clause("assign(A,B,C):- nearest_static(A,B,C),best_cost_for_order(A,B,C)."),))
TAUTOLOGY = RuleProgram((parse_clause("assign(A,B,C):- candidate_pair(A,B,C)."),))
EMPTY = RuleProgram()


def P(robot, order, cost, fifo, finish=None):
    return CandidatePair(robot, order, cost, cost if finish is None else finish, fifo)


class Recording:
    """A program that records the pair set it was asked about."""

    def __init__(self, program):
        self.program, self.seen = program, []

    def coverage(self, pairs):
        self.seen.append({(p.robot_id, p.order_id) for p in pairs})
        return self.program.coverage(pairs)


class Fixed:
    """Covers named pairs whenever they remain."""

    def __init__(self, ranks):
        self.ranks = ranks

    def coverage(self, pairs):
        return {
            (p.robot_id, p.order_id): self.ranks[(p.robot_id, p.order_id)]
            for p in pairs
            if (p.robot_id, p.order_id) in self.ranks
        }


def picks(pairs, program=None, mode="iterative"):
    return [(a.robot_id, a.order_id) for a in BatchMatcher(mode).match_pairs(pairs, program)]


def test_rules_are_reevaluated_on_the_remaining_pairs():
    pairs = [P("r1", "a", 1, 1), P("r1", "b", 5, 0), P("r2", "a", 2, 1), P("r2", "b", 6, 0)]
    program = Recording(R_AND_O)
    assert picks(pairs, program) == [("r1", "a"), ("r2", "b")]
    assert program.seen[0] == {("r1", "a"), ("r1", "b"), ("r2", "a"), ("r2", "b")}
    assert program.seen[1] == {("r2", "b")}


def test_a_residual_rule_pick_is_made_by_the_rule_not_fallback():
    # r2-b is no mutual best on E0, and becomes one once r1-a has left.
    pairs = [P("r1", "a", 1, 1), P("r1", "b", 2, 0), P("r2", "a", 2, 1), P("r2", "b", 3, 0)]
    assignments = BatchMatcher("iterative").match_pairs(pairs, R_AND_O)
    assert [a.matched_by_rule for a in assignments] == [True, True]


def test_each_robot_and_order_at_most_once():
    pairs = [P(f"r{i}", f"o{j}", 1.0 + i * j, j) for i in range(4) for j in range(6)]
    result = picks(pairs, R_AND_O)
    assert len({r for r, _ in result}) == len(result) == len({o for _, o in result}) == 4


def test_key_order_clause_rank_beats_fifo():
    pairs = [P("r1", "a", 1, 0), P("r2", "b", 1, 1)]
    assert picks(pairs, Fixed({("r1", "a"): 1, ("r2", "b"): 0}))[0] == ("r2", "b")


def test_key_order_covered_beats_older_uncovered():
    pairs = [P("r1", "old", 1, 0), P("r2", "new", 9, 5)]
    assert picks(pairs, Fixed({("r2", "new"): 0}))[0] == ("r2", "new")


def test_key_order_fifo_beats_cost():
    assert picks([P("r1", "near", 1, 3), P("r2", "old", 9, 0)], TAUTOLOGY)[0] == ("r2", "old")


def test_key_order_cost_then_finish_then_robot_id():
    assert picks([P("r1", "a", 2, 0), P("r2", "a", 1, 0)], TAUTOLOGY) == [("r2", "a")]
    assert picks([P("r1", "a", 1, 0, 9), P("r2", "a", 1, 0, 8)], TAUTOLOGY) == [("r2", "a")]
    assert picks([P("r2", "a", 1, 0, 8), P("r1", "a", 1, 0, 8)], TAUTOLOGY) == [("r1", "a")]


def test_fallback_only_when_no_covered_pair_remains():
    pairs = [P("r1", "a", 1, 0), P("r1", "b", 2, 1), P("r2", "b", 3, 1)]
    assignments = BatchMatcher("iterative").match_pairs(pairs, Fixed({("r2", "b"): 0}))
    assert [(a.robot_id, a.order_id, a.tier) for a in assignments] == [
        ("r2", "b", 0), ("r1", "a", 1),
    ]


def test_rules_fire_again_after_a_fallback_pick():
    class AfterFirst:
        def __init__(self):
            self.calls = 0

        def coverage(self, pairs):
            self.calls += 1
            return {} if self.calls == 1 else {(p.robot_id, p.order_id): 0 for p in pairs}

    pairs = [P("r1", "a", 1, 0), P("r2", "b", 1, 1)]
    assignments = BatchMatcher("iterative").match_pairs(pairs, AfterFirst())
    assert [a.tier for a in assignments] == [1, 0]


@pytest.mark.parametrize("program", [None, EMPTY, TAUTOLOGY])
def test_empty_or_tautological_rule_is_fifo_not_nearest_astar(program):
    # nearest_astar would take r1-near (cost 1) first; FIFO serves the oldest first.
    pairs = [
        P("r1", "old", 50, 0), P("r1", "near", 1, 1),
        P("r2", "old", 60, 0), P("r2", "near", 2, 1),
    ]
    assert picks(pairs, program) == [("r1", "old"), ("r2", "near")]


def test_tie_level_names_the_deciding_field():
    level = lambda pairs, program=None: BatchMatcher("iterative").match_pairs(pairs, program)[0].tie_level  # noqa: E731
    assert level([P("r1", "a", 1, 0)]) == "forced"
    assert level([P("r1", "a", 1, 0), P("r2", "b", 1, 1)], Fixed({("r1", "a"): 0})) == "tier"
    assert level([P("r1", "a", 1, 0), P("r2", "b", 1, 1)], Fixed({("r1", "a"): 0, ("r2", "b"): 1})) == "clause_rank"
    assert level([P("r1", "a", 1, 0), P("r2", "b", 1, 1)]) == "fifo_position"
    assert level([P("r1", "a", 1, 0), P("r2", "a", 2, 0)]) == "c_static_m"
    assert level([P("r1", "a", 1, 0, 1), P("r2", "a", 1, 0, 2)]) == "est_finish_s"
    assert level([P("r1", "a", 1, 0), P("r2", "a", 1, 0)]) == "robot_id"


def test_iterative_refuses_a_per_row_callable():
    frame = pd.DataFrame(
        [{"robot_id": "r1", "order_id": "a", "c_static_m": 1.0, "est_finish_s": 1.0, "fifo_position": 0}]
    )
    with pytest.raises(ValueError, match="rule_program"):
        BatchMatcher("iterative").match(frame, rule_coverage_fn=lambda row: (True, 0))


def test_iterative_through_a_frame_drops_ineligible_pairs():
    frame = pd.DataFrame(
        [
            {"robot_id": "r1", "order_id": "a", "c_static_m": 1.0, "est_finish_s": 1.0, "fifo_position": 0, "eligible": False},
            {"robot_id": "r1", "order_id": "b", "c_static_m": 9.0, "est_finish_s": 9.0, "fifo_position": 1, "eligible": True},
        ]
    )
    assert [a.order_id for a in BatchMatcher("iterative").match(frame, rule_program=TAUTOLOGY)] == ["b"]


# Maximum matching needs r1-b and r2-a; covering r1-a blocks r2, which can only take a.
BLOCKING = [P("r1", "a", 1, 0), P("r1", "b", 2, 1), P("r2", "a", 3, 0)]


@pytest.mark.parametrize("mode", ["iterative", "cardinality", "greedy"])
def test_a_covered_pair_is_never_given_up_for_cardinality(mode):
    result = picks(BLOCKING, Fixed({("r1", "a"): 0}), mode)
    assert result == [("r1", "a")]
    assert maximum_cardinality((p.robot_id, p.order_id) for p in BLOCKING) == 2


def test_m_card_maximises_cardinality_within_the_covered_pairs():
    # Rule-only maximum is 2 through r1-b, r2-a, though greedy would stop at r1-a.
    covered = {("r1", "a"): 0, ("r1", "b"): 0, ("r2", "a"): 0}
    assert sorted(picks(BLOCKING, Fixed(covered), "cardinality")) == [("r1", "b"), ("r2", "a")]


def test_m_card_fallback_fills_only_the_leftovers():
    pairs = [P("r1", "a", 1, 0), P("r1", "b", 2, 1), P("r2", "b", 3, 1)]
    frame = BatchMatcher("cardinality").match_pairs(pairs, Fixed({("r1", "a"): 0}))
    by_robot = {a.robot_id: a for a in frame}
    assert by_robot["r1"].order_id == "a" and by_robot["r1"].matched_by_rule and by_robot["r1"].round == 0
    assert by_robot["r2"].order_id == "b" and not by_robot["r2"].matched_by_rule and by_robot["r2"].round == 1


def test_m_card_with_an_empty_rule_stays_maximum():
    assert len(picks(BLOCKING, EMPTY, "cardinality")) == 2


def test_maximum_cardinality_of_nothing_is_zero():
    assert maximum_cardinality([]) == 0
