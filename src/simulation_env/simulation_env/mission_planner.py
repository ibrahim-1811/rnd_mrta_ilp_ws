"""Sequential zone mission planning for the lightweight warehouse simulator.

Shelf and drop-off cells are service cells, not transit space. A robot
may enter one only as the final cell of the leg that targets it, and
never as a shortcut. Every other service cell is blocked for the whole
leg, so a route to a charger or collection point never crosses racking
or a drop chute.

Having served one the robot reverses exactly one cell, back to the
aisle cell it approached from. The next leg starts from that aisle
cell, not from inside the station. That retreat is a real movement and
is counted in the mission distance and cost.

Charging and collection cells stay traversable for now.

The rule lives here, in the planner, not in the map. WarehouseMap's
occupancy layer stays exactly what it always was -- the red cells --
and the shelf restriction is a planning-time view laid on top.
"""

from dataclasses import dataclass

from mrta_core.grid import Cell, GridMap, astar_path
from mrta_core.warehouse_map import WarehouseMap, ZoneType


@dataclass(frozen=True)
class MissionSegment:
    from_zone: str
    to_zone: str
    start_cell: Cell
    goal_cell: Cell
    path: tuple[Cell, ...]
    distance_m: float
    cost: float

    # Aisle cell the robot reverses into after serving a station.
    # None for legs that do not end on a service cell.
    exit_cell: Cell | None = None


@dataclass(frozen=True)
class MissionPlan:
    zones: tuple[str, ...]
    segments: tuple[MissionSegment, ...]
    path: tuple[Cell, ...]
    total_distance_m: float
    total_cost: float


def station_cells(
    warehouse: WarehouseMap,
) -> frozenset[Cell]:
    """Every cell claimed by a semantic station."""

    cells: set[Cell] = set()

    for zone in warehouse.zones:
        cells |= zone.cells

    return frozenset(cells)


# Station types a robot may only enter as a leg's final cell,
# and must reverse out of before continuing.
SERVICE_TYPES: tuple[ZoneType, ...] = (
    ZoneType.SHELF,
    ZoneType.DROPOFF,
)


def service_cells(
    warehouse: WarehouseMap,
    types: tuple[ZoneType, ...] = SERVICE_TYPES,
) -> frozenset[Cell]:
    """Every cell occupied by racking or a drop chute."""

    cells: set[Cell] = set()

    for zone_type in types:
        for zone in warehouse.zones_of_type(zone_type):
            cells |= zone.cells

    return frozenset(cells)


def is_service_zone(
    warehouse: WarehouseMap,
    zone_id: str,
    types: tuple[ZoneType, ...] = SERVICE_TYPES,
) -> bool:

    return (
        warehouse.zone_by_id(zone_id).zone_type
        in types
    )


def navigation_grid(
    warehouse: WarehouseMap,
    *,
    occupied: frozenset[Cell],
    start: Cell,
    goal: Cell,
    congestion: dict[Cell, float] | None = None,
) -> GridMap:
    """Planning view: ``occupied`` blocks, except this leg's endpoints."""

    blocked = set(
        warehouse.grid.obstacles
    )

    blocked |= set(occupied)

    blocked.discard(start)
    blocked.discard(goal)

    return GridMap(
        width=warehouse.grid.width,
        height=warehouse.grid.height,
        obstacles=frozenset(blocked),
        congestion=congestion or {},
    )


def best_path_to_zone(
    warehouse: WarehouseMap,
    start: Cell,
    goal_zone_id: str,
    occupied: frozenset[Cell] = frozenset(),
):
    """Find cheapest path from one cell to any cell in a zone."""

    goal_zone = warehouse.zone_by_id(
        goal_zone_id
    )

    best = None

    for goal in sorted(goal_zone.cells):

        grid = navigation_grid(
            warehouse,
            occupied=occupied,
            start=start,
            goal=goal,
        )

        result = astar_path(
            grid,
            start,
            goal,
        )

        if not result.found:
            continue

        if (
            best is None
            or result.total_cost < best.total_cost
        ):
            best = result

    if best is None:
        raise RuntimeError(
            f"No path from {start} "
            f"to zone {goal_zone_id}"
        )

    return best


def plan_zone_mission(
    warehouse: WarehouseMap,
    zone_ids: list[str],
    *,
    services_block: bool = True,
) -> MissionPlan:
    """Plan a sequential mission through named warehouse zones.

    ``services_block`` keeps robots out of shelves and drop zones
    they are not visiting, and makes each such visit end with a
    one-cell retreat into the aisle. Set it False only to measure
    the unobstructed open-floor baseline.

    Retreat steps are included in ``path``, ``total_distance_m`` and
    ``total_cost``, because the robot really drives them.
    """

    if len(zone_ids) < 2:
        raise ValueError(
            "mission requires at least two zones"
        )

    # Validate all IDs first.
    for zone_id in zone_ids:
        warehouse.zone_by_id(zone_id)

    occupied = (
        service_cells(warehouse)
        if services_block
        else frozenset()
    )

    resolution = warehouse.grid.resolution

    first_zone = warehouse.zone_by_id(
        zone_ids[0]
    )

    # Start from the first deterministic cell.
    current_cell = min(
        first_zone.cells
    )

    segments = []
    complete_path = [
        current_cell
    ]

    total_distance = 0.0
    total_cost = 0.0

    for index in range(
        len(zone_ids) - 1
    ):

        from_zone = zone_ids[index]
        to_zone = zone_ids[index + 1]

        result = best_path_to_zone(
            warehouse,
            current_cell,
            to_zone,
            occupied,
        )

        distance_m = (
            result.distance
            * warehouse.grid.resolution
        )

        # Service cell reached: the robot backs out one step into
        # the aisle it came from, and the next leg departs there.
        exit_cell = None

        if occupied and is_service_zone(warehouse, to_zone):

            if len(result.path) < 2:
                raise RuntimeError(
                    f"cannot retreat from {to_zone}: "
                    f"leg has no approach cell"
                )

            exit_cell = result.path[-2]

        segment = MissionSegment(
            from_zone=from_zone,
            to_zone=to_zone,
            start_cell=result.path[0],
            goal_cell=result.path[-1],
            path=tuple(result.path),
            distance_m=distance_m,
            cost=result.total_cost,
            exit_cell=exit_cell,
        )

        segments.append(
            segment
        )

        # Avoid repeating the start cell
        # of every new segment.
        complete_path.extend(
            result.path[1:]
        )

        total_distance += distance_m
        total_cost += result.total_cost

        if exit_cell is None:
            current_cell = result.path[-1]

        else:
            complete_path.append(
                exit_cell
            )

            total_distance += resolution
            total_cost += resolution

            current_cell = exit_cell

    return MissionPlan(
        zones=tuple(zone_ids),
        segments=tuple(segments),
        path=tuple(complete_path),
        total_distance_m=total_distance,
        total_cost=total_cost,
    )
