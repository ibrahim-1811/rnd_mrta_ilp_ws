"""Allocation strategies (online, one task at a time).

Every strategy takes the list of :class:`~task_management.features.Candidate` robots
for the current task and returns a chosen ``robot_id`` (or ``None`` to defer). They
share one interface and a name registry so the task manager can switch policy by a
single parameter, and the experiment runner can sweep all of them under identical seeds.

Families implemented here (12 online selectors):
  random, round_robin, nearest, least_busy, highest_battery, spt, lpt, edf,
  heuristic (alias: greedy), score, bid, auction

The composite selectors (score, bid, auction) minimise the proposal's cost
C_ij = l1 T + l2 D + l3 G + l4 E + l5 W - l6 q via task_management.features.task_cost.

Batch/optimization (hungarian, genetic) and symbolic/learned (prolog, ILP variants)
allocators are added in later passes.

A second, batch interface lives at the bottom of this module:
:class:`BatchAllocationStrategy` pairs a set of *available robots* with a set of
*eligible FIFO orders* in one decision, which is what a warehouse fleet actually
needs -- eighteen robots freeing up over time, each wanting the best of several
multi-shelf orders. The online selectors above answer a narrower question (one
task, which robot) and are kept unchanged.

Both interfaces share one rule: allocation decides only *which robot takes which
order*. Which orders are eligible at all is FIFO, owned by
``mrta_core.order_queue.OrderQueue``, and no strategy may reach past it.
"""

import random
from dataclasses import dataclass

from task_management.features import (
    Candidate,
    CostWeights,
    OrderCandidate,
    OrderRoute,
    build_order_candidates,
    task_cost,
)

STRATEGIES: dict[str, type["AllocationStrategy"]] = {}


def register(cls):
    STRATEGIES[cls.name] = cls
    return cls


def get_strategy(name: str, **kwargs) -> "AllocationStrategy":
    key = name.lower()
    if key not in STRATEGIES:
        raise KeyError(f'unknown strategy {name!r}; known: {sorted(STRATEGIES)}')
    return STRATEGIES[key](**kwargs)


def _reachable(candidates: list[Candidate]) -> list[Candidate]:
    return [c for c in candidates if c.reachable]


class AllocationStrategy:
    name = 'base'

    def __init__(self, seed: int = 0, cost_weights: CostWeights | None = None):
        self.seed = seed
        self.cost_weights = cost_weights or CostWeights()
        self.reset()

    def reset(self):
        """Reset any per-trial internal state (RNG cursor, round-robin pointer)."""

    def select(self, candidates: list[Candidate]) -> int | None:
        raise NotImplementedError

    def explain(self, candidates: list[Candidate], chosen: int | None) -> str:
        if chosen is None:
            return f'{self.name}: no eligible robot -> defer'
        return f'{self.name}: selected robot {chosen}'


@register
class RandomAllocator(AllocationStrategy):
    name = 'random'

    def reset(self):
        self._rng = random.Random(self.seed)

    def select(self, candidates):
        pool = _reachable(candidates)
        if not pool:
            return None
        return self._rng.choice(pool).robot_id


@register
class RoundRobinAllocator(AllocationStrategy):
    name = 'round_robin'

    def reset(self):
        self._last_id = -1

    def select(self, candidates):
        pool = sorted(_reachable(candidates), key=lambda c: c.robot_id)
        if not pool:
            return None
        nxt = next((c for c in pool if c.robot_id > self._last_id), pool[0])
        self._last_id = nxt.robot_id
        return nxt.robot_id


@register
class NearestRobotAllocator(AllocationStrategy):
    name = 'nearest'

    def select(self, candidates):
        pool = _reachable(candidates)
        if not pool:
            return None
        return min(pool, key=lambda c: (c.distance, c.robot_id)).robot_id

    def explain(self, candidates, chosen):
        if chosen is None:
            return f'{self.name}: no reachable robot -> defer'
        c = next(x for x in candidates if x.robot_id == chosen)
        return f'nearest: robot {chosen} closest at {c.distance:.1f} m'


@register
class LeastBusyAllocator(AllocationStrategy):
    name = 'least_busy'

    def select(self, candidates):
        pool = _reachable(candidates)
        if not pool:
            return None
        return min(pool, key=lambda c: (c.workload, c.distance, c.robot_id)).robot_id


