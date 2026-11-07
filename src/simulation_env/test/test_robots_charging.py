"""robots_charging counts robots in the charging workflow now, not robots that ever charged."""

from dataclasses import replace
from fractions import Fraction

from simulation_env.charging import BUSY_STATES
from test_charger_evacuation import build
from test_charger_evacuation import config as config
from test_charger_evacuation import warehouse as warehouse


def charging_run(config, warehouse):
    ex, monitor, _ = build(
        replace(config, robot_count=4),
        warehouse,
        {"robot_001": Fraction(9), "robot_002": Fraction(18)},
        orders=12,
        window="legacy",
    )
    seen = []

    def sample(sim_time, batch):
        load = ex.decision_load(0, 0)
        in_workflow = {r for r, rec in ex.charge.items() if rec.state in BUSY_STATES}
        seen.append((float(ex.engine.time), load["robots_charging"], in_workflow,
                     set(ex.available_robots()), len(ex.charge)))
        monitor(sim_time, batch)

    ex._external_tick = sample  # the executor's tick callback, wrapping the monitor
    return ex, seen


def test_robots_charging_rises_and_falls_back_to_zero(config, warehouse):
    ex, seen = charging_run(config, warehouse)
    ex.run()

    assert len(ex.charge_history) == 2  # two charge trips happened
    counts = [count for _, count, *_ in seen]
    assert max(counts) >= 1
    # After the last robot rejoined, nobody is charging, although self.charge
    # still holds both finished trips (the old len(self.charge) read 2).
    last_rejoin = max(record.rejoined_at for record in ex.charge_history)
    after = [(count, held) for t, count, _, _, held in seen if t > float(last_rejoin)]
    assert after and all(count == 0 for count, _ in after)
    assert any(held >= 1 for _, held in after)
    assert ex.decision_load(0, 0)["robots_charging"] == 0


def test_a_robot_counted_as_charging_is_never_offered_an_order(config, warehouse):
    ex, seen = charging_run(config, warehouse)
    ex.run()
    for _, count, in_workflow, available, _ in seen:
        assert count == len(in_workflow) <= 4
        assert not in_workflow & available
