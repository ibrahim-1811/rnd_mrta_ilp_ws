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
from functools import lru_cache

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

# Cells that may be used as a leg endpoint but should not be used as
# intermediate transit space.
#
# STAGING is deliberately not a service type. Robots may start there
# and return there, but no pick/drop service or retreat is attached to
# a staging cell.
NON_TRANSIT_TYPES: tuple[ZoneType, ...] = (
    ZoneType.SHELF,
    ZoneType.DROPOFF,
    ZoneType.STAGING,
)


# ---------------------------------------------------------------------
# Fixed warehouse traffic topology.
#
# These are infrastructure rules shared by every MRTA strategy.
# They make structural head-on deadlock impossible without making
# allocation decisions for the robots.
#
# Grid convention:
#   east  = +x
#   west  = -x
#   north = +y
#   south = -y
#
# The core transit graph is fully directed: for every pair of adjacent
# non-service cells at most one of the two directions is legal. Two
# robots therefore can never legally claim the same edge from opposite
# ends, which is what used to let (10,15) and (10,16) block each other
# forever.
#
# Only these edges stay bidirectional, by design:
#
#   * aisle <-> shelf
#   * aisle <-> dropoff
#   * aisle <-> staging
#
# Those are service connections, not transit lanes. A service cell is
# never a through route (navigation_grid() blocks it), so a robot only
# ever sits on one as the endpoint of its own leg, then reverses out.
#
# Layout this policy is written against (17 x 35):
#
#   x = 3, 6, 9, 12   northbound trunk aisles
#   x = 5, 8, 11, 14  southbound trunk aisles
#   x = 4, 7, 10, 13  shelf columns; open floor only inside the three
#                     horizontal bands, northbound there
#   x = 1, 2          left staging frontage lanes  (y = 13..21)
#   x = 15            right staging frontage lane  (y = 13..21)
#
#   y = 1..5, 15..19, 29..33   open bands, alternating one-way rows
#   y = 6..14, 20..28          shelf blocks; the only horizontal core
#                              edges are the rungs between an aisle
#                              pair, all westbound
# ---------------------------------------------------------------------

EASTBOUND_ROWS = frozenset({
    2, 4,
    15, 17, 19,
    29, 31, 33,
})

WESTBOUND_ROWS = frozenset({
    1, 3, 5,
    16, 18,
    30, 32,
})


# Rows inside a shelf block.
#
# Racking at x = 4, 7, 10, 13 breaks these rows into short rungs that
# only ever join one southbound aisle to its northbound partner:
#
#   (5,y)-(6,y)   (8,y)-(9,y)   (11,y)-(12,y)
#
# All rungs run west, so an aisle pair can never form a two-cell
# rotary and a robot can always cross from the up-aisle to the
# down-aisle without meeting anyone head-on.
SHELF_BLOCK_ROWS = (
    frozenset(range(6, 15))
    | frozenset(range(20, 29))
)

SHELF_BLOCK_RUNG_DX = -1


NORTHBOUND_COLS = frozenset({
    3, 6, 9, 12,
})

SOUTHBOUND_COLS = frozenset({
    5, 8, 11, 14,
})


# Shelf columns.
#
# These carry racking for most of their height, so their free floor is
# three disjoint runs, one per open band. Vertical movement is legal
# only inside a single band, and always northbound.
#
# Northbound is not arbitrary. The bottom band is the awkward one:
# (3,3) is the foot of northbound x=3 and (14,1) is the foot of
# southbound x=14, so both need to be fed from their row rather than
# from the aisle above. That is why the bottom band's rows run
# opposite to the top band's, and why these columns lift traffic back
# out of the bottom band instead of pushing more into it.
SHELF_COLS = frozenset({
    4, 7, 10, 13,
})

# (y_min, y_max) of each open band, in ascending order.
OPEN_BANDS = (
    (0, 5),
    (15, 19),
    (29, 34),
)


# Short turnaround connectors that are allowed to stay bidirectional.
#
# The lane policy below no longer needs any: the bottom band's flipped
# handedness gives (3,3), (13,1) and (14,1) a legal way in and out on
# their own. The hook is kept because it is the only supported way to
# reintroduce a two-way core edge, and anything listed here must be
# covered by the local corridor mutex in the traffic controller.
BIDIRECTIONAL_CONNECTORS: frozenset[frozenset] = frozenset()


