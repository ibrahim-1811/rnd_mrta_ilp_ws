"""Batch MRTA allocation strategies for warehouse orders.

FIFO determines which pending orders are eligible.

An allocation strategy determines only which available robot receives
which eligible order.

Traffic planning, collision avoidance and execution are outside this
module and must never change robot-order ownership.
"""

from dataclasses import dataclass
from hashlib import sha256
from json import dumps
from pathlib import Path
from random import Random

import numpy as np
import scipy
from scipy.optimize import Bounds, LinearConstraint, linear_sum_assignment, milp
from scipy.sparse import coo_matrix

from mrta_core.battery import EnergyRequirement
from task_management.features import (
    DecisionTiming,
    OrderCandidate,
    OrderRoute,
    build_order_candidates,
    manhattan_order_cost,
)


# ======================================================================
# Batch allocation: available robots x eligible FIFO orders
# ======================================================================


@dataclass(frozen=True)
class OrderAssignment:
    """One robot-order pairing, with the route the allocator predicted."""

    robot_id: str
    order_id: str
    cost: float  # C_static(r, o), metres
    route: OrderRoute  # includes the chosen shelf visiting order

    # Decision-time battery features of the chosen pair, if any.
    energy: EnergyRequirement | None = None

    @property
    def zone_sequence(self) -> tuple[str, ...]:
        """Zones the executor must drive, in order."""
        return self.route.zone_sequence


def greedy_pairing(ordered, fifo_position) -> list[OrderAssignment]:
    """Take pairs in the order given, skipping what is already taken.

    Every strategy in this module is this same sweep over a different
    ordering. Keeping the sweep in one place is what makes the
    comparison controlled: two strategies then differ by their ranking
    and by nothing else -- not by how they resolve a conflict, not by
    how many pairs they emit, not by what they do when one side runs
    out.

    ``ordered`` must already be filtered to eligible candidates and
    sorted (or shuffled) by the strategy's own policy. The sweep itself
    never reorders it, so a strategy's ranking is fully visible in the
    strategy.
    """

    taken_robots: set[str] = set()
    taken_orders: set[str] = set()

    assignments: list[OrderAssignment] = []

    for candidate in ordered:
        if candidate.robot_id in taken_robots:
            continue

        if candidate.order_id in taken_orders:
            continue

        taken_robots.add(candidate.robot_id)
        taken_orders.add(candidate.order_id)

        assignments.append(
            OrderAssignment(
                robot_id=candidate.robot_id,
                order_id=candidate.order_id,
                # The cost carried forward is always C_static, whatever
                # the strategy ranked on. It is the predicted cost of
                # the route that will actually be driven, and the
                # dataset compares it against the realized distance.
                cost=candidate.cost,
                route=candidate.route,
                energy=candidate.energy,
            )
        )

    # Report in FIFO order: the oldest order first, whichever robot
    # happened to win it.
    assignments.sort(key=lambda a: fifo_position[a.order_id])

    return assignments


BATCH_STRATEGIES: dict[str, type["BatchAllocationStrategy"]] = {}


def register_batch(cls):
    BATCH_STRATEGIES[cls.name] = cls
    return cls


def get_batch_strategy(name: str, **kwargs) -> "BatchAllocationStrategy":
    key = name.lower()
    if key not in BATCH_STRATEGIES:
        raise KeyError(
            f"unknown batch strategy {name!r}; known: {sorted(BATCH_STRATEGIES)}"
        )
    return BATCH_STRATEGIES[key](**kwargs)


class AllocationSolverError(RuntimeError):
    """An optimising strategy's solver returned no decision.

    A failure of the strategy, not of the warehouse, so a run that ends
    on one is reported as a solver failure and never as a traffic stall
    (docs/DECISIONS.md, "Run Failure Classification").
    """


def artifact_record(role: str, path) -> dict:
    """Identity of one file a strategy reads, such as a learned rule set.

    The content hash is what identifies it; the path is for the reader.
    A missing file is an error rather than an empty hash: a strategy
    that runs must be able to say exactly what it ran.
    """

    path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(f"strategy artifact {role!r}: no such file {path}")

    return {
        "role": role,
        "path": str(path),
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }


