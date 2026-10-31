import pandas as pd

from ilp_learning.atoms import ProvenanceTracker
from ilp_learning.bias_templates import write_bias_pl
from ilp_learning.bk_builder import BKBuilder
from ilp_learning.popper_runner import run_popper
from ilp_learning.sampler import StratifiedExampleSampler, write_exs_pl


def test_popper_end_to_end_syntax_and_learning(tmp_path):
    # Construct a minimal 2-decision training dataset where nearest_static is the true pattern
    df = pd.DataFrame([
        # Decision 0: R01 chooses O001 (distance 10m vs O002 20m)
        {
            "decision_id": 0,
            "robot_id": "R01",
            "order_id": "O001",
            "c_static_m": 10.0,
            "est_finish_s": 8.0,
            "fifo_position": 0,
            "selected": True,
            "eligible": True,
            "sim_time": 0.0,
        },
        {
            "decision_id": 0,
            "robot_id": "R01",
            "order_id": "O002",
            "c_static_m": 20.0,
            "est_finish_s": 15.0,
            "fifo_position": 1,
            "selected": False,
            "eligible": True,
            "sim_time": 0.0,
        },
        # Decision 1: R02 chooses O010 (distance 12m vs O011 30m)
        {
            "decision_id": 1,
            "robot_id": "R02",
            "order_id": "O010",
            "c_static_m": 12.0,
            "est_finish_s": 9.0,
            "fifo_position": 0,
            "selected": True,
            "eligible": True,
            "sim_time": 3.0,
        },
        {
            "decision_id": 1,
            "robot_id": "R02",
            "order_id": "O011",
            "c_static_m": 30.0,
            "est_finish_s": 21.0,
            "fifo_position": 1,
            "selected": False,
            "eligible": True,
            "sim_time": 3.0,
        },
    ])

    tracker = ProvenanceTracker()
    builder = BKBuilder(tracker=tracker)
    builder.build_from_frames("run_test", df)
    builder.write_bk_pl(tmp_path / "bk.pl")

    # Generate bias.pl
    write_bias_pl(tmp_path / "bias.pl", include_tier_b=False, max_vars=4, max_body=3, max_clauses=2)

    # Sample examples
    sampler = StratifiedExampleSampler(k_neg=2, seed=42)
    examples = sampler.sample_examples_from_decisions("run_test", df, tracker)
    write_exs_pl(examples, tmp_path / "exs.pl")

    # Run Popper in isolated subprocess
    result = run_popper(tmp_path, timeout=10, noisy=False)

    assert result.success is True, f"Popper failed: {result.raw_output}"
    assert result.datalog is True
    assert result.score is not None
    # TP must be 2, FN must be 0
    assert result.score[0] == 2, f"Expected 2 TPs, got {result.score}"
    assert result.score[1] == 0, f"Expected 0 FNs, got {result.score}"
    assert len(result.rules) > 0, "Expected at least 1 learned rule"
    # The learned rule should use nearest_static, earliest_static_finish, or candidate_pair
    rule_str = " ".join(result.rules)
    assert "assign(" in rule_str
    assert (
        "nearest_static(" in rule_str
        or "earliest_static_finish(" in rule_str
        or "candidate_pair(" in rule_str
    )
