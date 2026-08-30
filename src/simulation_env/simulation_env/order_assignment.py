"""Order -> robot assignment policy.

Kept in its own module, and deliberately out of GridRobot and
MissionPlanner, so the real MRTA allocators (nearest, auction, ILP,
learned) can replace it later by swapping the object rather than
editing execution code.

Nothing here is an MRTA contribution. FirstPendingAssigner exists only
to validate the order pipeline end to end with a single robot: it
takes the FIFO head and the first free robot, which is a pipeline
check, not a strategy.
"""

from abc import ABC, abstractmethod

from mrta_core.orders import Order, OrderStatus


class OrderAssigner(ABC):
    """Chooses which pending order a free robot should take next."""

    @abstractmethod
    def select(
        self,
        orders: list[Order],
        free_robots: list[str],
        sim_time: float,
    ) -> tuple[Order, str] | None:
        """Return (order, robot_id), or None if nothing to assign."""


class FirstPendingAssigner(OrderAssigner):
    """Take the FIFO head of the pending queue. Pipeline check only.

    ``orders`` arrives in CSV sequence, so the first PENDING entry is
    the oldest waiting order. No sorting: re-ordering here would be an
    allocation decision.
    """

    name = "first_pending"

    def select(
        self,
        orders: list[Order],
        free_robots: list[str],
        sim_time: float,
    ) -> tuple[Order, str] | None:

        if not free_robots:
            return None

        for order in orders:

            if order.status is OrderStatus.PENDING:
                return order, free_robots[0]

        return None