def identity_sha256(identity: dict) -> str:
    """Short hash of a strategy identity, for manifests and summaries."""

    return sha256(dumps(identity, sort_keys=True, default=str).encode()).hexdigest()[:16]


class BatchAllocationStrategy:
    """Pairs available robots with eligible orders.

    One call is one allocation decision. The caller supplies:

      warehouse       grid + zone state, for grid-aware costing
      robots          (robot_id, cell) for every AVAILABLE robot
      orders          the candidate window: the FIFO prefix the
                      executor offers, sized by allocation.window
                      (W = fleet_size by default) and extended down
                      the queue when the prefix holds no assignable
                      pair at all. Sizing is infrastructure -- see
                      docs/DECISIONS.md, "Allocation Candidate Window"
                      -- and is identical for every strategy
      fifo_position   order_id -> FIFO index, for deterministic ties
      battery         a BatteryDecisionContext, or None
      timing          a DecisionTiming (robot speeds, service
                      durations), or None; strategies that estimate
                      time require it

    and gets back a list of :class:`OrderAssignment`. A strategy may
    return fewer assignments than robots (an unreachable order defers),
    but never more than one order per robot or one robot per order, and
    never an order outside ``orders``.

    Battery feasibility contract. With a ``battery`` context, every
    candidate from :meth:`score` carries its decision-time energy
    features, and a strategy may only return pairs whose
    ``candidate.eligible`` is true: reachable AND battery-eligible. The
    executor re-checks every returned pair independently and raises
    rather than execute an ineligible one. Among eligible pairs the
    strategy is free to choose however it likes.
    """

    name = "batch_base"

    def __init__(self, seed: int = 0):
        self.seed = seed
        self.reset()

    def reset(self):
        """Reset any per-trial internal state."""

    # -- identity (docs/DECISIONS.md, "Strategy Identity") ---------

    def parameters(self) -> dict:
        """Effective parameters that change this strategy's choices.

        The seed is not one of them: it is recorded per run, beside the
        strategy, because it also selects the workload.
        """

        return {}

    def execution(self) -> dict:
        """Execution environment its choices depend on, e.g. a solver."""

        return {}

    def artifacts(self) -> list[dict]:
        """Files it reads, as artifact_record() entries.

        A learned strategy lists its rule set here, so two runs of
        different rule sets can never be mistaken for one strategy.
        """

        return []

    def identity(self) -> dict:
        """Everything that defines this strategy's behaviour, for run.json."""

        return {
            "name": self.name,
            "implementation": f"{type(self).__module__}.{type(self).__qualname__}",
            "parameters": self.parameters(),
            "execution": self.execution(),
            "artifacts": self.artifacts(),
        }

    def score(
        self,
        warehouse,
        robots,
        orders,
        fifo_position,
        cache=None,
        battery=None,
    ) -> list[OrderCandidate]:
        """Cost every (robot, order) pair. Shared by every strategy."""
        return build_order_candidates(
            warehouse, robots, orders, fifo_position, cache, battery
        )

    def assign(
        self,
        warehouse,
        robots,
        orders,
        fifo_position,
        cache=None,
        battery=None,
        timing=None,
    ) -> list[OrderAssignment]:
        raise NotImplementedError

    def explain(self, assignments: list[OrderAssignment]) -> str:
        if not assignments:
            return f"{self.name}: nothing assignable -> defer"
        return f"{self.name}: {len(assignments)} assignment(s)"


