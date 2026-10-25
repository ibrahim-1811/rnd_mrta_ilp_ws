"""How a fleet smaller than the parking bank is placed at t=0.

The default ("home") puts robot k on the k-th home cell in id order,
which is what every frozen baseline uses. "spread" exists for fleet-size
sweeps on one map: without it, 10 robots on a 100-cell bank start bunched
in one corner, so the start condition changes in kind across the sweep.

Parking at the end of work is not affected either way: it is always the
nearest free cell (test_stage_parking and friends).
"""

from dataclasses import replace
from pathlib import Path

import pytest

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.fleet import build_fleet, home_bank, home_stations


CONFIGS = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def config():
    return load_sim_config(CONFIGS / "v2_reference.yaml")


@pytest.fixture(scope="module")
def w100(tmp_path_factory):
    # The generated 100-robot layout W100 was made from: two parking
    # banks of 50, on the left (x=0) and right (x=39) walls.
    from mrta_core.map_builder import example_layout, write_layout_xlsx

    path = tmp_path_factory.mktemp("w100") / "w100.xlsx"

    return load_warehouse_xlsx(write_layout_xlsx(example_layout(100), path))


def cells(config, warehouse, count, mode):
    return [
        robot.cell
        for robot in build_fleet(
            replace(config, robot_count=count, initial_position_mode=mode),
            warehouse,
        )
    ]


def test_ten_robots_are_shared_between_both_banks_and_spaced_out(config, w100):
    placed = cells(config, w100, 10, "spread")

    left = sorted(y for x, y in placed if x == 0)
    right = sorted(y for x, y in placed if x != 0)

    assert len(left) == len(right) == 5

    # Evenly spaced over the bank's whole height, not bunched at one end.
    assert left == right == [6, 16, 26, 36, 46]


def test_the_default_rule_is_unchanged(config, w100):
    """robot k on the k-th home cell: the five top rows of both banks."""

    placed = cells(config, w100, 10, "home")

    assert sorted(y for _, y in placed) == [47, 47, 48, 48, 49, 49, 50, 50, 51, 51]


@pytest.mark.parametrize("count", [1, 7, 10, 33, 99, 100])
def test_spread_gives_distinct_home_cells(config, w100, count):
    placed = cells(config, w100, count, "spread")

    assert len(placed) == count
    assert len(set(placed)) == count


def test_a_full_fleet_uses_every_cell_either_way(config, w100):
    assert sorted(cells(config, w100, 100, "spread")) == sorted(
        cells(config, w100, 100, "home")
    )


def test_an_uneven_split_still_uses_both_banks(config, w100):
    placed = cells(config, w100, 7, "spread")

    assert {x for x, _ in placed} == {0, 39}


def test_v2_is_identical_under_both_rules_at_full_fleet(config):
    v2 = load_warehouse_xlsx(config.map_path)

    assert cells(config, v2, 22, "spread") == cells(config, v2, 22, "home")


def test_reports_agree_with_the_fleet(config, w100):
    """home_stations must name the cells build_fleet actually used."""

    spread = replace(config, robot_count=10, initial_position_mode="spread")

    fleet = {robot.robot_id: robot.cell for robot in build_fleet(spread, w100)}
    homes = home_stations(spread, w100)

    by_id = {zone.zone_id: min(zone.cells) for zone in home_bank(w100)}

    assert {robot: by_id[zone] for robot, zone in homes.items()} == fleet


def test_an_unknown_mode_is_refused(config, w100):
    with pytest.raises(ValueError, match="initial_position_mode"):
        cells(config, w100, 10, "scatter")
