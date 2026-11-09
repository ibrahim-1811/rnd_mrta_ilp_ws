"""Live explanations of a learned policy's picks (display only)."""

from ilp_learning.explain import FALLBACK_TEXT, explain_decision
from ilp_learning.matcher import BatchMatcher
from ilp_learning.predicates import CandidatePair
from ilp_learning.rule_compiler import RuleProgram, parse_clause

RULE = "assign(A,B,C):- best_finish_for_order(A,B,C),nearest_static(A,B,C)."


def pair(robot, order, cost, finish, fifo):
    return CandidatePair(robot, order, cost, finish, fifo)


def test_each_pick_names_its_rule_and_the_numbers_behind_it():
    program = RuleProgram((parse_clause(RULE),))
    pairs = [pair("r1", "a", 10.0, 20.0, 0), pair("r1", "b", 30.0, 40.0, 1),
             pair("r2", "a", 25.0, 35.0, 0), pair("r2", "b", 50.0, 60.0, 1)]
    picks = BatchMatcher(mode="iterative").match_pairs(pairs, program)
    explained = explain_decision(program, pairs, picks)
    first = explained[0]
    assert (first["robot"], first["order"], first["rule"]) == ("r1", "a", 1)
    assert "r1 finishes a soonest (20 s, next best 35 s)" in first["text"]
    assert "a is r1's closest order (10 m" in first["text"]


def test_a_fallback_pick_says_so():
    program = RuleProgram(())
    pairs = [pair("r1", "a", 10.0, 20.0, 0), pair("r1", "b", 5.0, 15.0, 1)]
    picks = BatchMatcher(mode="iterative").match_pairs(pairs, program)
    explained = explain_decision(program, pairs, picks)
    assert explained[0]["rule"] is None and explained[0]["text"] == FALLBACK_TEXT
