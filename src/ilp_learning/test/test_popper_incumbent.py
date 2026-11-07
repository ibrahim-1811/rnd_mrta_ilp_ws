"""A Popper worker stopped after its grace keeps its best program (2026-11-07).

Popper checks its own timeout only between search steps, so one long solver call can
overrun timeout + grace; the runner then stops the worker. Before this fix such a run
returned no rules at all (5 of 6 runs at a 1200 s budget). The worker now reports each
new best program (Popper's incumbent) as it is found, and a stopped run keeps the last.
"""

import json

from ilp_learning.popper_runner import INCUMBENT_PREFIX, run_popper, stopped_result


def incumbent(rules, score):
    return INCUMBENT_PREFIX + json.dumps({"rules": rules, "score": score, "datalog": True})


def test_a_stopped_worker_keeps_its_last_incumbent():
    output = "\n".join([
        "noise",
        incumbent(["assign(A,B,C):- candidate_pair(A,B,C)."], [5, 0, 1, 9, 2]),
        incumbent(["assign(A,B,C):- nearest_static(A,B,C)."], [4, 1, 9, 1, 2]),
    ])
    result = stopped_result(output, 1260.0)
    assert result.success and result.timed_out and result.stopped_by_runner
    assert result.rules == ["assign(A,B,C):- nearest_static(A,B,C)."]
    assert result.score == (4, 1, 9, 1, 2)


def test_a_stopped_worker_without_an_incumbent_fails():
    result = stopped_result("no program yet", 1260.0)
    assert not result.success and result.stopped_by_runner and result.rules == []


def _row(decision, robot, order, cost, finish, fifo, selected):
    return {"decision_id": decision, "robot_id": robot, "order_id": order, "c_static_m": cost,
            "est_finish_s": finish, "fifo_position": fifo, "selected": selected,
            "eligible": True, "sim_time": 3.0 * decision}


def _small_kb(tmp_path):
    """Nearest wins eight times; one inconsistent decision means no perfect program, so Popper
    takes its noisy search path, which reports incumbents."""
    import pandas as pd

    from ilp_learning.atoms import ProvenanceTracker
    from ilp_learning.bias_templates import write_bias_pl
    from ilp_learning.bk_builder import BKBuilder
    from ilp_learning.sampler import StratifiedExampleSampler, write_exs_pl

    rows = []
    for d in range(8):  # nearest wins: a rule beats the empty program on MDL
        rows += [_row(d, f"R{d}", f"O{d}a", 10.0, 8.0, 0, True), _row(d, f"R{d}", f"O{d}b", 25.0, 19.0, 1, False)]
    rows += [_row(8, "R8", "O8a", 5.0, 4.0, 0, False), _row(8, "R8", "O8b", 30.0, 22.0, 1, True)]
    df = pd.DataFrame(rows)
    tracker = ProvenanceTracker()
    builder = BKBuilder(tracker=tracker)
    builder.build_from_frames("run_test", df)
    builder.write_bk_pl(tmp_path / "bk.pl")
    write_bias_pl(tmp_path / "bias.pl", include_tier_b=False, max_vars=4, max_body=3, max_clauses=2)
    examples = StratifiedExampleSampler(k_neg=2, seed=42).sample_examples_from_decisions("run_test", df, tracker)
    write_exs_pl(examples, tmp_path / "exs.pl")
    return tmp_path


def test_a_normal_finish_is_unchanged_and_ends_on_its_last_incumbent(tmp_path):
    result = run_popper(_small_kb(tmp_path), timeout=10, noisy=True)
    assert result.success and not result.stopped_by_runner
    reported = [json.loads(line[len(INCUMBENT_PREFIX):])
                for line in result.raw_output.splitlines() if line.startswith(INCUMBENT_PREFIX)]
    assert reported, "the noisy search should report at least one incumbent"
    # The final answer is the last incumbent Popper reported.
    assert sorted(reported[-1]["rules"]) == sorted(result.rules)
    assert tuple(reported[-1]["score"]) == result.score
