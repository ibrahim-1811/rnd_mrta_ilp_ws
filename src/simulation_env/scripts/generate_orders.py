#!/usr/bin/env python3

"""Generate the deterministic development workload.

Writes ``config/orders.csv`` under the active schema::

    order_id,item_id,shelf_id,quantity,dropoff_id

100 logical orders, 1-3 shelf lines each, drawn only from station ids
the warehouse map defines. Seeded, so rerunning it writes a
byte-identical file and the FIFO sequence never moves.

    python3 src/simulation_env/scripts/generate_orders.py

Regeneration is deliberate, not automatic: the committed CSV is the
reference workload every run and every strategy comparison shares.
"""

import argparse
import random
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

DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1] / "config" / "orders.csv"
)

SEED = 42
ORDERS = 100
MAX_LINES = 3
MAX_QUANTITY = 4


def build(orders: int, seed: int) -> str:
    """The whole CSV as text. Same seed in, same bytes out."""

    rng = random.Random(seed)

    rows = [HEADER]

    sku = 0

    for number in range(1, orders + 1):

        order_id = f"O{number:04d}"

        # Distinct shelves within one order, so a single order never
        # visits the same rack twice.
        lines = rng.choice(range(1, MAX_LINES + 1))
        shelves = rng.sample(SHELVES, lines)
        dropoff = rng.choice(DROPOFFS)

        for shelf in shelves:

            sku += 1

            quantity = rng.randint(1, MAX_QUANTITY)

            rows.append(
                f"{order_id},SKU{sku:03d},{shelf},"
                f"{quantity},{dropoff}"
            )

    return "\n".join(rows) + "\n"


def main() -> None:

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT
    )
    parser.add_argument("--orders", type=int, default=ORDERS)
    parser.add_argument("--seed", type=int, default=SEED)

    args = parser.parse_args()

    text = build(args.orders, args.seed)

    args.output.write_text(text)

    lines = len(text.strip().splitlines()) - 1

    print(
        f"{args.output}: {args.orders} logical orders, "
        f"{lines} line items, seed {args.seed}"
    )


if __name__ == "__main__":
    main()
