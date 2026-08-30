"""Static warehouse routing: the one grid-aware path layer.

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

This module sits in ``mrta_core`` because two layers need exactly the
same static answer and must never disagree:

  * ``task_management`` predicts route cost when allocating,
  * ``simulation_env`` executes the route it planned.

One A* (``mrta_core.grid.astar_path``), one free-cell rule
(``service_cells`` + ``navigation_grid``), used by both. Allocation
cost and executed mission are therefore the same computation, which is
what makes the predicted-vs-realized comparison meaningful later.
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


class RouteCache:
    """Memoized static legs for one warehouse and one occupancy view.

    Costing a batch of robot-order pairs asks for the same leg over and
    over: ``shelf_015 -> dropoff_016`` is identical no matter which
    robot is being scored or which shelf permutation asked for it. Only
    the opening leg, from a robot's own cell, is ever unique.

    A dispatch of 18 robots against 18 three-shelf orders is ~7800 A*
    searches without this and a few hundred with it. The cache is a
    pure memo -- same inputs, same PathResult -- so it changes cost and
    timing, never the route chosen.

    Safe to keep for a whole run: the map and the service-cell rule do
    not change while robots drive.
    """

    __slots__ = ("warehouse", "occupied", "_legs", "hits", "misses")

    def __init__(self, warehouse: WarehouseMap, occupied=None) -> None:

        self.warehouse = warehouse

        self.occupied = (
            service_cells(warehouse) if occupied is None else occupied
        )

        # (start_cell, goal_zone_id) -> PathResult, or None for
        # 'no route exists', which is worth remembering too.
        self._legs: dict = {}

        self.hits = 0
        self.misses = 0

    def leg(self, start: Cell, goal_zone_id: str):
        """``best_path_to_zone`` with the answer remembered."""

        key = (start, goal_zone_id)

        if key in self._legs:
            self.hits += 1

            found = self._legs[key]

            if found is None:
                raise RuntimeError(
                    f"No path from {start} to zone {goal_zone_id}"
                )

            return found

        self.misses += 1

        try:
            result = best_path_to_zone(
                self.warehouse, start, goal_zone_id, self.occupied
            )

        except RuntimeError:
            self._legs[key] = None
            raise

        self._legs[key] = result

        return result

    def stats(self) -> dict:
        return {
            "legs_cached": len(self._legs),
            "hits": self.hits,
            "misses": self.misses,
        }


def plan_return_to_zone(
    warehouse: WarehouseMap,
    start_cell: Cell,
    zone_id: str,
    from_label: str = "current",
    occupied: frozenset[Cell] | None = None,
) -> MissionPlan:
    """One-leg plan from wherever a robot is back to a zone.

    Used for parking. The robot is mid-aisle rather than on a station,
    so this takes a cell instead of a starting zone id.
    """

    if occupied is None:
        occupied = service_cells(warehouse)

    result = best_path_to_zone(
        warehouse,
        start_cell,
        zone_id,
        occupied,
    )

    distance_m = (
        result.distance
        * warehouse.grid.resolution
    )

    segment = MissionSegment(
        from_zone=from_label,
        to_zone=zone_id,
        start_cell=result.path[0],
        goal_cell=result.path[-1],
        path=tuple(result.path),
        distance_m=distance_m,
        cost=result.total_cost,
        # Staging is not a service cell: no hold, no retreat.
        exit_cell=None,
    )

    return MissionPlan(
        zones=(from_label, zone_id),
        segments=(segment,),
        path=tuple(result.path),
        total_distance_m=distance_m,
        total_cost=result.total_cost,
    )


def plan_from_cell(
    warehouse: WarehouseMap,
    start_cell: Cell,
    zone_ids: list[str],
    *,
    start_label: str = "current",
    services_block: bool = True,
    extra_blocked: frozenset[Cell] = frozenset(),
    cache: "RouteCache | None" = None,
) -> MissionPlan:
    """Plan a sequential mission from a bare cell through named zones.

    This is the one leg-walking implementation. A robot mid-aisle is
    the normal case once it has retreated from a station, so the start
    is a cell, not a zone.

    ``services_block`` keeps robots out of shelves and drop zones
    they are not visiting, and makes each such visit end with a
    one-cell retreat into the aisle. Set it False only to measure
    the unobstructed open-floor baseline.

    Retreat steps are included in ``path``, ``total_distance_m`` and
    ``total_cost``, because the robot really drives them.

    ``extra_blocked`` adds cells that are occupied right now -- most
    importantly the cells stationary robots are standing on. A robot
    that has parked is a solid obstacle, and a static route drawn
    straight through it cannot be realised by the space-time layer
    afterwards. Pass live occupancy here whenever the plan is going to
    be executed.

    Leave it empty for a *prediction*: an allocator costing a route
    should see warehouse geometry only, not where the other seventeen
    robots happen to be standing.

    Pass a :class:`RouteCache` when planning many routes over one map:
    it only remembers leg results, so the plan is unchanged. A cache is
    only consulted for the occupancy view it was built for.
    """

    if not zone_ids:
        raise ValueError(
            "mission requires at least one destination zone"
        )

    # Validate all IDs first.
    for zone_id in zone_ids:
        warehouse.zone_by_id(zone_id)

    occupied = (
        service_cells(warehouse)
        if services_block
        else frozenset()
    )

    if extra_blocked:
        # The robot's own cell is never an obstacle to itself.
        occupied = frozenset(occupied) | (
            frozenset(extra_blocked) - {start_cell}
        )

    # A cache is only valid for the occupancy view it was built for.
    if cache is not None and cache.occupied != occupied:
        cache = None

    resolution = warehouse.grid.resolution

    current_cell = start_cell

    segments = []
    complete_path = [current_cell]

    total_distance = 0.0
    total_cost = 0.0

    from_zone = start_label

    for to_zone in zone_ids:

        result = (
            cache.leg(current_cell, to_zone)
            if cache is not None
            else best_path_to_zone(
                warehouse,
                current_cell,
                to_zone,
                occupied,
            )
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

        segments.append(segment)

        # Avoid repeating the start cell of every new segment.
        complete_path.extend(result.path[1:])

        total_distance += distance_m
        total_cost += result.total_cost

        if exit_cell is None:
            current_cell = result.path[-1]

        else:
            complete_path.append(exit_cell)

            total_distance += resolution
            total_cost += resolution

            current_cell = exit_cell

        from_zone = to_zone

    return MissionPlan(
        zones=(start_label, *zone_ids),
        segments=tuple(segments),
        path=tuple(complete_path),
        total_distance_m=total_distance,
        total_cost=total_cost,
    )


def plan_zone_mission(
    warehouse: WarehouseMap,
    zone_ids: list[str],
    *,
    services_block: bool = True,
) -> MissionPlan:
    """Plan a mission that starts parked on ``zone_ids[0]``.

    Thin wrapper over :func:`plan_from_cell`: it only resolves the
    first zone to its deterministic start cell.
    """

    if len(zone_ids) < 2:
        raise ValueError(
            "mission requires at least two zones"
        )

    first_zone = warehouse.zone_by_id(zone_ids[0])

    return plan_from_cell(
        warehouse,
        min(first_zone.cells),
        list(zone_ids[1:]),
        start_label=zone_ids[0],
        services_block=services_block,
    )
