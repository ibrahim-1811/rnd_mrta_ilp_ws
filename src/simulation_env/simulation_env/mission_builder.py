"""Turn an order into a sequence of warehouse zones to visit.

The order says what to collect and where to drop it. It never carries
paths, charging stations or a robot id -- routing is the planner's job
and assignment happens at runtime.

Parking is deliberately not part of the order. A robot returning to its
start zone when idle is a runtime policy, driven by config.
"""

from mrta_core.orders import Order


def build_order_mission(
    order: Order,
    start_zone: str,
) -> list[str]:
    """Zones for one order: start, every shelf in row order, drop-off.

    No charging leg: charging belongs to a battery policy, not an order.
    """

    zones = [start_zone]

    for shelf_id in order.shelf_ids:

        # A repeated shelf would be a zero-length leg the planner
        # cannot turn into a path.
        if zones[-1] != shelf_id:
            zones.append(shelf_id)

    if zones[-1] != order.dropoff_id:
        zones.append(order.dropoff_id)

    if len(zones) < 2:
        raise ValueError(
            f"order {order.order_id} yields no travel"
        )

    return zones