@register_batch
class NearestAStarAllocator(BatchAllocationStrategy):
    """Greedy minimum static-A*-cost pairing.

    C_static(r, o) is the shortest static grid route taking robot r
    from where it stands, through every shelf of order o in the best
    visiting order, to o's drop-off. Grid distance through the aisle
    network -- never Euclidean, never a straight line through racking.

    Pairing is greedy and deterministic:

      1. take the cheapest remaining (robot, order) pair
      2. assign it
      3. drop that robot and that order
      4. repeat until one side runs out

    Greedy is not optimal -- the Hungarian assignment would be, and is
    a later milestone. It is stated here as the baseline it is.

    Ties break on (cost, order FIFO position, robot_id), so an equal
    cost always resolves to the older order, and then to the
    lower-numbered robot. Two runs of the same workload therefore
    produce byte-identical assignments.

    Battery eligibility is a hard mask applied before the sweep: an
    ineligible pair is simply not a candidate, however cheap. Among
    eligible pairs the rule above is unchanged.
    """

    name = "nearest_astar"

    def assign(
        self,
        warehouse,
        robots,
        orders,
        fifo_position,
        cache=None,
        battery=None,
        timing=None,
    ):

        eligible = [
            candidate
            for candidate in self.score(
                warehouse, robots, orders, fifo_position, cache, battery
            )
            if candidate.eligible
        ]

        # One total order over every pair, so the greedy sweep is fully
        # determined by the data and never by dict iteration.
        eligible.sort(key=lambda c: (c.cost, c.fifo_position, c.robot_id))

        return greedy_pairing(eligible, fifo_position)

    def explain(self, assignments):
        if not assignments:
            return f"{self.name}: no reachable robot-order pair -> defer"
        return ", ".join(
            f"{a.robot_id}->{a.order_id} ({a.cost:.1f} m)" for a in assignments
        )


@register_batch
class NearestManhattanAllocator(BatchAllocationStrategy):
    """Greedy pairing on Manhattan distance. Deliberately naive.

    The experimental control for grid-awareness. It ranks pairs by the
    Manhattan chain robot -> shelves -> drop-off, which ignores racking,
    walls and lane direction entirely, and then drives the same directed
    A* route every other strategy drives.

    That split is the whole point and it is not negotiable:

        Manhattan decides WHICH robot takes which order
        directed A* decides HOW the robot gets there

    So this strategy changes allocation and nothing else. Route, traffic,
    battery drain, service and speed are identical to nearest_astar's,
    which is what makes an outcome difference attributable to the
    allocation decision rather than to the execution.

    It should lose. In a one-way warehouse d(a, b) != d(b, a) for 90.9%
    of shelf pairs, so Manhattan is not merely imprecise here, it is
    systematically wrong in a direction a straight-line metric cannot
    see. Measuring how much it loses by is the experiment.

    Eligibility is untouched: the battery mask and reachability are
    decided by directed A*, and Manhattan is never consulted for them.
    A pair this strategy likes but the infrastructure refuses is simply
    not a candidate.

    Ties break on (manhattan, C_static, order FIFO position, robot_id).
    C_static sits in the tie-break rather than the key so that two pairs
    the naive metric cannot separate still resolve deterministically,
    without letting the real cost drive the ranking.
    """

    name = "nearest_manhattan"

    def assign(
        self,
        warehouse,
        robots,
        orders,
        fifo_position,
        cache=None,
        battery=None,
        timing=None,
    ):

        by_id = {order.order_id: order for order in orders}
        cell_of = dict(robots)

        eligible = [
            candidate
            for candidate in self.score(
                warehouse, robots, orders, fifo_position, cache, battery
            )
            if candidate.eligible
        ]

        def key(candidate):
            order = by_id[candidate.order_id]

            return (
                manhattan_order_cost(
                    warehouse,
                    cell_of[candidate.robot_id],
                    order.shelf_ids,
                    order.dropoff_id,
                ),
                candidate.cost,
                candidate.fifo_position,
                candidate.robot_id,
            )

        eligible.sort(key=key)

        return greedy_pairing(eligible, fifo_position)

    def explain(self, assignments):
        if not assignments:
            return f"{self.name}: no eligible robot-order pair -> defer"

        return ", ".join(
            f"{a.robot_id}->{a.order_id} (A* {a.cost:.1f} m, ranked by Manhattan)"
            for a in assignments
        )


