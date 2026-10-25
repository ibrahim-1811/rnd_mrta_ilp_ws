"""Load a human-readable warehouse map from CSV."""

import csv
from pathlib import Path

from mrta_core.warehouse_map import (
    WarehouseMap,
    WarehouseZone,
    ZoneType,
)
from mrta_core.world import WorldGrid


ZONE_ALIASES = {
    # Shelf
    "SHELF": ZoneType.SHELF,
    "SHELVES": ZoneType.SHELF,
    # Charging
    "CHARGING": ZoneType.CHARGING,
    "CHARGING ZONE": ZoneType.CHARGING,
    # Charging holding bank, not a generic task queue
    "CHARGE QUEUE": ZoneType.CHARGE_QUEUE,
    "CHARGING QUEUE": ZoneType.CHARGE_QUEUE,
    "CHARGE HOLDING": ZoneType.CHARGE_QUEUE,
    "CHARGING HOLDING": ZoneType.CHARGE_QUEUE,
    "CHARGE QUEUE ZONE": ZoneType.CHARGE_QUEUE,
    "CHARGING QUEUE ZONE": ZoneType.CHARGE_QUEUE,
    "CHARGE HOLDING ZONE": ZoneType.CHARGE_QUEUE,
    "CHARGING HOLDING ZONE": ZoneType.CHARGE_QUEUE,
    # Dropoff
    "DROPOFF": ZoneType.DROPOFF,
    "DROP OFF": ZoneType.DROPOFF,
    "DROP ZONE": ZoneType.DROPOFF,
    # Collection
    "COLLECTION": ZoneType.COLLECTION,
    "COLLECTION ZONE": ZoneType.COLLECTION,
    # Pickup
    "PICKUP": ZoneType.PICKUP,
    "PICK UP": ZoneType.PICKUP,
    "PICKUP ZONE": ZoneType.PICKUP,
    # Staging
    "STAGE": ZoneType.STAGING,
    "STAGE ZONE": ZoneType.STAGING,
    "STAGING": ZoneType.STAGING,
    "STAGING ZONE": ZoneType.STAGING,
    # Waiting
    "WAITING": ZoneType.WAITING,
    "WAITING ZONE": ZoneType.WAITING,
}


def _normalise(text: str) -> str:
    """Normalize spreadsheet labels."""
    return " ".join(text.strip().replace("_", " ").replace("-", " ").upper().split())


def load_warehouse_csv(
    path: str | Path,
    *,
    resolution: float = 1.0,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> WarehouseMap:

    path = Path(path)

    with path.open("r", newline="") as f:
        rows = [[cell.strip() for cell in row] for row in csv.reader(f) if row]

    if not rows:
        raise ValueError(f"warehouse CSV is empty: {path}")

    width = len(rows[0])
    height = len(rows)

    if any(len(row) != width for row in rows):
        raise ValueError("warehouse CSV must be rectangular")

    obstacles: set[tuple[int, int]] = set()

    zone_data: dict[
        str,
        tuple[ZoneType, set[tuple[int, int]]],
    ] = {}

    counters: dict[ZoneType, int] = {}

    for csv_row, row in enumerate(rows):
        # Spreadsheet top row corresponds to highest grid Y.
        grid_y = height - 1 - csv_row

        for x, raw_value in enumerate(row):
            value = raw_value.strip()

            # Blank = normal traversable floor.
            if not value:
                continue

            normalised = _normalise(value)

            # Normal floor.
            if normalised in {
                "FREE",
                "FLOOR",
            }:
                continue

            # Only these cells are non-traversable.
            if normalised in {
                "BLOCKED",
                "NO GO",
                "OBSTACLE",
            }:
                obstacles.add((x, grid_y))
                continue

            # ---------------------------------------------
            # Explicit ID syntax
            #
            # Example:
            #   Shelves:S01
            #   Charging:C01
            # ---------------------------------------------
            if ":" in value:
                type_text, zone_id = value.split(":", 1)

                type_key = _normalise(type_text)
                zone_id = zone_id.strip()

                if type_key not in ZONE_ALIASES:
                    raise ValueError(
                        f"unknown zone type {type_text!r} "
                        f"at CSV row {csv_row + 1}, "
                        f"column {x + 1}"
                    )

                if not zone_id:
                    raise ValueError(
                        f"empty zone ID at CSV row {csv_row + 1}, column {x + 1}"
                    )

                zone_type = ZONE_ALIASES[type_key]

                if zone_id not in zone_data:
                    zone_data[zone_id] = (
                        zone_type,
                        set(),
                    )

                existing_type, cells = zone_data[zone_id]

                if existing_type != zone_type:
                    raise ValueError(
                        f"zone ID {zone_id!r} is used with multiple zone types"
                    )

                cells.add((x, grid_y))
                continue

            # ---------------------------------------------
            # Human spreadsheet label
            #
            # Examples:
            #   Shelves
            #   Charging
            #   Drop ZOne
            #   COLLECTION ZONE
            # ---------------------------------------------
            if normalised in ZONE_ALIASES:
                zone_type = ZONE_ALIASES[normalised]

                counters[zone_type] = counters.get(zone_type, 0) + 1

                zone_id = f"{zone_type.value}_{counters[zone_type]:03d}"

                zone_data[zone_id] = (
                    zone_type,
                    {(x, grid_y)},
                )

                continue

            raise ValueError(
                f"invalid cell {value!r} at CSV row {csv_row + 1}, column {x + 1}"
            )

    grid = WorldGrid(
        origin_x=origin_x,
        origin_y=origin_y,
        resolution=resolution,
        width=width,
        height=height,
        obstacles=frozenset(obstacles),
    )

    zones = tuple(
        WarehouseZone(
            zone_id=zone_id,
            zone_type=zone_type,
            cells=frozenset(cells),
        )
        for zone_id, (
            zone_type,
            cells,
        ) in zone_data.items()
    )

    return WarehouseMap(
        grid=grid,
        zones=zones,
    )
