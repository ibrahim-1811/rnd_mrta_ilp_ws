#!/usr/bin/env python3

"""Generate a deterministic order workload.

Writes an order CSV under the active schema::

    order_id,item_id,shelf_id,quantity,dropoff_id

1-3 shelf lines per order, drawn only from station ids the warehouse
map defines. Seeded, so rerunning it writes a byte-identical file and
the FIFO sequence never moves.

    # orders for the current map (the one config), into config/orders.csv
    python3 src/simulation_env/scripts/generate_orders.py --orders 2000

    # the V2 reference workload used by the tests
    python3 src/simulation_env/scripts/generate_orders.py \
        --config src/simulation_env/test/fixtures/v2_reference.yaml \
        --output src/simulation_env/test/fixtures/orders.csv

build_scenario.py --map runs this for you when you switch maps.

Regeneration is deliberate, not automatic: the committed CSV is the
reference workload every run and every strategy comparison shares.
"""

import argparse
import random
import sys
from pathlib import Path

# The real warehouse: shelf_001..shelf_072, dropoff_001..dropoff_036.
#
# The dropoff count follows the map. Widening the home bank on V2 took
# one cell off each of the four dropoff columns, so the pool went from
# 40 to 36. Keep this in step with the XLSX or the generated CSV names
# stations the map does not define and load_orders_csv rejects it.
SHELVES = tuple(f"shelf_{index:03d}" for index in range(1, 73))
DROPOFFS = tuple(f"dropoff_{index:03d}" for index in range(1, 37))

HEADER = "order_id,item_id,shelf_id,quantity,dropoff_id"

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "config" / "orders.csv"

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "warehouse.yaml"

SEED = 42
ORDERS = 100
MAX_LINES = 3
MAX_QUANTITY = 4


def stations_of(config_path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Shelf and drop-off ids, read from the map a config names.

    In the map's own numbering order, which for V2 is exactly SHELVES
    and DROPOFFS above, so a V2 workload generated either way is
    byte-identical.
    """

    root = Path(__file__).resolve().parents[3]

    for package in ("mrta_core", "simulation_env", "task_management"):
        if str(root / "src" / package) not in sys.path:
            sys.path.insert(0, str(root / "src" / package))

    from mrta_core.sim_config import resolve_map_path
    from mrta_core.warehouse_map import ZoneType
    from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx

    # Only the map: the order file this script is about to write need
    # not exist yet.
    map_path = resolve_map_path(config_path)
    warehouse = load_warehouse_xlsx(map_path)

    shelves = tuple(z.zone_id for z in warehouse.zones_of_type(ZoneType.SHELF))
    dropoffs = tuple(z.zone_id for z in warehouse.zones_of_type(ZoneType.DROPOFF))

    if not shelves or not dropoffs:
        raise SystemExit(
            f"{map_path}: the map has {len(shelves)} shelves and "
            f"{len(dropoffs)} drop-offs; an order needs at least one of each"
        )

    return shelves, dropoffs


def build(
    orders: int,
    seed: int,
    shelves: tuple[str, ...] = SHELVES,
    dropoffs: tuple[str, ...] = DROPOFFS,
) -> str:
    """The whole CSV as text. Same seed in, same bytes out."""

    rng = random.Random(seed)

    rows = [HEADER]

    sku = 0

    for number in range(1, orders + 1):
        order_id = f"O{number:04d}"

        # Distinct shelves within one order, so a single order never
        # visits the same rack twice.
        lines = rng.choice(range(1, min(MAX_LINES, len(shelves)) + 1))
        visited = rng.sample(shelves, lines)
        dropoff = rng.choice(dropoffs)

        for shelf in visited:
            sku += 1

            quantity = rng.randint(1, MAX_QUANTITY)

            rows.append(f"{order_id},SKU{sku:03d},{shelf},{quantity},{dropoff}")

    return "\n".join(rows) + "\n"


def main() -> None:

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--orders", type=int, default=ORDERS)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="read shelf and drop-off ids from this config's map "
        "(default: the one config, so the current map)",
    )

    args = parser.parse_args()

    shelves, dropoffs = stations_of(args.config)
    text = build(args.orders, args.seed, shelves, dropoffs)

    args.output.write_text(text)

    lines = len(text.strip().splitlines()) - 1

    print(
        f"{args.output}: {args.orders} logical orders, "
        f"{lines} line items, seed {args.seed}"
    )


if __name__ == "__main__":
    main()