@register_batch
class RandomAllocator(BatchAllocationStrategy):
    """Uniformly random pairing among eligible pairs.

    The lower bound. It answers the question every allocation result has
    to answer first: is this strategy better than no policy at all?

    Random here means random *allocation*, not random behaviour. It is
    still bound by every constraint the others are: only eligible pairs,
    at most one order per robot, the same candidate window, the same
    routes, the same traffic and battery infrastructure. The only thing
    it discards is the ranking.

    Determinism is a requirement, not a contradiction. The stream is
    seeded, and the candidates are sorted into a canonical order before
    they are shuffled, so the result never depends on dict iteration
    order. The same seed and the same scenario produce byte-identical
    assignments; ``reset()`` restores the stream so a re-run of the same
    strategy object repeats exactly.
    """

    name = "random"

    def reset(self):
        # Seeded here rather than in __init__ so that reset() genuinely
        # resets: the base class calls it once at construction.
        self.rng = Random(self.seed)

    def assign(
        self,
        warehouse,
        robots,
        orders,
        fifo_position,
        cache=None,
        battery=None,
        timing=None,
    ):

        eligible = [
            candidate
            for candidate in self.score(
                warehouse, robots, orders, fifo_position, cache, battery
            )
            if candidate.eligible
        ]

        # Canonical order first. Shuffling a list whose order came from
        # dict iteration would make the run unreproducible while still
        # looking seeded.
        eligible.sort(key=lambda c: (c.robot_id, c.order_id))

        self.rng.shuffle(eligible)

        return greedy_pairing(eligible, fifo_position)

    def explain(self, assignments):
        if not assignments:
            return f"{self.name}: no eligible robot-order pair -> defer"

        return ", ".join(
            f"{a.robot_id}->{a.order_id} (random, A* {a.cost:.1f} m)"
            for a in assignments
        )


# ======================================================================
# Phase 7: the remaining conventional strategies
# ======================================================================
#
# All five see exactly what the first three see -- the same candidate
# window, the same C_static routes, the same battery mask -- plus the
# decision timing (robot speeds, service durations) the executor builds
# for every strategy. Each one returns pairs chosen from the eligible
# candidates and nothing else; the route driven is always the
# candidate's directed A* route and the cost carried forward is always
# C_static.


def _eligible(strategy, warehouse, robots, orders, fifo_position, cache, battery):
    return [
        candidate
        for candidate in strategy.score(
            warehouse, robots, orders, fifo_position, cache, battery
        )
        if candidate.eligible
    ]


def _require_timing(name: str, timing: DecisionTiming | None) -> DecisionTiming:
    if timing is None:
        raise ValueError(
            f"{name} estimates mission time and needs the decision timing "
            "(robot speeds, service durations); the executor supplies it"
        )

    return timing


def _canonical(candidates):
    """Robots and orders of the candidates, in a fixed order.

    Robots by id, orders by FIFO position: the matrix an optimizer sees
    never depends on dict or set iteration order.
    """

    robots = sorted({c.robot_id for c in candidates})
    position = {c.order_id: c.fifo_position for c in candidates}
    orders = sorted(position, key=position.get)

    return robots, orders


def earliest_finish_pairing(candidates, timing, fifo_position):
    """Greedy on estimated finish time: travel + service."""

    ranked = sorted(
        candidates,
        key=lambda c: (timing.finish_s(c), c.cost, c.fifo_position, c.robot_id),
    )

    return greedy_pairing(ranked, fifo_position)


