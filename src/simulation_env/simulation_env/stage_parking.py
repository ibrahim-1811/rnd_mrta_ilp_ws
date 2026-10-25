"""Final parking: the Stage Zone bank is a shared fleet resource.

A robot is deployed onto a Stage Zone cell, and that is the only thing
its spawn cell decides. It does not own that cell. While orders remain
the robot stays available from wherever its last retreat left it, and
takes more work from there -- it is never sent back to a home cell
between orders.

When the fleet runs out of work, each returning robot claims the
*nearest free* Stage Zone by directed A* cost over the production road
graph. Not Manhattan: the warehouse is one-way, so the cheapest bank
cell is a property of the legal route and frequently is not the
geometrically closest one, nor the cell the robot spawned on.

Reservation
-----------
A zone is claimed when the return mission is assigned, not when the
robot arrives, so two robots converging on the same bank cannot pick
the same cell. On arrival the claim simply becomes the occupancy. If
the nearest free zone is already claimed, the next cheapest reachable
free zone is taken instead.

Determinism
-----------
Pairing is greedy over the whole batch, ordered by

    (route cost, zone_id, robot_id)

Route cost is the only scientific quantity in that key. ``zone_id``
resolves an exact cost tie between two bank cells, and ``robot_id``
only ever breaks a tie that both earlier keys left equal. Robot
identity therefore carries no parking priority -- it exists so two runs
of the same workload park identically.

This is fixed infrastructure. Every MRTA strategy parks through it,
and parking never touches order ownership.
"""

from mrta_core.warehouse_routing import best_path_to_zone, non_transit_cells

from simulation_env.fleet import home_bank


class StageParking:
    """Claims Stage Zone cells for returning robots, without duplicates."""

    def __init__(self, warehouse) -> None:

        self.warehouse = warehouse

        self.zones = home_bank(warehouse)

        # A bank cell is one parking slot. Zones are single-cell on
        # every current map; ``min`` keeps the choice deterministic if
        # one is ever drawn wider.
        self.cell_of = {zone.zone_id: min(zone.cells) for zone in self.zones}

        self._occupied = non_transit_cells(warehouse)

        # zone_id -> robot_id, and its inverse. A claim is permanent:
        # it is made when the return mission is assigned and becomes
        # the final occupancy when the robot arrives.
        self._holder: dict[str, str] = {}
        self._claim: dict[str, str] = {}

    # -- state ----------------------------------------------

    @property
    def claims(self) -> dict[str, str]:
        """robot_id -> the Stage Zone it holds."""
        return dict(self._claim)

    def claim_of(self, robot_id: str) -> str | None:
        return self._claim.get(robot_id)

    def holder_of(self, zone_id: str) -> str | None:
        return self._holder.get(zone_id)

    def cell_for(self, robot_id: str):
        """The cell this robot is parking on, if it holds a claim."""

        zone_id = self._claim.get(robot_id)

        return None if zone_id is None else self.cell_of[zone_id]

    def release(self, robot_id: str) -> None:
        """Give a claim back, for a return mission that never planned."""

        zone_id = self._claim.pop(robot_id, None)

        if zone_id is not None:
            self._holder.pop(zone_id, None)

    def free_zones(self, blocked_cells=frozenset()) -> list[str]:
        """Bank cells nobody holds and nobody is standing on."""

        return [
            zone.zone_id
            for zone in self.zones
            if zone.zone_id not in self._holder
            and self.cell_of[zone.zone_id] not in blocked_cells
        ]

    # -- allocation -----------------------------------------

    def route_cost(self, start_cell, zone_id: str, cache=None):
        """Directed A* cost from a cell to a bank cell, or None.

        Uses the production router, so the cost that decides parking is
        the same number the executor will drive. ``cache`` is the run's
        RouteCache; it only memoizes, so the answer is identical with
        or without it.
        """

        try:
            if cache is not None and cache.occupied == self._occupied:
                return cache.leg(start_cell, zone_id).total_cost

            return best_path_to_zone(
                self.warehouse,
                start_cell,
                zone_id,
                self._occupied,
            ).total_cost

        except RuntimeError:
            # No legal route to that bank cell from here.
            return None

    def assign(
        self,
        robot_cells: dict,
        *,
        blocked_cells=frozenset(),
        cache=None,
    ) -> dict:
        """Claim the cheapest free Stage Zone for each robot.

        ``robot_cells`` is robot_id -> the cell it is standing on.
        ``blocked_cells`` are cells held by robots outside this batch,
        so a bank cell somebody is still standing on is not handed to
        somebody else.

        Returns robot_id -> zone_id for every robot that got one. A
        robot with no reachable free zone is simply absent, and the
        caller decides what to do about it.
        """

        # A robot that already holds a claim keeps it. Re-deciding
        # would let a second robot take the cell it is driving to.
        result = {
            robot_id: self._claim[robot_id]
            for robot_id in robot_cells
            if robot_id in self._claim
        }

        pending = [
            robot_id for robot_id in sorted(robot_cells) if robot_id not in result
        ]

        if not pending:
            return result

        available = self.free_zones(blocked_cells)

        candidates = []

        for robot_id in pending:
            start = robot_cells[robot_id]

            for zone_id in available:
                cost = self.route_cost(start, zone_id, cache)

                if cost is None:
                    continue

                candidates.append((cost, zone_id, robot_id))

        # Cost decides. zone_id then robot_id only separate exact ties,
        # so the outcome is a property of the routes and not of the
        # order robots were offered in.
        candidates.sort()

        taken_robots: set[str] = set()
        taken_zones: set[str] = set()

        for cost, zone_id, robot_id in candidates:
            if robot_id in taken_robots or zone_id in taken_zones:
                continue

            taken_robots.add(robot_id)
            taken_zones.add(zone_id)

            self._claim[robot_id] = zone_id
            self._holder[zone_id] = robot_id

            result[robot_id] = zone_id

        return result
