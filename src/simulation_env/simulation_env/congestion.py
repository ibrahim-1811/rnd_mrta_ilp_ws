"""M1: decision-time congestion and route-overlap features (diagnostic).

Approved for investigation 2026-10-27 (DECISIONS.md, "Research
Direction After the Audit"); not part of the dataset schema. A recorded
run can write them for every candidate pair at every decision beside
its tables, and the M1-screened ones feed the Tier B ILP predicates: a
rule reads them through pair_features, the code that recorded its
training data. No other strategy is offered them.

Everything is a function of the state S_t at the decision instant t:

    p_k          robot k's cell at t
    sigma_k      k's remaining planned cells at t: the rest of its
                 current segment, then every later segment of the
                 mission plan it was given at or before t
    theta_k(j)   estimated time k reaches sigma_k[j]: j cell moves at
                 delta_k = resolution / speed_k, plus the configured
                 service of each stop it passes on the way
    pi           the candidate route (C_static's plan), cells c_0..c_n
    tau(i)       the same estimate for the candidate robot r along pi

No realized waiting, no future position and no later decision enters
any of them: a plan is what was issued before t, its timing is the
uncongested estimate, and the stops are the ones already in the plan.
With K = robots other than r, U = union of sigma_k over K, P = {p_k}:

    route_cells          n + 1
    overlap_cells        |{i : c_i in U}|
    overlap_ratio        overlap_cells / (n + 1)
    occupied_now         |{i : c_i in P}|
    st_conflicts         |{(i, k, j) : c_i = sigma_k[j],
                                       |tau(i) - theta_k(j)| <= h}|
                         h = 2 delta_r (two cell moves)
    st_conflict_robots   |{k : some (i, k, j) above}|
    st_conflicts_30s     st_conflicts with tau(i) <= 30 s
    dropoff_load         |{k : o's drop-off is a remaining stop of k}|
    shelf_load           sum over o's shelves of |{k : shelf is a
                         remaining stop of k}|
    local_density        |{k : |p_k - p_r|_1 <= 5}|

These are estimates, not predictions of waiting: a robot k that is
itself waiting will be later than theta_k says. That is the point of
testing them against the residual.
"""

from collections import defaultdict
from fractions import Fraction

from mrta_core.warehouse_map import ZoneType


CONFLICT_WINDOW_MOVES = 2
NEAR_TERM_S = 30.0
DENSITY_RADIUS = 5

FEATURE_COLUMNS = (
    "route_cells",
    "overlap_cells",
    "overlap_ratio",
    "occupied_now",
    "st_conflicts",
    "st_conflict_robots",
    "st_conflicts_30s",
    "dropoff_load",
    "shelf_load",
    "local_density",
)


def _service_s(warehouse, service_seconds, zone_id) -> float:
    zone_type = warehouse.zone_by_id(zone_id).zone_type

    if zone_type in (ZoneType.SHELF, ZoneType.DROPOFF):
        return float(service_seconds.get(zone_type, 0))

    return 0.0


def timed_cells(first_cells, segments, delta, warehouse, service_seconds, first_stop=None):
    """[(cell, time)] along a path, then along later segments.

    ``first_cells`` starts at the robot's cell (time 0) and, if
    ``first_stop`` is given, ends on that service stop; each later
    segment starts where the previous one ended. A segment that ends on
    a service stop (it has an exit cell) adds that stop's service time
    before the next cell. Returns the timed cells and the service stops.
    """

    timed, stops = [], []
    clock = 0.0

    for index, cell in enumerate(first_cells):
        if index:
            clock += delta
        timed.append((cell, clock))

    if first_stop is not None:
        stops.append(first_stop)
        clock += _service_s(warehouse, service_seconds, first_stop)

    for segment in segments:
        for cell in segment.path[1:]:
            clock += delta
            timed.append((cell, clock))

        if segment.exit_cell is not None:
            stops.append(segment.to_zone)
            clock += _service_s(warehouse, service_seconds, segment.to_zone)

    return timed, stops


