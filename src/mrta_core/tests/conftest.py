"""Shared fixtures for the order workload tests.

The tests use a synthetic map rather than the production XLSX so they
stay fast and dependency-free, but its station ids are exactly the
ones the real warehouse defines: shelf_001..shelf_072,
dropoff_001..dropoff_040 and staging_001..staging_018. Validation
against the real map is covered by scripts/smoke_orders.py.
"""

from pathlib import Path

import pytest

from mrta_core.warehouse_map import (
    WarehouseMap,
    WarehouseZone,
    ZoneType,
)
from mrta_core.world import WorldGrid


SHELF_COUNT = 72
DROPOFF_COUNT = 40
STAGING_COUNT = 18

WORKLOAD_CSV = (
    Path(__file__).resolve().parents[2]
    / "simulation_env"
    / "test"
    / "fixtures"
    / "orders.csv"
)

HEADER = "order_id,item_id,shelf_id,quantity,dropoff_id"


def _stations():
    """(zone_id, zone_type) for every station, in a stable sequence."""

    for index in range(1, SHELF_COUNT + 1):
        yield f"shelf_{index:03d}", ZoneType.SHELF

    for index in range(1, DROPOFF_COUNT + 1):
        yield f"dropoff_{index:03d}", ZoneType.DROPOFF

    for index in range(1, STAGING_COUNT + 1):
        yield f"staging_{index:03d}", ZoneType.STAGING


@pytest.fixture(scope="session")
def warehouse():
    """A map carrying the real warehouse's station ids, one cell each."""

    width = 16

    zones = tuple(
        WarehouseZone(
            zone_id=zone_id,
            zone_type=zone_type,
            cells=frozenset(((position % width, position // width),)),
        )
        for position, (zone_id, zone_type) in enumerate(_stations())
    )

    return WarehouseMap(
        grid=WorldGrid(
            origin_x=0.0,
            origin_y=0.0,
            resolution=1.0,
            width=width,
            height=len(zones) // width + 1,
        ),
        zones=zones,
    )


@pytest.fixture
def csv_from_rows(tmp_path):
    """Write ``rows`` under the active schema and return the path."""

    def write(rows, name="workload.csv"):
        path = tmp_path / name
        path.write_text(HEADER + "\n" + "\n".join(rows) + "\n")
        return path

    return write
