"""The deterministic FIFO warehouse workload.

Every order is known at startup and every order starts PENDING, so
there is nothing to gate on a clock. What the workload decides is
narrower and purely structural: *which* orders an allocator is allowed
to look at right now.

The rule is FIFO on the CSV sequence. Given a window width W and M
orders still PENDING::

    eligible_count = min(W, M)

and the eligible set is exactly the next ``eligible_count`` PENDING
orders in CSV sequence. Nothing later is ever skipped: an order can
only leave the queue by leaving PENDING, which happens when an
allocator claims it.

Choosing W is the caller's decision, not this layer's. It used to be
the number of available robots, which collapsed almost every decision
to one robot and one order; it is now set from the configured
allocation window (docs/DECISIONS.md, "Allocation Candidate Window").

Deliberately absent: allocation. This layer says *which orders are on
the table*; deciding which available robot takes which of them is the
MRTA decision and belongs to the allocator.
"""

from pathlib import Path

from mrta_core.orders import Order, OrderStatus, load_orders_csv


class OrderQueue:
    """Logical orders in CSV sequence, plus the FIFO eligibility rule.

    Ordering is deterministic and total: the CSV sequence, and nothing
    else. Two loads of the same file always produce the same queue.
    """

    def __init__(self, orders) -> None:

        self._orders: tuple[Order, ...] = tuple(orders)

        if not self._orders:
            raise ValueError("order queue must not be empty")

        self._index = {}

        for position, order in enumerate(self._orders):
            if order.order_id in self._index:
                raise ValueError(f"duplicate order_id {order.order_id!r}")

            self._index[order.order_id] = position

    @classmethod
    def from_csv(
        cls,
        path: str | Path,
        warehouse=None,
    ) -> "OrderQueue":
        """Load a workload CSV into a queue in one step."""
        return cls(load_orders_csv(path, warehouse))

    # -- inspection -------------------------------------

    def __len__(self) -> int:
        return len(self._orders)

    def __iter__(self):
        return iter(self._orders)

    def __contains__(self, order_id: str) -> bool:
        return order_id in self._index

    @property
    def orders(self) -> tuple[Order, ...]:
        """Every order, in FIFO (CSV) sequence, whatever its status."""
        return self._orders

    @property
    def order_ids(self) -> tuple[str, ...]:
        return tuple(order.order_id for order in self._orders)

    @property
    def line_item_count(self) -> int:
        return sum(len(order.items) for order in self._orders)

    def order(self, order_id: str) -> Order:
        return self._orders[self._index[order_id]]

    def position(self, order_id: str) -> int:
        """0-based FIFO position of an order in the CSV sequence."""
        return self._index[order_id]

    # -- the FIFO workload rule -------------------------

    def pending(self) -> tuple[Order, ...]:
        """Every still-PENDING order, in FIFO sequence.

        The head of this queue is unambiguously 'the oldest waiting
        order', because the sequence is the CSV sequence.
        """

        return tuple(
            order for order in self._orders if order.status is OrderStatus.PENDING
        )

    def eligible(self, count: int) -> tuple[Order, ...]:
        """The next ``min(count, pending)`` FIFO orders.

        The prefix primitive the candidate window is built from. It is a
        prefix of :meth:`pending`, so a later order is never offered
        while an earlier one is still waiting.

        ``count`` is a window width, not a robot count. The executor may
        widen it past the width it first asked for when the prefix holds
        no assignable pair at all -- see the FIFO liveness rule -- but
        the result is always still a prefix.
        """

        if count < 0:
            raise ValueError(f"count must be >= 0, got {count}")

        if count == 0:
            return ()

        return self.pending()[:count]

    def reset(self) -> None:
        """Return every order to PENDING and clear its assignment."""

        for order in self._orders:
            order.status = OrderStatus.PENDING
            order.assigned_robot = ""
            order.start_time = None
            order.completion_time = None
