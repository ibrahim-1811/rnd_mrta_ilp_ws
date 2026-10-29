import re
from ilp_learning.bias_templates import generate_bias_pl


def test_popper_v440_syntax_rules():
    bias = generate_bias_pl(include_tier_b=True, max_vars=4, max_body=4, max_clauses=5)

    # Constraint 1: Zero whitespace inside head_pred/body_pred
    bad_whitespace = re.findall(r"(?:head_pred|body_pred)\([^)]*\s+[^)]*\)", bias)
    assert len(bad_whitespace) == 0, f"Found whitespace in pred declarations: {bad_whitespace}"

    # Constraint 2: Unary tuples in type/direction must have trailing comma
    unary_tuples_without_comma = re.findall(r"(?:type|direction)\([^,]+,\s*\(([a-zA-Z0-9_]+)\)\)", bias)
    assert len(unary_tuples_without_comma) == 0, f"Found unary tuple missing trailing comma: {unary_tuples_without_comma}"

    # Constraint 3: Approved budgets
    assert "max_vars(4)." in bias
    assert "max_body(4)." in bias
    assert "max_clauses(5)." in bias

    # Constraint 4: Pruning constraints
    assert ":- singleton(3)." in bias
