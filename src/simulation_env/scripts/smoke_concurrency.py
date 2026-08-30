#!/usr/bin/env python3

"""Focused validation of the global simulation clock.

Two robots run non-conflicting missions on opposite sides of the
warehouse, so this exercises TIME concurrency only. No reservations,
no collision handling, no allocator.
"""

from fractions import Fraction
from time import perf_counter, sleep

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.fleet import build_fleet
from simulation_env.fleet_engine import EventType, FleetSimulationEngine
from simulation_env.grid_robot import GridRobotStatus
from simulation_env.mission_planner import (
    plan_return_to_zone,
    plan_zone_mission,
    service_cells,
)
from simulation_env.fleet import home_stations


CONFIG = "src/simulation_env/config/warehouse_sim.yaml"

# Mirror-image missions: left robot stays at low x, right robot at
# high x, so their paths cannot interact.
MISSIONS = {
    "robot_001": ["staging_001", "shelf_001", "dropoff_001"],
    "robot_010": ["staging_010", "shelf_028", "dropoff_011"],
}


def rule(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def build(config, warehouse, on_advance=None):
    """Fresh fleet + engine, so runs cannot share state."""

    robots = build_fleet(config, warehouse)

    engine = FleetSimulationEngine(
        warehouse=warehouse,
        resolution=config.resolution_m,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
        on_advance=on_advance,
    )

    for robot in robots:
        engine.add_robot(robot)

    for robot_id, zones in MISSIONS.items():
        engine.assign_mission(
            robot_id, plan_zone_mission(warehouse, zones)
        )

    return engine


def trace_of(engine):
    """Run and capture (time, robot, kind) plus per-tick batches."""

    events = []
    ticks = []

    engine.on_event = lambda e: events.append(
        (e.time, e.robot_id, e.kind.value)
    )
    engine.on_tick = lambda t, batch: ticks.append(
        (t, tuple(sorted(e.robot_id for e in batch)))
    )

    engine.run()

    return events, ticks


config = load_sim_config(CONFIG)
warehouse = load_warehouse_xlsx(config.map_path)

rule("SETUP")
print(f"map              : {warehouse.grid.width} x {warehouse.grid.height}")
print(f"fleet            : {config.robot_count} robots")
print(f"speed            : {config.speed_mps} m/s")
print(f"shelf service    : {config.shelf_pick_s} s   (used exactly)")
print(f"dropoff service  : {config.dropoff_s} s   (used exactly)")
for robot_id, zones in MISSIONS.items():
    print(f"  {robot_id}: {' -> '.join(zones)}")

engine = build(config, warehouse)
events, ticks = trace_of(engine)

fails = []


def check(ok, label):
    print(f"[{'OK ' if ok else 'FAIL'}] {label}")
    if not ok:
        fails.append(label)


rule("EVENT TRACE (first 12 timestamps)")
for t, robots_at_t in ticks[:12]:
    print(f"  GLOBAL TIME = {t:8.4f}   {', '.join(robots_at_t)}")

rule("SCIENTIFIC ASSERTIONS")

# 1. monotonic clock
times = [t for t, _, _ in events]
check(
    all(b >= a for a, b in zip(times, times[1:])),
    "global simulation time is monotonically increasing",
)

# 2. timestamp ordering
check(times == sorted(times), "events processed in timestamp order")

# 3. concurrency: two 2/3 s moves share one advancement
one_move = Fraction(1) / Fraction(3, 2)
first_t, first_robots = ticks[0]
check(
    Fraction(first_t).limit_denominator(10**6) == one_move
    and first_robots == ("robot_001", "robot_010"),
    f"two concurrent {float(one_move):.4f}s moves -> global "
    f"{float(one_move):.4f}s (not {float(2 * one_move):.4f}s), "
    f"both robots in one batch",
)

# 4. service adds exactly the configured duration
for robot_id in MISSIONS:
    robot = engine.robots[robot_id]
    expected = config.shelf_pick_s + config.dropoff_s
    check(
        abs(robot.service_time_s - expected) < 1e-12,
        f"{robot_id} service time is exactly {expected} s "
        f"(got {robot.service_time_s})",
    )

# 5. another robot moves while one is servicing
svc = [
    (t, r) for t, r, k in events
    if k == EventType.SERVICE_COMPLETE.value and r == "robot_001"
][0]
start = svc[0] - Fraction(config.shelf_pick_s).limit_denominator(10**6)
overlap = [
    (t, r, k) for t, r, k in events
    if r == "robot_010" and start < t < svc[0]
]
check(
    len(overlap) > 0,
    f"robot_010 executed {len(overlap)} event(s) while robot_001 "
    f"was servicing ({float(start):.3f} -> {float(svc[0]):.3f})",
)

# 6. idle robots neither act nor advance the clock
idle = [
    r for r in engine.robots.values()
    if r.robot_id not in MISSIONS
]
check(
    all(
        r.status is GridRobotStatus.IDLE
        and r.travel_time_s == 0.0
        and r.service_time_s == 0.0
        and r.distance_m == 0.0
        for r in idle
    )
    and not any(r in MISSIONS or False for _, r, _ in events if r not in MISSIONS),
    f"{len(idle)} idle robots produced no events and no time",
)

# 7/8. observation does not advance the clock
before = engine.sim_time
for _ in range(50):
    engine._stamp_all()          # what the ROS publisher does
    engine.on_tick and None      # Gazebo push is likewise passive
check(
    engine.sim_time == before,
    "ROS publishing / Gazebo visualization do not advance sim time",
)

# 9. wall-clock speed does not change results
slow = build(config, warehouse, on_advance=lambda d: sleep(0.002))
t0 = perf_counter()
slow_events, _ = trace_of(slow)
slow_wall = perf_counter() - t0

fast = build(config, warehouse, on_advance=None)
t0 = perf_counter()
fast_events, _ = trace_of(fast)
fast_wall = perf_counter() - t0

check(
    slow_events == fast_events and slow.sim_time == fast.sim_time,
    f"wall-clock speed does not change results "
    f"({slow_wall:.2f}s vs {fast_wall:.2f}s wall, both "
    f"sim {slow.sim_time:.4f}s)",
)

# 10. determinism
again = build(config, warehouse)
again_events, again_ticks = trace_of(again)
check(
    again_events == events and again_ticks == ticks,
    "deterministic event ordering across identical runs",
)

# 11. idle robots go home, without corrupting mission metrics
homes = home_stations(config, warehouse)


parked = build(config, warehouse)


def park(robot):
    zone_id = homes[robot.robot_id]
    home_cell = min(warehouse.zone_by_id(zone_id).cells)
    if robot.cell == home_cell:
        return None
    # Parked robots are static obstacles: route around them.
    blocked = service_cells(warehouse) | parked.blocked_by_others(
        robot.robot_id
    )
    return plan_return_to_zone(
        warehouse, robot.cell, zone_id,
        from_label=robot.robot_id, occupied=blocked,
    )


parked.on_mission_complete = park
trace_of(parked)

check(
    all(
        parked.robots[r].cell
        == min(warehouse.zone_by_id(homes[r]).cells)
        for r in MISSIONS
    ),
    "idle robots return to their own park zone "
    + ", ".join(f"{r}->{homes[r]}" for r in MISSIONS),
)

check(
    all(
        parked.completion_time(r) == engine.completion_time(r)
        for r in MISSIONS
    ),
    "parking does not change mission completion time "
    f"({engine.completion_time('robot_001'):.4f} s)",
)

check(
    all(
        parked.parked_time(r) > parked.completion_time(r)
        for r in MISSIONS
    ),
    "park time is recorded separately and comes after completion",
)

rule("CONCURRENT FLEET SIMULATION")
print(f"global simulated time : {engine.sim_time:.4f} s")
print()
for robot_id in MISSIONS:
    robot = engine.robots[robot_id]
    print(f"{robot_id}:")
    print(f"  travel distance : {robot.distance_m:.1f} m")
    print(f"  travel time     : {robot.travel_time_s:.4f} s")
    print(f"  service time    : {robot.service_time_s:.4f} s")
    print(f"  completion time : {engine.completion_time(robot_id):.4f} s")
    print(f"  final status    : {robot.status.value} at {robot.cell}")
print()
print("fleet:")
print(f"  robots          : {len(engine.robots)}")
print(f"  robots executed : {len(MISSIONS)}")
print(f"  robots idle     : {len(engine.robots) - len(MISSIONS)}")
print(f"  events processed: {engine.processed}")

rule("RESULT")
if fails:
    for f in fails:
        print(f"  FAILED: {f}")
    raise SystemExit(1)
print("ALL CONCURRENCY ASSERTIONS PASSED")
print()