@register_batch
class EarliestFinishAllocator(BatchAllocationStrategy):
    """Greedy minimum estimated completion time.

    For each eligible pair, the time from now until the order would be
    finished, using only what is known at the decision:

        directed A* route length (C_static) / the robot's speed
      + configured shelf service x the number of shelf visits
      + configured drop-off service

    Every robot offered to a decision is free now, so they all start
    now. Traffic waiting is a realized outcome and is never included
    (docs/DECISIONS.md, "Decision-Time Features").

    Pairing is the shared greedy sweep, ranked by that estimate; ties on
    (C_static, FIFO position, robot_id). It differs from nearest_astar
    exactly where service time changes the order: an order with more
    shelves takes longer to finish at the same route length, and robots
    of different speeds finish the same route at different times. With
    equal speeds and short services the two rank alike most of the
    time; how often they differ is measured, not assumed.
    """

    name = "earliest_finish"

    def assign(
        self,
        warehouse,
        robots,
        orders,
        fifo_position,
        cache=None,
        battery=None,
        timing=None,
    ):
        timing = _require_timing(self.name, timing)
        candidates = _eligible(
            self, warehouse, robots, orders, fifo_position, cache, battery
        )

        return earliest_finish_pairing(candidates, timing, fifo_position)

    def explain(self, assignments):
        if not assignments:
            return f"{self.name}: no eligible robot-order pair -> defer"

        return ", ".join(
            f"{a.robot_id}->{a.order_id} (A* {a.cost:.1f} m, earliest finish)"
            for a in assignments
        )


# Criterion weights of multi_criteria: untuned heuristic weights, fixed
# by the researcher 2026-10-27 and never tuned on evaluation workloads
# (DECISIONS.md, "Conventional strategy definitions"). Travel and
# service time dominate, the oldest orders are pulled forward, and
# battery headroom and ending near a charger break near-ties.
MULTI_CRITERIA_WEIGHTS = {
    "finish": 0.5,
    "fifo_age": 0.2,
    "energy_margin": 0.2,
    "charger_distance": 0.1,
}


def _normalised(values):
    """Min-max to [0, 1] within one decision; all equal -> all 0."""

    low, high = min(values), max(values)

    if high == low:
        return [0.0] * len(values)

    return [(v - low) / (high - low) for v in values]


def multi_criteria_scores(candidates, timing, weights) -> list[float]:
    """Weighted sum of decision-time criteria, lower is better.

    Each criterion is min-max normalised over this decision's eligible
    pairs, so the weights trade relative positions, not units:

        finish             estimated travel + service time   lower
        fifo_age           FIFO position of the order         lower
        energy_margin      SOC left above the requirement     higher
        charger_distance   directed A* leg to a charger
                           from where the mission ends        lower

    Without battery features the two battery criteria drop out and the
    remaining weights are rescaled to the same total.
    """

    columns = {
        "finish": [timing.finish_s(c) for c in candidates],
        "fifo_age": [float(c.fifo_position) for c in candidates],
    }

    if all(c.energy is not None for c in candidates):
        columns["energy_margin"] = [
            -float(c.energy.energy_margin_percent) for c in candidates
        ]
        columns["charger_distance"] = [
            float(c.energy.predicted_nearest_charger_distance_m) for c in candidates
        ]

    used = {name: weights.get(name, 0.0) for name in columns}
    total = sum(used.values())
    scale = sum(weights.values()) / total if total else 0.0

    scores = [0.0] * len(candidates)

    for name, column in columns.items():
        weight = used[name] * scale

        for index, value in enumerate(_normalised(column)):
            scores[index] += weight * value

    return scores


@register_batch
class MultiCriteriaGridAwareAllocator(BatchAllocationStrategy):
    """Greedy on a weighted score of grid-aware decision-time criteria.

    Where nearest_astar and earliest_finish rank on one number, this
    trades several the operator cares about: how soon the order is done
    (directed A* travel + service), how long the order has waited (FIFO
    age), how much battery the robot keeps, and whether the mission
    ends near a charger (directed A*). Every criterion is known at the
    decision; none is a realized outcome. Weights default to
    MULTI_CRITERIA_WEIGHTS and are a constructor argument.

    Pairing is the shared greedy sweep ranked by the score; ties on
    (estimated finish, C_static, FIFO position, robot_id).
    """

    name = "multi_criteria"

    def __init__(self, seed: int = 0, weights: dict | None = None):
        self.weights = dict(MULTI_CRITERIA_WEIGHTS if weights is None else weights)

        unknown = set(self.weights) - set(MULTI_CRITERIA_WEIGHTS)

        if unknown:
            raise ValueError(f"unknown multi_criteria weight(s): {sorted(unknown)}")

        super().__init__(seed)

    def parameters(self) -> dict:
        return {"weights": dict(sorted(self.weights.items()))}

    def assign(
        self,
        warehouse,
        robots,
        orders,
        fifo_position,
        cache=None,
        battery=None,
        timing=None,
    ):
        timing = _require_timing(self.name, timing)
        candidates = _eligible(
            self, warehouse, robots, orders, fifo_position, cache, battery
        )

        if not candidates:
            return []

        scores = multi_criteria_scores(candidates, timing, self.weights)
        ranked = [
            c
            for _, _, _, _, _, c in sorted(
                (s, timing.finish_s(c), c.cost, c.fifo_position, c.robot_id, c)
                for s, c in zip(scores, candidates)
            )
        ]

        return greedy_pairing(ranked, fifo_position)

    def explain(self, assignments):
        if not assignments:
            return f"{self.name}: no eligible robot-order pair -> defer"

        return ", ".join(
            f"{a.robot_id}->{a.order_id} (A* {a.cost:.1f} m, multi-criteria)"
            for a in assignments
        )


