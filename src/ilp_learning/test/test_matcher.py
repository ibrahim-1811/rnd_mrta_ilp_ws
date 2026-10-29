import pandas as pd

from ilp_learning.matcher import BatchMatcher


def _make_sample_batch():
    # 2 free robots (R01, R02), 3 available orders (O001, O002, O003)
    return pd.DataFrame([
        # R01 options:
        {"robot_id": "R01", "order_id": "O001", "c_static_m": 10.0, "est_finish_s": 8.0, "fifo_position": 1, "eligible": True},
        {"robot_id": "R01", "order_id": "O002", "c_static_m": 25.0, "est_finish_s": 18.0, "fifo_position": 0, "eligible": True},
        {"robot_id": "R01", "order_id": "O003", "c_static_m": 5.0,  "est_finish_s": 4.0,  "fifo_position": 2, "eligible": False},  # Ineligible!
        # R02 options:
        {"robot_id": "R02", "order_id": "O001", "c_static_m": 12.0, "est_finish_s": 9.0, "fifo_position": 1, "eligible": True},
        {"robot_id": "R02", "order_id": "O002", "c_static_m": 14.0, "est_finish_s": 10.0, "fifo_position": 0, "eligible": True},
    ])


def test_hard_feasibility_and_ineligible_exclusion():
    df = _make_sample_batch()
    matcher = BatchMatcher(mode="cardinality")
    assignments = matcher.match(df)

    # 1. 2 robots matched to 2 distinct orders
    assert len(assignments) == 2
    matched_robots = {a.robot_id for a in assignments}
    matched_orders = {a.order_id for a in assignments}
    assert len(matched_robots) == 2
    assert len(matched_orders) == 2

    # 2. Ineligible order O003 is NEVER matched
    assert "O003" not in matched_orders


def test_learned_rule_preference_influences_matching():
    df = _make_sample_batch()
    matcher = BatchMatcher(mode="cardinality")

    # Learned rule fires ONLY on (R01, O002) - even though O002 is statically further (25m vs 10m)
    def rule_coverage(row):
        if row["robot_id"] == "R01" and row["order_id"] == "O002":
            return (True, 0)
        return (False, 999)

    assignments = matcher.match(df, rule_coverage_fn=rule_coverage)
    r1_assign = next(a for a in assignments if a.robot_id == "R01")

    # R01 MUST be assigned to O002 due to tier 0 rule preference!
    assert r1_assign.order_id == "O002"
    assert r1_assign.tier == 0


def test_anti_circularity_tie_break():
    # If no rule fires (tier 1 for all), matching must prioritize fifo_position over c_static_m
    df = pd.DataFrame([
        # R01 can take O_old (fifo 0, dist 20m) or O_close (fifo 1, dist 5m)
        {"robot_id": "R01", "order_id": "O_old", "c_static_m": 20.0, "est_finish_s": 15.0, "fifo_position": 0, "eligible": True},
        {"robot_id": "R01", "order_id": "O_close", "c_static_m": 5.0, "est_finish_s": 4.0, "fifo_position": 1, "eligible": True},
    ])

    matcher = BatchMatcher(mode="cardinality")
    assignments = matcher.match(df)

    assert len(assignments) == 1
    # Must assign O_old (FIFO queue priority) rather than secretly doing nearest A*!
    assert assignments[0].order_id == "O_old"


def test_greedy_ablation_mode():
    df = _make_sample_batch()
    greedy_matcher = BatchMatcher(mode="greedy")
    assignments = greedy_matcher.match(df)

    assert len(assignments) == 2
    matched_robots = {a.robot_id for a in assignments}
    matched_orders = {a.order_id for a in assignments}
    assert len(matched_robots) == 2
    assert len(matched_orders) == 2
    assert "O003" not in matched_orders


def test_adversarial_disconnected_components():
    """Bipartite graph with 3 disconnected components: {R01, R02} - {O01}, {R03} - {O02}, {R04, R05} - {O03}.

    Max feasible cardinality is exactly 3. Exactly 2 robots must remain unmatched.
    """
    df = pd.DataFrame([
        # Component 1
        {"robot_id": "R01", "order_id": "O01", "c_static_m": 10.0, "est_finish_s": 10.0, "fifo_position": 0, "eligible": True},
        {"robot_id": "R02", "order_id": "O01", "c_static_m": 12.0, "est_finish_s": 12.0, "fifo_position": 0, "eligible": True},
        # Component 2
        {"robot_id": "R03", "order_id": "O02", "c_static_m": 15.0, "est_finish_s": 15.0, "fifo_position": 1, "eligible": True},
        # Component 3
        {"robot_id": "R04", "order_id": "O03", "c_static_m": 8.0, "est_finish_s": 8.0, "fifo_position": 2, "eligible": True},
        {"robot_id": "R05", "order_id": "O03", "c_static_m": 9.0, "est_finish_s": 9.0, "fifo_position": 2, "eligible": True},
    ])

    matcher = BatchMatcher(mode="cardinality")
    assignments = matcher.match(df)

    assert len(assignments) == 3
    matched_robots = {a.robot_id for a in assignments}
    matched_orders = {a.order_id for a in assignments}

    assert matched_orders == {"O01", "O02", "O03"}
    # R01 beats R02 on distance; R04 beats R05 on distance
    assert matched_robots == {"R01", "R03", "R04"}
    assert {"R02", "R05"}.isdisjoint(matched_robots)