# Left staging frontage.
#
# Staging cells themselves are at x=0 and remain terminal cells.
#
# x=1 is the northbound frontage lane nearest the staging bank.
# x=2 is the southbound feeder/return lane.
#
# These fixed infrastructure rules prevent opposing robots from
# meeting head-on directly in front of the parking bank.
STAGING_FRONTAGE_Y_MIN = 13
STAGING_FRONTAGE_Y_MAX = 21

LEFT_FRONTAGE_NORTH_X = 1
LEFT_FRONTAGE_SOUTH_X = 2

# The right bank has only one dedicated lane, x=15. x=14 is a normal
# southbound trunk aisle that doubles as its feeder, so there is no
# second frontage lane and no external boundary to police here.
RIGHT_FRONTAGE_NORTH_X = 15
RIGHT_FRONTAGE_FEEDER_X = 14


def _open_band(y: int) -> tuple[int, int] | None:
    """The open band containing ``y``, or None inside a shelf block."""

    for low, high in OPEN_BANDS:
        if low <= y <= high:
            return (low, high)

    return None


def _directional_edge_allowed(
    source: Cell,
    target: Cell,
    terminal_cells: frozenset[Cell],
) -> bool:
    """Whether one geometric grid edge obeys warehouse traffic flow."""

    sx, sy = source
    tx, ty = target

    if abs(tx - sx) + abs(ty - sy) != 1:
        return False

    # Shelf, dropoff and staging connection edges stay bidirectional.
    #
    # This preserves:
    #   aisle -> service/staging
    #   service/staging -> aisle
    #
    # Service cells themselves remain non-transit in navigation_grid().
    if source in terminal_cells or target in terminal_cells:
        return True

    if (
        frozenset({source, target})
        in BIDIRECTIONAL_CONNECTORS
    ):
        return True

    dx = tx - sx
    dy = ty - sy

    if dy == 0:

        # -----------------------------------------------------
        # Staging frontage circulation.
        #
        # Left frontage:
        #
        #   x1 = northbound staging lane
        #   x2 = southbound staging lane
        #   x3 = warehouse side
        #
        # Warehouse entry into the frontage happens only at the
        # top, y=21:
        #
        #   (3,21) -> (2,21)
        #
        # Warehouse exit happens only at the bottom, y=13:
        #
        #   (2,13) -> (3,13)
        #
        # Between x1 and x2, returning robots move from x2 to x1.
        # At the top, outbound robots cross from x1 to x2.
        #
        # This prevents bidirectional staging feeder swaps while
        # preserving a complete one-way circulation loop.
        # -----------------------------------------------------

        in_staging_band = (
            STAGING_FRONTAGE_Y_MIN
            <= sy
            <= STAGING_FRONTAGE_Y_MAX
        )

        if in_staging_band:

            # ---------------------------------------------
            # LEFT INTERNAL FRONTAGE
            # x1 <-> x2
            # ---------------------------------------------

            if {sx, tx} == {
                LEFT_FRONTAGE_NORTH_X,
                LEFT_FRONTAGE_SOUTH_X,
            }:

                # Top crossover for outbound traffic:
                #
                # x1 -> x2
                if sy == STAGING_FRONTAGE_Y_MAX:
                    return (
                        sx == LEFT_FRONTAGE_NORTH_X
                        and
                        tx == LEFT_FRONTAGE_SOUTH_X
                    )

                # Everywhere else returning traffic moves:
                #
                # x2 -> x1
                return (
                    sx == LEFT_FRONTAGE_SOUTH_X
                    and
                    tx == LEFT_FRONTAGE_NORTH_X
                )

            # ---------------------------------------------
            # LEFT EXTERNAL FRONTAGE
            # x2 <-> x3
            # ---------------------------------------------

            if {sx, tx} == {
                LEFT_FRONTAGE_SOUTH_X,
                LEFT_FRONTAGE_SOUTH_X + 1,
            }:

                # Top ingress:
                #
                # warehouse x3 -> staging lane x2
                if sy == STAGING_FRONTAGE_Y_MAX:
                    return (
                        sx == LEFT_FRONTAGE_SOUTH_X + 1
                        and
                        tx == LEFT_FRONTAGE_SOUTH_X
                    )

                # Bottom egress:
                #
                # staging lane x2 -> warehouse x3
                if sy == STAGING_FRONTAGE_Y_MIN:
                    return (
                        sx == LEFT_FRONTAGE_SOUTH_X
                        and
                        tx == LEFT_FRONTAGE_SOUTH_X + 1
                    )

                # No warehouse/frontage crossing in the middle.
                return False

            # ---------------------------------------------
            # RIGHT FRONTAGE
            # x14 (trunk feeder) <-> x15 (frontage lane)
            #
            # Robots turn in off the southbound trunk at any row
            # and leave again over the top, which keeps the lane
            # a one-way loop.
            #
            # x13 <-> x14 is deliberately NOT special-cased. x13
            # is ordinary warehouse floor in the middle band, and
            # policing it here used to cut rows 15..19 in half.
            # ---------------------------------------------

            if {sx, tx} == {
                RIGHT_FRONTAGE_FEEDER_X,
                RIGHT_FRONTAGE_NORTH_X,
            }:

                # Top crossover back onto the trunk:
                #
                # x15 -> x14
                if sy == STAGING_FRONTAGE_Y_MAX:
                    return (
                        sx == RIGHT_FRONTAGE_NORTH_X
                        and
                        tx == RIGHT_FRONTAGE_FEEDER_X
                    )

                # Turn-in from the trunk:
                #
                # x14 -> x15
                return (
                    sx == RIGHT_FRONTAGE_FEEDER_X
                    and
                    tx == RIGHT_FRONTAGE_NORTH_X
                )

        # Rungs inside a shelf block.
        if sy in SHELF_BLOCK_ROWS:
            return dx == SHELF_BLOCK_RUNG_DX

        if sy in EASTBOUND_ROWS:
            return dx == 1

        if sy in WESTBOUND_ROWS:
            return dx == -1

        return False

    if dx == 0:

        # -----------------------------------------------------
        # Staging frontage lanes.
        #
        # Only edges completely inside y=13..21 are directional.
        # Entry and exit at the ends of the frontage remain
        # governed by the surrounding warehouse topology.
        #
        # Edges touching a STAGING terminal were already accepted
        # above, so parking entry and departure remain possible.
        # -----------------------------------------------------

        in_frontage = (
            STAGING_FRONTAGE_Y_MIN <= sy <= STAGING_FRONTAGE_Y_MAX
            and
            STAGING_FRONTAGE_Y_MIN <= ty <= STAGING_FRONTAGE_Y_MAX
        )

        if in_frontage:

            if sx == LEFT_FRONTAGE_NORTH_X:
                return dy == 1

            if sx == LEFT_FRONTAGE_SOUTH_X:
                return dy == -1

            if sx == RIGHT_FRONTAGE_NORTH_X:
                return dy == 1

        # Shelf columns: northbound, and never across a shelf block.
        if sx in SHELF_COLS:

            band = _open_band(sy)

            if band is None or band != _open_band(ty):
                return False

            return dy == 1

        # Normal warehouse vertical highways.
        if sx in NORTHBOUND_COLS:
            return dy == 1

        if sx in SOUTHBOUND_COLS:
            return dy == -1

        return False

    return False


