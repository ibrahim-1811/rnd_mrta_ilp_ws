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
    model = fit_thresholds_from_frames([df1, df2], seeds_used=[104, 105])
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

