#!/usr/bin/env python3

"""Validation of the order input layer (M4.1).

A: loading and grouping, against the real warehouse map
B: rejection of invalid input
C: the deterministic FIFO workload rule
D: shared_memory_node agrees with the loader   (--ros, needs a live node)

No allocation is exercised here. This layer says which orders exist and
which of them are eligible; deciding which robot takes which eligible
order is the MRTA decision and is not made anywhere in this file.
"""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from mrta_core.order_queue import OrderQueue
from mrta_core.orders import OrderStatus, load_orders_csv
from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.fleet import build_fleet
from simulation_env.fleet_engine import FleetSimulationEngine
from simulation_env.mission_planner import plan_zone_mission

CONFIG = "src/simulation_env/config/warehouse_sim.yaml"

config = load_sim_config(CONFIG)
warehouse = load_warehouse_xlsx(config.map_path)

ORDERS_CSV = config.orders_path

# The fleet size the workload rule is exercised against.
ROBOTS = config.robot_count

fails = []


def check(ok, label):
    print(f"[{'OK ' if ok else 'FAIL'}] {label}")
    if not ok:
        fails.append(label)


def rule(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def rejects(rows, label):
    """A CSV built from ``rows`` must fail to load."""

    with TemporaryDirectory() as directory:

        path = Path(directory) / "bad.csv"

        path.write_text(
            "order_id,item_id,shelf_id,quantity,dropoff_id\n"
            + "\n".join(rows)
            + "\n"
        )

        try:
            load_orders_csv(path, warehouse)
        except ValueError as error:
            print(f"       rejected: {str(error).split(': ', 1)[-1]}")
            check(True, label)
            return

    check(False, label)


def claim(orders, robots=()):
    """Take orders off the queue, as an allocator would.

    The robot ids are passed through unexamined and in no particular
    correspondence: the queue must behave the same however an
    allocator decided to pair them.
    """

    for index, order in enumerate(orders):
        order.status = OrderStatus.ASSIGNED
        order.assigned_robot = (
            robots[index] if index < len(robots) else ""
        )


# ----------------------------------------------------------
rule("A  LOADING AND GROUPING")
# ----------------------------------------------------------

orders = load_orders_csv(ORDERS_CSV, warehouse)

queue = OrderQueue(orders)

print(f"file        : {ORDERS_CSV}")
print(f"orders      : {len(queue)}")
print(f"line items  : {queue.line_item_count}")
print(f"fleet size  : {ROBOTS}")
print()

for order in queue.orders[:ROBOTS]:
    print(
        f"  {order.order_id}  {len(order.items)} item(s) -> "
        f"{order.dropoff_id}  [{', '.join(order.shelf_ids)}]"
    )

print(f"  ... {len(queue) - ROBOTS} more, in FIFO sequence")
print()

check(len(orders) == 100, "100 logical orders loaded")

check(
    len({o.order_id for o in orders}) == 100,
    "all 100 order ids are unique",
)

check(
    open(ORDERS_CSV).readline().strip()
    == "order_id,item_id,shelf_id,quantity,dropoff_id",
    "CSV header is the active five-column schema",
)

check(
    all(1 <= len(o.items) <= 3 for o in orders),
    f"every order has 1-3 shelf lines "
    f"({queue.line_item_count} rows -> 100 orders)",
)

check(
    sum(len(o.items) for o in orders)
    == len(open(ORDERS_CSV).read().strip().splitlines()) - 1,
    "every CSV row belongs to exactly one logical order",
)

check(
    all(
        warehouse.zone_by_id(shelf).zone_type is ZoneType.SHELF
        for o in orders
        for shelf in o.shelf_ids
    ),
    "every shelf_id resolves to a SHELF station on the real map",
)

check(
    all(
        warehouse.zone_by_id(o.dropoff_id).zone_type
        is ZoneType.DROPOFF
        for o in orders
    ),
    "every dropoff_id resolves to a DROPOFF station on the real map",
)

check(
    all(o.status is OrderStatus.PENDING for o in orders),
    "every logical order starts PENDING",
)

check(
    all(len(set(o.shelf_ids)) == len(o.shelf_ids) for o in orders),
    "no order visits the same shelf twice",
)

header = open(ORDERS_CSV).readline().lower()

check(
    "priority" not in header
    and "release_time" not in header
    and "deadline" not in header
    and not hasattr(orders[0], "release_time")
    and not hasattr(orders[0], "deadline"),
    "no priority, release_time or deadline column or field",
)


# ----------------------------------------------------------
rule("B  INVALID INPUT IS REJECTED")
# ----------------------------------------------------------

rejects(
    [",SKU001,shelf_003,1,dropoff_001"],
    "empty order_id rejected",
)

rejects(
    ["O0001,,shelf_003,1,dropoff_001"],
    "empty item_id rejected",
)

rejects(
    ["O0001,SKU001,shelf_999,1,dropoff_001"],
    "unknown shelf id rejected",
)

rejects(
    ["O0001,SKU001,shelf_003,1,dropoff_999"],
    "unknown dropoff id rejected",
)

rejects(
    ["O0001,SKU001,staging_001,1,dropoff_001"],
    "shelf_id naming a non-shelf station rejected",
)

rejects(
    ["O0001,SKU001,shelf_003,1,shelf_004"],
    "dropoff_id naming a non-dropoff station rejected",
)

rejects(
    [
        "O0001,SKU001,shelf_003,1,dropoff_001",
        "O0001,SKU002,shelf_010,1,dropoff_002",
    ],
    "one order with two dropoffs rejected",
)

rejects(
    ["O0001,SKU001,shelf_003,0,dropoff_001"],
    "quantity 0 rejected",
)

rejects(
    ["O0001,SKU001,shelf_003,-2,dropoff_001"],
    "negative quantity rejected",
)

rejects(
    ["O0001,SKU001,shelf_003,two,dropoff_001"],
    "non-numeric quantity rejected",
)


# ----------------------------------------------------------
rule("C  THE DETERMINISTIC FIFO WORKLOAD")
# ----------------------------------------------------------

FIRST_25 = [f"O{n:04d}" for n in range(1, 26)]

check(
    list(queue.order_ids[:25]) == FIRST_25,
    "FIFO sequence follows the CSV: O0001..O0025",
)

check(
    len(queue.pending()) == 100,
    "the whole workload is pending at startup (nothing gated)",
)

first_batch = queue.eligible(ROBOTS)

print()
print(f"  eligible with {ROBOTS} available robots:")
print(f"    {', '.join(o.order_id for o in first_batch)}")
print()

check(
    [o.order_id for o in first_batch]
    == [f"O{n:04d}" for n in range(1, ROBOTS + 1)],
    f"first eligible batch is exactly O0001..O{ROBOTS:04d}",
)

check(
    [o.order_id for o in queue.eligible(ROBOTS)]
    == [o.order_id for o in first_batch],
    "asking again changes nothing: the queue makes no decision",
)

# An allocator claims the batch. Which robot got which order is its
# decision; here they are paired in reverse purely to show that the
# queue does not care.
claim(
    first_batch,
    [f"robot_{n:03d}" for n in reversed(range(1, ROBOTS + 1))],
)

check(
    len(queue.pending()) == 100 - ROBOTS
    and queue.pending()[0].order_id == "O0019",
    "the remaining pending queue starts at O0019",
)

one = queue.eligible(1)

print(f"  one robot becomes available -> {one[0].order_id}")

check(
    [o.order_id for o in one] == ["O0019"],
    "one available robot makes exactly O0019 eligible",
)

three = queue.eligible(3)

print(f"  three available at one instant -> "
      f"{', '.join(o.order_id for o in three)}")
print()

check(
    [o.order_id for o in three] == ["O0019", "O0020", "O0021"],
    "three available robots make O0019, O0020, O0021 eligible",
)

claim(three, ["robot_005", "robot_012", "robot_002"])

check(
    queue.pending()[0].order_id == "O0022",
    "no later order was skipped: the head advances to O0022",
)

check(
    len(queue.eligible(250)) == len(queue.pending()),
    "eligible_count = min(robots, pending): more robots than work",
)

check(
    queue.eligible(0) == (),
    "no available robot makes nothing eligible",
)

# Drain the rest in ragged batches and confirm strict FIFO throughout.
drained = [o.order_id for o in first_batch] + [
    o.order_id for o in three
]

for size in (7, 2, 40, 11, 5, 13, 1):

    batch = queue.eligible(size)
    drained.extend(o.order_id for o in batch)
    claim(batch)

check(
    drained == [f"O{n:04d}" for n in range(1, 101)],
    "draining in ragged batches yields strict FIFO O0001..O0100",
)

check(
    queue.pending() == (),
    "the workload is exhausted with nothing left pending",
)


# -- determinism and clock independence ---------------------

print()
print("-- two independent loads, and the global clock --")
print()

left = OrderQueue.from_csv(ORDERS_CSV, warehouse)
right = OrderQueue.from_csv(ORDERS_CSV, warehouse)

check(
    left.order_ids == right.order_ids,
    "two independent loads produce identical FIFO ordering",
)

identical = True

for size in (ROBOTS, 1, 3, 12, 40):

    chosen_left = left.eligible(size)
    chosen_right = right.eligible(size)

    identical = identical and (
        [o.order_id for o in chosen_left]
        == [o.order_id for o in chosen_right]
    )

    # Opposite robot pairings on each side: an allocator's choice must
    # not move the queue.
    claim(chosen_left, [f"robot_{n:03d}" for n in range(1, size + 1)])
    claim(
        chosen_right,
        [f"robot_{n:03d}" for n in reversed(range(1, size + 1))],
    )

check(
    identical
    and [o.order_id for o in left.pending()]
    == [o.order_id for o in right.pending()],
    "identical batches under opposite robot-order pairings",
)


def eligibility_across_a_run():
    """Drive the real engine and sample eligibility as time passes."""

    robot = build_fleet(config, warehouse)[0]

    plan = plan_zone_mission(
        warehouse,
        [
            "staging_001",
            "shelf_001",
            "dropoff_001",
            "shelf_072",
            "dropoff_040",
            "staging_001",
        ],
    )

    running = OrderQueue.from_csv(ORDERS_CSV, warehouse)

    engine = FleetSimulationEngine(
        warehouse=warehouse,
        resolution=config.resolution_m,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
    )

    engine.add_robot(robot)

    samples = []

    engine.on_tick = lambda sim_time, batch: samples.append(
        (
            sim_time,
            tuple(o.order_id for o in running.eligible(ROBOTS)),
        )
    )

    engine.assign_mission(robot.robot_id, plan)

    engine.run()

    return engine, samples


engine, samples = eligibility_across_a_run()

expected = tuple(f"O{n:04d}" for n in range(1, ROBOTS + 1))

print(f"  engine makespan  : {engine.sim_time:.4f} s")
print(f"  eligibility polls: {len(samples)}")
print()

check(
    len(samples) > 0
    and all(sample == expected for _, sample in samples),
    f"eligibility is clock-independent: the same {ROBOTS} orders at "
    f"every one of {len(samples)} ticks",
)


# ----------------------------------------------------------
if "--ros" in sys.argv:

    rule("D  SHARED MEMORY MATCHES THE LOADER")

    import rclpy
    from rclpy.node import Node

    from robot_interfaces.srv import GetOrderStates

    rclpy.init()

    probe = Node("smoke_orders_probe")

    client = probe.create_client(GetOrderStates, "/get_order_states")

    if not client.wait_for_service(timeout_sec=10.0):
        check(False, "/get_order_states is available")

    else:
        future = client.call_async(GetOrderStates.Request())

        rclpy.spin_until_future_complete(
            probe, future, timeout_sec=10.0
        )

        response = future.result()

        remote = list(response.orders) if response else []

        print(f"shared memory reports {len(remote)} order(s)")
        print()

        check(
            len(remote) == len(orders),
            f"shared memory holds {len(orders)} orders",
        )

        check(
            [o.order_id for o in remote]
            == [o.order_id for o in orders],
            "order ids and their FIFO sequence match the loader",
        )

        check(
            all(
                (
                    remote[i].dropoff_id == orders[i].dropoff_id
                    and list(remote[i].shelf_ids)
                    == list(orders[i].shelf_ids)
                    and list(remote[i].item_ids)
                    == [it.item_id for it in orders[i].items]
                    and list(remote[i].quantities)
                    == [it.quantity for it in orders[i].items]
                )
                for i in range(min(len(remote), len(orders)))
            ),
            "every field round-trips: dropoff, items, shelves, qty",
        )

        check(
            all(o.status == "PENDING" for o in remote),
            "shared memory reports every order PENDING",
        )

    probe.destroy_node()
    rclpy.shutdown()


rule("RESULT")

if fails:
    for item in fails:
        print(f"  FAILED: {item}")
    raise SystemExit(1)

print("ALL ORDER ASSERTIONS PASSED")
print()