def auction_pairing(candidates, fifo_position):
    """Sequential single-item auction in FIFO order.

    Orders are put up one at a time, oldest first. Every robot still
    unassigned bids its marginal cost for the order -- the route length
    it would add to its plan, which, holding no other order, is
    C_static -- and the lowest bid wins (ties: lower robot_id). An order
    no remaining robot can take is passed over.
    """

    bids: dict[str, list[OrderCandidate]] = {}

    for candidate in candidates:
        bids.setdefault(candidate.order_id, []).append(candidate)

    taken: set[str] = set()
    won: list[OrderCandidate] = []

    for order_id in sorted(bids, key=lambda o: fifo_position[o]):
        open_bids = [c for c in bids[order_id] if c.robot_id not in taken]

        if not open_bids:
            continue

        best = min(open_bids, key=lambda c: (c.cost, c.robot_id))
        taken.add(best.robot_id)
        won.append(best)

    return greedy_pairing(won, fifo_position)


@register_batch
class AuctionAllocator(BatchAllocationStrategy):
    """Market-based allocation: a sequential single-item auction.

    Orders are auctioned one at a time in FIFO order, the way a
    market-based dispatcher announces tasks as they arrive (MURDOCH,
    Gerkey and Mataric 2002). Each free robot bids its marginal cost:
    the directed A* route length the order would add to its plan.

    Why FIFO announcement and not rounds. In the round-based variant
    every robot bids on every open order and the globally cheapest bid
    wins each round. A robot here holds at most one order per decision,
    so its marginal cost for any order is that order's C_static, and the
    round-based auction reduces exactly to nearest_astar's greedy sweep.
    The same behaviour under a second name is not a second strategy, so
    the auction announces orders in arrival order: the oldest order
    goes to its cheapest robot even when that robot is cheaper still
    for a younger order.
    """

    name = "auction"

    def assign(
        self,
        warehouse,
        robots,
        orders,
        fifo_position,
        cache=None,
        battery=None,
        timing=None,
    ):
        candidates = _eligible(
            self, warehouse, robots, orders, fifo_position, cache, battery
        )

        return auction_pairing(candidates, fifo_position)

    def explain(self, assignments):
        if not assignments:
            return f"{self.name}: no eligible robot-order pair -> defer"

        return ", ".join(
            f"{a.robot_id}->{a.order_id} (won at {a.cost:.1f} m)" for a in assignments
        )


def hungarian_pairing(candidates, fifo_position):
    """Maximum matching of minimum total C_static.

    First as many pairs as the eligibility mask allows, then, among all
    matchings of that size, the one with the smallest summed route
    length. Ineligible pairs carry a cost larger than any feasible
    matching can reach, so the solver never trades a pair away to save
    distance.
    """

    if not candidates:
        return []

    chosen = _min_total_matching(candidates, [c.cost for c in candidates])

    return greedy_pairing(chosen, fifo_position)