@lru_cache(maxsize=16)
def _cached_directional_blocked_edges(
    width: int,
    height: int,
    obstacles: frozenset[Cell],
    terminal_cells: frozenset[Cell],
):
    """Build the fixed directed-edge restriction set once per layout."""

    blocked_edges = set()

    for y in range(height):
        for x in range(width):

            source = (x, y)

            if source in obstacles:
                continue

            for target in (
                (x + 1, y),
                (x - 1, y),
                (x, y + 1),
                (x, y - 1),
            ):

                tx, ty = target

                if not (
                    0 <= tx < width
                    and 0 <= ty < height
                ):
                    continue

                if target in obstacles:
                    continue

                if not _directional_edge_allowed(
                    source,
                    target,
                    terminal_cells,
                ):
                    blocked_edges.add(
                        (source, target)
                    )

    return frozenset(blocked_edges)


def directional_blocked_edges(
    warehouse: WarehouseMap,
) -> frozenset[tuple[Cell, Cell]]:
    """Directed edges forbidden by the fixed warehouse lane policy."""

    terminal_cells = non_transit_cells(
        warehouse
    )

    return _cached_directional_blocked_edges(
        warehouse.grid.width,
        warehouse.grid.height,
        warehouse.grid.obstacles,
        terminal_cells,
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


def non_transit_cells(
    warehouse: WarehouseMap,
    types: tuple[ZoneType, ...] = NON_TRANSIT_TYPES,
) -> frozenset[Cell]:
    """Cells excluded from intermediate static routing.

    navigation_grid() removes the current leg's start and goal from
    the blocked set, so a robot may still leave its own staging cell
    and enter its own staging destination.
    """

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
        blocked_edges=directional_blocked_edges(
            warehouse
        ),
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
            non_transit_cells(warehouse)
            if occupied is None
            else occupied
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
        non_transit_cells(warehouse)
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