@register
class HighestBatteryAllocator(AllocationStrategy):
    name = 'highest_battery'

    def select(self, candidates):
        pool = _reachable(candidates)
        if not pool:
            return None
        return min(pool, key=lambda c: (-c.battery, c.distance, c.robot_id)).robot_id


@register
class SPTAllocator(AllocationStrategy):
    """Shortest Processing Time: robot that finishes this task soonest."""

    name = 'spt'

    def select(self, candidates):
        pool = _reachable(candidates)
        if not pool:
            return None
        return min(pool, key=lambda c: (c.proc_time, c.robot_id)).robot_id


@register
class LPTAllocator(AllocationStrategy):
    """Online Longest Processing Time: assign to the least cumulatively loaded robot."""

    name = 'lpt'

    def select(self, candidates):
        pool = _reachable(candidates)
        if not pool:
            return None
        return min(pool, key=lambda c: (c.cumulative_load, c.distance, c.robot_id)).robot_id


@register
class EDFAllocator(AllocationStrategy):
    """Earliest Deadline First.

    Deadline governs the task-queue *order* (handled by the task manager); for the
    robot choice within a task EDF falls back to nearest.
    """

    name = 'edf'

    def select(self, candidates):
        pool = _reachable(candidates)
        if not pool:
            return None
        return min(pool, key=lambda c: (c.distance, c.robot_id)).robot_id


@register
class HeuristicAllocator(AllocationStrategy):
    """Lexicographic hand-crafted rule (the classic 'greedy heuristic'):
    battery-feasible -> least workload -> nearest -> most battery -> lowest id."""

    name = 'heuristic'

    def select(self, candidates):
        pool = [c for c in _reachable(candidates) if c.battery_feasible]
        if not pool:
            return None
        return min(pool, key=lambda c: (c.workload, c.distance, -c.battery, c.robot_id)).robot_id

    def explain(self, candidates, chosen):
        if chosen is None:
            return f'{self.name}: no battery-feasible reachable robot -> defer'
        c = next(x for x in candidates if x.robot_id == chosen)
        return (f'heuristic: robot {chosen} feasible, workload={c.workload}, '
                f'distance={c.distance:.1f} m, battery={c.battery:.0f}%')


# 'greedy' is an alias for the lexicographic heuristic.
STRATEGIES['greedy'] = HeuristicAllocator


@register
class WeightedScoreAllocator(AllocationStrategy):
    """Minimise the proposal cost C_ij = l1 T + l2 D + l3 G + l4 E + l5 W - l6 q."""

    name = 'score'

    def select(self, candidates):
        pool = _reachable(candidates)
        if not pool:
            return None
        return min(pool, key=lambda c: (task_cost(c, self.cost_weights), c.robot_id)).robot_id

    def explain(self, candidates, chosen):
        if chosen is None:
            return f'{self.name}: no reachable robot -> defer'
        c = next(x for x in candidates if x.robot_id == chosen)
        return f'score: robot {chosen} min cost C_ij={task_cost(c, self.cost_weights):.3f}'


class _AuctionBase(AllocationStrategy):
    """Sealed-bid auction: each robot bids utility 1/(1+C_ij); highest bid (lowest
    cost) wins. Battery-infeasible robots bid 0 and defer if nobody is feasible."""

    _weights: CostWeights

    def _bid(self, c: Candidate) -> float:
        if not c.battery_feasible:
            return 0.0
        return 1.0 / (1.0 + task_cost(c, self._weights))

    def select(self, candidates):
        pool = _reachable(candidates)
        if not pool:
            return None
        best = max(pool, key=lambda c: (self._bid(c), -c.robot_id))
        return best.robot_id if self._bid(best) > 0.0 else None

    def explain(self, candidates, chosen):
        if chosen is None:
            return f'{self.name}: all bids zero -> defer'
        c = next(x for x in candidates if x.robot_id == chosen)
        return f'{self.name}: robot {chosen} won with bid={self._bid(c):.4f}'


