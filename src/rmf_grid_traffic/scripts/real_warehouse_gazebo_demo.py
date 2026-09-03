#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass


from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import (
    best_path_to_zone,
    service_cells,
)

from simulation_env.fleet import (
    build_fleet,
    home_stations,
)

from simulation_env.fleet_playback import (
    FleetTrajectoryPlayback,
)

from simulation_env.gazebo_bridge import (
    DEFAULT_ROBOT_Z,
    FleetPoseSink,
    provider_count,
    read_robot_z,
)


DEFAULT_CONFIG = "src/simulation_env/config/warehouse_sim.yaml"

DT = 2.0 / 3.0


@dataclass(frozen=True)
class TimedAction:
    kind: str
    cell: tuple[int, int]
    start: float
    end: float


@dataclass(frozen=True)
class TimedTrajectory:
    robot_id: str
    start_cell: tuple[int, int]
    actions: tuple[TimedAction, ...]


def robot_number(robot_id: str) -> int:
    match = re.search(r"(\d+)$", robot_id)

    if match is None:
        raise ValueError(
            f"Cannot extract numeric ID from {robot_id!r}"
        )

    return int(match.group(1))


def find_robot_at_home(
    robots,
    homes,
    staging_name: str,
):
    for robot in robots:
        if homes[robot.robot_id] == staging_name:
            return robot

    raise RuntimeError(
        f"No robot assigned to {staging_name}"
    )


def right_of_way(
    robot_a_id: str,
    a_remaining: int,
    robot_b_id: str,
    b_remaining: int,
):
    """
    Temporary local traffic priority.

    Both robots are BUSY in this demo.

    1. Smaller remaining distance wins.
    2. Lower robot ID breaks an exact tie.
    """

    key_a = (
        a_remaining,
        robot_number(robot_a_id),
    )

    key_b = (
        b_remaining,
        robot_number(robot_b_id),
    )

    if key_a <= key_b:
        return "A"

    return "B"


