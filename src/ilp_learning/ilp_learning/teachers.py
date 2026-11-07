"""The eight conventional teachers, and how a rule learned from each is executed.

    python -m ilp_learning.teachers matching nearest_astar          -> iterative
    python -m ilp_learning.teachers matching runs/.../rule_artifact.json

Execution semantics (DECISIONS.md, "M3 Execution Semantics Amendment (2026-10-31)",
items 1-2): a rule learned from a teacher that picks pairs one at a time runs under
the iterative matcher; a rule learned from a teacher that solves the whole batch at
once (hungarian, milp) runs under one-shot M-Card ("cardinality"). random and
nearest_manhattan are the same greedy sweep as nearest_astar with another ordering
(ROADMAP.md, Phase 7 slice 1), so they take the iterative matcher too. Pooled,
panel-vote and outcome-aware rule sets have no single teacher and take the iterative
default. Fidelity and closed-loop evaluation read the matcher from here, so a teacher's
rules are scored the way that teacher decides.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


TEACHERS = (
    "random", "nearest_manhattan", "nearest_astar", "earliest_finish",
    "multi_criteria", "auction", "hungarian", "milp",
)
ONE_SHOT = frozenset({"hungarian", "milp"})
DEFAULT_MATCHING = "iterative"


def matching_for(teacher: str) -> str:
    """The matcher a rule set learned from this label source runs under."""
    if teacher in TEACHERS:
        return "cardinality" if teacher in ONE_SHOT else DEFAULT_MATCHING
    if teacher.startswith(("pooled", "panel_")) or teacher == "outcome_aware":
        return DEFAULT_MATCHING
    raise ValueError(f"unknown label source {teacher!r}; teachers: {TEACHERS}")


def matching_of_artifact(path: Path | str) -> str:
    """The matcher for a written rule artifact, from the label source it records."""
    return matching_for(json.loads(Path(path).read_text(encoding="utf-8"))["teacher"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("what", choices=("matching", "list"))
    parser.add_argument("source", nargs="?", help="teacher name or rule_artifact.json")
    args = parser.parse_args()
    if args.what == "list":
        print("\n".join(TEACHERS))
        return 0
    if args.source is None:
        parser.error("matching needs a teacher name or an artifact path")
    source = Path(args.source)
    print(matching_of_artifact(source) if source.suffix == ".json" else matching_for(args.source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
