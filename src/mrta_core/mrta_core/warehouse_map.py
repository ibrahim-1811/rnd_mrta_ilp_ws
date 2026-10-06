"""Layered warehouse map model.

Navigation and warehouse semantics are intentionally separate.

WorldGrid.obstacles answers whether a cell is traversable.
WarehouseZone answers what a traversable cell means operationally.

Only obstacle cells are non-traversable.
Semantic cells such as shelves, pickup points, drop-off points,
charging stations, charge-queue holding cells, staging areas, and
waiting areas remain valid robot positions.

CHARGE_QUEUE is the dedicated holding bank beside the chargers: where
a robot waits for a charging slot, and where a preempted robot goes
when it is not energy-eligible to rejoin the fleet. It is deliberately
charging-specific rather than a generic task queue.
"""

from dataclasses import dataclass
from enum import Enum

from mrta_core.grid import Cell
from mrta_core.world import WorldGrid


class ZoneType(str, Enum):
    """Operational meaning of a warehouse cell."""

    NORMAL = "normal"
    SHELF = "shelf"
    PICKUP = "pickup"
    DROPOFF = "dropoff"
    COLLECTION = "collection"
    CHARGING = "charging"
    CHARGE_QUEUE = "charge_queue"
    STAGING = "staging"
    WAITING = "waiting"


@dataclass(frozen=True)
class WarehouseZone:
    """Named semantic zone occupying one or more traversable cells."""

    zone_id: str
    zone_type: ZoneType
    cells: frozenset[Cell]

    def __post_init__(self) -> None:
        if not self.zone_id.strip():
            raise ValueError("zone_id must not be empty")

        if not self.cells:
            raise ValueError(
                f"zone {self.zone_id!r} must contain at least one cell"
            )


@dataclass(frozen=True)
class WarehouseMap:
    """Warehouse map with independent occupancy and semantic layers."""

    grid: WorldGrid
    zones: tuple[WarehouseZone, ...] = ()

    def __post_init__(self) -> None:
        grid_map = self.grid.grid_map()

        seen_ids: set[str] = set()
        claimed_cells: dict[Cell, str] = {}

        for zone in self.zones:

            if zone.zone_id in seen_ids:
                raise ValueError(
                    f"duplicate zone_id: {zone.zone_id!r}"
                )

            seen_ids.add(zone.zone_id)

            for cell in zone.cells:

                if not grid_map.in_bounds(cell):
                    raise ValueError(
                        f"zone {zone.zone_id!r} contains "
                        f"out-of-bounds cell {cell}"
                    )

                if not grid_map.is_free(cell):
                    raise ValueError(
                        f"zone {zone.zone_id!r} contains "
                        f"blocked cell {cell}; "
                        "semantic zones must be traversable"
                    )

                if cell in claimed_cells:
                    raise ValueError(
                        f"cell {cell} belongs to both "
                        f"{claimed_cells[cell]!r} "
                        f"and {zone.zone_id!r}"
                    )

                claimed_cells[cell] = zone.zone_id

    def is_traversable(self, cell: Cell) -> bool:
        """Check only the occupancy layer."""
        return self.grid.grid_map().is_free(cell)

    def zone_at(self, cell: Cell) -> WarehouseZone | None:
        """Return semantic zone containing the cell."""
        for zone in self.zones:
            if cell in zone.cells:
                return zone

        return None

    def zone_type_at(self, cell: Cell) -> ZoneType:
        """Unlabelled traversable cells are NORMAL."""
        zone = self.zone_at(cell)

        if zone is None:
            return ZoneType.NORMAL

        return zone.zone_type

    def zone_by_id(self, zone_id: str) -> WarehouseZone:
        """Find a zone using its warehouse ID."""
        for zone in self.zones:
            if zone.zone_id == zone_id:
                return zone

        raise KeyError(zone_id)

    def zones_of_type(
        self,
        zone_type: ZoneType,
    ) -> tuple[WarehouseZone, ...]:

        return tuple(
            zone
            for zone in self.zones
            if zone.zone_type == zone_type
        )