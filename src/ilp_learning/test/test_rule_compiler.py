"""Clause parsing, refusal, safe interpretation, ranking and the K_max cap."""

import ast
from pathlib import Path

import pytest

import ilp_learning.predicates
import ilp_learning.rule_compiler
from ilp_learning.predicates import CandidatePair, TierAFacts
from ilp_learning.rule_compiler import (
    K_MAX,
    Clause,
    DecisionExamples,
    RuleProgram,
    RuleRefused,
    compile_rules,
    parse_clause,
)

PAIRS = (
    CandidatePair("r1", "a", 5.0, 9.0, 1),
    CandidatePair("r1", "b", 7.0, 8.0, 0),
    CandidatePair("r2", "a", 3.0, 4.0, 1),
    CandidatePair("r2", "b", 8.0, 9.0, 0),
)


def covers(text, pairs=PAIRS):
    return parse_clause(text).covers(TierAFacts(pairs))


def test_popper_text_parses_to_an_ast():
    clause = parse_clause("assign(V0,V1,V2):- nearest_static(V0,V1,V2),best_cost_for_order(V0,V1,V2).")
    assert isinstance(clause, Clause)
    assert [b.predicate for b in clause.body] == ["best_cost_for_order", "nearest_static"]


def test_canonical_text_ignores_variable_names_and_body_order():
    a = parse_clause("assign(A,B,C):- nearest_static(A,B,C),oldest_fifo(A,C).")
    b = parse_clause("assign(X,Y,Z):- oldest_fifo(X,Z),nearest_static(X,Y,Z).")
    assert a.text == b.text == "assign(D,R,O):- nearest_static(D,R,O),oldest_fifo(D,O)."
    assert parse_clause(a.text).text == a.text


@pytest.mark.parametrize(
    "text",
    [
        "assign(A,B,C):- robot_is_first(A,B,C).",                # outside the set
        "assign(A,B,C):- lowest_occupied_for_robot(A,B,C).",     # Tier B
        "assign(A,B,C):- nearest_static(A,robot_000,C).",        # constant
        "assign(A,B,C):- nearest_static(A,B,'o0001').",          # quoted constant
        "assign(A,B,C):- closer_static(A,B,C,3).",               # number
        "prefer(A,B,C):- nearest_static(A,B,C).",                # head
        "assign(A,B):- candidate_robot(A,B).",                   # head arity
        "assign(A,A,C):- oldest_fifo(A,C).",                     # head repeats
        "assign(a,B,C):- oldest_fifo(a,C).",                     # head constant
        "assign(A,B,C).",                                        # no body
        "assign(A,B,C):- .",                                     # empty body
        "assign(A,B,C):- nearest_static(A,B,C)",                 # no full stop
        "assign(A,B,C):- nearest_static(A,B).",                  # arity
        "assign(A,B,C):- oldest_fifo(D,C).",                     # another decision
        "assign(A,B,C):- nearest_static(A,C,B).",                # kinds swapped
        "assign(A,B,C):- \\+nearest_static(A,B,C).",             # negation
        "assign(A,B,C):- nearest_static(A,B,C);oldest_fifo(A,C).",
        "assign(A,B,C):- nearest_static(A,B,C):- oldest_fifo(A,C).",
        "assign(A,B,C):- nearest_static(A,B,C),X is 1.",
        "assign(A,B,C):- __import__(A).",
    ],
)
def test_outside_the_language_is_refused(text):
    with pytest.raises(RuleRefused):
        parse_clause(text)


