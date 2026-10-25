"""The map's zone lookups answer exactly what a scan of every zone did.

Routing asks "which zone is this id", "which zones are shelves" and
"which cells are non-transit" on every leg of every candidate route.
Answering each by scanning all zones was most of the allocator's time
at 100 robots, and since the fleet node only sends Gazebo frames
between decisions, every robot visibly stopped while it ran. The map
now builds these answers once; these tests pin that they are the same
answers.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import (
    NON_TRANSIT_TYPES,
    SERVICE_TYPES,
    non_transit_cells,
    service_cells,
)


CONFIGS = Path(__file__).resolve().parents[2] / "simulation_env" / "test" / "fixtures"


@pytest.fixture(scope="module")
def v2():
    config = load_sim_config(CONFIGS / "v2_reference.yaml")
    return load_warehouse_xlsx(config.map_path)


def scanned_cells(warehouse, types):
    return frozenset(
        cell
        for zone in warehouse.zones
        if zone.zone_type in types
        for cell in zone.cells
    )


def test_zones_of_type_matches_a_scan_in_map_order(v2):
    for zone_type in ZoneType:
        scanned = tuple(zone for zone in v2.zones if zone.zone_type == zone_type)

        assert v2.zones_of_type(zone_type) == scanned


def test_zones_of_type_accepts_the_plain_string(v2):
    assert v2.zones_of_type("shelf") == v2.zones_of_type(ZoneType.SHELF)


def test_zone_by_id_matches_a_scan(v2):
    for zone in v2.zones:
        assert v2.zone_by_id(zone.zone_id) is zone


def test_unknown_zone_id_is_a_key_error(v2):
    with pytest.raises(KeyError, match="shelf_9999"):
        v2.zone_by_id("shelf_9999")


def test_cell_sets_match_a_scan(v2):
    assert non_transit_cells(v2) == scanned_cells(v2, NON_TRANSIT_TYPES)
    assert service_cells(v2) == scanned_cells(v2, SERVICE_TYPES)


def test_non_transit_cells_are_built_once(v2):
    # The regression: a fresh set per call, rebuilt from every zone.
    assert non_transit_cells(v2) is non_transit_cells(v2)


def test_lookups_are_not_part_of_map_identity(v2):
    copy = replace(v2)

    assert copy == v2
    assert hash(copy) == hash(v2)
    assert copy.zones_of_type(ZoneType.SHELF) == v2.zones_of_type(ZoneType.SHELF)
