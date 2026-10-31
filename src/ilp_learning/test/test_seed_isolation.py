import pandas as pd
import pytest

from ilp_learning.atoms import ProvenanceTracker
from ilp_learning.supervision.single_teacher import build_single_teacher_supervision
from ilp_learning.thresholds import fit_thresholds_from_frames
from simulation_env.seed_split import (
    SeedLeakageError,
    TRAINING_EXPLORATORY,
    TRAINING_INDUCTION,
    check_induction_purpose,
    is_induction_seed,
)


def test_seed_isolation_ranges():
    # Primary induction pool is strictly 104-110
    assert list(TRAINING_INDUCTION) == [104, 105, 106, 107, 108, 109, 110]
    assert list(TRAINING_EXPLORATORY) == [101, 102, 103]

    for s in TRAINING_INDUCTION:
        assert is_induction_seed(s) is True
        check_induction_purpose([s])  # must not raise

    for s in TRAINING_EXPLORATORY:
        assert is_induction_seed(s) is False
        with pytest.raises(SeedLeakageError, match="are not induction seeds"):
            check_induction_purpose([s])

    for s in [111, 112, 115]:
        assert is_induction_seed(s) is False
        with pytest.raises(SeedLeakageError, match="are not induction seeds"):
            check_induction_purpose([s])

    for s in [1, 2, 30]:
        assert is_induction_seed(s) is False
        with pytest.raises(SeedLeakageError, match="are not induction seeds"):
            check_induction_purpose([s])

    for s in [201, 202, 230]:
        assert is_induction_seed(s) is False
        with pytest.raises(SeedLeakageError, match="are not induction seeds"):
            check_induction_purpose([s])


def test_threshold_fitting_enforces_induction_seed():
    df = pd.DataFrame({"local_density": [1.0, 2.0, 3.0]})
    spec = {"local_density": ("local_density", [50.0])}

    # Valid induction seed: passes
    model = fit_thresholds_from_frames([df], seeds_used=[104, 105], spec=spec)
    assert model is not None

    # Exploratory seed 101: rejected
    with pytest.raises(SeedLeakageError, match="are not induction seeds"):
        fit_thresholds_from_frames([df], seeds_used=[101])

    # Validation seed 111: rejected
    with pytest.raises(SeedLeakageError, match="are not induction seeds"):
        fit_thresholds_from_frames([df], seeds_used=[111])

    # Final seed 201: rejected
    with pytest.raises(SeedLeakageError, match="are not induction seeds"):
        fit_thresholds_from_frames([df], seeds_used=[201])


def test_supervision_builder_enforces_induction_seed():
    df = pd.DataFrame([
        {
            "decision_id": 0,
            "robot_id": "R01",
            "order_id": "O001",
            "c_static_m": 10.0,
            "selected": True,
            "eligible": True,
            "sim_time": 0.0,
        },
        {
            "decision_id": 0,
            "robot_id": "R01",
            "order_id": "O002",
            "c_static_m": 20.0,
            "selected": False,
            "eligible": True,
            "sim_time": 0.0,
        },
    ])
    tracker = ProvenanceTracker()

    # Induction seed 104: passes
    exs = build_single_teacher_supervision(
        run_id="run_104",
        decisions_df=df,
        tracker=tracker,
        run_manifest={"seed": 104, "orders_path": "a/orders_s104.csv"},
    )
    assert len(exs) == 2

    # Exploratory seed 101: rejected
    with pytest.raises(SeedLeakageError, match="are not induction seeds"):
        build_single_teacher_supervision(
            run_id="run_101",
            decisions_df=df,
            tracker=tracker,
            run_manifest={"seed": 101, "orders_path": "a/orders_s101.csv"},
        )

