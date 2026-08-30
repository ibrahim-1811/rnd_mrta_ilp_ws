"""FIFO workload semantics: which orders an allocator may see.

The queue answers only ``which orders are on the table``. Pairing an
available robot with one of them is the MRTA decision and is not made
here -- these tests deliberately never assert a robot-order pairing.
"""

import pytest

from mrta_core.order_queue import OrderQueue
from mrta_core.orders import OrderStatus, load_orders_csv

from conftest import WORKLOAD_CSV


ROBOTS = 18


@pytest.fixture
def queue(warehouse):
    return OrderQueue.from_csv(WORKLOAD_CSV, warehouse)


def claim(orders, robot_ids=()):
    """Take orders off the queue, as an allocator would.

    ``robot_ids`` is optional and unordered on purpose: the queue must
    behave identically however the pairing was decided.
    """

    for index, order in enumerate(orders):
        order.status = OrderStatus.ASSIGNED
        order.assigned_robot = (
            robot_ids[index] if index < len(robot_ids) else ""
        )


# -- loading ------------------------------------------------


def test_the_queue_holds_one_hundred_pending_orders(queue):
    assert len(queue) == 100
    assert len(queue.pending()) == 100
    assert queue.line_item_count == sum(
        len(order.items) for order in queue
    )


def test_first_twenty_five_fifo_order_ids(queue):
    assert list(queue.order_ids[:25]) == [
        f"O{n:04d}" for n in range(1, 26)
    ]


def test_a_duplicate_order_id_is_rejected(warehouse):
    orders = list(load_orders_csv(WORKLOAD_CSV, warehouse))

    with pytest.raises(ValueError, match="duplicate order_id"):
        OrderQueue(orders + [orders[0]])


def test_an_empty_queue_is_rejected():
    with pytest.raises(ValueError, match="must not be empty"):
        OrderQueue(())


# -- the FIFO batch rule ------------------------------------


def test_eighteen_robots_make_the_first_eighteen_orders_eligible(
    queue,
):
    eligible = queue.eligible(ROBOTS)

    assert [order.order_id for order in eligible] == [
        f"O{n:04d}" for n in range(1, 19)
    ]


def test_the_queue_head_after_the_first_batch_is_o0019(queue):
    claim(queue.eligible(ROBOTS))

    assert len(queue.pending()) == 82
    assert queue.pending()[0].order_id == "O0019"


def test_one_freed_robot_gets_exactly_one_eligible_order(queue):
    claim(queue.eligible(ROBOTS))

    eligible = queue.eligible(1)

    assert [order.order_id for order in eligible] == ["O0019"]


def test_three_freed_robots_get_o0019_o0020_o0021(queue):
    claim(queue.eligible(ROBOTS))
    claim(queue.eligible(3))

    # The batch of three is taken from the head, in FIFO sequence.
    assert [
        order.order_id
        for order in queue.orders
        if order.status is OrderStatus.ASSIGNED
    ][18:] == ["O0019", "O0020", "O0021"]


def test_no_later_order_is_ever_skipped(queue):
    """Draining the queue in ragged batches keeps strict FIFO."""

    taken = []

    for batch in (18, 1, 3, 7, 2, 40, 11, 5, 13):

        eligible = queue.eligible(batch)
        taken.extend(order.order_id for order in eligible)
        claim(eligible)

    assert taken == [f"O{n:04d}" for n in range(1, 101)]
    assert queue.pending() == ()


def test_eligible_count_is_min_of_robots_and_pending(queue):
    assert len(queue.eligible(5)) == 5
    assert len(queue.eligible(100)) == 100

    # More robots than work: capped by what is still PENDING.
    assert len(queue.eligible(250)) == 100

    claim(queue.pending()[:96])

    assert len(queue.eligible(ROBOTS)) == 4


def test_zero_available_robots_make_nothing_eligible(queue):
    assert queue.eligible(0) == ()


def test_a_negative_robot_count_is_rejected(queue):
    with pytest.raises(ValueError, match=">= 0"):
        queue.eligible(-1)


def test_eligible_is_always_a_prefix_of_pending(queue):
    claim(queue.eligible(ROBOTS))

    pending = queue.pending()

    for count in (1, 2, 3, 9, 40):
        assert queue.eligible(count) == pending[:count]


def test_eligibility_is_read_only_until_an_allocator_claims(queue):
    """Asking twice changes nothing: the queue makes no decisions."""

    first = queue.eligible(ROBOTS)
    second = queue.eligible(ROBOTS)

    assert [order.order_id for order in first] == [
        order.order_id for order in second
    ]
    assert len(queue.pending()) == 100


def test_a_completed_order_leaves_the_queue_and_a_failed_one_too(
    queue,
):
    head = queue.pending()[:2]

    head[0].status = OrderStatus.COMPLETED
    head[1].status = OrderStatus.FAILED

    assert queue.pending()[0].order_id == "O0003"


def test_in_progress_orders_are_not_eligible(queue):
    queue.order("O0001").status = OrderStatus.IN_PROGRESS

    assert queue.eligible(2)[0].order_id == "O0002"


# -- determinism --------------------------------------------


def test_two_independent_queues_agree_on_every_batch(warehouse):
    left = OrderQueue.from_csv(WORKLOAD_CSV, warehouse)
    right = OrderQueue.from_csv(WORKLOAD_CSV, warehouse)

    for batch in (ROBOTS, 1, 3, 12):

        chosen_left = left.eligible(batch)
        chosen_right = right.eligible(batch)

        assert [order.order_id for order in chosen_left] == [
            order.order_id for order in chosen_right
        ]

        # Pair the same orders with different robots on each side: the
        # allocator's choice must not move the queue.
        claim(chosen_left, [f"R{n:02d}" for n in range(batch)])
        claim(chosen_right, [f"R{n:02d}" for n in reversed(range(batch))])

    assert [order.order_id for order in left.pending()] == [
        order.order_id for order in right.pending()
    ]


def test_reset_returns_every_order_to_pending(queue):
    claim(queue.eligible(ROBOTS), [f"R{n:02d}" for n in range(ROBOTS)])

    queue.reset()

    assert len(queue.pending()) == 100
    assert queue.pending()[0].order_id == "O0001"
    assert all(order.assigned_robot == "" for order in queue)


def test_position_reports_the_fifo_index(queue):
    assert queue.position("O0001") == 0
    assert queue.position("O0019") == 18
    assert queue.position("O0100") == 99
