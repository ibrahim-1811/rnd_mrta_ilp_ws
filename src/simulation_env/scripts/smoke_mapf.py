#!/usr/bin/env python3

"""Validation of the prioritized MAPF traffic layer.

A: 18 robots crossing the warehouse, planned then executed.
B: a planned trajectory invalidated at runtime, forcing a replan.
"""

from fractions import Fraction
from time import perf_counter

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.fleet_engine import FleetSimulationEngine
from simulation_env.grid_robot import GridRobot, GridRobotStatus
from simulation_env.mapf import SERVICE, TimedReservations
from simulation_env.mapf_demo import make_replanner, plan_scenario
from simulation_env.mapf_planner import MapfMissionPlanner

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
    print("=" * 74)
    print(title)
    print("=" * 74)


def engine_for(robots):
    engine = FleetSimulationEngine(
        warehouse=warehouse,
        resolution=config.resolution_m,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
    )
    for robot in robots:
        engine.add_robot(robot)
    return engine


# ----------------------------------------------------------
rule("A  18-ROBOT CROSSING TRAFFIC")
# ----------------------------------------------------------

t0 = perf_counter()
by_id, homes, goals, missions, planner, plan = plan_scenario(
    config, warehouse
)
plan_wall = perf_counter() - t0

check(
    len(plan.trajectories) == 18 and not plan.failures,
    f"all 18 robots received timed paths "
    f"(failures: {plan.failures or 'none'})",
)

engine = engine_for(list(by_id.values()))

trace = []
engine.on_event = lambda e: trace.append(
    (e.time, e.robot_id, e.kind.value)
)

t0 = perf_counter()
deadlock = None
try:
    for robot_id in sorted(plan.trajectories):
        engine.assign_trajectory(robot_id, plan.trajectories[robot_id])
    engine.run()
except Exception as exc:            # noqa: BLE001 - report any failure
    deadlock = exc
run_wall = perf_counter() - t0

check(deadlock is None, f"no DeadlockError in the solvable demo ({deadlock})")

engine.reservations.close(engine.time)
problems = engine.reservations.audit()

check(not problems, f"runtime audit clean ({len(problems)} problems)")

reached = [
    rid for rid, traj in plan.trajectories.items()
    if by_id[rid].cell == traj.end_cell
]
check(len(reached) == 18, f"{len(reached)}/18 robots reached their target")

serviced = [
    rid for rid, traj in plan.trajectories.items()
    if any(a.kind == SERVICE for a in traj.actions)
]
check(
    len(serviced) == 18,
    f"{len(serviced)}/18 robots performed their shelf service",
)
check(
    all(
        abs(by_id[r].service_time_s - config.shelf_pick_s) < 1e-12
        for r in sorted(by_id)
    ),
    f"every robot serviced for exactly {config.shelf_pick_s}s",
)

planned_wait = sum(float(t.planned_wait) for t in plan.trajectories.values())
actual_wait = sum(r.waiting_time_s for r in by_id.values())
check(planned_wait > 0, f"planned waits occurred ({planned_wait:.4f}s)")
check(
    abs(planned_wait - actual_wait) < 1e-9,
    f"planned waiting matches actual ({planned_wait:.4f} vs "
    f"{actual_wait:.4f}s)",
)
check(
    all(r.replan_count == 0 for r in by_id.values()),
    "no replan needed: the prioritized plan held",
)

# determinism
again = plan_scenario(config, warehouse)[5]
check(
    all(
        [(a.kind, a.cell, a.start, a.end)
         for a in plan.trajectories[r].actions]
        == [(a.kind, a.cell, a.start, a.end)
            for a in again.trajectories[r].actions]
        for r in sorted(plan.trajectories)
    ),
    "identical runs produce identical plans",
)

engine2 = engine_for(
    list(plan_scenario(config, warehouse)[0].values())
)
trace2 = []
engine2.on_event = lambda e: trace2.append(
    (e.time, e.robot_id, e.kind.value)
)
for robot_id in sorted(again.trajectories):
    engine2.assign_trajectory(robot_id, again.trajectories[robot_id])
