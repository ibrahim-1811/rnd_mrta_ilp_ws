#!/usr/bin/env python3

"""Local head-on detection, yielding and rejoining on the real map.

Since the warehouse core became fully one-way, two robots can no longer
meet head-on in an aisle: for any adjacent pair of transit cells only
one of the two directions is legal, so the opposing intent is never
planned in the first place.

The one place a head-on survives is a *service* edge. Shelf, dropoff
and staging connections stay bidirectional on purpose, because a robot
has to be able to drive in and reverse back out. So the residual case
is two robots contending for the same station: one arriving, one
leaving.

That is the case exercised here, at shelf cell (4,10):

    robot_001  (5,10) -> (4,10)   arriving to pick
    robot_002  (4,10) -> (5,10)   retreating after its pick

robot_001 has the shorter remaining distance and therefore right of
way, so robot_002 must step aside into the aisle on the far side of the
rack, (3,10), and let it in.
"""

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import (
    directional_blocked_edges,
    non_transit_cells,
)

from simulation_env.local_traffic_controller import (
    LocalTrafficController,
    MovementIntent,
    TrafficDecision,
)

from simulation_env.local_yield_selector import (
    select_local_yield_cell,
)


# Follow the configured map rather than pinning a version.
CONFIG = "src/simulation_env/config/warehouse_sim.yaml"

warehouse = load_warehouse_xlsx(
    load_sim_config(CONFIG).map_path
)
controller = LocalTrafficController()

blocked_edges = directional_blocked_edges(warehouse)
service_cells = non_transit_cells(warehouse)


SHELF = (4, 10)
NEAR_AISLE = (5, 10)
FAR_AISLE = (3, 10)


def resolve(label, intents, positions):
    print()
    print("=" * 72)
    print(label)
    print("=" * 72)

    occupied = {
        cell: robot_id
        for robot_id, cell in positions.items()
    }

    decisions = controller.resolve(
        intents,
        occupied_by=occupied,
    )

    for intent in intents:
        result = decisions[intent.robot_id]

        print(
            f"{intent.robot_id:10s} "
            f"{intent.source} -> {intent.target}   "
            f"{result.decision.value:14s}   "
            f"{result.reason}"
        )

    return decisions


print()
print("=" * 72)
print("REAL WAREHOUSE HEAD-ON LOCAL RESOLUTION")
print("=" * 72)


# ---------------------------------------------------------------------
# STEP 0
#
# State the invariant this scenario now rests on: the conflict is only
# constructible because one end of the edge is a service cell.
# ---------------------------------------------------------------------

assert SHELF in service_cells, "scenario expects a service cell"
assert NEAR_AISLE not in service_cells
assert FAR_AISLE not in service_cells

assert (SHELF, NEAR_AISLE) not in blocked_edges
assert (NEAR_AISLE, SHELF) not in blocked_edges

# ...and that no purely-core edge anywhere is still two-way.
core_head_on = [
    (source, target)
    for (source, target) in (
        (a, b)
        for a in (
            cell
            for cell in [
                (x, y)
                for y in range(warehouse.grid.height)
                for x in range(warehouse.grid.width)
            ]
            if cell not in warehouse.grid.obstacles
            and cell not in service_cells
        )
        for b in (
            (a[0] + 1, a[1]),
            (a[0], a[1] + 1),
        )
    )
    if target not in warehouse.grid.obstacles
    and target not in service_cells
    and warehouse.grid.grid_map().in_bounds(target)
    and (source, target) not in blocked_edges
    and (target, source) not in blocked_edges
]

assert not core_head_on, (
    f"core is not fully one-way: {core_head_on[:5]}"
)

print()
print("invariant: no two-way core edge remains")
print(f"  service edge under test: {NEAR_AISLE} <-> {SHELF}")


positions = {
    "robot_001": NEAR_AISLE,
    "robot_002": SHELF,
}

print()
print("initial:")
print(f"  robot_001 = {positions['robot_001']}  arriving")
print(f"  robot_002 = {positions['robot_002']}  on the shelf, leaving")


# =====================================================================
# STEP 1
#
# Direct head-on edge swap.
#
# robot_001 has right-of-way but cannot move into an occupied cell.
# robot_002 must yield first.
# =====================================================================

intents = [
    MovementIntent(
        robot_id="robot_001",
        source=NEAR_AISLE,
        target=SHELF,
        busy=True,
        remaining_distance=1,
    ),
    MovementIntent(
        robot_id="robot_002",
        source=SHELF,
        target=NEAR_AISLE,
        busy=True,
        remaining_distance=2,
    ),
]

decisions = resolve(
    "STEP 1: DETECT HEAD-ON CONFLICT",
    intents,
    positions,
)