def _min_total_matching(candidates, weights) -> list[OrderCandidate]:
    """Maximum-cardinality matching of minimum summed weight."""

    robots, orders = _canonical(candidates)
    row = {r: i for i, r in enumerate(robots)}
    col = {o: j for j, o in enumerate(orders)}

    blocked = (max(weights) + 1.0) * (min(len(robots), len(orders)) + 1)

    matrix = np.full((len(robots), len(orders)), blocked)
    by_cell = {}

    for c, weight in zip(candidates, weights):
        matrix[row[c.robot_id], col[c.order_id]] = weight
        by_cell[row[c.robot_id], col[c.order_id]] = c

    rows, cols = linear_sum_assignment(matrix)

    return [by_cell[i, j] for i, j in zip(rows, cols) if (i, j) in by_cell]


@register_batch
class HungarianAllocator(BatchAllocationStrategy):
    """Optimal assignment: minimum total route length, per decision.

    The Hungarian method (Kuhn 1955; scipy's linear_sum_assignment)
    over the decision's eligible pairs: the matching with the most pairs
    the mask allows and, among those, the least summed C_static. Where
    nearest_astar is greedy and can lock a robot into a cheap pair that
    forces an expensive one elsewhere, this minimises the sum.

    It optimises one decision at a time, not the workload: the window,
    the free robots and the timing of decisions are infrastructure and
    the same for every strategy. When a decision offers one free robot,
    every minimum-cost rule picks the same pair, so its difference from
    nearest_astar shows only in decisions with several free robots.
    """

    name = "hungarian"

    def execution(self):
        return {
            "solver": "scipy.optimize.linear_sum_assignment",
            "scipy": scipy.__version__,
        }

    def assign(
        self,
        warehouse,
        robots,
        orders,
        fifo_position,
        cache=None,
        battery=None,
        timing=None,
    ):
        candidates = _eligible(
            self, warehouse, robots, orders, fifo_position, cache, battery
        )

        return hungarian_pairing(candidates, fifo_position)

    def explain(self, assignments):
        if not assignments:
            return f"{self.name}: no eligible robot-order pair -> defer"

        total = sum(a.cost for a in assignments)
        return f"{self.name}: {len(assignments)} pair(s), {total:.1f} m in total"


