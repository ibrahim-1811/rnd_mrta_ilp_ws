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
"""

import random

from task_management.features import Candidate, CostWeights, task_cost

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