@pytest.mark.parametrize("module", [ilp_learning.rule_compiler, ilp_learning.predicates])
def test_no_eval_exec_or_prolog_at_run_time(module):
    tree = ast.parse(Path(module.__file__).read_text())
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & {"eval", "exec", "compile", "__import__"}
    imported = {
        alias.name.split(".")[0]
        for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names
    } | {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not imported & {"janus_swi", "popper", "clingo", "subprocess"}


def test_conjunction_covers_the_mutual_best_only():
    assert covers("assign(A,B,C):- nearest_static(A,B,C),best_cost_for_order(A,B,C).") == {("r2", "a")}


def test_existential_witness_joins_through_a_second_literal():
    # r has a costlier order than O, and that order is the oldest.
    text = "assign(A,B,C):- closer_static(A,B,C,D),oldest_fifo(A,D)."
    assert covers(text) == {("r1", "a"), ("r2", "a")}


def test_a_head_variable_the_body_never_binds_ranges_over_the_candidates():
    assert covers("assign(A,B,C):- oldest_fifo(A,C).") == {("r1", "b"), ("r2", "b")}


def test_program_rank_is_the_lowest_firing_clause():
    program = RuleProgram((
        parse_clause("assign(A,B,C):- oldest_fifo(A,C)."),
        parse_clause("assign(A,B,C):- candidate_pair(A,B,C)."),
    ))
    ranks = program.coverage(PAIRS)
    assert ranks[("r1", "b")] == 0 and ranks[("r1", "a")] == 1


def test_empty_program_covers_nothing():
    assert RuleProgram().coverage(PAIRS) == {}


def decision(pos, neg):
    return DecisionExamples(PAIRS, frozenset(pos), frozenset(neg))


def test_rank_is_laplace_then_body_then_text():
    # All three: TP 1, FP 0, so Laplace ties; the smaller body wins, then the text.
    examples = [decision({("r2", "a")}, {("r2", "b")})]
    compiled = compile_rules(
        [
            "assign(A,B,C):- nearest_static(A,B,C),best_cost_for_order(A,B,C).",
            "assign(A,B,C):- candidate_order(A,C),best_cost_for_order(A,B,C).",
            "assign(A,B,C):- best_cost_for_order(A,B,C).",
        ],
        examples,
    )
    assert len({m.laplace for m in compiled.clause_metrics}) == 1
    assert [c.text for c in compiled.program.clauses] == [
        "assign(D,R,O):- best_cost_for_order(D,R,O).",
        "assign(D,R,O):- best_cost_for_order(D,R,O),candidate_order(D,O).",
        "assign(D,R,O):- best_cost_for_order(D,R,O),nearest_static(D,R,O).",
    ]


def test_laplace_beats_body_size():
    examples = [decision({("r2", "a"), ("r1", "a")}, {("r1", "b"), ("r2", "b")})]
    compiled = compile_rules(
        [
            "assign(A,B,C):- candidate_pair(A,B,C).",  # TP 2 FP 2
            "assign(A,B,C):- nearest_static(A,B,C),candidate_order(A,C).",  # r2-a, r1-a: TP 2 FP 0
        ],
        examples,
    )
    assert len(compiled.program.clauses[0].body) == 2


def test_per_clause_metrics_are_reported_separately():
    examples = [decision({("r2", "a"), ("r1", "b")}, {("r2", "b")})]
    compiled = compile_rules(
        ["assign(A,B,C):- best_cost_for_order(A,B,C).", "assign(A,B,C):- oldest_fifo(A,C)."],
        examples,
    )
    by_text = {
        c.text: m.as_dict() for c, m in zip(compiled.program.clauses, compiled.clause_metrics)
    }
    best = by_text["assign(D,R,O):- best_cost_for_order(D,R,O)."]
    assert (best["tp"], best["fp"], best["fn"], best["tn"]) == (2, 0, 0, 1)
    assert best["precision"] == 1.0 and best["recall"] == 1.0
    assert best["rule_coverage"] == 2 / 4
    oldest = by_text["assign(D,R,O):- oldest_fifo(D,O)."]
    assert (oldest["tp"], oldest["fp"]) == (1, 1)
    assert oldest["laplace_precision"] == 2 / 4


def wide_examples():
    """Seven orders, each with its own true rule-shaped signal for the cap."""
    pairs = tuple(CandidatePair("r1", f"o{i}", float(i), float(10 - i), i) for i in range(4))
    return [DecisionExamples(pairs, frozenset({("r1", "o0"), ("r1", "o1")}), frozenset({("r1", "o3")}))]


def test_a_program_over_k_max_is_cut_and_logged():
    raw = [
        "assign(A,B,C):- nearest_static(A,B,C).",
        "assign(A,B,C):- second_nearest_static(A,B,C).",
        "assign(A,B,C):- oldest_fifo(A,C).",
        "assign(A,B,C):- earliest_static_finish(A,B,C).",
        "assign(A,B,C):- second_earliest_static_finish(A,B,C).",
        "assign(A,B,C):- candidate_pair(A,B,C).",
    ]
    assert len(raw) > K_MAX
    compiled = compile_rules(raw, wide_examples())
    assert len(compiled.program.clauses) <= K_MAX
    assert compiled.raw == tuple(raw)
    kept = {c.text for c in compiled.program.clauses}
    assert compiled.pruned and kept.isdisjoint(compiled.pruned)
    assert len(kept) + len(compiled.pruned) == len(raw)
    # Training error of the kept program: both positives, the negative left out.
    assert compiled.program_metrics.fn == 0 and compiled.program_metrics.fp == 0


def test_a_program_within_k_max_is_not_pruned():
    compiled = compile_rules(["assign(A,B,C):- nearest_static(A,B,C)."], wide_examples())
    assert compiled.pruned == ()


def test_an_empty_program_compiles():
    compiled = compile_rules([], wide_examples())
    assert compiled.program.clauses == () and compiled.program_metrics.tp == 0
    assert compiled.program_metrics.fn == 2


def test_a_refused_clause_stops_compilation():
    with pytest.raises(RuleRefused):
        compile_rules(["assign(A,B,C):- robot_is_first(A,B,C)."], wide_examples())
