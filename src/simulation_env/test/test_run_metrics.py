"""metrics.json reports exactly what the run did.

run_metrics() gathers a finished run's results into one dict so an
experiment can be compared and plotted without reading terminal output.
Every number must be the run's own: the same totals the final report
prints, the same counters the executor kept.
"""

import json

import pytest

from simulation_env.run_metrics import run_metrics, write_metrics
from test_charger_evacuation import SATURATED, run
from test_charger_evacuation import config as config
from test_charger_evacuation import warehouse as warehouse


@pytest.fixture(scope="module")
def saturated(config, warehouse):
    executor, _, _ = run(config, warehouse, SATURATED, holding=2)
    return executor


def test_metrics_are_the_runs_own_numbers(saturated):
    executor = saturated
    totals = executor.totals()
    result = executor.result
    m = run_metrics(executor, strategy="nearest_astar", seed=42, wall_s=1.0)

    assert m["orders_completed"] == len(result.completions) == m["orders"]
    assert m["makespan_s"] == float(totals["makespan_s"])
    assert m["total_distance_m"] == float(totals["distance_m"])
    assert m["total_waiting_s"] == float(totals["waiting_time_s"])
    assert m["reservation_violations"] == 0
    assert m["throughput_orders_per_h"] == pytest.approx(
        m["orders_completed"] / m["makespan_s"] * 3600
    )
    assert m["mean_completion_time_s"] == pytest.approx(
        sum(c.sim_time for c in result.completions) / len(result.completions)
    )

    assert m["charge_sessions"] == result.charge_sessions
    assert m["saturation_events"] == result.saturation_events == 1
    assert m["promotions"] == result.promotions
    assert m["stage_promotions"] == result.stage_promotions == 1
    assert m["below_zero_robots"] == 0


def test_time_out_of_the_fleet_counts_each_request_once(saturated):
    """A request that waited and then charged is one span, not two."""

    executor = saturated
    m = run_metrics(executor, strategy="nearest_astar", seed=42, wall_s=1.0)

    trips = {
        id(r): r for r in (*executor.charge_history, *executor.charge.values())
    }.values()

    requests = {(r.robot_id, r.requested_at) for r in trips}

    # Every request in this run ends back in the fleet, on the charger
    # trip; a holding or Stage Zone trip ends at promotion instead.
    spans = {
        (r.robot_id, r.requested_at): float(r.rejoined_at) - float(r.requested_at)
        for r in trips
        if r.rejoined_at is not None
    }

    assert set(spans) == requests

    assert m["charge_requests"] == len(requests) == 11
    assert m["out_of_fleet_time_s"] == pytest.approx(sum(spans.values()))
    # Two robots waited in holding and one on a Stage Zone.
    assert m["charger_wait_time_s"] > 0


def test_metrics_are_written_as_json(saturated, tmp_path):
    m = run_metrics(saturated, strategy="nearest_astar", seed=42, wall_s=1.0)

    path = write_metrics(tmp_path, m)

    assert path.name == "metrics.json"
    assert json.loads(path.read_text()) == pytest.approx(m)
