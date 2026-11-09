"""Parse learned Popper clauses and interpret them safely over decision-time pairs.

Enforces:
1. No eval, exec or Prolog at run time: clause text becomes a small AST, which a
   join over ilp_learning.predicates interprets.
2. Permitted vocabulary only: any predicate outside Tiers A and B, any constant, and any
   head other than assign/3 is refused, so a rule cannot name an identifier
   (docs/DECISIONS.md, "Identifiers").
3. Clause rank: Laplace training precision (TP+1)/(TP+FP+2), then smaller body,
   then canonical text (M2 spec 4.4).
4. K_max: Popper's combiner does not enforce it, so a longer program is cut by
   greedy forward selection on training coverage only, keeping raw and pruned
   programs (M2 spec 5.1 item 4).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from fractions import Fraction
import re
from typing import Any, Iterable, Mapping, Sequence

from ilp_learning.predicates import (
    BATTERY_SIGNATURES,
    WORKLOAD_SIGNATURES,
    EXTENDED_SIGNATURES,
    PREDICATE_SIGNATURES,
    TIER_B_SIGNATURES,
    CandidatePair,
    DecisionFacts,
    TierAFacts,
)


K_MAX = 5  # learned clauses kept, M1 Acceptance Record item 5
HEAD_PREDICATE = "assign"

_ATOM = re.compile(r"\s*([a-z][a-z0-9_]*)\(([^()]*)\)\s*")
_VARIABLE = re.compile(r"[A-Z][A-Za-z0-9_]*\Z")


class RuleRefused(ValueError):
    """A clause outside the permitted language: never executed, never repaired."""


@dataclass(frozen=True)
class Literal:
    predicate: str
    args: tuple[str, ...]  # variables, the decision variable first


@dataclass(frozen=True)
class Clause:
    """assign(D,R,O) :- body, with body literals in canonical order."""

    decision: str
    robot: str
    order: str
    body: tuple[Literal, ...]

    @property
    def text(self) -> str:
        """Canonical text: D, R, O, then V0.. in body order, sorted literals."""
        names = {self.decision: "D", self.robot: "R", self.order: "O"}

        def spell(literal: Literal, known: Mapping[str, str]) -> str:
            args = ",".join(known.get(v, "_") for v in literal.args)
            return f"{literal.predicate}({args})"

        # Sort with witnesses blanked, then name them by first appearance, so two
        # spellings of one clause cannot differ by variable naming.
        ordered = sorted(self.body, key=lambda literal: spell(literal, names))
        for literal in ordered:
            for variable in literal.args:
                names.setdefault(variable, f"V{len(names) - 3}")
        body = ",".join(spell(literal, names) for literal in ordered)
        return f"assign(D,R,O):- {body}."

    def covers(self, facts: TierAFacts) -> set[tuple[str, str]]:
        """The (robot, order) pairs of this pair set for which the body holds."""
        anchored = {self.robot, self.order}
        covered = set(facts.relation("candidate_pair"))
        witnessed = []

        for literal in self.body:
            arguments = literal.args[1:]
            if not set(arguments) <= anchored:
                witnessed.append(literal)
                continue
            relation = facts.relation(literal.predicate)
            if arguments == (self.robot, self.order):
                covered &= relation
                continue
            covered = {
                (r, o)
                for r, o in covered
                if tuple(r if a == self.robot else o for a in arguments) in relation
            }

        if not witnessed:
            return covered
        return {
            (r, o)
            for r, o in covered
            if _satisfiable(witnessed, {self.robot: r, self.order: o}, facts)
        }


def _satisfiable(
    literals: Sequence[Literal], bound: Mapping[str, str], facts: TierAFacts
) -> bool:
    """Whether some value of the witness variables makes every literal true."""
    if not literals:
        return True
    literal, *rest = literals
    known = {i: bound[v] for i, v in enumerate(literal.args[1:]) if v in bound}
    for values in facts.matching(literal.predicate, known):
        extended = dict(bound)
        for variable, value in zip(literal.args[1:], values):
            if extended.setdefault(variable, value) != value:
                break
        else:
            if _satisfiable(rest, extended, facts):
                return True
    return False


def _parse_atom(text: str) -> tuple[str, tuple[str, ...]]:
    match = _ATOM.fullmatch(text)
    if match is None:
        raise RuleRefused(f"not a plain atom: {text!r}")
    name = match.group(1)
    args = tuple(arg.strip() for arg in match.group(2).split(","))
    for arg in args:
        if _VARIABLE.match(arg) is None:
            raise RuleRefused(f"constant or malformed argument {arg!r} in {name}: {text!r}")
    return name, args


def _split_atoms(text: str) -> list[str]:
    atoms, depth, start = [], 0, 0
    for index, char in enumerate(text):
        depth += (char == "(") - (char == ")")
        if char == "," and depth == 0:
            atoms.append(text[start:index])
            start = index + 1
    atoms.append(text[start:])
    return atoms


def parse_clause(text: str) -> Clause:
    """One clause of Popper output to its AST, or RuleRefused."""
    stripped = text.strip()
    if not stripped.endswith("."):
        raise RuleRefused(f"clause does not end with a full stop: {text!r}")
    parts = stripped[:-1].split(":-")
    if len(parts) != 2 or not parts[1].strip():
        raise RuleRefused(f"expected one head and a non-empty body: {text!r}")

    head_name, head_args = _parse_atom(parts[0])
    if head_name != HEAD_PREDICATE or len(head_args) != 3 or len(set(head_args)) != 3:
        raise RuleRefused(f"head must be assign/3 over three variables: {text!r}")
    decision, robot, order = head_args

    kinds = {decision: "decision", robot: "robot", order: "order"}
    body = []
    for atom in _split_atoms(parts[1]):
        name, args = _parse_atom(atom)
        if name not in PREDICATE_SIGNATURES:
            raise RuleRefused(f"predicate '{name}' is outside the permitted set")
        expected = ("decision", *PREDICATE_SIGNATURES[name])
        if len(args) != len(expected):
            raise RuleRefused(f"{name} takes {len(expected)} arguments: {text!r}")
        for variable, kind in zip(args, expected):
            if kinds.setdefault(variable, kind) != kind:
                raise RuleRefused(f"variable {variable} used as two kinds: {text!r}")
        # One decision variable: a rule cannot join facts across decisions.
        if args[0] != decision:
            raise RuleRefused(f"{name} reads another decision: {text!r}")
        body.append(Literal(name, args))
    return Clause(decision, robot, order, tuple(sorted(body, key=lambda b: (b.predicate, b.args))))


@dataclass(frozen=True)
class RuleProgram:
    """Clauses in rank order; clause_rank is the position here."""

    clauses: tuple[Clause, ...] = ()
    thresholds: Any = field(default=None, compare=False)

    @property
    def needs_congestion(self) -> bool:
        return any(
            literal.predicate in TIER_B_SIGNATURES
            for clause in self.clauses
            for literal in clause.body
        )

    @property
    def needs_battery(self) -> bool:
        return any(
            literal.predicate in BATTERY_SIGNATURES
            for clause in self.clauses
            for literal in clause.body
        )

    @property
    def needs_workload(self) -> bool:
        return any(
            literal.predicate in WORKLOAD_SIGNATURES
            for clause in self.clauses
            for literal in clause.body
        )

    @property
    def needs_load(self) -> bool:
        return any(
            literal.predicate in EXTENDED_SIGNATURES
            for clause in self.clauses
            for literal in clause.body
        )

    def coverage(self, pairs: Sequence[CandidatePair]) -> dict[tuple[str, str], int]:
        """(robot, order) -> lowest rank of a clause firing on it, on this pair set."""
        facts = DecisionFacts(pairs, self.thresholds)
        ranks: dict[tuple[str, str], int] = {}
        for rank, clause in enumerate(self.clauses):
            for pair in clause.covers(facts):
                ranks.setdefault(pair, rank)
        return ranks


@dataclass(frozen=True)
class DecisionExamples:
    """One training decision: all its eligible pairs, and its labelled subset."""

    pairs: tuple[CandidatePair, ...]
    positives: frozenset[tuple[str, str]]
    negatives: frozenset[tuple[str, str]]


@dataclass(frozen=True)
class ClauseMetrics:
    tp: int
    fp: int
    fn: int
    tn: int
    covered_pairs: int
    eligible_pairs: int

    @property
    def laplace(self) -> Fraction:
        return Fraction(self.tp + 1, self.tp + self.fp + 2)

    def as_dict(self) -> dict:
        total_positive = self.tp + self.fn
        return {
            **asdict(self),
            "laplace_precision": float(self.laplace),
            "precision": self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0,
            "recall": self.tp / total_positive if total_positive else 0.0,
            "rule_coverage": self.covered_pairs / self.eligible_pairs if self.eligible_pairs else 0.0,
        }


def _coverage(
    clause: Clause, facts_of: Sequence[TierAFacts]
) -> list[set[tuple[str, str]]]:
    return [clause.covers(facts) for facts in facts_of]


def _metrics(
    covered: Iterable[set[tuple[str, str]]], examples: Sequence[DecisionExamples]
) -> ClauseMetrics:
    tp = fp = fn = tn = covered_pairs = eligible_pairs = 0
    for pairs, decision in zip(covered, examples):
        tp += len(pairs & decision.positives)
        fn += len(decision.positives - pairs)
        fp += len(pairs & decision.negatives)
        tn += len(decision.negatives - pairs)
        covered_pairs += len(pairs)
        eligible_pairs += len(decision.pairs)
    return ClauseMetrics(tp, fp, fn, tn, covered_pairs, eligible_pairs)


def _select(
    ranked: Sequence[Clause],
    covered: Mapping[Clause, list[set[tuple[str, str]]]],
    examples: Sequence[DecisionExamples],
    k_max: int,
) -> list[Clause]:
    """Greedy forward selection: each step takes the clause lowering FN + FP most."""
    chosen: list[Clause] = []
    union: list[set[tuple[str, str]]] = [set() for _ in examples]

    def error(coverage: Sequence[set[tuple[str, str]]]) -> int:
        metrics = _metrics(coverage, examples)
        return metrics.fn + metrics.fp

    best_error = error(union)
    while len(chosen) < k_max:
        scored = [
            (error([u | c for u, c in zip(union, covered[clause])]), rank)
            for rank, clause in enumerate(ranked)
            if clause not in chosen
        ]
        if not scored or min(scored)[0] >= best_error:
            break
        best_error, rank = min(scored)
        chosen.append(ranked[rank])
        union = [u | c for u, c in zip(union, covered[ranked[rank]])]
    return sorted(chosen, key=ranked.index)


@dataclass(frozen=True)
class CompiledRules:
    raw: tuple[str, ...]
    program: RuleProgram
    pruned: tuple[str, ...]  # canonical clauses dropped by K_max selection
    clause_metrics: tuple[ClauseMetrics, ...]  # by clause_rank
    program_metrics: ClauseMetrics


def compile_rules(
    raw_rules: Sequence[str],
    examples: Sequence[DecisionExamples],
    k_max: int = K_MAX,
    thresholds: Any = None,
) -> CompiledRules:
    """Parse, rank and cap a learned program using training coverage only."""
    clauses = list({clause.text: clause for clause in map(parse_clause, raw_rules)}.values())
    facts_of = [DecisionFacts(decision.pairs, thresholds) for decision in examples]
    covered = {clause: _coverage(clause, facts_of) for clause in clauses}
    metrics = {clause: _metrics(covered[clause], examples) for clause in clauses}

    ranked = sorted(
        clauses, key=lambda c: (-metrics[c].laplace, len(c.body), c.text)
    )
    kept = ranked if len(ranked) <= k_max else _select(ranked, covered, examples, k_max)

    union = [set().union(*sets) for sets in zip(*(covered[c] for c in kept))] if kept else [
        set() for _ in examples
    ]
    return CompiledRules(
        raw=tuple(raw_rules),
        program=RuleProgram(tuple(kept), thresholds),
        pruned=tuple(c.text for c in ranked if c not in kept),
        clause_metrics=tuple(metrics[c] for c in kept),
        program_metrics=_metrics(union, examples),
    )
