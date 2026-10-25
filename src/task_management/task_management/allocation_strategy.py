"""Batch MRTA allocation strategies for warehouse orders.

FIFO determines which pending orders are eligible.

An allocation strategy determines only which available robot receives
which eligible order.

Traffic planning, collision avoidance and execution are outside this
module and must never change robot-order ownership.
"""

from dataclasses import dataclass
from random import Random

from mrta_core.battery import EnergyRequirement
from task_management.features import (
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
