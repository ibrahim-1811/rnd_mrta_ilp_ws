"""Charger and holding terminals: who holds each cell, and in what state.

Battery V1, increment B3. A robot in charge REQUEST is taken off the
transit network: it reserves a dedicated non-transit terminal and drives
there by the production directed A* route. There is no charging yet --
a robot that reaches a charger stays on it and its SOC keeps draining at
the idle rate.

Resource states
---------------
Every charging cell and every holding cell is one slot::

    FREE -> RESERVED -> OCCUPIED -> VACATING -> FREE

``reserve``   the slot is claimed before the robot starts to move
``occupy``    the robot is physically on the terminal cell
``vacate``    the robot has started to leave (B4+)
``cleared``   the robot has crossed the access edge back into the aisle

A slot is offered to a new robot only while FREE. It stays unavailable
through RESERVED, OCCUPIED and VACATING, so a robot can never be routed
into a terminal that another robot owns, stands on, or is backing out
of. Every illegal transition raises :class:`ResourceError`; none is
ever repaired silently.

Terminal-access safety
----------------------
Charger and holding cells are one-cell-deep banks entered from the aisle
cell directly outside them and left by reversing into that cell, so the
access edge is two-way. A head-on swap on it needs one robot entering
while another leaves the same terminal. The slot rule above excludes
that: the only robot that may enter a terminal is its reservation
holder, and the slot is not FREE again until any robot leaving it has
cleared the edge. In B3 nothing ever leaves a charger or holding cell at
all. See docs/CURRENT_PHASE.md, "B3 traffic-safety argument".

Selection
---------
Among FREE slots, by directed A* route cost from the robot's cell --
never Manhattan. Simultaneous requesters are paired greedily over

    (route cost, zone_id, robot_id)

exactly like Stage Zone parking. Cost is the only scientific quantity;
``zone_id`` separates an exact cost tie between cells and ``robot_id``
only breaks a tie both earlier keys left equal.

This is fixed infrastructure shared by every MRTA strategy. It never
touches order ownership.
"""

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction

from mrta_core.warehouse_routing import best_path_to_zone, non_transit_cells


class ResourceState(str, Enum):
    FREE = "FREE"
    RESERVED = "RESERVED"
    OCCUPIED = "OCCUPIED"
    VACATING = "VACATING"


class ChargeState(str, Enum):
    """Where a robot is in the charging workflow."""

    REQUEST = "REQUEST"
    TO_CHARGER = "TO_CHARGER"
    OCCUPIED_CHARGER = "OCCUPIED_CHARGER"
    TO_HOLDING = "TO_HOLDING"
    HOLDING = "HOLDING"
    # Saturation: every charger and holding cell is taken.
    TO_STAGE_FALLBACK = "TO_STAGE_FALLBACK"
    STAGE_FALLBACK = "STAGE_FALLBACK"


TRAVEL_STATES = frozenset({
    ChargeState.TO_CHARGER,
    ChargeState.TO_HOLDING,
    ChargeState.TO_STAGE_FALLBACK,
})

# Standing on a reserved non-transit terminal. B3 has no way out of
# these: charging completion and holding promotion come later.
SETTLED_STATES = frozenset({
    ChargeState.OCCUPIED_CHARGER,
    ChargeState.HOLDING,
    ChargeState.STAGE_FALLBACK,
})

# (travel state, settled state) for each kind of destination.
DESTINATION = {
    "charger": (ChargeState.TO_CHARGER, ChargeState.OCCUPIED_CHARGER),
    "holding": (ChargeState.TO_HOLDING, ChargeState.HOLDING),
    "stage": (ChargeState.TO_STAGE_FALLBACK, ChargeState.STAGE_FALLBACK),
}


class ResourceError(RuntimeError):
    """An illegal terminal-resource transition. Always a bug."""


class ChargingSaturationError(RuntimeError):
    """No charger, holding cell or Stage Zone left for a REQUEST robot.

    Raised rather than leaving the robot standing in an aisle.
    """


@dataclass
class TerminalSlot:
    zone_id: str
    cell: tuple
    state: ResourceState = ResourceState.FREE
    robot_id: str | None = None
    reserved_at: Fraction | None = None
    occupied_at: Fraction | None = None
    # Directed A* cost the reservation was chosen on.
    route_cost_m: float | None = None


@dataclass
class ChargeRecord:
    """One robot's trip through the charging workflow."""

    robot_id: str
    reason: str                     # CHARGE_DUE or CHARGE_NEEDED
    requested_at: Fraction
    request_cell: tuple
    request_soc: Fraction
    state: ChargeState = ChargeState.REQUEST
    kind: str = ""                  # charger, holding or stage
    zone_id: str | None = None
    target_cell: tuple | None = None
    route_cost_m: float | None = None
    path: tuple = ()
    departed_at: Fraction | None = None
    arrived_at: Fraction | None = None
    arrival_soc: Fraction | None = None


