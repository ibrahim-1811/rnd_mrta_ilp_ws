"""Grid-aware features for batch warehouse MRTA.

This module contains prediction-time features only.

For each candidate robot-order pair, C_static(r, o) is the shortest
static A* route from the robot's current cell through every required
shelf and finally to the order's drop-off.

For orders with multiple shelves, every shelf permutation is evaluated.
With at most three shelves this requires at most 3! = 6 route candidates.

C_static is a geometric prediction. It contains no realized MAPF
waiting, reservation conflicts, execution outcomes or future leakage.
"""

from dataclasses import dataclass
from itertools import permutations
from math import inf

from mrta_core.grid import Cell
from mrta_core.warehouse_routing import (
    MissionPlan,
    RouteCache,
    plan_from_cell,
)


# ----------------------------------------------------------------------
# Multi-shelf order routing: C_static(r, o)
# ----------------------------------------------------------------------
#
# An allocator ranking (robot, order) pairs needs one number per pair.
# For a warehouse order that number is the length of the best static
# route through every shelf the order names, ending at its drop-off.
#
# Deliberately STATIC. It is a prediction made from grid geometry
# alone: no reservation table, no other robot's timing, no realized
# MAPF waiting, no execution outcome. Execution then runs the same
# route through the shared space-time MAPF layer and picks up real
# waiting. Keeping the two apart is what makes predicted-vs-realized a
# meaningful signal for Outcome-Aware ILP later; folding realized
# waiting back into the cost would destroy it.


@dataclass(frozen=True)
class OrderRoute:
    """The best static route for one robot-order pair."""

    shelf_sequence: tuple[str, ...]   # shelves in visiting order
    dropoff_id: str
    distance_m: float                 # C_static(r, o)
    plan: MissionPlan | None = None   # None when unreachable

    @property
    def reachable(self) -> bool:
        return self.plan is not None

    @property
    def zone_sequence(self) -> tuple[str, ...]:
        """Zones to visit, in order, excluding the robot's start."""
        return (*self.shelf_sequence, self.dropoff_id)


def order_route_cost(
    warehouse,
    start_cell: Cell,
    shelf_ids,
    dropoff_id: str,
    start_label: str = "robot",
    cache: RouteCache | None = None,
) -> OrderRoute:
    """C_static(r, o): the cheapest static route through all shelves.

    Evaluates every shelf visiting order and keeps the shortest. An
    order carries 1-3 shelves, so this is at most 3! = 6 candidate
    routes -- exhaustive, hence genuinely optimal for the sequence,
    with no heuristic ordering to justify.

    Route length is A* grid distance through the aisle network, never
    Euclidean, and includes the one-cell retreat after each station
    because the robot really drives it.

    Ties between permutations break on the shelf sequence itself, so
    the chosen route is deterministic.
    """

    shelves = tuple(shelf_ids)

    if not shelves:
        raise ValueError("an order must name at least one shelf")

    best: OrderRoute | None = None

    for candidate in sorted(set(permutations(shelves))):

        try:
            plan = plan_from_cell(
                warehouse,
                start_cell,
                [*candidate, dropoff_id],
                start_label=start_label,
                cache=cache,
            )

        except (RuntimeError, ValueError):
            # No route through this permutation; try the next.
            continue

        if best is not None and plan.total_distance_m >= best.distance_m:
            continue

        best = OrderRoute(
            shelf_sequence=candidate,
            dropoff_id=dropoff_id,
            distance_m=plan.total_distance_m,
            plan=plan,
        )

    if best is None:
        return OrderRoute(
            shelf_sequence=shelves,
            dropoff_id=dropoff_id,
            distance_m=inf,
            plan=None,
        )

    return best


@dataclass(frozen=True)
class OrderCandidate:
    """One (robot, order) pair scored by C_static, ready to rank."""

    robot_id: str
    order_id: str
    fifo_position: int      # FIFO index of the order, for tie-breaking
    cost: float             # C_static(r, o), metres
    route: OrderRoute

    @property
    def reachable(self) -> bool:
        return self.route.reachable


def build_order_candidates(
    warehouse,
    robots,
    orders,
    fifo_position,
    cache: RouteCache | None = None,
) -> list[OrderCandidate]:
    """Score every (robot, order) pair with C_static.

    ``robots`` is ``(robot_id, cell)`` pairs; ``orders`` is the
    eligible FIFO batch; ``fifo_position`` maps order_id -> FIFO index.
    """

    candidates: list[OrderCandidate] = []

    for robot_id, cell in robots:

        for order in orders:

            route = order_route_cost(
                warehouse,
                cell,
                order.shelf_ids,
                order.dropoff_id,
                start_label=robot_id,
                cache=cache,
            )

            candidates.append(
                OrderCandidate(
                    robot_id=robot_id,
                    order_id=order.order_id,
                    fifo_position=fifo_position[order.order_id],
                    cost=route.distance_m,
                    route=route,
                )
            )

    return candidates
