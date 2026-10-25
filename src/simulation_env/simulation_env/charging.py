"""Charger and holding terminals: who holds each cell, and in what state.

Battery V1, increments B3, B4 and B5. A robot in charge REQUEST is
taken off the transit network: it reserves a dedicated non-transit
terminal and drives there by the production directed A* route (B3). On a
charger it then charges to 100%, reverses into the access aisle cell and
rejoins the fleet (B4).

A robot that reached a *holding* cell, or a Stage Zone under
saturation, is a waiting robot. When a charger becomes FREE the waiting
robot first in the queue is promoted onto it (B5), and a CHARGE_DUE
robot that finds every charger occupied may preempt the highest-SOC
charging robot (B5). Both are frozen policy; see docs/DECISIONS.md,
"Charger Saturation and Preemption", "Charger Queue Discipline",
"Stage Zone Promotion" and "Preempted Robot".

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
cleared the edge. See docs/CURRENT_PHASE.md, "B3 traffic-safety
argument".

That rule is what makes B4's departure safe. A full robot goes
OCCUPIED -> VACATING *before* it moves, and the slot becomes FREE only
once the robot physically stands on the aisle cell. So no robot can be
entering while another is leaving, and the access edge never carries an
opposite-direction swap.

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


# Recorded on every preemption, never branched on. The frozen rule
# always selects the maximum-SOC victim, so "prefer a robot at or above
# 80%, otherwise the highest-SOC robot" is one rule, not two; the
# threshold is kept only so the event record can be analysed later
# (docs/DECISIONS.md, "Charger Saturation and Preemption").
PREEMPT_RECORD_SOC = Fraction(80)


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
    # Charging to 100% on its own reserved charger (B4).
    CHARGING = "CHARGING"
    # Full: reversing off the charger into the access aisle cell (B4).
    LEAVING_CHARGER = "LEAVING_CHARGER"
    # Cut short by a CHARGE_DUE requester: reversing off the charger
    # with less than a full battery (B5).
    PREEMPTED_LEAVING_CHARGER = "PREEMPTED_LEAVING_CHARGER"
    # Clear of the charger it gave up, and not energy-eligible for
    # work, so the charge request continues as a new holding trip (B5).
    PREEMPTED = "PREEMPTED"
    # Off the charger, access edge cleared, back in the fleet (B4).
    REJOINED = "REJOINED"
    TO_HOLDING = "TO_HOLDING"
    HOLDING = "HOLDING"
    # Granted a free charger from holding: reversing off the holding
    # cell into its access aisle cell (B5). The charger trip itself is a
    # new record, so this state ends the holding trip.
    LEAVING_HOLDING = "LEAVING_HOLDING"
    # The waiting trip ended in a granted charger: off holding, or
    # driving off a Stage Zone (B5).
    PROMOTED = "PROMOTED"
    # Saturation: every charger and holding cell is taken. The robot
    # waits on the Stage Zone and is promoted like a holding robot.
    TO_STAGE_FALLBACK = "TO_STAGE_FALLBACK"
    STAGE_FALLBACK = "STAGE_FALLBACK"


# Driving *to* a reserved terminal. Arrival settles the robot onto it.
TRAVEL_STATES = frozenset(
    {
        ChargeState.TO_CHARGER,
        ChargeState.TO_HOLDING,
        ChargeState.TO_STAGE_FALLBACK,
    }
)

# Driving *off* a terminal. Arrival clears the slot instead (B4), so
# this is deliberately not part of TRAVEL_STATES. What happens once the
# slot is clear differs per state: a full robot rejoins the fleet, a
# preempted one rejoins only if it is energy-eligible, and a promoted
# one sets off for the charger it was granted.
LEAVING_STATES = frozenset(
    {
        ChargeState.LEAVING_CHARGER,
        ChargeState.PREEMPTED_LEAVING_CHARGER,
        ChargeState.LEAVING_HOLDING,
    }
)

# Leaving a charger, either full (B4) or preempted (B5).
LEAVING_CHARGER_STATES = frozenset(
    {
        ChargeState.LEAVING_CHARGER,
        ChargeState.PREEMPTED_LEAVING_CHARGER,
    }
)

# In flight either way: the robot is not a stationary obstacle.
MOVING_STATES = TRAVEL_STATES | LEAVING_STATES

# Waiting for a charger, in the queue the promotion pass serves.
WAITING_STATES = frozenset({ChargeState.HOLDING, ChargeState.STAGE_FALLBACK})

# Standing still on a reserved non-transit terminal, with nothing
# scheduled to move it. A charging robot is NOT settled -- its charge
# will finish and it will rejoin -- so the run must not be considered
# over while it charges. A HOLDING or STAGE_FALLBACK robot is settled
# only for as long as no FREE charger is within its reach: since B5 it
# is promoted in the same instant one becomes FREE, so the pair (FREE
# reachable charger, waiting robot) never survives a tick.
SETTLED_STATES = frozenset(
    {
        ChargeState.OCCUPIED_CHARGER,
        ChargeState.HOLDING,
        ChargeState.STAGE_FALLBACK,
    }
)

# Anywhere in the charging workflow except back in the fleet. A robot
# in any of these is never offered an order.
BUSY_STATES = (
    frozenset({ChargeState.REQUEST, ChargeState.CHARGING})
    | MOVING_STATES
    | SETTLED_STATES
)

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
    # The robot this slot is already assigned to for its *next*
    # occupancy, while the current holder is still leaving it (B5).
    #
    # "A charger is reserved when assigned to a robot" and "a slot is
    # offered only while FREE" are both frozen rules: a preemption
    # assigns the charger to the requester at the instant it preempts,
    # and the requester may still not enter until the victim has
    # physically cleared the access edge. Without this the victim, which
    # is standing right next to the charger it just gave up, simply
    # re-reserves it and the preemption achieves nothing.
    promised_to: str | None = None
    promised_at: Fraction | None = None


@dataclass
class ChargeRecord:
    """One robot's trip through the charging workflow."""

    robot_id: str
    reason: str  # CHARGE_DUE or CHARGE_NEEDED
    requested_at: Fraction
    request_cell: tuple
    request_soc: Fraction
    state: ChargeState = ChargeState.REQUEST
    kind: str = ""  # charger, holding or stage
    zone_id: str | None = None
    target_cell: tuple | None = None
    route_cost_m: float | None = None
    path: tuple = ()
    departed_at: Fraction | None = None
    arrived_at: Fraction | None = None
    arrival_soc: Fraction | None = None

    # -- charging (B4). Only a charger trip fills these in. --------
    charge_started_at: Fraction | None = None
    charge_duration_s: Fraction | None = None
    charge_completed_at: Fraction | None = None
    charge_gained: Fraction | None = None
    # The aisle cell the robot reverses into, i.e. the cell it entered
    # the terminal from.
    access_cell: tuple | None = None
    left_charger_at: Fraction | None = None
    rejoined_at: Fraction | None = None
    rejoin_soc: Fraction | None = None

    # -- preemption (B5). Only a charger trip cut short fills these. ---
    #
    # ``charge_duration_s`` stays the duration that was scheduled when
    # the session began; ``charge_elapsed_s`` is what really elapsed
    # before the requester took the slot, and is the interval booked.
    preempted_at: Fraction | None = None
    preempted_by: str | None = None
    charge_elapsed_s: Fraction | None = None
    # Recorded, never branched on: the frozen rule always selects the
    # maximum-SOC victim (docs/DECISIONS.md, "Charger Saturation and
    # Preemption").
    victim_soc_ge_80: bool | None = None
    # Set once the preempted robot has cleared the charger: did it have
    # the energy to rejoin the fleet, or did it go to holding?
    preempt_rejoined: bool | None = None

    # -- promotion (B5) ------------------------------------------------
    #
    # A holding or Stage Zone trip ends when its robot is granted a
    # charger. The charger trip that follows is a separate record, so
    # each record still describes exactly one terminal. A holding robot
    # first reverses off its cell (left_holding_at); a Stage Zone robot
    # drives off in the instant it is promoted.
    promoted_at: Fraction | None = None
    left_holding_at: Fraction | None = None
    # The first instant a charger was FREE but none was within this
    # waiting robot's SOC. Reporting only.
    charger_out_of_reach_at: Fraction | None = None
    # On the charger trip: the holding zone or Stage Zone the robot was
    # promoted from, and which of the two it was.
    promoted_from: str | None = None
    promoted_from_kind: str | None = None


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
        """FREE slots nobody is standing on or promised, in zone_id order."""

        return [
            zone_id
            for zone_id, slot in self.slots.items()
            if slot.state is ResourceState.FREE
            and slot.promised_to is None
            and slot.cell not in blocked_cells
        ]

    def promised(self, robot_id: str) -> str | None:
        """The slot promised to this robot for its next occupancy, if any."""

        for zone_id, slot in self.slots.items():
            if slot.promised_to == robot_id:
                return zone_id

        return None

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

    def promise(self, zone_id, robot_id, now) -> None:
        """Assign an occupied slot to its next holder, before it is free.

        The slot keeps its current holder and its current state; it is
        simply no longer on offer to anybody else.
        """

        slot = self.slots.get(zone_id)

        if slot is None:
            raise ResourceError(f"{zone_id} is not a {self.kind} slot")

        if slot.promised_to is not None:
            raise ResourceError(
                f"{self.kind} {zone_id} is already promised to {slot.promised_to}"
            )

        if slot.robot_id == robot_id:
            raise ResourceError(
                f"{robot_id} cannot be promised {self.kind} {zone_id}, "
                f"which it is holding now"
            )

        slot.promised_to = robot_id
        slot.promised_at = now

    def cancel_promise(self, zone_id) -> None:
        """Put a promised slot back on offer."""

        slot = self.slots[zone_id]

        slot.promised_to = None
        slot.promised_at = None

    def reserve(self, zone_id, robot_id, now, route_cost_m=None) -> None:
        """FREE -> RESERVED, before the robot starts to move."""

        if robot_id in self._by_robot:
            raise ResourceError(
                f"{robot_id} already holds {self.kind} {self._by_robot[robot_id]}"
            )

        promised = self.slots[zone_id].promised_to if zone_id in self.slots else None

        if promised is not None and promised != robot_id:
            raise ResourceError(
                f"{robot_id} cannot reserve {self.kind} {zone_id}: it is "
                f"promised to {promised}"
            )

        slot = self._slot(zone_id, robot_id, ResourceState.FREE)

        # Honoured, so it is no longer outstanding. A robot that takes a
        # different slot has its own promise cancelled by the caller.
        slot.promised_to = None
        slot.promised_at = None

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
                f"{robot_id} is at {cell}, not on {self.kind} {zone_id} at {slot.cell}"
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

    def assign(
        self, robot_cells: dict, *, now, blocked_cells=frozenset(), cache=None
    ) -> dict:
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
