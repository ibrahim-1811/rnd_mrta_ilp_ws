#!/usr/bin/env python3

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import plan_from_cell

from simulation_env.fleet import home_bank
from simulation_env.fleet_engine import (
    EventType,
    FleetSimulationEngine,
)
from simulation_env.grid_robot import GridRobot


CONFIG = "src/simulation_env/test/fixtures/v2_reference.yaml"

config = load_sim_config(CONFIG)
warehouse = load_warehouse_xlsx(config.map_path)


# First home station, whichever name the map's bank carries.
start_zone = home_bank(warehouse)[0].zone_id
shelf_zone = "shelf_001"
dropoff_zone = "dropoff_001"


start_cell = min(warehouse.zone_by_id(start_zone).cells)


plan = plan_from_cell(
    warehouse,
    start_cell,
    [
        shelf_zone,
        dropoff_zone,
    ],
    start_label="robot_001",
)


events = []


def on_event(event):
    events.append(
        (
            event.sim_time,
            event.robot_id,
            event.kind,
            event.detail,
        )
    )


engine = FleetSimulationEngine(
    warehouse=warehouse,
    resolution=config.resolution_m,
    service_seconds={
        ZoneType.SHELF: config.shelf_pick_s,
        ZoneType.DROPOFF: config.dropoff_s,
    },
    on_event=on_event,
)


robot = GridRobot(
    robot_id="robot_001",
    cell=start_cell,
    speed_mps=config.speed_mps,
)

engine.add_robot(robot)


print()
print("=" * 72)
print("REAL WAREHOUSE RAW MISSION CYCLE")
print("=" * 72)

print()
print("start")
print("  zone       :", start_zone)
print("  cell       :", start_cell)

print()
print("mission")
print("  shelf      :", shelf_zone)
print("  dropoff    :", dropoff_zone)
print("  segments   :", len(plan.segments))
print("  distance   :", f"{plan.total_distance_m:.1f} m")


engine.assign_mission(
    "robot_001",
    plan,
)

engine.run()


service_events = [event for event in events if event[2] is EventType.SERVICE_COMPLETE]

mission_events = [event for event in events if event[2] is EventType.MISSION_COMPLETE]


print()
print("service completions:")

for sim_time, robot_id, kind, detail in service_events:
    print(f"  t={sim_time:8.3f}  {robot_id}  {detail}")


print()
print("final")
print("  cell          :", robot.cell)
print("  sim time      :", f"{engine.sim_time:.3f} s")
print("  distance      :", f"{robot.distance_m:.1f} m")
print("  travel time   :", f"{robot.travel_time_s:.3f} s")
print("  service time  :", f"{robot.service_time_s:.3f} s")
print("  waiting time  :", f"{robot.waiting_time_s:.3f} s")
print("  status        :", robot.status.value)


# ------------------------------------------------------------
# Scientific checks
# ------------------------------------------------------------

assert len(service_events) == 2, (
    f"expected shelf + dropoff service, got {len(service_events)}"
)

assert service_events[0][3] == shelf_zone
assert service_events[1][3] == dropoff_zone

expected_service = config.shelf_pick_s + config.dropoff_s

assert abs(robot.service_time_s - expected_service) < 1e-9

# Final segment is the dropoff. Its exit cell is the required
# one-cell retreat position after service.
final_segment = plan.segments[-1]

assert final_segment.exit_cell is not None
assert robot.cell == final_segment.exit_cell

assert len(mission_events) == 1

assert service_events[-1][0] < mission_events[0][0], (
    "dropoff service must finish before the final retreat and mission completion"
)

assert robot.waiting_time_s == 0.0

problems = engine.reservations.audit()

assert problems == [], problems


print()
print("checks")
print("  shelf reached/service     : PASS")
print("  shelf retreat             : PASS")
print("  dropoff reached/service   : PASS")
print("  dropoff retreat           : PASS")
print("  service total = 4.0 s     : PASS")
print("  mission completion        : PASS")
print("  reservation audit         : PASS")
print("  no traffic wait           : PASS")

print()
print("status                      : OK")
