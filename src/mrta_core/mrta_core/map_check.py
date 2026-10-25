"""Fast structural checks for a warehouse map, in plain language.

Built for the person drawing a map: it runs in a second or two, so it
can be run after every edit, and every problem it reports names the
cell and says what to do about it.

It checks the road network, not the routes. The full acceptance gate,
``scripts/check_map_topology.py``, additionally plans a route between
every pair of stations; that is slow on a large map and belongs at the
end, once this check is clean.

Everything is computed through the production policy --
``directional_blocked_edges`` and ``non_transit_cells`` -- so the
answer is about the graph robots will actually drive, never a
restatement of the rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mrta_core.grid import Cell
from mrta_core.warehouse_map import WarehouseMap, ZoneType
from mrta_core.warehouse_routing import (
    HOME_TYPES,
    directional_blocked_edges,
    non_transit_cells,
)


STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1))


@dataclass
class Problem:
    """One thing wrong with the map, and where.

    An ``error`` means robots cannot use the map as drawn. A
    ``warning`` means they can, but it carries a risk that grows with
    the number of robots -- on a map meant for a large fleet, treat a
    warning as an error.
    """

    kind: str
    cells: tuple[Cell, ...]
    message: str
    severity: str = "error"


@dataclass
class MapReport:
    width: int
    height: int
    roads: int
    stations: dict[str, int]
    parking: int
    drawn: bool
    problems: list[Problem] = field(default_factory=list)

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == "error"]

    @property
    def warnings(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == "warning"]

    @property
    def ok(self) -> bool:
        """Usable: no errors. Warnings may remain."""
        return not self.errors

    @property
    def clean(self) -> bool:
        """No errors and no warnings."""
        return not self.problems

    def max_robots(self) -> int:
        """Every robot needs its own parking cell to start on."""
        return self.parking


def _neighbours(cell: Cell):
    x, y = cell

    for dx, dy in STEPS:
        yield (x + dx, y + dy)


def _components(nodes, edges) -> list[set[Cell]]:
    """Strongly connected components, largest first (Kosaraju)."""

    forward = {node: [] for node in nodes}
    backward = {node: [] for node in nodes}

    for source, target in edges:
        forward[source].append(target)
        backward[target].append(source)

    order = []
    seen = set()

    for start in sorted(nodes):
        if start in seen:
            continue

        seen.add(start)
        stack = [(start, iter(sorted(forward[start])))]

        while stack:
            node, children = stack[-1]
            child = next(children, None)

            if child is None:
                stack.pop()
                order.append(node)
            elif child not in seen:
                seen.add(child)
                stack.append((child, iter(sorted(forward[child]))))

    components = []
    assigned = set()

    for start in reversed(order):
        if start in assigned:
            continue

        component = {start}
        assigned.add(start)
        stack = [start]

        while stack:
            node = stack.pop()

            for parent in backward[node]:
                if parent not in assigned:
                    assigned.add(parent)
                    component.add(parent)
                    stack.append(parent)

        components.append(component)

    components.sort(key=lambda c: (-len(c), min(c)))

    return components


def check_map(warehouse: WarehouseMap) -> MapReport:
    """Every structural rule a map must meet before robots can use it."""

    grid = warehouse.grid
    terminals = non_transit_cells(warehouse)
    blocked = directional_blocked_edges(warehouse)

    roads = {
        (x, y)
        for y in range(grid.height)
        for x in range(grid.width)
        if (x, y) not in grid.obstacles and (x, y) not in terminals
    }

    road_edges = {
        (cell, target)
        for cell in roads
        for target in _neighbours(cell)
        if target in roads and (cell, target) not in blocked
    }

    stations = {}

    for zone in warehouse.zones:
        stations[zone.zone_type.value] = stations.get(zone.zone_type.value, 0) + 1

    parking = sum(len(warehouse.zones_of_type(kind)) for kind in HOME_TYPES)

    report = MapReport(
        width=grid.width,
        height=grid.height,
        roads=len(roads),
        stations=dict(sorted(stations.items())),
        parking=parking,
        drawn=warehouse.lane_edges is not None,
    )

    problems = report.problems

    # ---- 1. roads with no way out --------------------------------
    exits = {cell: [] for cell in roads}

    for source, target in road_edges:
        exits[source].append(target)

    dead_ends = sorted(cell for cell in roads if not exits[cell])

    for cell in dead_ends:
        problems.append(
            Problem(
                "dead end",
                (cell,),
                "road with no arrow out of it. Give it at least one arrow "
                "pointing to a neighbouring road.",
            )
        )

    # ---- 2. two-way roads ----------------------------------------
    for source, target in sorted(road_edges):
        if source < target and (target, source) in road_edges:
            problems.append(
                Problem(
                    "two-way road",
                    (source, target),
                    "these two roads point at each other, so two robots "
                    "can meet head-on. Remove one of the two arrows.",
                )
            )

    # ---- 3. one connected road network ---------------------------
    components = _components(roads, road_edges) if roads else []

    main = components[0] if components else set()

    for piece in components[1:]:
        cells = tuple(sorted(piece))

        if len(cells) == 1 and cells[0] in dead_ends:
            # Already reported as a dead end; a single stranded cell
            # would only be the same problem said twice.
            continue

        problems.append(
            Problem(
                "disconnected roads",
                cells,
                f"{len(cells)} road cell(s) you can drive into but not "
                "back out of to the rest of the warehouse, or the other "
                "way round. Check the arrows where this area meets the "
                "main roads: traffic must be able to both enter and leave.",
            )
        )

    # ---- 3b. tight loops four robots can lock up in --------------
    #
    # Four road cells in a square whose arrows chase each other round
    # it. One-way and connected, so it passes every rule above, and it
    # is still a trap: with a robot on each cell, each waits for the
    # next and none can ever move.
    #
    # Not hypothetical. The V2 deadlock under the random strategy (500
    # orders, seed 42) is four robots on (13,3) (14,3) (14,4) (13,4),
    # one of V2's 33 rotaries. V2 keeps them out of its shelf blocks
    # (warehouse_routing: "an aisle pair can never form a two-cell
    # rotary") but its open bands, where alternating rows cross
    # alternating columns, contain all 33.
    #
    # A warning rather than an error: the map is legal and runs, and the
    # risk scales with robot density. At 22 robots V2 deadlocks only
    # under a policy that scatters work; at 100 robots a rotary is close
    # to certain to close.
    for x in range(grid.width - 1):
        for y in range(grid.height - 1):
            square = ((x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1))

            if not all(cell in roads for cell in square):
                continue

            for order in (square, square[::-1]):
                if all(
                    (order[i], order[(i + 1) % 4]) in road_edges
                    for i in range(4)
                ):
                    problems.append(
                        Problem(
                            "rotary",
                            tuple(sorted(square)),
                            "these four roads form a tight loop: four "
                            "robots on it would each wait for the next, "
                            "forever. Reverse one arrow so traffic can "
                            "leave the square instead of circling it.",
                            severity="warning",
                        )
                    )

    # ---- 4. every station reachable ------------------------------
    for zone in warehouse.zones:
        if zone.zone_type not in _STATION_TYPES:
            continue

        for cell in zone.cells:
            touching = [n for n in _neighbours(cell) if n in roads]

            if not touching:
                problems.append(
                    Problem(
                        "station not on a road",
                        (cell,),
                        f"{zone.zone_id} does not touch any road, so no "
                        "robot can reach it. Put a road cell next to it.",
                    )
                )

            elif not any(n in main for n in touching):
                problems.append(
                    Problem(
                        "station unreachable",
                        (cell,),
                        f"{zone.zone_id} only touches roads that are cut "
                        "off from the main network (see 'disconnected "
                        "roads').",
                    )
                )

    # ---- 5. the basics an order needs ----------------------------
    for zone_type, need in (
        (ZoneType.SHELF, "a shelf"),
        (ZoneType.DROPOFF, "a drop-off"),
    ):
        if not warehouse.zones_of_type(zone_type):
            problems.append(
                Problem(
                    "missing station",
                    (),
                    f"the map has no {zone_type.value} cells; every order "
                    f"needs {need}.",
                )
            )

    if parking == 0:
        problems.append(
            Problem(
                "missing station",
                (),
                "the map has no parking (P) cells. Each robot starts on "
                "its own parking cell, so the fleet size is the number "
                "of P cells.",
            )
        )

    if not warehouse.zones_of_type(ZoneType.CHARGING):
        problems.append(
            Problem(
                "missing station",
                (),
                "the map has no chargers (C). Robots run out of battery "
                "on any workload long enough to drain them.",
            )
        )

    return report


_STATION_TYPES = frozenset(
    {
        ZoneType.SHELF,
        ZoneType.DROPOFF,
        ZoneType.CHARGING,
        ZoneType.CHARGE_QUEUE,
        *HOME_TYPES,
    }
)
