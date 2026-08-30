#!/usr/bin/env python3

"""Focused validation of time-indexed cell and edge reservations.

Three hand-built conflicts, each isolating one guarantee:

  A  two robots want the same aisle cell at the same instant
  B  a robot is blocked by another robot servicing a shelf
  C  a direct A->B / B->A swap

Paths are explicit so the conflicts are exact; the allocator and the
order pipeline play no part here.
"""

from fractions import Fraction

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.fleet_engine import (
    DeadlockError,
    FleetSimulationEngine,
)
from simulation_env.grid_robot import GridRobot, GridRobotStatus
from simulation_env.mission_planner import MissionPlan, MissionSegment


CONFIG = "src/simulation_env/config/warehouse_sim.yaml"

config = load_sim_config(CONFIG)
warehouse = load_warehouse_xlsx(config.map_path)

fails = []


def check(ok, label):
    print(f"[{'OK ' if ok else 'FAIL'}] {label}")
    if not ok:
        fails.append(label)


def rule(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def plan_from_path(path, to_zone, exit_cell=None):
    """A one-segment mission over an explicit path."""

    distance = (len(path) - 1) * config.resolution_m

    segment = MissionSegment(
        from_zone="start",
        to_zone=to_zone,
        start_cell=path[0],
        goal_cell=path[-1],
        path=tuple(path),
        distance_m=distance,
        cost=distance,
        exit_cell=exit_cell,
    )

    return MissionPlan(
        zones=("start", to_zone),
        segments=(segment,),
        path=tuple(path),
        total_distance_m=distance,
        total_cost=distance,
    )


def scenario(missions):
    """Build an engine with one robot per explicit path."""

    engine = FleetSimulationEngine(
        warehouse=warehouse,
        resolution=config.resolution_m,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
    )

    trace = []
    engine.on_event = lambda e: trace.append(
        (e.time, e.robot_id, e.kind.value)
    )

    for robot_id, (path, to_zone, exit_cell) in missions.items():
        robot = GridRobot(
            robot_id=robot_id,
            cell=path[0],
            speed_mps=config.speed_mps,
        )
        engine.add_robot(robot)

    for robot_id, (path, to_zone, exit_cell) in missions.items():
        engine.assign_mission(
            robot_id, plan_from_path(path, to_zone, exit_cell)
        )

    return engine, trace


MOVE = Fraction(1) / Fraction(3, 2)


# ----------------------------------------------------------
rule("A  VERTEX CONFLICT: two robots want (1,14) at t=0")
# ----------------------------------------------------------

engine, trace = scenario({
    "robot_a": ([(0, 14), (1, 14), (2, 14)], "aisle", None),
    "robot_b": ([(1, 13), (1, 14), (1, 15)], "aisle", None),
})
engine.run()
engine.reservations.close(engine.time)

a = engine.robots["robot_a"]
b = engine.robots["robot_b"]

for event in trace[:8]:
    print(f"  t={float(event[0]):7.4f}  {event[1]}  {event[2]}")

print(f"  robot_a waited {a.waiting_time_s:.4f}s, "
      f"robot_b waited {b.waiting_time_s:.4f}s")

check(
    b.waiting_time_s > 0 and a.waiting_time_s == 0.0,
    f"one robot waited, the other proceeded "
    f"(b waited {b.waiting_time_s:.4f}s)",
)
check(
    b.vertex_conflict_count > 0 and b.wait_event_count > 0,
    f"vertex conflict recorded "
    f"(count={b.vertex_conflict_count}, "
    f"waits={b.wait_event_count})",
)
check(
    b.distance_m == (len([(1, 13), (1, 14), (1, 15)]) - 1)
    * config.resolution_m,
    "waiting added no extra distance",
)
check(
    abs(b.travel_time_s - 2 * float(MOVE)) < 1e-12,
    f"waiting added no travel time "
    f"(travel {b.travel_time_s:.4f}s for 2 moves)",
)
check(not engine.reservations.audit(), "audit: no overlapping cell holds")


# ----------------------------------------------------------
rule("B  SERVICE BLOCK: robot_d waits out a 2.0 s pick on (4,20)")
# ----------------------------------------------------------

engine, trace = scenario({
    "robot_c": ([(4, 19), (4, 20)], "shelf_001", (4, 19)),
    "robot_d": ([(3, 20), (4, 20)], "shelf_001", (3, 20)),
})
engine.run()
engine.reservations.close(engine.time)

c = engine.robots["robot_c"]
d = engine.robots["robot_d"]

for event in trace:
    print(f"  t={float(event[0]):7.4f}  {event[1]}  {event[2]}")

print(f"  robot_d waited {d.waiting_time_s:.4f}s "
      f"(>= the {config.shelf_pick_s}s service)")

check(
    d.waiting_time_s >= config.shelf_pick_s,
    f"blocked robot waited at least the full service duration "
    f"({d.waiting_time_s:.4f}s >= {config.shelf_pick_s}s)",
)
check(
    c.service_time_s == config.shelf_pick_s
    and d.service_time_s == config.shelf_pick_s,
    "both robots still got exactly one full service",
)
check(not engine.reservations.audit(),
      "audit: service cell never doubly held")


# ----------------------------------------------------------
rule("C  EDGE SWAP: robot_e (1,13)->(1,14) vs robot_f (1,14)->(1,13)")
# ----------------------------------------------------------

engine, trace = scenario({
    "robot_e": ([(1, 13), (1, 14)], "aisle", None),
    "robot_f": ([(1, 14), (1, 13)], "aisle", None),
})

deadlocked = False
try:
    engine.run()
except DeadlockError as exc:
    deadlocked = True
    print(f"  DeadlockError: {exc}")

engine.reservations.close(engine.time)

e = engine.robots["robot_e"]
f = engine.robots["robot_f"]

check(deadlocked, "head-on swap refused, reported as deadlock")
check(
    e.cell == (1, 13) and f.cell == (1, 14),
    f"neither robot moved (e at {e.cell}, f at {f.cell})",
)
check(
    e.distance_m == 0.0 and f.distance_m == 0.0,
    "WAITING added no travel distance",
)
check(
    e.status is GridRobotStatus.WAITING
    and f.status is GridRobotStatus.WAITING,
    "both robots are WAITING",
)
check(
    not engine.reservations.audit(),
    "audit: no opposite edge traversal occurred",
)


# ----------------------------------------------------------
rule("D  DETERMINISM")
# ----------------------------------------------------------

runs = []
for _ in range(3):
    engine, trace = scenario({
        "robot_a": ([(0, 14), (1, 14), (2, 14)], "aisle", None),
        "robot_b": ([(1, 13), (1, 14), (1, 15)], "aisle", None),
    })
    engine.run()
    runs.append(tuple(trace))

check(
    runs[0] == runs[1] == runs[2],
    f"identical runs produce identical traces "
    f"({len(runs[0])} events each)",
)


rule("RESULT")
if fails:
    for item in fails:
        print(f"  FAILED: {item}")
    raise SystemExit(1)
print("ALL RESERVATION ASSERTIONS PASSED")
print()