def test_adversarial_infeasible_perfect_matching():
    """Graph cannot admit a perfect matching: 3 robots, 3 orders, but max cardinality is 1.

    R01 and R02 can only do O01. R03 has no feasible orders. O02 and O03 have no feasible robots.
    M-Card must assign exactly 1 pair, leaving 2 robots and 2 orders unmatched.
    """
    df = pd.DataFrame([
        {"robot_id": "R01", "order_id": "O01", "c_static_m": 10.0, "est_finish_s": 10.0, "fifo_position": 0, "eligible": True},
        {"robot_id": "R02", "order_id": "O01", "c_static_m": 5.0, "est_finish_s": 5.0, "fifo_position": 0, "eligible": True},
        {"robot_id": "R03", "order_id": "O02", "c_static_m": 20.0, "est_finish_s": 20.0, "fifo_position": 1, "eligible": False},
        {"robot_id": "R01", "order_id": "O03", "c_static_m": 30.0, "est_finish_s": 30.0, "fifo_position": 2, "eligible": False},
    ])

    matcher = BatchMatcher(mode="cardinality")
    assignments = matcher.match(df)

    assert len(assignments) == 1
    # R02 is closer (5m vs 10m) to O01
    assert assignments[0].robot_id == "R02"
    assert assignments[0].order_id == "O01"


def test_adversarial_asymmetric_fleet_and_orders():
    """Test rectangular matching with unmatched robots (Nr > No) and unmatched orders (No > Nr)."""
    # Case A: 5 robots, 2 orders -> exactly 2 matched, 3 robots unmatched
    df_more_robots = pd.DataFrame([
        {"robot_id": f"R0{i}", "order_id": "O01", "c_static_m": float(i * 5), "est_finish_s": float(i * 5), "fifo_position": 0, "eligible": True}
        for i in range(1, 6)
    ] + [
        {"robot_id": f"R0{i}", "order_id": "O02", "c_static_m": float(i * 6), "est_finish_s": float(i * 6), "fifo_position": 1, "eligible": True}
        for i in range(1, 6)
    ])

    matcher = BatchMatcher(mode="cardinality")
    assign_a = matcher.match(df_more_robots)
    assert len(assign_a) == 2
    assert len({a.robot_id for a in assign_a}) == 2
    assert {a.order_id for a in assign_a} == {"O01", "O02"}

    # Case B: 2 robots, 5 orders -> exactly 2 matched, 3 orders unmatched
    df_more_orders = pd.DataFrame([
        {"robot_id": "R01", "order_id": f"O0{j}", "c_static_m": float(j * 10), "est_finish_s": float(j * 10), "fifo_position": j, "eligible": True}
        for j in range(1, 6)
    ] + [
        {"robot_id": "R02", "order_id": f"O0{j}", "c_static_m": float(j * 8), "est_finish_s": float(j * 8), "fifo_position": j, "eligible": True}
        for j in range(1, 6)
    ])

    assign_b = matcher.match(df_more_orders)
    assert len(assign_b) == 2
    assert len({a.order_id for a in assign_b}) == 2
    # FIFO queue priority: O01 and O02 must be chosen over O03, O04, O05
    assert {a.order_id for a in assign_b} == {"O01", "O02"}


def test_augmenting_path_beats_short_sighted_greedy():
    """Classic augmenting path case where greedy matching yields suboptimal cardinality 1, while M-Card yields 2.

    R01 can do O01 (dist 1m) and O02 (dist 10m).
    R02 can ONLY do O01 (dist 5m).
    Greedy assigns R01 -> O01 (dist 1m), leaving R02 stranded (cardinality 1).
    M-Card must assign R01 -> O02 and R02 -> O01 (cardinality 2).
    """
    df = pd.DataFrame([
        {"robot_id": "R01", "order_id": "O01", "c_static_m": 1.0, "est_finish_s": 1.0, "fifo_position": 0, "eligible": True},
        {"robot_id": "R01", "order_id": "O02", "c_static_m": 10.0, "est_finish_s": 10.0, "fifo_position": 0, "eligible": True},
        {"robot_id": "R02", "order_id": "O01", "c_static_m": 5.0, "est_finish_s": 5.0, "fifo_position": 0, "eligible": True},
    ])

    m_card = BatchMatcher(mode="cardinality")
    assign_card = m_card.match(df)
    assert len(assign_card) == 2
    card_map = {a.robot_id: a.order_id for a in assign_card}
    assert card_map == {"R01": "O02", "R02": "O01"}

    # In contrast, greedy picks R01 -> O01 first, stranding R02:
    m_greedy = BatchMatcher(mode="greedy")
    assign_greedy = m_greedy.match(df)
    assert len(assign_greedy) == 1
    assert assign_greedy[0].robot_id == "R01"
    assert assign_greedy[0].order_id == "O01"


