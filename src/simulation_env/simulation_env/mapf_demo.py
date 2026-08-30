"""Deterministic 18-robot MAPF stress scenario.

Every robot crosses to the far side of the warehouse: the nine left
robots target right-hand shelves and the nine right robots target
left-hand shelves. That forces head-on aisle contention, shared cells
and queueing at the shelf faces.

Temporary test goals only. No orders, no allocator.
"""

from fractions import Fraction

from mrta_core.warehouse_map import ZoneType

from simulation_env.fleet import build_fleet, home_stations
from simulation_env.mapf_planner import MapfMissionPlanner
from simulation_env.mission_planner import plan_zone_mission


def crossing_goals(warehouse, robots) -> dict:
    """Send each robot to a shelf on the opposite side.

    Shelf columns are mirrored about the warehouse centre, so pairing
    the left robots with the right-hand column (and vice versa)
    guarantees every path crosses the middle.
    """

    shelves = sorted(
        warehouse.zones_of_type(ZoneType.SHELF),
        key=lambda z: z.zone_id,
    )

    width = warehouse.grid.width

    left, right = [], []

    for zone in shelves:
        cell = next(iter(zone.cells))
        (left if cell[0] < width / 2 else right).append(zone.zone_id)

    goals = {}

    left_side, right_side = [], []

    for robot in robots:
        if robot.cell[0] < width / 2:
            left_side.append(robot.robot_id)
        else:
            right_side.append(robot.robot_id)

    for index, robot_id in enumerate(sorted(left_side)):
        goals[robot_id] = right[index % len(right)]

    for index, robot_id in enumerate(sorted(right_side)):
        goals[robot_id] = left[index % len(left)]

    return goals


def build_scenario(config, warehouse):
    """Fleet, missions and a planner, all from config."""

    robots = build_fleet(config, warehouse)

    homes = home_stations(config, warehouse)

    goals = crossing_goals(warehouse, robots)

    missions = {
        robot.robot_id: plan_zone_mission(
            warehouse,
            [homes[robot.robot_id], goals[robot.robot_id]],
        )
        for robot in robots
    }

    planner = MapfMissionPlanner(
        warehouse=warehouse,
        resolution=config.resolution_m,
        speed_mps=config.speed_mps,
        service_seconds={
            ZoneType.SHELF: config.shelf_pick_s,
            ZoneType.DROPOFF: config.dropoff_s,
        },
    )

    return robots, homes, goals, missions, planner


def plan_scenario(config, warehouse):
    """Plan the whole fleet once, prioritized."""

    robots, homes, goals, missions, planner = build_scenario(
        config, warehouse
    )

    by_id = {robot.robot_id: robot for robot in robots}

    fleet_plan = planner.plan_fleet(
        robots=by_id,
        missions=missions,
        start_time=Fraction(0),
        hold_forever=True,
    )

    # Free-running shortest path, for traffic-overhead comparison.
    for robot_id, mission in missions.items():
        by_id[robot_id].static_distance_m = mission.total_distance_m

    return by_id, homes, goals, missions, planner, fleet_plan


def make_replanner(planner, engine, goal_cells):
    """Replan a robot from where it is, now, around live occupancy.

    Used when the environment moved under a planned trajectory. This
    is a real replan, not a retry: the robot gets a fresh timed path.
    """

    from simulation_env.mapf import MapfFailure, TimedReservations

    def replan(robot, now):

        reservations = TimedReservations()

        for cell in engine.blocked_by_others(robot.robot_id):
            reservations.reserve_cell(cell, now, None, "occupied")

        goal = goal_cells[robot.robot_id]

        try:
            return planner.search.plan(
                robot_id=robot.robot_id,
                start_cell=robot.cell,
                goal_cell=goal,
                start_time=now,
                grid=planner._grid(robot.cell, goal),
                reservations=reservations,
                goal_hold_steps=None,
            )
        except MapfFailure:
            return None

    return replan
