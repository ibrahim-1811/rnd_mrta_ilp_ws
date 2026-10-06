"""Battery V1 model: exact energy arithmetic and a strict config.

Pure: no engine, no map. The ledger and engine integration are covered
in simulation_env/test/test_battery_ledger.py.
"""

from fractions import Fraction

import pytest

from mrta_core.battery import (
    BatteryConfig,
    exact,
    service_energy,
    stationary_energy,
    travel_energy,
)
from mrta_core.sim_config import load_sim_config


DEFAULT = BatteryConfig()


def test_twenty_metres_of_travel_is_exactly_one_percent():
    assert travel_energy(DEFAULT, 20) == 1
    assert travel_energy(DEFAULT, Fraction(1)) == Fraction(1, 20)
    assert travel_energy(DEFAULT, 6430) == Fraction(643, 2)


def test_sixty_seconds_of_service_is_exactly_one_percent():
    assert service_energy(DEFAULT, 60) == 1
    assert service_energy(DEFAULT, 2) == Fraction(1, 30)


def test_three_hundred_seconds_standing_still_is_exactly_one_percent():
    """Waiting and idle drain through the same function and rate."""

    assert stationary_energy(DEFAULT, 300) == 1
    assert stationary_energy(DEFAULT, Fraction(2, 3)) == Fraction(1, 450)


def test_rates_follow_the_config():

    doubled = BatteryConfig(
        travel_percent_per_20m=Fraction(2),
        service_percent_per_60s=Fraction(3),
        waiting_idle_percent_per_300s=Fraction(1, 2),
    )

    assert travel_energy(doubled, 20) == 2
    assert service_energy(doubled, 60) == 3
    assert stationary_energy(doubled, 300) == Fraction(1, 2)


def test_defaults_are_the_frozen_design():

    parsed = BatteryConfig.from_mapping({})

    assert parsed == DEFAULT
    assert parsed.enabled is True
    assert parsed.initial_soc_percent == 100
    assert parsed.travel_percent_per_20m == 1
    assert parsed.service_percent_per_60s == 1
    assert parsed.waiting_idle_percent_per_300s == 1
    assert parsed.charging_percent_per_second == 1
    assert parsed.reserve_percent == 10
    assert parsed.charge_due_percent == 10
    assert parsed.initial_soc_overrides == {}


def test_yaml_numbers_are_read_exactly():
    """A decimal from YAML is the decimal, not the nearest binary float."""

    parsed = BatteryConfig.from_mapping({"initial_soc_percent": 12.3})

    assert parsed.initial_soc_percent == Fraction(123, 10)
    assert exact(0.1) == Fraction(1, 10)


def test_per_robot_override():

    parsed = BatteryConfig.from_mapping(
        {"initial_soc_overrides": {"robot_003": 12.5}}
    )

    assert parsed.initial_soc_for("robot_003") == Fraction(25, 2)
    assert parsed.initial_soc_for("robot_001") == 100


@pytest.mark.parametrize("raw", [
    {"travel_percent_per_20": 1.0},              # misspelt key
    {"travel_percent_per_20m": 0.0},             # rate must be > 0
    {"waiting_idle_percent_per_300s": -1.0},
    {"initial_soc_percent": 100.5},              # out of 0..100
    {"reserve_percent": -1.0},
    {"initial_soc_overrides": {"robot_003": 101.0}},
    {"initial_soc_overrides": [12.5]},           # must be a mapping
    {"enabled": "yes"},                          # must be a boolean
])
def test_bad_config_is_refused(raw):
    with pytest.raises(ValueError):
        BatteryConfig.from_mapping(raw)


def test_old_fleet_key_is_refused(tmp_path):
    """Initial SOC has one home now; the old key must not be ignored."""

    config = tmp_path / "sim.yaml"
    config.write_text(
        "fleet:\n"
        "  robot_count: 1\n"
        "  initial_battery_percent: 50.0\n"
    )

    with pytest.raises(ValueError, match="battery.initial_soc_percent"):
        load_sim_config(config)