def milp_pairing(candidates, timing, fifo_position):
    """Minimum batch makespan, then minimum total finish time.

    x_e in {0, 1} for every eligible pair e, z >= 0 continuous:

        stage 1   minimise  z
                  s.t.      sum of x over each robot              <= 1
                            sum of x over each order              <= 1
                            sum of all x                          =  K
                            sum of finish_e * x_e over a robot    <= z
                            sum of finish_e * x_e over an order   <= z

        stage 2   minimise  sum of finish_e * x_e
                  s.t.      the same, and z <= z* (stage 1 optimum)

    A robot and an order each hold at most one pair, so each of the last
    two sums is the finish time of that robot's (or order's) one pair:
    z is the latest finish of the matching. Written per robot and per
    order rather than per pair, the program has the same optimum and a
    much tighter LP relaxation (100 x 100: 13 s instead of 98 s).

    K is the maximum matching size the eligibility mask allows, so the
    MILP pairs as many robots as the Hungarian method does. finish_e is
    DecisionTiming.finish_s: directed A* travel + service, nothing
    realized.

    Before solving, a pair that finishes later than the latest finish of
    one feasible K-matching (the minimum-total matching on finish times)
    is left out: it cannot be in an optimal solution of either stage,
    since z* is no larger than that matching's latest finish. This is
    exact, not a heuristic. Solved with HiGHS through scipy.optimize.milp.
    """

    if not candidates:
        return []

    feasible = _min_total_matching(candidates, [timing.finish_s(c) for c in candidates])
    size = len(feasible)
    bound = max(timing.finish_s(c) for c in feasible)
    tolerance = 1e-9 * max(1.0, bound)

    candidates = [c for c in candidates if timing.finish_s(c) <= bound + tolerance]

    robots, orders = _canonical(candidates)
    n = len(candidates)
    finish = np.array([timing.finish_s(c) for c in candidates])

    row = {r: i for i, r in enumerate(robots)}
    col = {o: len(robots) + j for j, o in enumerate(orders)}
    count = len(robots) + len(orders)

    entries, rows_of, cols_of = [], [], []

    for e, c in enumerate(candidates):
        # each robot once, each order once
        rows_of += [row[c.robot_id], col[c.order_id]]
        cols_of += [e, e]
        entries += [1.0, 1.0]

        # exactly K pairs
        rows_of.append(count)
        cols_of.append(e)
        entries.append(1.0)

        # the robot's and the order's finish time, against z
        rows_of += [count + 1 + row[c.robot_id], count + 1 + col[c.order_id]]
        cols_of += [e, e]
        entries += [float(finish[e]), float(finish[e])]

    for k in range(count):
        rows_of.append(count + 1 + k)
        cols_of.append(n)
        entries.append(-1.0)

    shape = (2 * count + 1, n + 1)
    matrix = coo_matrix((entries, (rows_of, cols_of)), shape=shape).tocsr()

    lower = np.concatenate([np.zeros(count), [size], np.full(count, -np.inf)])
    upper = np.concatenate([np.ones(count), [size], np.zeros(count)])
    constraints = LinearConstraint(matrix, lower, upper)
    integrality = np.concatenate([np.ones(n), [0]])

    makespan = milp(
        c=np.concatenate([np.zeros(n), [1.0]]),
        constraints=constraints,
        integrality=integrality,
        bounds=Bounds(np.zeros(n + 1), np.concatenate([np.ones(n), [np.inf]])),
    )

    if not makespan.success:
        raise AllocationSolverError(f"milp stage 1 failed: {makespan.message}")

    ceiling = makespan.x[n] + 1e-6 * max(1.0, makespan.x[n])

    total = milp(
        c=np.concatenate([finish, [0.0]]),
        constraints=constraints,
        integrality=integrality,
        bounds=Bounds(np.zeros(n + 1), np.concatenate([np.ones(n), [ceiling]])),
    )

    if not total.success:
        raise AllocationSolverError(f"milp stage 2 failed: {total.message}")

    chosen = [c for c, x in zip(candidates, total.x[:n]) if x > 0.5]

    return greedy_pairing(chosen, fifo_position)


@register_batch
class MILPAllocator(BatchAllocationStrategy):
    """Mixed-integer program: minimise the decision's makespan.

    A different objective from the Hungarian method, not a different
    solver for the same one. Hungarian minimises the *sum* of route
    lengths; this minimises the *latest* estimated finish among the
    pairs it makes (a bottleneck objective, which a linear assignment
    cannot express), and only then the total estimated finish time. It
    accepts a longer total to bring the slowest mission of the batch in
    earlier.

    Estimated finish is DecisionTiming.finish_s (directed A* travel +
    service), the same estimate earliest_finish ranks on. The
    formulation is in milp_pairing. With one free robot in a decision
    the bottleneck is that robot's own mission, so it picks what
    earliest_finish picks; the objectives part only with several free
    robots.

    Ties between equally optimal matchings are resolved by the solver
    (HiGHS), deterministic for one scipy version. The headless runner
    (pixi) and the ROS node (system Python) use different scipy builds,
    so an exact tie could resolve differently between the two.
    """

    name = "milp"

    def execution(self):
        # Ties between equally optimal matchings are the solver's, so
        # its build is part of what this strategy is (see above).
        return {"solver": "HiGHS via scipy.optimize.milp", "scipy": scipy.__version__}

    def assign(
        self,
        warehouse,
        robots,
        orders,
        fifo_position,
        cache=None,
        battery=None,
        timing=None,
    ):
        timing = _require_timing(self.name, timing)
        candidates = _eligible(
            self, warehouse, robots, orders, fifo_position, cache, battery
        )

        return milp_pairing(candidates, timing, fifo_position)

    def explain(self, assignments):
        if not assignments:
            return f"{self.name}: no eligible robot-order pair -> defer"

        return f"{self.name}: {len(assignments)} pair(s), batch makespan minimised"
