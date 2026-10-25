"""A shorter run takes the first N logical orders, never part of one.

``--orders N`` (headless) and ``order_limit:=N`` (ROS) both go through
select_orders(), so they run exactly the same orders. An order is the
unit: a limit counts orders, not CSV rows, and the order file is never
rewritten.
"""

import pytest

from mrta_core.order_queue import OrderQueue
from mrta_core.orders import describe_order_selection, load_orders_csv, select_orders


# Three orders over six rows: 2, 1 and 3 line items.
CSV = """order_id,item_id,shelf_id,quantity,dropoff_id
O1,i1,shelf_001,1,dropoff_001
O1,i2,shelf_002,2,dropoff_001
O2,i3,shelf_003,1,dropoff_002
O3,i4,shelf_004,1,dropoff_003
O3,i5,shelf_005,1,dropoff_003
O3,i6,shelf_006,4,dropoff_003
"""


@pytest.fixture
def orders(tmp_path):
    path = tmp_path / "orders.csv"
    path.write_text(CSV)
    return load_orders_csv(path)


def test_a_limit_takes_the_first_n_logical_orders(orders):
    chosen = select_orders(orders, 2)

    assert [order.order_id for order in chosen] == ["O1", "O2"]
    assert chosen == orders[:2]


def test_an_order_is_never_split(orders):
    """Two orders are three rows here, not two."""

    chosen = select_orders(orders, 2)

    assert [len(order.items) for order in chosen] == [2, 1]
    assert OrderQueue(chosen).line_item_count == 3

    # A limit of 1 takes the whole first order, both of its rows.
    (first,) = select_orders(orders, 1)
    assert first.shelf_ids == ("shelf_001", "shelf_002")


def test_no_limit_takes_every_order(orders):
    assert select_orders(orders, 0) == orders
    assert select_orders(orders) == orders


def test_a_limit_above_the_workload_takes_every_order(orders):
    assert select_orders(orders, 5000) == orders


def test_a_negative_limit_is_refused(orders):
    with pytest.raises(ValueError, match="0 \\(all\\) or positive"):
        select_orders(orders, -1)


def test_the_order_file_is_not_changed(tmp_path, orders):
    path = tmp_path / "orders.csv"

    select_orders(load_orders_csv(path), 1)

    assert path.read_text() == CSV


@pytest.mark.parametrize(
    "limit, expected",
    [
        (0, "all 3 logical orders (6 line items) from orders.csv"),
        (2, "first 2 of 3 logical orders (3 line items) from orders.csv, limit 2"),
        (3, "first 3 of 3 logical orders (6 line items) from orders.csv, limit 3"),
        (
            9,
            "all 3 logical orders (6 line items) from orders.csv: "
            "the limit 9 is more than the file holds",
        ),
    ],
)
def test_the_selection_is_described_with_requested_and_actual(orders, limit, expected):
    chosen = select_orders(orders, limit)

    assert describe_order_selection(limit, orders, chosen, "orders.csv") == expected
