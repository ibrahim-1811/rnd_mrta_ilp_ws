"""Batch MRTA allocation strategies for warehouse orders.

FIFO determines which pending orders are eligible.

An allocation strategy determines only which available robot receives
which eligible order.

Traffic planning, collision avoidance and execution are outside this
module and must never change robot-order ownership.
"""

from dataclasses import dataclass

from mrta_core.battery import EnergyRequirement
from task_management.features import (
    OrderCandidate,
    OrderRoute,
    build_order_candidates,
)


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

    # Decision-time battery features of the chosen pair, if any.
    energy: EnergyRequirement | None = None

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

    name = 'batch_base'

    def __init__(self, seed: int = 0):
        self.seed = seed
        self.reset()

    def reset(self):
        """Reset any per-trial internal state."""

    def score(
        self, warehouse, robots, orders, fifo_position, cache=None,
        battery=None,
    ) -> list[OrderCandidate]:
        """Cost every (robot, order) pair. Shared by every strategy."""
        return build_order_candidates(
            warehouse, robots, orders, fifo_position, cache, battery
        )

    def assign(
        self, warehouse, robots, orders, fifo_position, cache=None,
        battery=None,
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

    Battery eligibility is a hard mask applied before the sweep: an
    ineligible pair is simply not a candidate, however cheap. Among
    eligible pairs the rule above is unchanged.
    """

    name = 'nearest_astar'

    def assign(
        self, warehouse, robots, orders, fifo_position, cache=None,
        battery=None,
    ):

        candidates = [
            candidate
            for candidate in self.score(
                warehouse, robots, orders, fifo_position, cache, battery
            )
            if candidate.eligible
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
                    energy=candidate.energy,
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