@register
class BidBasedAllocator(_AuctionBase):
    """First-price bid on the robot's *own* task cost only (no fleet load balancing:
    workload and priority terms dropped)."""

    name = 'bid'

    @property
    def _weights(self) -> CostWeights:
        w = self.cost_weights
        return CostWeights(time=w.time, distance=w.distance, congestion=w.congestion,
                           energy=w.energy, workload=0.0, priority=0.0)


@register
class AuctionAllocator(_AuctionBase):
    """Sealed-bid auction on the full C_ij (includes workload load-balancing)."""

    name = 'auction'

    @property
    def _weights(self) -> CostWeights:
        return self.cost_weights


# ======================================================================
# Batch allocation: available robots x eligible FIFO orders
# ======================================================================


@dataclass(frozen=True)
class OrderAssignment:
    """One robot-order pairing, with the route the allocator predicted."""

    robot_id: str
    order_id: str
    cost: float          # C_static(r, o), metres
    route: OrderRoute    # includes the chosen shelf visiting order

    @property
    def zone_sequence(self) -> tuple[str, ...]:
        """Zones the executor must drive, in order."""
        return self.route.zone_sequence


BATCH_STRATEGIES: dict[str, type["BatchAllocationStrategy"]] = {}


def register_batch(cls):
    BATCH_STRATEGIES[cls.name] = cls
    return cls


def get_batch_strategy(name: str, **kwargs) -> "BatchAllocationStrategy":
    key = name.lower()
    if key not in BATCH_STRATEGIES:
        raise KeyError(
            f'unknown batch strategy {name!r}; '
            f'known: {sorted(BATCH_STRATEGIES)}'
        )
    return BATCH_STRATEGIES[key](**kwargs)


class BatchAllocationStrategy:
    """Pairs available robots with eligible orders.

    One call is one allocation decision. The caller supplies:

      warehouse       grid + zone state, for grid-aware costing
      robots          (robot_id, cell) for every AVAILABLE robot
      orders          the eligible FIFO batch, already capped at
                      min(available_robots, pending_orders)
      fifo_position   order_id -> FIFO index, for deterministic ties

    and gets back a list of :class:`OrderAssignment`. A strategy may
    return fewer assignments than robots (an unreachable order defers),
    but never more than one order per robot or one robot per order, and
    never an order outside ``orders``.
    """

    name = 'batch_base'

    def __init__(self, seed: int = 0, cost_weights: CostWeights | None = None):
        self.seed = seed
        self.cost_weights = cost_weights or CostWeights()
        self.reset()

    def reset(self):
        """Reset any per-trial internal state."""

    def score(
        self, warehouse, robots, orders, fifo_position, cache=None
    ) -> list[OrderCandidate]:
        """Cost every (robot, order) pair. Shared by every strategy."""
        return build_order_candidates(
            warehouse, robots, orders, fifo_position, cache
        )

    def assign(
        self, warehouse, robots, orders, fifo_position, cache=None
    ) -> list[OrderAssignment]:
        raise NotImplementedError

    def explain(self, assignments: list[OrderAssignment]) -> str:
        if not assignments:
            return f'{self.name}: nothing assignable -> defer'
        return f'{self.name}: {len(assignments)} assignment(s)'


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
    """

    name = 'nearest_astar'

    def assign(self, warehouse, robots, orders, fifo_position, cache=None):

        candidates = [
            candidate
            for candidate in self.score(
                warehouse, robots, orders, fifo_position, cache
            )
            if candidate.reachable
        ]

        # One total order over every pair, so the greedy sweep below is
        # fully determined by the data and never by dict iteration.
        candidates.sort(
            key=lambda c: (c.cost, c.fifo_position, c.robot_id)
        )

        taken_robots: set[str] = set()
        taken_orders: set[str] = set()

        assignments: list[OrderAssignment] = []

        for candidate in candidates:

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
                    cost=candidate.cost,
                    route=candidate.route,
                )
            )

        # Report in FIFO order: the oldest order first, whichever robot
        # happened to win it.
        assignments.sort(key=lambda a: fifo_position[a.order_id])

        return assignments

    def explain(self, assignments):
        if not assignments:
            return f'{self.name}: no reachable robot-order pair -> defer'
        return ', '.join(
            f'{a.robot_id}->{a.order_id} ({a.cost:.1f} m)'
            for a in assignments
        )
