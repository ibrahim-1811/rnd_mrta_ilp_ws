import pandas as pd
import pytest
from ilp_learning.thresholds import ThresholdModel, fit_thresholds_from_frames
from simulation_env.seed_split import SeedLeakageError


def test_categorize():
    model = ThresholdModel(
        cutoffs={
            "local_density": [5.0],
            "fleet_load": [30.0, 70.0],
        }
    )

    assert model.categorize("local_density", 2.0) == "low"
    assert model.categorize("local_density", 7.0) == "high"

    assert model.categorize("fleet_load", 15.0) == "low"
    assert model.categorize("fleet_load", 50.0) == "medium"
    assert model.categorize("fleet_load", 85.0) == "high"


def test_fit_thresholds_and_save(tmp_path):
    df1 = pd.DataFrame({"local_density": [1.0, 3.0, 5.0, 7.0, 9.0]})
    df2 = pd.DataFrame({"local_density": [2.0, 4.0, 6.0, 8.0, 10.0]})

    # Valid induction training seeds
    model = fit_thresholds_from_frames(
        [df1, df2], seeds_used=[104, 105], spec={"local_density": ("local_density", [50.0])}
    )
    assert "local_density" in model.cutoffs
    assert len(model.cutoffs["local_density"]) == 1

    json_path = tmp_path / "thresholds.json"
    model.save_json(json_path)

    loaded = ThresholdModel.load_json(json_path)
    assert loaded.cutoffs["local_density"] == model.cutoffs["local_density"]


def test_seed_leakage_rejected():
    df = pd.DataFrame({"local_density": [1.0, 2.0]})
    # Sealed final evaluation seed 205 must be refused
    with pytest.raises(SeedLeakageError):
        fit_thresholds_from_frames([df], seeds_used=[205])



def test_an_absent_column_is_refused_rather_than_fitted_to_zero():
    df = pd.DataFrame({"local_density": [1.0, 2.0]})
    with pytest.raises(ValueError, match="column 'overlap_ratio' is absent"):
        fit_thresholds_from_frames(
            [df], seeds_used=[104], spec={"overlap_ratio": ("overlap_ratio", [50.0])}
        )


def test_a_column_with_no_values_is_refused():
    df = pd.DataFrame({"local_density": [None, None]}, dtype=float)
    with pytest.raises(ValueError, match="has no values"):
        fit_thresholds_from_frames(
            [df], seeds_used=[104], spec={"local_density": ("local_density", [50.0])}
        )
