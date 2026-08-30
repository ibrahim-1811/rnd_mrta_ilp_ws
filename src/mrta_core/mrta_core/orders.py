"""Customer orders loaded from CSV.

Pure data plus a focused loader: no ROS, so both ``shared_memory_node``
(which owns order state) and ``simulation_env`` (which executes it) can
share one definition.

One CSV row is one order line item. Rows sharing an ``order_id`` form a
single logical order and must agree on dropoff.

The workload is deterministic and fully known at startup: every order
exists from the moment the CSV is read and every order starts PENDING.
There is no release schedule and no deadline -- which orders an
allocator may see is a FIFO question, answered by
:class:`mrta_core.order_queue.OrderQueue`, not a clock question.

Deliberately absent: priority, release_time, deadline, robot
assignment, paths and charging. Assignment happens at runtime;
routing belongs to the planner.
"""

import csv
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from mrta_core.warehouse_map import ZoneType


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


REQUIRED_COLUMNS = (
    "order_id",
    "item_id",
    "shelf_id",
    "quantity",
    "dropoff_id",
)


def _station_problem(warehouse, zone_id: str, expected) -> str | None:
    """Why ``zone_id`` is not a usable station of ``expected`` type.

    Existence alone is not enough: ``dropoff_id=shelf_004`` names a
    real zone and would route a robot to racking to drop a parcel.
    Returns None when the id is valid.
    """

    if not zone_id:
        return "is empty"

    try:
        zone = warehouse.zone_by_id(zone_id)
    except KeyError:
        return "is not in the warehouse map"

    if zone.zone_type is not expected:
        return (
            f"is a {zone.zone_type.value} station, "
            f"not a {expected.value} station"
        )

    return None


def load_orders_csv(
    path: str | Path,
    warehouse=None,
) -> tuple[Order, ...]:
    """Load orders, grouping rows by ``order_id``.

    The returned tuple is the FIFO workload sequence: logical orders in
    the sequence their first row appears in the CSV. Reading the same
    file twice always yields the same sequence.

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

    # FIFO sequence: order_ids in the sequence their first row appears.
    order_of_appearance: list[str] = []

    header: dict[str, tuple[str, int]] = {}

    for line, row in rows:

        order_id = (row["order_id"] or "").strip()

        if not order_id:
            raise ValueError(
                f"{path}:{line}: order_id must not be empty"
            )

        item_id = (row["item_id"] or "").strip()
        shelf_id = (row["shelf_id"] or "").strip()
        dropoff_id = (row["dropoff_id"] or "").strip()

        if not item_id:
            raise ValueError(
                f"{path}:{line}: item_id must not be empty"
            )

        try:
            quantity = int(row["quantity"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{path}:{line}: non-numeric field: {exc}"
            ) from exc

        if quantity <= 0:
            raise ValueError(
                f"{path}:{line}: quantity must be > 0, got {quantity}"
            )

        if warehouse is not None:

            for label, station_id, expected in (
                ("shelf", shelf_id, ZoneType.SHELF),
                ("dropoff", dropoff_id, ZoneType.DROPOFF),
            ):
                problem = _station_problem(
                    warehouse, station_id, expected
                )

                if problem is not None:
                    raise ValueError(
                        f"{path}:{line}: {label} "
                        f"{station_id!r} {problem}"
                    )

        if order_id not in grouped:
            grouped[order_id] = []
            order_of_appearance.append(order_id)
            header[order_id] = (dropoff_id, line)

        else:
            first_drop, first_line = header[order_id]

            if dropoff_id != first_drop:
                raise ValueError(
                    f"{path}:{line}: order {order_id!r} has "
                    f"dropoff_id={dropoff_id!r} but line "
                    f"{first_line} has {first_drop!r}; all rows of "
                    f"one order must agree"
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

        dropoff_id, _ = header[order_id]

        orders.append(
            Order(
                order_id=order_id,
                dropoff_id=dropoff_id,
                items=tuple(grouped[order_id]),
            )
        )

    if not orders:
        raise ValueError(f"{path}: no orders found")

    return tuple(orders)