class TerminalBank:
    """Slots of one terminal type -- chargers, or holding cells."""

    def __init__(self, warehouse, zone_type, kind: str) -> None:

        self.warehouse = warehouse
        self.kind = kind

        # A bank cell is one slot. Zones are single-cell on every
        # current map; ``min`` keeps the choice deterministic if one is
        # ever drawn wider.
        self.slots = {
            zone.zone_id: TerminalSlot(zone.zone_id, min(zone.cells))
            for zone in sorted(
                warehouse.zones_of_type(zone_type),
                key=lambda zone: zone.zone_id,
            )
        }

        self._occupied = non_transit_cells(warehouse)
        self._by_robot: dict[str, str] = {}

    # -- state ----------------------------------------------

    def held_by(self, robot_id: str) -> str | None:
        return self._by_robot.get(robot_id)

    def free(self, blocked_cells=frozenset()) -> list[str]:
        """FREE slots nobody is standing on, in zone_id order."""

        return [
            zone_id
            for zone_id, slot in self.slots.items()
            if slot.state is ResourceState.FREE
            and slot.cell not in blocked_cells
        ]

    def count(self, state: ResourceState) -> int:
        return sum(1 for slot in self.slots.values() if slot.state is state)

    # -- transitions ----------------------------------------

    def _slot(self, zone_id: str, robot_id: str, expected: ResourceState):

        slot = self.slots.get(zone_id)

        if slot is None:
            raise ResourceError(f"{zone_id} is not a {self.kind} slot")

        if slot.state is not expected or (
            expected is not ResourceState.FREE and slot.robot_id != robot_id
        ):
            raise ResourceError(
                f"{robot_id}: {self.kind} {zone_id} is {slot.state.value}"
                f"{f' by {slot.robot_id}' if slot.robot_id else ''}, "
                f"expected {expected.value}"
            )

        return slot

    def reserve(self, zone_id, robot_id, now, route_cost_m=None) -> None:
        """FREE -> RESERVED, before the robot starts to move."""

        if robot_id in self._by_robot:
            raise ResourceError(
                f"{robot_id} already holds {self.kind} "
                f"{self._by_robot[robot_id]}"
            )

        slot = self._slot(zone_id, robot_id, ResourceState.FREE)

        slot.state = ResourceState.RESERVED
        slot.robot_id = robot_id
        slot.reserved_at = now
        slot.occupied_at = None
        slot.route_cost_m = route_cost_m

        self._by_robot[robot_id] = zone_id

    def occupy(self, zone_id, robot_id, now, cell) -> None:
        """RESERVED -> OCCUPIED, with the robot physically on the cell."""

        slot = self._slot(zone_id, robot_id, ResourceState.RESERVED)

        if cell != slot.cell:
            raise ResourceError(
                f"{robot_id} is at {cell}, not on {self.kind} {zone_id} "
                f"at {slot.cell}"
            )

        slot.state = ResourceState.OCCUPIED
        slot.occupied_at = now

    def vacate(self, zone_id, robot_id) -> None:
        """OCCUPIED -> VACATING: leaving, still unavailable."""

        slot = self._slot(zone_id, robot_id, ResourceState.OCCUPIED)
        slot.state = ResourceState.VACATING

    def cleared(self, zone_id, robot_id) -> None:
        """VACATING -> FREE, once the robot has crossed the access edge."""

        slot = self._slot(zone_id, robot_id, ResourceState.VACATING)

        slot.state = ResourceState.FREE
        slot.robot_id = None
        slot.reserved_at = None
        slot.occupied_at = None
        slot.route_cost_m = None

        del self._by_robot[robot_id]

    # -- selection ------------------------------------------

    def route_cost(self, start_cell, zone_id: str, cache=None):
        """Directed A* cost from a cell to a slot, or None if unreachable.

        The production router over the same non-transit view the
        executor drives, so the cost that chooses the slot is the cost
        of the route the robot then follows.
        """

        try:
            if cache is not None and cache.occupied == self._occupied:
                return cache.leg(start_cell, zone_id).total_cost

            return best_path_to_zone(
                self.warehouse, start_cell, zone_id, self._occupied
            ).total_cost

        except RuntimeError:
            return None

    def assign(self, robot_cells: dict, *, now, blocked_cells=frozenset(),
               cache=None) -> dict:
        """Reserve the cheapest FREE slot for each robot, without duplicates.

        ``robot_cells`` is robot_id -> the cell it stands on. Returns
        robot_id -> (zone_id, route cost) for every robot that got a
        slot; a robot with none is absent and the caller decides.
        """

        available = self.free(blocked_cells)

        candidates = []

        for robot_id in sorted(robot_cells):
            for zone_id in available:

                cost = self.route_cost(robot_cells[robot_id], zone_id, cache)

                if cost is not None:
                    candidates.append((cost, zone_id, robot_id))

        candidates.sort()

        result = {}
        taken = set()

        for cost, zone_id, robot_id in candidates:

            if robot_id in result or zone_id in taken:
                continue

            self.reserve(zone_id, robot_id, now, cost)

            taken.add(zone_id)
            result[robot_id] = (zone_id, cost)

        return result
