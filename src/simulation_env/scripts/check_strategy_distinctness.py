#!/usr/bin/env python3

"""Strategy distinctness gate (docs/DECISIONS.md, "Strategy Distinctness Gate").

A new baseline is accepted only when it is shown distinct from every
already-accepted strategy. The measure is *pairwise decision
divergence*: the share of choice-bearing decisions at which two
strategies select a different pair set.

Comparing two independent runs would not measure that. After the first
decision where they differ the two fleets are in different states, so
their decision N is not the same decision and the comparison is
meaningless. This script therefore measures a **counterfactual**: one
strategy drives the run, and at every decision each rival is asked the
*same* question -- same free robots, same candidate window, same
positions, same SOC, same route cache -- and its answer is compared
against the one that was taken.

    python3 src/simulation_env/scripts/check_strategy_distinctness.py
    python3 src/simulation_env/scripts/check_strategy_distinctness.py --orders 40

A choice-bearing decision is one where robots x orders > 1. Forced
decisions are excluded from the denominator: no strategy deserves
credit for agreeing where there was nothing to decide, and counting
them would let the gate be passed by widening the candidate window.

Probing is read-only with respect to the simulation: the rivals' answers
are recorded and discarded, and only the driving strategy's assignment
is ever returned to the executor.
"""

import argparse
from dataclasses import replace
from itertools import permutations
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]

for package in ("mrta_core", "simulation_env", "task_management"):
    candidate = ROOT / "src" / package

    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from mrta_core.order_queue import OrderQueue  # noqa: E402
from mrta_core.orders import load_orders_csv  # noqa: E402
from mrta_core.sim_config import load_sim_config  # noqa: E402
from mrta_core.warehouse_map import ZoneType  # noqa: E402
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx  # noqa: E402
from simulation_env.fleet import build_fleet  # noqa: E402
from simulation_env.workload_executor import WorkloadExecutor  # noqa: E402
from task_management.allocation_strategy import (  # noqa: E402
    BATCH_STRATEGIES,
    get_batch_strategy,
)


CONFIG = "src/simulation_env/config/warehouse.yaml"

# The gate's engineering default, recorded 2026-10-22. Not a measured
# value; revisable once the first three strategies are measured.
THRESHOLD_PERCENT = 5.0


class ProbingAllocator:
    """Drives with one strategy and asks the rivals the same question.

    Deliberately not a BatchAllocationStrategy subclass. It is a
    measurement wrapper, and making it look like a strategy would invite
    someone to run an experiment through it.
    """

    def __init__(self, primary, rivals):
        self.primary = primary
        self.rivals = rivals

        self.name = primary.name

        # rival name -> [choice-bearing decisions, decisions it differed on]
        self.tally = {rival.name: [0, 0] for rival in rivals}

    def score(self, *args, **kwargs):
        return self.primary.score(*args, **kwargs)

    def assign(self, warehouse, robots, orders, fifo_position, cache=None, **kw):
        taken = self.primary.assign(
            warehouse, robots, orders, fifo_position, cache, **kw
        )

        # Forced decisions carry no information about a policy.
        if len(robots) * len(orders) <= 1:
            return taken

        chosen = {(a.robot_id, a.order_id) for a in taken}

        for rival in self.rivals:
            alternative = rival.assign(
                warehouse, robots, orders, fifo_position, cache, **kw
            )

            counts = self.tally[rival.name]
            counts[0] += 1

            if {(a.robot_id, a.order_id) for a in alternative} != chosen:
                counts[1] += 1

        return taken


def run(config, warehouse, strategy, rivals, orders, seed):
    queue = OrderQueue(load_orders_csv(config.orders_path, warehouse)[:orders])

    allocator = ProbingAllocator(
        get_batch_strategy(strategy, seed=seed),
        [get_batch_strategy(name, seed=seed) for name in rivals],
    )

    executor = WorkloadExecutor(
        warehouse=warehouse,
        robots=build_fleet(config, warehouse),
        queue=queue,
        allocator=allocator,
        resolution=config.resolution_m,
        speed_mps=config.speed_mps,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
        progress_interval_s=0,
        battery=config.battery,
        window=config.allocation_window,
        dispatch_quantum_s=config.dispatch_quantum_s,
        strategy_name=strategy,
    )

    executor.run()

    return allocator.tally, executor.totals(), len(queue)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--config", default=CONFIG)
    parser.add_argument("--orders", type=int, default=0)
    parser.add_argument(
        "--robots",
        type=int,
        default=0,
        help="fleet size (0 = the configured fleet.robot_count)",
    )
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--quantum",
        type=float,
        default=None,
        help="dispatch quantum in s (default: the config's), as run_workload.py",
    )
    parser.add_argument(
        "--strategy",
        action="append",
        default=[],
        help="repeatable; default: every registered strategy",
    )

    args = parser.parse_args()

    config = load_sim_config(args.config)

    if args.robots:
        config = replace(config, robot_count=args.robots)

    if args.quantum is not None:
        config = replace(config, dispatch_quantum_s=args.quantum)

    warehouse = load_warehouse_xlsx(config.map_path)

    seed = config.seed if args.seed is None else args.seed

    strategies = args.strategy or sorted(BATCH_STRATEGIES)

    orders = args.orders or len(load_orders_csv(config.orders_path, warehouse))

    print("=" * 70)
    print("STRATEGY DISTINCTNESS GATE")
    print("=" * 70)
    print(f"  scenario   : {orders} orders, {config.robot_count} robots, seed {seed}")
    print(f"  window     : {config.allocation_window}")
    print(f"  quantum    : {config.dispatch_quantum_s} s")
    print(f"  threshold  : {THRESHOLD_PERCENT:.1f} % of choice-bearing decisions")
    print()

    divergence = {}
    outcomes = {}

    for strategy in strategies:
        rivals = [name for name in strategies if name != strategy]

        tally, totals, count = run(config, warehouse, strategy, rivals, orders, seed)

        outcomes[strategy] = totals

        print(f"  driving with {strategy}")

        for rival, (seen, differed) in sorted(tally.items()):
            share = 100.0 * differed / seen if seen else 0.0

            divergence[(strategy, rival)] = share

            print(
                f"    vs {rival:<20} {differed:>4}/{seen:<4} "
                f"choice-bearing decisions differ  ({share:5.1f} %)"
            )

        print()

    print("=" * 70)
    print("OUTCOMES")
    print("=" * 70)
    print(f"  {'strategy':<20} {'makespan':>12} {'distance':>12} {'waiting':>12}")

    for strategy in strategies:
        totals = outcomes[strategy]

        print(
            f"  {strategy:<20} {totals['makespan_s']:>10.4f} s "
            f"{totals['distance_m']:>10.1f} m {totals['waiting_time_s']:>10.4f} s"
        )

    print()
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)

    failures = []

    for left, right in permutations(strategies, 2):
        share = divergence.get((left, right))

        if share is None:
            continue

        if share < THRESHOLD_PERCENT:
            failures.append((left, right, share))

    for left, right, share in failures:
        print(
            f"  FAIL  {right} differs from {left} on only {share:.1f} % "
            f"of choice-bearing decisions (threshold {THRESHOLD_PERCENT:.1f} %)"
        )

    if not failures:
        print("  every pair is distinct above the threshold, in both directions")

    print()
    print("status                   : " + ("FAIL" if failures else "OK"))

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