def test_exact_tie_breaking_hierarchy_all_levels():
    """Verify each level of (tier, rule_rank, fifo_position, c_static_m, est_finish_s, robot_id)."""
    matcher = BatchMatcher(mode="cardinality")

    # Level 1: Tier 0 beats Tier 1 (even if Tier 1 has 0 FIFO and shorter distance)
    df_tier = pd.DataFrame([
        {"robot_id": "R01", "order_id": "O01", "c_static_m": 50.0, "est_finish_s": 50.0, "fifo_position": 5, "eligible": True},
        {"robot_id": "R01", "order_id": "O02", "c_static_m": 5.0, "est_finish_s": 5.0, "fifo_position": 0, "eligible": True},
    ])
    assign_tier = matcher.match(df_tier, rule_coverage_fn=lambda r: (True, 0) if r["order_id"] == "O01" else (False, 999))
    assert assign_tier[0].order_id == "O01"
    assert assign_tier[0].tier == 0

    # Level 2: Rule rank 0 beats Rule rank 1 (even if rank 1 has 0 FIFO and shorter distance)
    df_rule = pd.DataFrame([
        {"robot_id": "R01", "order_id": "O01", "c_static_m": 50.0, "est_finish_s": 50.0, "fifo_position": 5, "eligible": True},
        {"robot_id": "R01", "order_id": "O02", "c_static_m": 5.0, "est_finish_s": 5.0, "fifo_position": 0, "eligible": True},
    ])
    assign_rule = matcher.match(df_rule, rule_coverage_fn=lambda r: (True, 0) if r["order_id"] == "O01" else (True, 1))
    assert assign_rule[0].order_id == "O01"
    assert assign_rule[0].rule_rank == 0

    # Level 3: FIFO 0 beats FIFO 1 (even if FIFO 1 has shorter distance)
    df_fifo = pd.DataFrame([
        {"robot_id": "R01", "order_id": "O01", "c_static_m": 50.0, "est_finish_s": 50.0, "fifo_position": 0, "eligible": True},
        {"robot_id": "R01", "order_id": "O02", "c_static_m": 5.0, "est_finish_s": 5.0, "fifo_position": 1, "eligible": True},
    ])
    assign_fifo = matcher.match(df_fifo)
    assert assign_fifo[0].order_id == "O01"

    # Level 4: c_static_m 10m beats 20m (same FIFO)
    df_dist = pd.DataFrame([
        {"robot_id": "R01", "order_id": "O01", "c_static_m": 10.0, "est_finish_s": 50.0, "fifo_position": 0, "eligible": True},
        {"robot_id": "R01", "order_id": "O02", "c_static_m": 20.0, "est_finish_s": 5.0, "fifo_position": 0, "eligible": True},
    ])
    assign_dist = matcher.match(df_dist)
    assert assign_dist[0].order_id == "O01"

    # Level 5: est_finish_s 10s beats 20s (same distance)
    df_finish = pd.DataFrame([
        {"robot_id": "R01", "order_id": "O01", "c_static_m": 10.0, "est_finish_s": 10.0, "fifo_position": 0, "eligible": True},
        {"robot_id": "R01", "order_id": "O02", "c_static_m": 10.0, "est_finish_s": 20.0, "fifo_position": 0, "eligible": True},
    ])
    assign_finish = matcher.match(df_finish)
    assert assign_finish[0].order_id == "O01"

    # Level 6: robot_id 'R01' beats 'R02' for the same order when all else is identical
    df_robot = pd.DataFrame([
        {"robot_id": "R01", "order_id": "O01", "c_static_m": 10.0, "est_finish_s": 10.0, "fifo_position": 0, "eligible": True},
        {"robot_id": "R02", "order_id": "O01", "c_static_m": 10.0, "est_finish_s": 10.0, "fifo_position": 0, "eligible": True},
    ])
    assign_robot = matcher.match(df_robot)
    assert len(assign_robot) == 1
    assert assign_robot[0].robot_id == "R01"

