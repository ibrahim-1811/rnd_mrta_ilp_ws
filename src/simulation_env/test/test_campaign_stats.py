"""M8 campaign statistics (F9): Friedman, Wilcoxon-Holm, t-interval, d_z, failures."""

import math

import numpy as np
import pandas as pd
import pytest

from simulation_env.campaign_stats import (
    PRIMARY,
    failures,
    families,
    friedman,
    holm,
    main,
    paired_wilcoxon,
    pairs,
)


def _rows(arms: dict[str, float], seeds=range(201, 211), robots=(70, 100), orders=1500, noise=1.0):
    """One row per arm x seed x fleet size; each arm's makespan is its offset plus seed noise."""
    rng = np.random.default_rng(0)
    out = []
    for r in robots:
        for s in seeds:
            base = 400 + rng.normal(0, 20)
            for arm, offset in arms.items():
                out.append({"orders": orders, "robots": r, "seed": s, "strategy": arm,
                            PRIMARY: base + offset + rng.normal(0, noise),
                            "total_distance_m": 1000.0})
    return pd.DataFrame(out)


def test_holm_is_step_down_and_monotone():
    adj = holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj == pytest.approx({"a": 0.03, "c": 0.06, "b": 0.06})
    assert holm({"a": 0.5, "b": 0.9}) == {"a": 1.0, "b": 1.0}
    nan = holm({"a": 0.01, "b": math.nan})
    assert nan["a"] == pytest.approx(0.01) and math.isnan(nan["b"])


def test_wilcoxon_of_identical_arms_is_one():
    assert paired_wilcoxon([0.0, 0.0, 0.0]) == 1.0
    assert math.isnan(paired_wilcoxon([]))
    assert paired_wilcoxon([-5.0] * 10) < 0.01


def test_families_list_only_present_arms():
    fam = families(["nearest_astar", "milp", "ilp_single_milp", "ilp_pooled", "multi_criteria",
                    "ilp_outcome_multi_criteria"])
    assert ("ilp_single_milp", "milp") in fam["ilp"]
    assert ("ilp_pooled", "nearest_astar") in fam["ilp"]
    assert ("ilp_outcome_multi_criteria", "multi_criteria") in fam["ilp"]
    assert ("ilp_outcome_multi_criteria", "ilp_single_multi_criteria") not in fam["ilp"]
    assert fam["conventional"] == [("multi_criteria", "nearest_astar"), ("milp", "nearest_astar")]


def test_friedman_finds_a_consistently_faster_arm_and_uses_complete_blocks_only():
    rows = _rows({"nearest_astar": 0.0, "milp": -15.0, "random": 30.0})
    rows = rows[~((rows.strategy == "milp") & (rows.seed == 201) & (rows.robots == 70))]
    fr = friedman(rows).set_index("robots")
    assert fr.loc[70, "blocks"] == 9 and fr.loc[70, "blocks_dropped"] == 1
    assert fr.loc[100, "blocks"] == 10 and fr.loc[100, "p"] < 0.001
    assert fr.loc[100, "kendall_w"] == pytest.approx(1.0)
    assert fr.loc[100, "mean_ranks"].startswith("milp 1.00")


def test_pairs_give_difference_interval_effect_and_holm_per_family_and_length():
    rows = _rows({"nearest_astar": 0.0, "milp": -15.0, "random": 0.0})
    copy = rows[rows.strategy == "milp"].assign(strategy="ilp_single_milp")  # behaves exactly as milp
    rows = pd.concat([rows, copy], ignore_index=True)
    pr = pairs(rows, [PRIMARY]).set_index(["robots", "a", "b"])
    milp = pr.loc[(100, "milp", "nearest_astar")]
    assert milp["family"] == "conventional" and milp["pairs"] == 10
    assert milp["mean_diff"] == pytest.approx(-15.0, abs=1.5)
    assert milp["ci_low"] < milp["mean_diff"] < milp["ci_high"] < 0
    assert milp["d_z"] < -3 and milp["wins"] == 10 and milp["significant"]
    student = pr.loc[(100, "ilp_single_milp", "milp")]
    assert student["family"] == "ilp" and student["p"] == 1.0 and student["mean_diff"] == 0.0
    # Holm: the conventional family holds milp and random at both fleet sizes (4 tests).
    conv = pr[pr["family"] == "conventional"]
    assert len(conv) == 4 and (conv["p_holm"] >= conv["p"]).all()


def test_a_missing_run_is_counted_never_imputed():
    rows = _rows({"nearest_astar": 0.0, "milp": -15.0}, robots=(70,))
    rows = rows[~((rows.strategy == "milp") & (rows.seed == 205))]
    f = failures(rows).set_index("strategy")
    assert f.loc["milp", "missing"] == 1 and f.loc["milp", "missing_seeds"] == "205"
    assert f.loc["nearest_astar", "missing"] == 0
    pr = pairs(rows, [PRIMARY])
    assert pr.iloc[0]["pairs"] == 9


def test_the_cli_writes_its_tables(tmp_path):
    rows = _rows({"nearest_astar": 0.0, "milp": -15.0, "random": 30.0})
    paths = []
    for r, g in rows.groupby("robots"):
        p = tmp_path / f"r{r}.csv"
        g.to_csv(p, index=False)
        paths.append(str(p))
    assert main([*paths, "--out", str(tmp_path / "stats")]) == 0
    for name in ("friedman.csv", "pairs.csv", "failures.csv"):
        assert (tmp_path / "stats" / name).exists()
    with pytest.raises(ValueError, match="duplicate"):
        main([paths[0], paths[0], "--out", str(tmp_path / "dup")])
