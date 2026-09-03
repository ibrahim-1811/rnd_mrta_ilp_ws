#!/usr/bin/env python3

"""Head-on prevention in the production fleet engine.

This check used to drive a head-on between two robots at (4,1) and
(5,1) and assert that the loser found a one-cell yield bay. That
scenario is no longer constructible: the warehouse core is fully
one-way, so for any adjacent pair of transit cells only one direction
is legal and A* can never hand two robots opposing intents on the same
edge.

The 100-order workload used to deadlock exactly here:

    robot_006  (10,15) -> (10,16)
    robot_013  (10,16) -> (10,15)

x=10 is a shelf column. It had no direction assigned inside the open
band, so both intents were legal, the two robots each waited for the
other, and every other robot queued up behind them. Sixteen more
robots were stalled behind that single pair.

So what is worth checking now is the property that replaced the yield:

  1. no pair of adjacent transit cells is legal in both directions
  2. the specific (10,15)/(10,16) edge is one-way
  3. two robots driven straight at each other through that band both
     finish, and every edge the engine executes is lane-legal
"""

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import (
    directional_blocked_edges,
    non_transit_cells,
    plan_from_cell,
)

from simulation_env.fleet import build_fleet, home_stations
from simulation_env.fleet_engine import FleetSimulationEngine


CONFIG = "src/simulation_env/config/warehouse_sim.yaml"

config = load_sim_config(CONFIG)
warehouse = load_warehouse_xlsx(config.map_path)
grid = warehouse.grid

blocked_edges = directional_blocked_edges(warehouse)
service_cells = non_transit_cells(warehouse)


print()
print("=" * 72)
print("PRODUCTION ENGINE HEAD-ON PREVENTION")
print("=" * 72)


# ------------------------------------------------------------
# 1. No transit edge is legal in both directions.
# ------------------------------------------------------------

def transit(cell):
    x, y = cell

    return (
        0 <= x < grid.width
        and 0 <= y < grid.height
        and cell not in grid.obstacles
        and cell not in service_cells
    )


two_way = []

for y in range(grid.height):
    for x in range(grid.width):

        source = (x, y)

        if not transit(source):
            continue

        for target in (
            (x + 1, y),
            (x, y + 1),
        ):

            if not transit(target):
                continue

            if (
                (source, target) not in blocked_edges
                and (target, source) not in blocked_edges
            ):
                two_way.append((source, target))


print()
print(f"two-way transit edges: {len(two_way)}")

assert not two_way, (
    f"core is not one-way, {len(two_way)} two-way edges "
    f"such as {two_way[:3]}"
)


# ------------------------------------------------------------
# 2. The edge that deadlocked the 100-order run.
# ------------------------------------------------------------

SOUTH = (10, 15)
NORTH = (10, 16)

northbound = (SOUTH, NORTH) not in blocked_edges
southbound = (NORTH, SOUTH) not in blocked_edges

print()
print(f"  {SOUTH} -> {NORTH}: {'legal' if northbound else 'blocked'}")
print(f"  {NORTH} -> {SOUTH}: {'legal' if southbound else 'blocked'}")

assert northbound != southbound, (
    "the (10,15)/(10,16) edge is still two-way"
)


# ------------------------------------------------------------
# 3. Drive robots at each other through that band.
# ------------------------------------------------------------

robots = build_fleet(config, warehouse)
homes = home_stations(config, warehouse)

robot_ids = sorted(robot.robot_id for robot in robots)

# One robot from each staging bank, sent to a shelf on the far side and
# then parked on the other's home, so their routes must cross the
# middle band in opposite senses.
left_id = robot_ids[0]
right_id = robot_ids[len(robot_ids) // 2]

pairs = [
    (left_id, "shelf_045", homes[right_id]),
    (right_id, "shelf_010", homes[left_id]),
]

engine = FleetSimulationEngine(
    warehouse=warehouse,
    resolution=1.0,
    service_seconds={
        ZoneType.SHELF: 2.0,
        ZoneType.DROPOFF: 2.0,
    },
)

missions = {}

for robot_id, shelf, park_zone in pairs:

    robot = next(
        candidate
        for candidate in robots
        if candidate.robot_id == robot_id
    )

    engine.add_robot(robot)

    missions[robot_id] = plan_from_cell(
        warehouse,
        robot.cell,
        [shelf, park_zone],
        start_label=robot_id,
    )

engine.assign_missions_batch(missions)


# Audit every edge the engine actually commits.
illegal = []

original_begin_move = engine.reservations.begin_move
original_begin_batch = engine.reservations.begin_moves_batch


def audited_begin_move(robot_id, source, target, now, duration):

    if (source, target) in blocked_edges:
        illegal.append((robot_id, source, target))

    return original_begin_move(
        robot_id, source, target, now, duration
    )


def audited_begin_batch(moves, now, duration):

    moves = tuple(moves)

    for robot_id, source, target in moves:
        if (source, target) in blocked_edges:
            illegal.append((robot_id, source, target))

    return original_begin_batch(moves, now, duration)


engine.reservations.begin_move = audited_begin_move
engine.reservations.begin_moves_batch = audited_begin_batch


while engine.step_once():
    if engine.time > 2000:
        raise RuntimeError(
            "engine did not settle; suspected deadlock"
        )


print()
print(f"simulated time      : {float(engine.time):.3f} s")

for robot_id, _shelf, _park in pairs:

    robot = engine.robots[robot_id]

    print(
        f"  {robot_id}: cell={robot.cell} "
        f"finished={robot.finished} "
        f"waits={robot.wait_event_count} "
        f"edge_conflicts={robot.edge_conflict_count}"
    )

print()
print(f"illegal edges executed: {len(illegal)}")
print(f"unfinished yields     : {len(engine._yielding)}")
print(f"unfinished waits      : {len(engine._waiting)}")


for robot_id, _shelf, _park in pairs:
    assert engine.robots[robot_id].finished, (
        f"{robot_id} did not finish its mission"
    )

assert not illegal, illegal
assert not engine._yielding, engine._yielding
assert not engine._waiting, engine._waiting

# No robot may record a head-on, because none is constructible.
for robot_id, _shelf, _park in pairs:
    assert engine.robots[robot_id].edge_conflict_count == 0, (
        f"{robot_id} recorded a head-on edge conflict"
    )


print()
print("core is fully one-way      : PASS")
print("(10,15)/(10,16) one-way    : PASS")
print("crossing missions complete : PASS")
print("all executed edges legal   : PASS")
print("no head-on conflicts       : PASS")
print("no stranded wait or yield  : PASS")

print()
print("status                     : OK")
