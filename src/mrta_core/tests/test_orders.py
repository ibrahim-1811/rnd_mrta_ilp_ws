"""The deterministic FIFO workload: schema, grouping and validation.

The active schema is exactly::

    order_id,item_id,shelf_id,quantity,dropoff_id

with no release_time and no deadline: the whole workload is known at
startup and every logical order starts PENDING.
"""

import pytest

from mrta_core.orders import (
    REQUIRED_COLUMNS,
    Order,
    OrderStatus,
    load_orders_csv,
)

from conftest import WORKLOAD_CSV


# -- schema -------------------------------------------------


def test_schema_is_the_five_active_columns():
    assert REQUIRED_COLUMNS == (
        "order_id",
        "item_id",
        "shelf_id",
        "quantity",
        "dropoff_id",
    )


def test_workload_csv_header_matches_the_schema():
    header = WORKLOAD_CSV.read_text().splitlines()[0]

    assert header.split(",") == list(REQUIRED_COLUMNS)


def test_the_order_model_carries_no_release_time_or_deadline():
    fields = Order.__dataclass_fields__

    assert "release_time" not in fields
    assert "deadline" not in fields
    assert not hasattr(Order, "lateness")


def test_order_states_are_the_five_lifecycle_states():
    assert [status.value for status in OrderStatus] == [
        "PENDING",
        "ASSIGNED",
        "IN_PROGRESS",
        "COMPLETED",
        "FAILED",
    ]


# -- the development workload -------------------------------


def test_one_hundred_logical_orders_load(warehouse):
    orders = load_orders_csv(WORKLOAD_CSV, warehouse)

    assert len(orders) == 100
    assert len({order.order_id for order in orders}) == 100


def test_every_loaded_order_starts_pending(warehouse):
    orders = load_orders_csv(WORKLOAD_CSV, warehouse)

    assert all(order.status is OrderStatus.PENDING for order in orders)
    assert all(order.assigned_robot == "" for order in orders)
    assert all(order.start_time is None for order in orders)
    assert all(order.completion_time is None for order in orders)


def test_every_order_has_one_to_three_shelf_lines(warehouse):
    orders = load_orders_csv(WORKLOAD_CSV, warehouse)

    assert all(1 <= len(order.items) <= 3 for order in orders)

    # Every row of the file is accounted for by exactly one order.
    rows = len(WORKLOAD_CSV.read_text().strip().splitlines()) - 1

    assert sum(len(order.items) for order in orders) == rows


def test_first_twenty_five_fifo_ids_are_the_csv_sequence(warehouse):
    orders = load_orders_csv(WORKLOAD_CSV, warehouse)

    assert [order.order_id for order in orders[:25]] == [
        f"O{n:04d}" for n in range(1, 26)
    ]


def test_two_independent_loads_produce_identical_fifo_ordering(
    warehouse,
):
    first = load_orders_csv(WORKLOAD_CSV, warehouse)
    second = load_orders_csv(WORKLOAD_CSV, warehouse)

    assert [order.order_id for order in first] == [order.order_id for order in second]
    assert [order.shelf_ids for order in first] == [order.shelf_ids for order in second]
    assert [order.dropoff_id for order in first] == [
        order.dropoff_id for order in second
    ]


# -- grouping -----------------------------------------------


def test_rows_sharing_an_order_id_form_one_logical_order(warehouse, csv_from_rows):
    path = csv_from_rows(
        [
            "O0001,SKU001,shelf_003,1,dropoff_001",
            "O0001,SKU002,shelf_010,2,dropoff_001",
            "O0002,SKU011,shelf_025,1,dropoff_004",
        ]
    )

    orders = load_orders_csv(path, warehouse)

    assert [order.order_id for order in orders] == ["O0001", "O0002"]
    assert orders[0].shelf_ids == ("shelf_003", "shelf_010")
    assert [item.quantity for item in orders[0].items] == [1, 2]
    assert orders[0].dropoff_id == "dropoff_001"
    assert orders[1].shelf_ids == ("shelf_025",)


