"""Gate 2 verdict: paired Wilcoxon, Holm, Problem A gate, recovered share (2026-11-10)."""

import math

import pandas as pd
import pytest

from ilp_learning.gate2_verdict import VerdictError, holm, verdict

RUNS = [(r, s) for r in (70, 80, 90, 100) for s in (116, 117, 118, 119, 120)]


def _rows(strategy, makespans, rule_picks=None, fallback=None):
    rows = []
    for (robots, seed), m in zip(RUNS, makespans):
        row = {"strategy": strategy, "robots": robots, "seed": seed, "order_makespan_s": m,
               "mean_completion_time_s": m / 4, "total_waiting_s": m / 2, "total_distance_m": 1000.0}
        if rule_picks is not None:
            row.update(rule_picks=rule_picks, fallback_rate=fallback)
        rows.append(row)
    return rows


def _teacher(name="milp"):
    return _rows(name, [400.0 + 3 * i for i in range(20)])


def test_holm_matches_the_textbook_example():
    # p = 0.01, 0.04, 0.03 for m = 3: sorted 0.01*3, 0.03*2, 0.04*1 -> 0.03, 0.06, 0.06 (monotone)
    assert holm({"a": 0.01, "b": 0.04, "c": 0.03}) == pytest.approx({"a": 0.03, "b": 0.06, "c": 0.06})
    assert holm({"a": 0.9, "b": 0.8}) == {"a": 1.0, "b": 1.0}


def test_a_clear_consistent_win_passes():
    student = _rows("ilp_oracle_milp", [400.0 + 3 * i - 10 - (i % 3) for i in range(20)], 50, 0.1)
    result = verdict(pd.DataFrame(_teacher() + student), {"ilp_oracle_milp": "milp"})
    (r,) = result["pairs"]
    assert r["pairs"] == 20 and r["wins"] == 20 and r["mean_diff_s"] < 0
    assert r["p_holm"] < 0.001 and r["problem_a_gate"] and r["pass"]


def test_noise_around_the_teacher_does_not_pass():
    noise = [5, -4, 3, -6, 2, -1, 4, -3, 6, -5, 1, -2, 3, -4, 5, -6, 2, -1, 4, -3]
    student = _rows("ilp_oracle_milp", [400.0 + 3 * i + n for i, n in enumerate(noise)], 50, 0.1)
    (r,) = verdict(pd.DataFrame(_teacher() + student), {"ilp_oracle_milp": "milp"})["pairs"]
    assert not r["significant"] and not r["pass"]


def test_a_win_with_mostly_fallback_fails_the_problem_a_gate():
    student = _rows("ilp_oracle_milp", [400.0 + 3 * i - 10 for i in range(20)], 50, 0.7)
    (r,) = verdict(pd.DataFrame(_teacher() + student), {"ilp_oracle_milp": "milp"})["pairs"]
    assert r["significant"] and not r["problem_a_gate"] and not r["pass"]


def test_rules_that_never_fire_fail_the_gate():
    student = _rows("ilp_oracle_milp", [400.0 + 3 * i - 10 for i in range(20)], 0, 0.0)
    (r,) = verdict(pd.DataFrame(_teacher() + student), {"ilp_oracle_milp": "milp"})["pairs"]
    assert not r["rules_fire"] and not r["pass"]


def test_a_significant_loss_does_not_pass():
    student = _rows("ilp_oracle_milp", [400.0 + 3 * i + 10 for i in range(20)], 50, 0.1)
    (r,) = verdict(pd.DataFrame(_teacher() + student), {"ilp_oracle_milp": "milp"})["pairs"]
    assert r["significant"] and r["mean_diff_s"] > 0 and not r["pass"]


def test_a_missing_run_is_refused_not_dropped():
    student = _rows("ilp_oracle_milp", [400.0 + 3 * i - 10 for i in range(20)], 50, 0.1)[:-1]
    with pytest.raises(VerdictError, match="same runs"):
        verdict(pd.DataFrame(_teacher() + student), {"ilp_oracle_milp": "milp"})


def test_holm_runs_over_all_pairs_and_the_recovered_share_uses_the_oracle():
    teachers = _teacher("milp") + _teacher("nearest_astar")
    students = (_rows("ilp_oracle_milp", [400.0 + 3 * i - 10 for i in range(20)], 50, 0.1)
                + _rows("ilp_oracle_nearest_astar", [400.0 + 3 * i - 10 for i in range(20)], 50, 0.1))
    oracle = {(t, r, s): 400.0 + 3 * i - 40 for t in ("milp", "nearest_astar")
              for i, (r, s) in enumerate(RUNS)}
    result = verdict(pd.DataFrame(teachers + students),
                     {"ilp_oracle_milp": "milp", "ilp_oracle_nearest_astar": "nearest_astar"}, oracle=oracle)
    for r in result["pairs"]:
        assert r["p_holm"] == pytest.approx(min(1.0, 2 * r["p"]))  # equal p: both times m
        assert r["recovered_share"] == pytest.approx(10 / 40)


def test_the_h1_contrast_is_reported_against_the_imitation_arm():
    oracle_arm = _rows("ilp_oracle_milp", [400.0 + 3 * i - 10 for i in range(20)], 50, 0.1)
    imitation = _rows("ilp_imitation_milp", [400.0 + 3 * i for i in range(20)], 50, 0.1)
    result = verdict(pd.DataFrame(_teacher() + oracle_arm + imitation), {"ilp_oracle_milp": "milp"},
                     controls={"ilp_oracle_milp": "ilp_imitation_milp"})
    (c,) = result["h1_contrasts"]
    assert c["teacher"] == "ilp_imitation_milp" and c["mean_diff_s"] == pytest.approx(-10.0)
    assert c["significant"]


def test_the_one_sided_test_is_recorded():
    student = _rows("ilp_oracle_milp", [400.0 + 3 * i - 10 for i in range(20)], 50, 0.1)
    result = verdict(pd.DataFrame(_teacher() + student), {"ilp_oracle_milp": "milp"}, alternative="less")
    assert result["alternative"] == "less"
    assert not math.isnan(result["pairs"][0]["p"])
