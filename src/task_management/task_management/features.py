"""Per-candidate allocation features and the proposal's cost model.

For a task, each idle robot becomes a :class:`Candidate` carrying the grid-aware
quantities the proposal defines for a robot-task pair (i, j):

    D_ij  distance to pickup            (A* path length, metres)
    T_ij  travel time to pickup         (physics trapezoidal profile, seconds)
    G_ij  congestion cost               (sum of congestion along the A* path)
    E_ij  energy of the whole task      (physics model, percent of battery)

battery feasibility follows the proposal:  b_i - E_ij >= b_min.

The weighted cost is the proposal's allocation cost (page 8):

    C_ij = l1*T_ij + l2*D_ij + l3*G_ij + l4*E_ij + l5*W_i - l6*q_j

Distances/times/energies all come from the shared WorldGrid + RobotModel, so every
strategy sees exactly what the simulator will actually do.

Multi-shelf warehouse orders use the second half of this module:
:func:`order_route_cost` computes C_static(r, o), the static grid route
estimate a batch allocator ranks robot-order pairs by. It calls the same
``mrta_core.warehouse_routing`` planner the executor calls, so the
prediction and the executed mission come from one implementation.
"""

from dataclasses import dataclass
from itertools import permutations
from math import inf

from mrta_core.grid import Cell, astar_path
from mrta_core.kinematics import RobotModel, path_energy_percent, path_time
from mrta_core.warehouse_routing import (
    MissionPlan,
    RouteCache,
    plan_from_cell,
)
from mrta_core.world import WorldGrid


@dataclass(frozen=True)
class Candidate:
    robot_id: int
    reachable: bool
    distance: float          # D_ij, metres to pickup
    travel_time: float       # T_ij, seconds to pickup
    round_trip: float        # metres, robot -> pickup -> drop
    proc_time: float         # seconds, whole-task processing time
    congestion: float        # G_ij, congestion cost along the path
    energy_pct: float        # E_ij, percent of battery for the whole task
    battery: float           # percent, 0..100
    workload: int            # W_i, in-flight tasks
    cumulative_load: float   # seconds, manager-tracked cumulative proc time
    battery_feasible: bool   # b_i - E_ij >= b_min
    priority: int            # q_j, task priority (constant across candidates)


@dataclass(frozen=True)
class CostWeights:
    """Lambda weights for C_ij. Defaults balance the term magnitudes to O(1);
    calibrate against logged data in the evaluation phase."""

    time: float = 0.05        # l1, per second
    distance: float = 0.03    # l2, per metre
    congestion: float = 1.0   # l3
    energy: float = 0.10      # l4, per percent
    workload: float = 1.0     # l5, per in-flight task
    priority: float = 1.0     # l6, per priority level (subtracted)


def task_cost(c: Candidate, w: CostWeights = CostWeights()) -> float:
    """C_ij for a single robot-task pair."""
    return (
        w.time * c.travel_time
        + w.distance * c.distance
        + w.congestion * c.congestion
        + w.energy * c.energy_pct
        + w.workload * c.workload
        - w.priority * c.priority
    )


def build_candidates(
    world_grid: WorldGrid,
    congestion: dict[Cell, float],
    robots: list[tuple[int, Cell, float, int, float]],
    shelf_cell: Cell,
    drop_cell: Cell,
    model: RobotModel,
    priority: int = 1,
    min_battery_after: float = 20.0,
) -> list[Candidate]:
    """Build a Candidate per robot.

    ``robots`` is ``(robot_id, cell, battery, workload, cumulative_load)`` each.
    """
    grid = world_grid.grid_map(congestion)
    res = world_grid.resolution
    candidates: list[Candidate] = []

    for robot_id, cell, battery, workload, cumulative_load in robots:
        leg1 = astar_path(grid, cell, shelf_cell)
        if not leg1.found:
            candidates.append(Candidate(
                robot_id=robot_id, reachable=False, distance=inf, travel_time=inf,
                round_trip=inf, proc_time=inf, congestion=inf, energy_pct=inf,
                battery=battery, workload=workload, cumulative_load=cumulative_load,
                battery_feasible=False, priority=priority))
            continue

        leg2 = astar_path(grid, shelf_cell, drop_cell)
        full_path = leg1.path + (leg2.path[1:] if leg2.found else [])

        distance = leg1.distance * res
        travel_time = path_time(leg1.path, res, model)
        round_trip = (leg1.distance + (leg2.distance if leg2.found else 0.0)) * res
        proc_time = path_time(full_path, res, model)
        congestion = leg1.congestion_cost + (leg2.congestion_cost if leg2.found else 0.0)
        energy_pct = path_energy_percent(full_path, res, model)
        battery_feasible = (battery - energy_pct) >= min_battery_after

        candidates.append(Candidate(
            robot_id=robot_id, reachable=True, distance=distance, travel_time=travel_time,
            round_trip=round_trip, proc_time=proc_time, congestion=congestion,
            energy_pct=energy_pct, battery=battery, workload=workload,
            cumulative_load=cumulative_load, battery_feasible=battery_feasible,
            priority=priority))

    return candidates


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