def test_line_items_keep_csv_row_order_within_one_order(warehouse, csv_from_rows):
    path = csv_from_rows(
        [
            "O0001,SKU003,shelf_033,1,dropoff_001",
            "O0001,SKU001,shelf_007,1,dropoff_001",
            "O0001,SKU002,shelf_022,1,dropoff_001",
        ]
    )

    orders = load_orders_csv(path, warehouse)

    assert orders[0].shelf_ids == (
        "shelf_033",
        "shelf_007",
        "shelf_022",
    )
    assert [item.item_id for item in orders[0].items] == [
        "SKU003",
        "SKU001",
        "SKU002",
    ]


def test_fifo_sequence_follows_first_appearance_not_sorting(warehouse, csv_from_rows):
    """Interleaved and out-of-sequence ids keep their file order."""

    path = csv_from_rows(
        [
            "O0009,SKU001,shelf_003,1,dropoff_001",
            "O0002,SKU002,shelf_010,1,dropoff_002",
            "O0009,SKU003,shelf_011,1,dropoff_001",
            "O0005,SKU004,shelf_012,1,dropoff_003",
        ]
    )

    orders = load_orders_csv(path, warehouse)

    assert [order.order_id for order in orders] == [
        "O0009",
        "O0002",
        "O0005",
    ]
    assert orders[0].shelf_ids == ("shelf_003", "shelf_011")


# -- validation ---------------------------------------------


def bad(warehouse, csv_from_rows, rows):
    """The CSV built from ``rows`` must be rejected."""

    with pytest.raises(ValueError) as failure:
        load_orders_csv(csv_from_rows(rows), warehouse)

    return str(failure.value)


def test_missing_order_id_is_rejected(warehouse, csv_from_rows):
    assert "order_id" in bad(
        warehouse,
        csv_from_rows,
        [",SKU001,shelf_003,1,dropoff_001"],
    )


def test_missing_item_id_is_rejected(warehouse, csv_from_rows):
    assert "item_id" in bad(
        warehouse,
        csv_from_rows,
        ["O0001,,shelf_003,1,dropoff_001"],
    )


@pytest.mark.parametrize("quantity", ["0", "-1"])
def test_non_positive_quantity_is_rejected(warehouse, csv_from_rows, quantity):
    assert "quantity must be > 0" in bad(
        warehouse,
        csv_from_rows,
        [f"O0001,SKU001,shelf_003,{quantity},dropoff_001"],
    )


def test_non_numeric_quantity_is_rejected(warehouse, csv_from_rows):
    assert "non-numeric" in bad(
        warehouse,
        csv_from_rows,
        ["O0001,SKU001,shelf_003,two,dropoff_001"],
    )


def test_unknown_shelf_id_is_rejected(warehouse, csv_from_rows):
    assert "not in the warehouse map" in bad(
        warehouse,
        csv_from_rows,
        ["O0001,SKU001,shelf_999,1,dropoff_001"],
    )


def test_unknown_dropoff_id_is_rejected(warehouse, csv_from_rows):
    assert "not in the warehouse map" in bad(
        warehouse,
        csv_from_rows,
        ["O0001,SKU001,shelf_003,1,dropoff_999"],
    )


def test_shelf_id_naming_a_non_shelf_station_is_rejected(warehouse, csv_from_rows):
    assert "not a shelf station" in bad(
        warehouse,
        csv_from_rows,
        ["O0001,SKU001,staging_001,1,dropoff_001"],
    )


def test_dropoff_id_naming_a_non_dropoff_station_is_rejected(warehouse, csv_from_rows):
    assert "not a dropoff station" in bad(
        warehouse,
        csv_from_rows,
        ["O0001,SKU001,shelf_003,1,shelf_004"],
    )


def test_one_order_with_two_dropoffs_is_rejected(warehouse, csv_from_rows):
    assert "must agree" in bad(
        warehouse,
        csv_from_rows,
        [
            "O0001,SKU001,shelf_003,1,dropoff_001",
            "O0001,SKU002,shelf_010,1,dropoff_002",
        ],
    )


def test_a_release_time_or_deadline_column_is_not_required(warehouse, csv_from_rows):
    """The loader reads the five active columns and ignores extras."""

    path = csv_from_rows(
        [
            "O0001,SKU001,shelf_003,1,dropoff_001",
        ]
    )

    assert len(load_orders_csv(path, warehouse)) == 1
