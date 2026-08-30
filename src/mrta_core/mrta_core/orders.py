"""Customer orders loaded from CSV.

Pure data plus a focused loader: no ROS, so both ``shared_memory_node``
(which owns order state) and ``simulation_env`` (which executes it) can
share one definition.

One CSV row is one order line item. Rows sharing an ``order_id`` form a
single order and must agree on release_time, deadline and dropoff.

Deliberately absent: priority, robot assignment, paths and charging.
Assignment happens at runtime; routing belongs to the planner.
"""

import csv
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class OrderStatus(str, Enum):
    """Lifecycle of an order inside shared memory."""

    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class OrderItem:
    item_id: str
    shelf_id: str
    quantity: int


@dataclass
class Order:
    order_id: str
    release_time: float
    deadline: float
    dropoff_id: str
    items: tuple[OrderItem, ...] = ()

    status: OrderStatus = OrderStatus.PENDING
    assigned_robot: str = ""

    start_time: float | None = None
    completion_time: float | None = None

    @property
    def shelf_ids(self) -> tuple[str, ...]:
        """Shelves to visit, in CSV row order."""
        return tuple(item.shelf_id for item in self.items)

    def lateness(self) -> float | None:
        """max(0, completion - deadline). None until completed."""

        if self.completion_time is None:
            return None

        return max(0.0, self.completion_time - self.deadline)


REQUIRED_COLUMNS = (
    "order_id",
    "release_time",
    "item_id",
    "shelf_id",
    "quantity",
    "dropoff_id",
    "deadline",
)


def _zone_exists(warehouse, zone_id: str) -> bool:
    try:
        warehouse.zone_by_id(zone_id)
    except KeyError:
        return False

    return True


def load_orders_csv(
    path: str | Path,
    warehouse=None,
) -> tuple[Order, ...]:
    """Load orders, grouping rows by ``order_id``.

    When ``warehouse`` is given, every shelf_id and dropoff_id is
    checked against the map so a typo fails at load time rather than
    halfway through a mission.
    """

    path = Path(path)

    with path.open(newline="") as handle:

        reader = csv.DictReader(handle)

        missing = [
            column
            for column in REQUIRED_COLUMNS
            if column not in (reader.fieldnames or [])
        ]

        if missing:
            raise ValueError(
                f"{path}: missing column(s): {', '.join(missing)}"
            )

        rows = list(enumerate(reader, start=2))

    grouped: dict[str, list] = {}

    order_of_appearance: list[str] = []

    header: dict[str, tuple[float, float, str, int]] = {}

    for line, row in rows:

        order_id = (row["order_id"] or "").strip()

        if not order_id:
            raise ValueError(
                f"{path}:{line}: order_id must not be empty"
            )

        item_id = (row["item_id"] or "").strip()
        shelf_id = (row["shelf_id"] or "").strip()
        dropoff_id = (row["dropoff_id"] or "").strip()

        try:
            release_time = float(row["release_time"])
            deadline = float(row["deadline"])
            quantity = int(row["quantity"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{path}:{line}: non-numeric field: {exc}"
            ) from exc

        if quantity <= 0:
            raise ValueError(
                f"{path}:{line}: quantity must be > 0, got {quantity}"
            )

        if release_time < 0.0:
            raise ValueError(
                f"{path}:{line}: release_time must be >= 0, "
                f"got {release_time}"
            )

        if deadline < release_time:
            raise ValueError(
                f"{path}:{line}: deadline {deadline} is before "
                f"release_time {release_time}"
            )

        if warehouse is not None:

            if not _zone_exists(warehouse, shelf_id):
                raise ValueError(
                    f"{path}:{line}: shelf {shelf_id!r} "
                    f"is not in the warehouse map"
                )

            if not _zone_exists(warehouse, dropoff_id):
                raise ValueError(
                    f"{path}:{line}: dropoff {dropoff_id!r} "
                    f"is not in the warehouse map"
                )

        if order_id not in grouped:
            grouped[order_id] = []
            order_of_appearance.append(order_id)
            header[order_id] = (
                release_time,
                deadline,
                dropoff_id,
                line,
            )

        else:
            first_release, first_deadline, first_drop, first_line = (
                header[order_id]
            )

            for label, seen, first in (
                ("release_time", release_time, first_release),
                ("deadline", deadline, first_deadline),
                ("dropoff_id", dropoff_id, first_drop),
            ):
                if seen != first:
                    raise ValueError(
                        f"{path}:{line}: order {order_id!r} has "
                        f"{label}={seen!r} but line {first_line} "
                        f"has {first!r}; all rows of one order "
                        f"must agree"
                    )

        grouped[order_id].append(
            OrderItem(
                item_id=item_id,
                shelf_id=shelf_id,
                quantity=quantity,
            )
        )

    orders = []

    for order_id in order_of_appearance:

        release_time, deadline, dropoff_id, _ = header[order_id]

        orders.append(
            Order(
                order_id=order_id,
                release_time=release_time,
                deadline=deadline,
                dropoff_id=dropoff_id,
                items=tuple(grouped[order_id]),
            )
        )

    if not orders:
        raise ValueError(f"{path}: no orders found")

    return tuple(orders)
