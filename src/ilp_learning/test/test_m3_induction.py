"""The M3 reproduce command stays on induction seeds and computes the frozen limits."""

import pytest

from ilp_learning import m3_induction as m3
from simulation_env.seed_split import check_induction_purpose


def test_only_induction_seeds_are_reachable():
    assert m3.SEEDS == tuple(range(104, 111))
    check_induction_purpose(m3.SEEDS)


def test_every_condition_of_the_preregistration_is_covered():
    assert m3.conditions() == [
        ("q3", 30), ("q3", 70), ("q3", 100), ("q0", 30), ("q0", 70), ("q0", 100),
    ]


def test_a_fold_trains_on_every_seed_but_its_own():
    assert m3.training_seeds(m3.FULL) == list(range(104, 111))
    assert m3.training_seeds("loso_107") == [104, 105, 106, 108, 109, 110]
    assert m3.folds() == ["full", *(f"loso_{s}" for s in range(104, 111))]


def _fold(held_seed, rates):
    """A fold entry whose fidelity on each seed is given as agreed out of 100."""
    return {
        "per_seed": {
            str(held_seed): {"choice_bearing": {"frozen_key": rates[held_seed] / 100}},
        },
        "per_seed_results": {
            str(seed): {"scopes": {"choice_bearing": {"agreed": agreed, "teacher_pairs": 100}}}
            for seed, agreed in rates.items()
        },
    }


@pytest.mark.parametrize("worst_seed", [104, 110])
def test_g5_limit_is_the_larger_of_worst_drop_and_two_sd(worst_seed):
    rates = {seed: 100 for seed in m3.SEEDS}
    results = {}
    for q, robots in m3.conditions():
        for seed in m3.SEEDS:
            held = {**rates, seed: 90 if seed == worst_seed else 100}
            results[f"{q}/{robots}/loso_{seed}"] = _fold(seed, held)

    limit = m3.g5_limits(results)["q3/30"]
    assert limit["worst_drop"] == pytest.approx(0.10)
    assert limit["two_sd"] == pytest.approx(2 * 0.10 / 7**0.5)
    assert limit["limit"] == pytest.approx(0.10)


def test_the_command_takes_no_seed_option(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["m3_induction", "--help"])
    with pytest.raises(SystemExit):
        m3.main()
    usage = capsys.readouterr().out
    assert "--out" in usage and "--seed" not in usage
