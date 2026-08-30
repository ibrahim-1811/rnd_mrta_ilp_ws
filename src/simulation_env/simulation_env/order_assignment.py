"""Order -> robot assignment policy.

Kept in its own module, and deliberately out of GridRobot and
MissionPlanner, so the real MRTA allocators (nearest, auction, ILP,
learned) can replace it later by swapping the object rather than
editing execution code.

Nothing here is an MRTA contribution. FirstReleasedAssigner exists only
to validate the order pipeline end to end with a single robot.
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


class FirstReleasedAssigner(OrderAssigner):
    """Take the earliest released pending order. Pipeline check only.

    Ties break on order_id so a run is reproducible.
    """

    name = "first_released"

    def select(
        self,
        orders: list[Order],
        free_robots: list[str],
        sim_time: float,
    ) -> tuple[Order, str] | None:

        if not free_robots:
            return None

        released = [
            order
            for order in orders
            if order.status is OrderStatus.PENDING
            and order.release_time <= sim_time
        ]

        if not released:
            return None

        released.sort(
            key=lambda order: (order.release_time, order.order_id)
        )

        return released[0], free_robots[0]