engine2.run()
check(trace == trace2, f"identical traces ({len(trace)} events)")

# ----------------------------------------------------------
rule("B  REPLAN AFTER THE ENVIRONMENT CHANGES")
# ----------------------------------------------------------

mover = GridRobot(robot_id="mover", cell=(1, 13), speed_mps=config.speed_mps)
blocker = GridRobot(
    robot_id="blocker", cell=(5, 16), speed_mps=config.speed_mps
)

solo = MapfMissionPlanner(
    warehouse=warehouse,
    resolution=config.resolution_m,
    speed_mps=config.speed_mps,
    service_seconds={
        ZoneType.SHELF: config.shelf_pick_s,
        ZoneType.DROPOFF: config.dropoff_s,
    },
)

goal = (1, 20)

# Plan alone: the planner knows nothing about the blocker.
lonely = solo.search.plan(
    robot_id="mover",
    start_cell=mover.cell,
    goal_cell=goal,
    start_time=Fraction(0),
    grid=solo._grid(mover.cell, goal),
    reservations=TimedReservations(),
    goal_hold_steps=None,
)

engine3 = engine_for([mover, blocker])
engine3.replanner = make_replanner(
    solo, engine3, {"mover": goal, "blocker": (5, 16)}
)

# Drop the blocker onto the planned route after planning.
blocked_cell = lonely.actions[2].cell
engine3.reservations.release(blocker.cell, "blocker", Fraction(0))
blocker.cell = blocked_cell
engine3.reservations.occupy(blocked_cell, "blocker", Fraction(0))

engine3.assign_trajectory("mover", lonely)
engine3.run()
engine3.reservations.close(engine3.time)

check(
    mover.replan_count > 0,
    f"stale trajectory triggered a replan "
    f"(count={mover.replan_count})",
)
check(mover.cell == goal, f"replanned robot still reached {goal}")
check(
    not engine3.reservations.audit(),
    "replanned execution is conflict free",
)
check(
    blocker.cell == blocked_cell and blocker.distance_m == 0.0,
    "the blocking robot never moved",
)

# ----------------------------------------------------------
rule("MAPF SUMMARY")
# ----------------------------------------------------------

total_distance = sum(r.distance_m for r in by_id.values())
static_distance = sum(r.static_distance_m for r in by_id.values())

print(f"robots                     : {len(by_id)}")
print(f"makespan                   : {float(plan.makespan):.4f} s")
print(f"total distance             : {total_distance:.1f} m")
print(f"static shortest-path total : {static_distance:.1f} m")
print(
    f"traffic overhead           : "
    f"{total_distance - static_distance:.1f} m"
)
print(f"total planned waiting      : {planned_wait:.4f} s")
print(f"total actual waiting       : {actual_wait:.4f} s")
print(
    f"reservation conflicts      : "
    f"{sum(r.vertex_conflict_count + r.edge_conflict_count for r in by_id.values())}"
)
print(f"space-time expansions      : {plan.expansions}")
print(f"replans                    : {sum(r.replan_count for r in by_id.values())}")
print(f"MAPF planning runtime      : {plan_wall:.3f} s")
print(f"execution runtime          : {run_wall:.3f} s")
print()
print(
    f"{'robot':<11}{'start':<14}{'goal':<13}{'dist':>7}"
    f"{'wait':>9}{'done':>10}"
)
for robot_id in sorted(by_id):
    robot = by_id[robot_id]
    done = engine.completion_time(robot_id)
    print(
        f"{robot_id:<11}{homes[robot_id]:<14}{goals[robot_id]:<13}"
        f"{robot.distance_m:>6.1f}m{robot.waiting_time_s:>8.3f}s"
        f"{done:>9.3f}s"
    )

rule("RESULT")
if fails:
    for item in fails:
        print(f"  FAILED: {item}")
    raise SystemExit(1)
print("ALL MAPF ASSERTIONS PASSED")
print()