def fleet_state(engine, warehouse, exclude=frozenset()):
    """S_t for the features: positions, remaining timed plans, stops."""

    service = engine.service_seconds
    positions, plans, stops = {}, {}, {}

    for robot_id, robot in engine.robots.items():
        positions[robot_id] = robot.cell

        if robot_id in exclude:
            continue

        assignment = engine.assignments.get(robot_id)
        delta = float(engine.resolution / Fraction(robot.speed_mps).limit_denominator(10**6))

        if assignment is None or assignment.segment_index >= len(assignment.plan.segments):
            plans[robot_id], stops[robot_id] = [(robot.cell, 0.0)], []
            continue

        current = robot.path[robot.path_index:] if robot.path else [robot.cell]
        segments = assignment.plan.segments
        here = segments[assignment.segment_index]

        timed, remaining_stops = timed_cells(
            current,
            segments[assignment.segment_index + 1:],
            delta,
            warehouse,
            service,
            # The current segment's own stop, if it ends on one.
            first_stop=here.to_zone if here.exit_cell is not None else None,
        )

        plans[robot_id] = timed
        stops[robot_id] = remaining_stops

    occupancy = defaultdict(list)
    for robot_id, timed in plans.items():
        for cell, when in timed:
            occupancy[cell].append((robot_id, when))

    return {"positions": positions, "plans": plans, "stops": stops, "occupancy": occupancy}


def candidate_features(state, robot_id, order, route, delta, warehouse, service_seconds) -> dict:
    """The features of one candidate pair against the fleet state."""

    timed, _ = timed_cells(
        [route.plan.path[0]], route.plan.segments, delta, warehouse, service_seconds
    )
    window = CONFLICT_WINDOW_MOVES * delta
    others = {k for k in state["positions"] if k != robot_id}
    now_cells = {state["positions"][k] for k in others}

    overlap = occupied = conflicts = near = 0
    conflict_robots = set()

    for cell, when in timed:
        users = [(k, t) for k, t in state["occupancy"].get(cell, ()) if k != robot_id]

        if users:
            overlap += 1

        if cell in now_cells:
            occupied += 1

        for k, t in users:
            if abs(when - t) <= window:
                conflicts += 1
                conflict_robots.add(k)
                if when <= NEAR_TERM_S:
                    near += 1

    stops = {k: set(v) for k, v in state["stops"].items() if k != robot_id}
    origin = state["positions"][robot_id]

    return {
        "route_cells": len(timed),
        "overlap_cells": overlap,
        "overlap_ratio": overlap / len(timed),
        "occupied_now": occupied,
        "st_conflicts": conflicts,
        "st_conflict_robots": len(conflict_robots),
        "st_conflicts_30s": near,
        "dropoff_load": sum(order.dropoff_id in s for s in stops.values()),
        "shelf_load": sum(shelf in s for shelf in order.shelf_ids for s in stops.values()),
        "local_density": sum(
            abs(state["positions"][k][0] - origin[0]) + abs(state["positions"][k][1] - origin[1])
            <= DENSITY_RADIUS
            for k in others
        ),
    }


def pair_features(engine, warehouse, robots, orders, candidates) -> list[dict]:
    """The features of each candidate with a route, at this decision.

    ``robots`` are the ones on offer: their plans are not in S_t.
    """

    state = fleet_state(engine, warehouse, exclude=frozenset(r for r, _ in robots))
    by_id = {order.order_id: order for order in orders}
    rows = []

    for candidate in candidates:
        if candidate.route.plan is None:
            continue

        robot = engine.robots[candidate.robot_id]
        delta = float(engine.resolution / Fraction(robot.speed_mps).limit_denominator(10**6))

        rows.append(
            {
                "robot_id": candidate.robot_id,
                "order_id": candidate.order_id,
                **candidate_features(
                    state,
                    candidate.robot_id,
                    by_id[candidate.order_id],
                    candidate.route,
                    delta,
                    warehouse,
                    engine.service_seconds,
                ),
            }
        )

    return rows


class FeatureObserver:
    """Wraps a strategy; records features at each decision; decides nothing.

    ``assign`` returns exactly what the wrapped strategy returns. Routes
    are scored without the battery context, so no eligibility counter
    the run reports is touched; the route cache only memoises a pure
    function. Every other attribute is the wrapped strategy's.
    """

    def __init__(self, inner, executor=None):
        self.inner = inner
        self.executor = executor
        self.rows = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def assign(self, warehouse, robots, orders, fifo_position, cache=None, battery=None, timing=None):
        executor = self.executor
        engine = executor.engine
        decision_id = executor.result.decisions - 1

        self.rows.extend(
            {"decision_id": decision_id, **row}
            for row in pair_features(
                engine,
                warehouse,
                robots,
                orders,
                self.inner.score(warehouse, robots, orders, fifo_position, cache),
            )
        )

        return self.inner.assign(
            warehouse, robots, orders, fifo_position, cache, battery=battery, timing=timing
        )