assert (
    decisions["robot_001"].decision
    is TrafficDecision.WAIT
)

assert (
    decisions["robot_002"].decision
    is TrafficDecision.YIELD_REQUIRED
)


yield_cell = select_local_yield_cell(
    warehouse=warehouse,
    source=positions["robot_002"],
    intended_target=NEAR_AISLE,
    occupied=set(positions.values()),
    reserved_targets=frozenset(),
    forbidden={NEAR_AISLE},
)

if yield_cell is None:
    raise RuntimeError(
        "Expected one-cell local yield position but none was found."
    )

assert yield_cell == FAR_AISLE, yield_cell

print()
print(
    f"robot_002 selected yield cell: "
    f"{positions['robot_002']} -> {yield_cell}"
)

positions["robot_002"] = yield_cell

print()
print("after step 1:")
print(f"  robot_001 = {positions['robot_001']}  WAIT")
print(f"  robot_002 = {positions['robot_002']}  YIELD")


# =====================================================================
# STEP 2
#
# The shelf is clear. The right-of-way robot may enter it.
# =====================================================================

intents = [
    MovementIntent(
        robot_id="robot_001",
        source=positions["robot_001"],
        target=SHELF,
        busy=True,
        remaining_distance=1,
    ),
]

decisions = resolve(
    "STEP 2: RIGHT-OF-WAY ROBOT PASSES",
    intents,
    positions,
)

assert (
    decisions["robot_001"].decision
    is TrafficDecision.MOVE
)

positions["robot_001"] = SHELF

print()
print("after step 2:")
print(f"  robot_001 = {positions['robot_001']}  MOVE (picking)")
print(f"  robot_002 = {positions['robot_002']}  WAIT IN YIELD CELL")


# =====================================================================
# STEP 3
#
# Both robots move in the same batch:
#
#   robot_001 reverses out of the shelf into the aisle it came from
#   robot_002 resumes its own route northbound up x=3
#
# On a one-way map the yielder does not reverse back the way it came.
# It rejoins by continuing along a legal lane, which is why the yield
# cell only has to be somewhere it can still route home from.
# =====================================================================

intents = [
    MovementIntent(
        robot_id="robot_001",
        source=SHELF,
        target=NEAR_AISLE,
        busy=True,
        remaining_distance=2,
    ),
    MovementIntent(
        robot_id="robot_002",
        source=FAR_AISLE,
        target=(3, 11),
        busy=True,
        remaining_distance=1,
    ),
]

decisions = resolve(
    "STEP 3: YIELDER RESUMES, WINNER RETREATS",
    intents,
    positions,
)

assert (
    decisions["robot_001"].decision
    is TrafficDecision.MOVE
)

assert (
    decisions["robot_002"].decision
    is TrafficDecision.MOVE
)

positions["robot_001"] = NEAR_AISLE
positions["robot_002"] = (3, 11)

print()
print("after step 3:")
print(f"  robot_001 = {positions['robot_001']}  RETREATED")
print(f"  robot_002 = {positions['robot_002']}  RESUMED")


# =====================================================================
# STEP 4
#
# robot_001 continues south down the one-way aisle x=5.
# =====================================================================

intents = [
    MovementIntent(
        robot_id="robot_001",
        source=positions["robot_001"],
        target=(5, 9),
        busy=True,
        remaining_distance=1,
    ),
]

decisions = resolve(
    "STEP 4: ORIGINAL ROUTE CONTINUES",
    intents,
    positions,
)

assert (
    decisions["robot_001"].decision
    is TrafficDecision.MOVE
)

positions["robot_001"] = (5, 9)


# ---------------------------------------------------------------------
# Final safety checks
# ---------------------------------------------------------------------

assert positions["robot_001"] != positions["robot_002"]

assert (
    abs(yield_cell[0] - SHELF[0])
    + abs(yield_cell[1] - SHELF[1])
    == 1
)

# Every move this scenario performed had to be legal on the directed
# graph, not merely collision-free.
for source, target in (
    (SHELF, FAR_AISLE),
    (NEAR_AISLE, SHELF),
    (SHELF, NEAR_AISLE),
    (FAR_AISLE, (3, 11)),
    (NEAR_AISLE, (5, 9)),
):
    assert (source, target) not in blocked_edges, (
        f"{source} -> {target} violates the lane policy"
    )


print()
print("=" * 72)
print("FINAL RESULT")
print("=" * 72)

print()
print(f"robot_001 = {positions['robot_001']}")
print(f"robot_002 = {positions['robot_002']}")
print()
print("head-on detected      : PASS")
print("yield cell found      : PASS")
print("winner passed         : PASS")
print("yielder resumed       : PASS")
print("all moves lane-legal  : PASS")