def build_local_traffic_trajectories(
    robot_a_id: str,
    route_a,
    robot_b_id: str,
    route_b,
):
    route_a = [
        (int(x), int(y))
        for x, y in route_a
    ]

    route_b = [
        (int(x), int(y))
        for x, y in route_b
    ]

    a_index = 0
    b_index = 0

    a_actions = []
    b_actions = []

    a_wait_ticks = 0
    b_wait_ticks = 0

    conflicts = []

    tick = 0

    while (
        a_index < len(route_a) - 1
        or b_index < len(route_b) - 1
    ):

        start_t = tick * DT
        end_t = (tick + 1) * DT

        a_finished = (
            a_index >= len(route_a) - 1
        )

        b_finished = (
            b_index >= len(route_b) - 1
        )

        a_now = route_a[a_index]
        b_now = route_b[b_index]

        a_next = (
            a_now
            if a_finished
            else route_a[a_index + 1]
        )

        b_next = (
            b_now
            if b_finished
            else route_b[b_index + 1]
        )

        vertex_conflict = (
            not a_finished
            and not b_finished
            and a_next == b_next
        )

        edge_swap = (
            not a_finished
            and not b_finished
            and a_next == b_now
            and b_next == a_now
        )

        if edge_swap:
            raise RuntimeError(
                "This demo encountered an edge-swap conflict. "
                "We have not implemented local edge-swap yielding yet. "
                f"A={a_now}->{a_next}, "
                f"B={b_now}->{b_next}"
            )

        if vertex_conflict:

            a_remaining = (
                len(route_a) - 1 - a_index
            )

            b_remaining = (
                len(route_b) - 1 - b_index
            )

            winner = right_of_way(
                robot_a_id,
                a_remaining,
                robot_b_id,
                b_remaining,
            )

            if winner == "A":

                a_index += 1

                a_actions.append(
                    TimedAction(
                        kind="MOVE",
                        cell=route_a[a_index],
                        start=start_t,
                        end=end_t,
                    )
                )

                b_actions.append(
                    TimedAction(
                        kind="WAIT",
                        cell=b_now,
                        start=start_t,
                        end=end_t,
                    )
                )

                b_wait_ticks += 1

                conflicts.append(
                    (
                        start_t,
                        a_next,
                        robot_a_id,
                        robot_b_id,
                        a_remaining,
                        b_remaining,
                    )
                )

            else:

                b_index += 1

                b_actions.append(
                    TimedAction(
                        kind="MOVE",
                        cell=route_b[b_index],
                        start=start_t,
                        end=end_t,
                    )
                )

                a_actions.append(
                    TimedAction(
                        kind="WAIT",
                        cell=a_now,
                        start=start_t,
                        end=end_t,
                    )
                )

                a_wait_ticks += 1

                conflicts.append(
                    (
                        start_t,
                        a_next,
                        robot_b_id,
                        robot_a_id,
                        b_remaining,
                        a_remaining,
                    )
                )

        else:

            if not a_finished:
                a_index += 1

                a_actions.append(
                    TimedAction(
                        kind="MOVE",
                        cell=route_a[a_index],
                        start=start_t,
                        end=end_t,
                    )
                )

            if not b_finished:
                b_index += 1

                b_actions.append(
                    TimedAction(
                        kind="MOVE",
                        cell=route_b[b_index],
                        start=start_t,
                        end=end_t,
                    )
                )

        if (
            route_a[a_index]
            == route_b[b_index]
        ):
            raise RuntimeError(
                "Vertex collision generated by local traffic controller: "
                f"{route_a[a_index]}"
            )

        tick += 1

    trajectory_a = TimedTrajectory(
        robot_id=robot_a_id,
        start_cell=route_a[0],
        actions=tuple(a_actions),
    )

    trajectory_b = TimedTrajectory(
        robot_id=robot_b_id,
        start_cell=route_b[0],
        actions=tuple(b_actions),
    )

    return {
        robot_a_id: trajectory_a,
        robot_b_id: trajectory_b,
    }, {
        "ticks": tick,
        "makespan_s": tick * DT,
        "a_wait_s": a_wait_ticks * DT,
        "b_wait_s": b_wait_ticks * DT,
        "conflicts": conflicts,
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
    )

    parser.add_argument(
        "--visual-speed",
        type=float,
        default=1.0,
        help=(
            "Visualization speed multiplier. "
            "1.0 means scientific time equals wall time."
        ),
    )

    args = parser.parse_args()

    if args.visual_speed <= 0.0:
        raise ValueError(
            "--visual-speed must be positive"
        )

    config = load_sim_config(
        args.config
    )

    warehouse = load_warehouse_xlsx(
        config.map_path
    )

    robots = build_fleet(
        config,
        warehouse,
        config.speed_mps,
    )

    homes = home_stations(
        config,
        warehouse,
    )

    robot_a = find_robot_at_home(
        robots,
        homes,
        "staging_006",
    )

    robot_b = find_robot_at_home(
        robots,
        homes,
        "staging_008",
    )

    services = service_cells(
        warehouse
    )

    route_a_result = best_path_to_zone(
        warehouse,
        robot_a.cell,
        "shelf_001",
        services,
    )

    route_b_result = best_path_to_zone(
        warehouse,
        robot_b.cell,
        "shelf_010",
        services,
    )

    route_a = list(
        route_a_result.path
    )

    route_b = list(
        route_b_result.path
    )

    trajectories, result = (
        build_local_traffic_trajectories(
            robot_a.robot_id,
            route_a,
            robot_b.robot_id,
            route_b,
        )
    )

    print()
    print("=" * 72)
    print("REAL WAREHOUSE TWO-ROBOT GAZEBO DEMO")
    print("=" * 72)

    print()
    print(
        f"A: {robot_a.robot_id} "
        f"staging_006 -> shelf_001"
    )

    print(
        f"   start={route_a[0]}"
        f"  distance={len(route_a) - 1} m"
    )

    print()

    print(
        f"B: {robot_b.robot_id} "
        f"staging_008 -> shelf_010"
    )

    print(
        f"   start={route_b[0]}"
        f"  distance={len(route_b) - 1} m"
    )

    print()
    print("LOCAL TRAFFIC RESULT")
    print("-" * 72)

    print(
        f"makespan       : "
        f"{result['makespan_s']:.3f} s"
    )

    print(
        f"{robot_a.robot_id} wait : "
        f"{result['a_wait_s']:.3f} s"
    )

    print(
        f"{robot_b.robot_id} wait : "
        f"{result['b_wait_s']:.3f} s"
    )

    print(
        f"conflicts      : "
        f"{len(result['conflicts'])}"
    )

    for (
        conflict_time,
        cell,
        winner,
        loser,
        winner_remaining,
        loser_remaining,
    ) in result["conflicts"]:

        print()
        print(
            f"  t={conflict_time:.3f} s"
            f"  conflict={cell}"
        )

        print(
            f"  right-of-way={winner}"
            f" remaining={winner_remaining}"
        )

        print(
            f"  waits={loser}"
            f" remaining={loser_remaining}"
        )

    print()

    world = config.gazebo_world_name

    providers = provider_count(
        world,
        "set_pose_vector",
    )

    if providers == 0:
        raise RuntimeError(
            f"/world/{world}/set_pose_vector is not available. "
            "Start Gazebo first."
        )

    if providers > 1:
        raise RuntimeError(
            f"{providers} Gazebo servers advertise "
            f"/world/{world}/set_pose_vector. "
            "Leave exactly one Gazebo server running."
        )

    robot_z = read_robot_z(
        robot_a.robot_id,
        DEFAULT_ROBOT_Z,
    )

    playback = FleetTrajectoryPlayback(
        trajectories={},
        resolution=config.resolution_m,
        z=robot_z,
        frame_hz=20.0,
    )

    # Keep the entire 18-robot fleet visible.
    #
    # Only two robots will move in this experiment.
    for robot in robots:
        playback.place(
            robot.robot_id,
            robot.cell,
        )

    playback.set_trajectory(
        robot_a.robot_id,
        trajectories[robot_a.robot_id],
    )

    playback.set_trajectory(
        robot_b.robot_id,
        trajectories[robot_b.robot_id],
    )

    sink = FleetPoseSink(
        world
    )

    try:

        # Put every robot exactly at its authoritative
        # staging position before starting.
        sink.send(
            playback.poses_at(0.0)
        )

        time.sleep(0.5)

        print()
        print(
            "[OK] Initial fleet frame sent to Gazebo."
        )

        print()
        input(
            "Position the Gazebo camera, then press Enter to run..."
        )

        wall_seconds = (
            result["makespan_s"]
            / args.visual_speed
        )

        print()
        print(
            f"Playing {result['makespan_s']:.3f} s "
            f"of scientific motion over "
            f"{wall_seconds:.3f} s wall time."
        )

        playback.play_interval(
            start_sim=0.0,
            end_sim=result["makespan_s"],
            wall_seconds=wall_seconds,
            emit=lambda sim_time, poses: sink.send(poses),
        )

        # Ensure final pose has reached the asynchronous sink.
        sink.send(
            playback.poses_at(
                result["makespan_s"]
            )
        )

        time.sleep(0.5)

        print()
        print("=" * 72)
        print("GAZEBO DEMO COMPLETE")
        print("=" * 72)

        print(
            f"{robot_a.robot_id} final: "
            f"{route_a[-1]}"
        )

        print(
            f"{robot_b.robot_id} final: "
            f"{route_b[-1]}"
        )

        print(
            f"traffic waiting: "
            f"{result['a_wait_s'] + result['b_wait_s']:.3f} s"
        )

        print()
        print("status: OK")

    finally:
        sink.close()


if __name__ == "__main__":
    main()
