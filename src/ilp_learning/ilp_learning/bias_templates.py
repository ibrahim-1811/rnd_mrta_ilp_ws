"""Popper v4.4.0 bias template generator adhering to verified syntax constraints.

Verified Popper constraints:
1. Zero internal whitespace inside head_pred(...) and body_pred(...).
2. Trailing commas on all unary tuples: type(p,(t,)). and direction(p,(d,)).
3. Pure Datalog typing and valid variable flow (in/out directions).
4. Pruning constraints: :- singleton(3). to avoid redundant search.
"""

from __future__ import annotations

from pathlib import Path


BASE_PREDICATES = [
    # (predicate_name, arity, type_tuple, direction_tuple)
    ("candidate_pair", 3, "(decision,robot,order)", "(in,in,in)"),
    ("candidate_robot", 2, "(decision,robot)", "(in,in)"),
    ("candidate_order", 2, "(decision,order)", "(in,in)"),
    ("nearest_static", 3, "(decision,robot,order)", "(in,in,in)"),
    ("best_cost_for_order", 3, "(decision,robot,order)", "(in,in,in)"),
    ("second_nearest_static", 3, "(decision,robot,order)", "(in,in,in)"),
    ("earliest_static_finish", 3, "(decision,robot,order)", "(in,in,in)"),
    ("second_earliest_static_finish", 3, "(decision,robot,order)", "(in,in,in)"),
    ("oldest_fifo", 2, "(decision,order)", "(in,in)"),
    ("closer_static", 4, "(decision,robot,order,order)", "(in,in,in,out)"),
    ("earlier_static_finish", 4, "(decision,robot,order,order)", "(in,in,in,out)"),
    ("prior_fifo", 3, "(decision,order,order)", "(in,in,out)"),
]

TIER_B_CONGESTION_PREDICATES = [
    ("lowest_occupied_for_robot", 3, "(decision,robot,order)", "(in,in,in)"),
    ("fewest_conflict_robots_for_robot", 3, "(decision,robot,order)", "(in,in,in)"),
    ("fewest_conflicts_for_robot", 3, "(decision,robot,order)", "(in,in,in)"),
    ("low_local_density", 2, "(decision,robot)", "(in,in)"),
    ("low_route_overlap", 3, "(decision,robot,order)", "(in,in,in)"),
    ("high_occupied_now", 3, "(decision,robot,order)", "(in,in,in)"),
    ("high_conflict_robots", 3, "(decision,robot,order)", "(in,in,in)"),
]


def generate_bias_pl(
    include_tier_b: bool = True,
    max_vars: int = 4,
    max_body: int = 4,
    max_clauses: int = 5,
) -> str:
    """Generate validated Popper v4.4.0 bias.pl contents."""
    lines: list[str] = [
        "%% Head declaration",
        "head_pred(assign,3).",
        "type(assign,(decision,robot,order)).",
        "direction(assign,(in,in,in)).",
        "",
        "%% Search budget constraints",
        f"max_vars({max_vars}).",
        f"max_body({max_body}).",
        f"max_clauses({max_clauses}).",
        "",
        "%% Body declarations",
    ]

    preds = list(BASE_PREDICATES)
    if include_tier_b:
        preds.extend(TIER_B_CONGESTION_PREDICATES)

    for name, arity, types, dirs in preds:
        lines.append(f"body_pred({name},{arity}).")
        lines.append(f"type({name},{types}).")
        lines.append(f"direction({name},{dirs}).")
        lines.append("")

    lines.extend([
        "%% Pruning constraints",
        ":- singleton(3).",
        "",
    ])

    return "\n".join(lines)


def write_bias_pl(
    path: Path | str,
    include_tier_b: bool = True,
    max_vars: int = 4,
    max_body: int = 4,
    max_clauses: int = 5,
) -> None:
    """Write bias.pl file to destination."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = generate_bias_pl(
        include_tier_b=include_tier_b,
        max_vars=max_vars,
        max_body=max_body,
        max_clauses=max_clauses,
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

