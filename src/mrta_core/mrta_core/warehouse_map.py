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

from dataclasses import dataclass, field
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
            raise ValueError(f"zone {self.zone_id!r} must contain at least one cell")


@dataclass(frozen=True)
class WarehouseMap:
    """Warehouse map with independent occupancy and semantic layers."""

    grid: WorldGrid
    zones: tuple[WarehouseZone, ...] = ()

    # Drawn traffic lanes: every directed move between two floor cells
    # that the map author permits, as (from_cell, to_cell) pairs.
    #
    # None means the map draws no lanes, and routing falls back to the
    # built-in V2 lane policy in warehouse_routing. A map that draws
    # lanes is fully self-describing: its own arrows are the one-way
    # policy, so any layout can be routed without code changes.
    #
    # Only floor-to-floor moves live here. Moves into and out of
    # terminals (shelf, dropoff, charger, ...) are not drawn: they
    # follow the fixed terminal rules for every map alike.
    lane_edges: frozenset[tuple[Cell, Cell]] | None = None

    # Lookups built from the zones. Routing asks for them on every leg
    # of every candidate route, and a scan of every zone per question
    # was most of the allocator's time at 100 robots. The map is frozen,
    # so they never go stale. Not part of equality or the hash.
    _zones_by_id: dict = field(default_factory=dict, init=False, repr=False, compare=False)
    _zones_by_type: dict = field(default_factory=dict, init=False, repr=False, compare=False)
    _cells_of_types: dict = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        grid_map = self.grid.grid_map()

        seen_ids: set[str] = set()
        claimed_cells: dict[Cell, str] = {}

        for zone in self.zones:
            if zone.zone_id in seen_ids:
                raise ValueError(f"duplicate zone_id: {zone.zone_id!r}")

            seen_ids.add(zone.zone_id)

            for cell in zone.cells:
                if not grid_map.in_bounds(cell):
                    raise ValueError(
                        f"zone {zone.zone_id!r} contains out-of-bounds cell {cell}"
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

        by_type: dict[ZoneType, list[WarehouseZone]] = {}

        for zone in self.zones:
            self._zones_by_id[zone.zone_id] = zone
            by_type.setdefault(zone.zone_type, []).append(zone)

        # Tuples in map order, exactly what the old scan returned.
        self._zones_by_type.update(
            (zone_type, tuple(zones)) for zone_type, zones in by_type.items()
        )

        if self.lane_edges is None:
            return

        # A drawn lane joins two neighbouring floor cells and nothing
        # else. Anything that touches a wall, leaves the map, skips a
        # cell or runs into a station is a drawing error, and it is
        # refused here so no construction path can route on it.
        for source, target in self.lane_edges:
            for cell in (source, target):
                if not grid_map.in_bounds(cell) or not grid_map.is_free(cell):
                    raise ValueError(
                        f"lane {source} -> {target} touches {cell}, "
                        "which is a wall or outside the map"
                    )

                if cell in claimed_cells:
                    raise ValueError(
                        f"lane {source} -> {target} touches {cell}, which "
                        f"is station {claimed_cells[cell]!r}; lanes run on "
                        "floor only, and stations are entered by the fixed "
                        "terminal rules"
                    )

            if abs(source[0] - target[0]) + abs(source[1] - target[1]) != 1:
                raise ValueError(
                    f"lane {source} -> {target} does not join two "
                    "neighbouring cells"
                )

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
        return self._zones_by_id[zone_id]

    def zones_of_type(
        self,
        zone_type: ZoneType,
    ) -> tuple[WarehouseZone, ...]:

        return self._zones_by_type.get(zone_type, ())

    def cells_of_types(self, types) -> frozenset[Cell]:
        """Every cell of every zone whose type is in ``types``."""

        key = tuple(types)
        cells = self._cells_of_types.get(key)

        if cells is None:
            cells = frozenset(
                cell
                for zone_type in key
                for zone in self.zones_of_type(zone_type)
                for cell in zone.cells
            )
            self._cells_of_types[key] = cells

        return cells
