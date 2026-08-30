from fractions import Fraction

import pytest

from simulation_env.fleet_playback import FleetPose, FleetTrajectoryPlayback
from simulation_env.gazebo_bridge import FleetPoseSink
from simulation_env.mapf import (
    MOVE,
    SERVICE,
    WAIT,
    PlanAction,
    Trajectory,
)


EDGE_TIME = Fraction(2, 3)


def pose_by_name(playback, sim_time):
    return {pose.name: pose for pose in playback.poses_at(sim_time)}


def test_fleet_poses_share_one_time_and_move_simultaneously():
    robot_001 = Trajectory(
        "robot_001",
        (0, 0),
        [
            PlanAction(MOVE, (1, 0), Fraction(0), EDGE_TIME),
            PlanAction(WAIT, (1, 0), EDGE_TIME, 2 * EDGE_TIME),
            PlanAction(
                SERVICE,
                (1, 0),
                2 * EDGE_TIME,
                2 * EDGE_TIME + 2,
                zone="shelf",
            ),
        ],
    )
    robot_002 = Trajectory(
        "robot_002",
        (0, 1),
        [PlanAction(MOVE, (0, 2), Fraction(0), EDGE_TIME)],
    )
    snapshot = {
        trajectory.robot_id: tuple(trajectory.actions)
        for trajectory in (robot_001, robot_002)
    }
    playback = FleetTrajectoryPlayback(
        {
            "robot_001": robot_001,
            "robot_002": robot_002,
        },
        resolution=1.0,
        z=0.155,
        frame_hz=20.0,
    )

    middle = pose_by_name(playback, float(EDGE_TIME / 2))
    assert middle["robot_001"].x == pytest.approx(1.0)
    assert middle["robot_001"].y == pytest.approx(0.5)
    assert middle["robot_002"].x == pytest.approx(0.5)
    assert middle["robot_002"].y == pytest.approx(2.0)

    # WAIT and shelf SERVICE (PICKING) remain exactly at the cell centre.
    waiting = pose_by_name(playback, float(EDGE_TIME + EDGE_TIME / 2))
    picking = pose_by_name(playback, float(2 * EDGE_TIME + 1))
    assert (waiting["robot_001"].x, waiting["robot_001"].y) == (1.5, 0.5)
    assert (picking["robot_001"].x, picking["robot_001"].y) == (1.5, 0.5)

    times = playback.sample_times(0.0, 1.0, wall_seconds=1.0)
    assert len(times) == 20
    assert times[-1] == 1.0
    assert all(
        len(playback.poses_at(sim_time)) == 2 for sim_time in times
    )

    # Playback copied the tracks and has no route back into planner state.
    assert tuple(robot_001.actions) == snapshot["robot_001"]
    assert tuple(robot_002.actions) == snapshot["robot_002"]


def test_transport_protocol_batches_the_whole_fleet_in_one_frame():
    line = FleetPoseSink._frame_line(
        7,
        (
            FleetPose("robot_001", 1.0, 2.0, 0.155),
            FleetPose("robot_002", 3.0, 4.0, 0.155),
        ),
    )

    assert line.count("FRAME") == 1
    assert line.startswith("FRAME 7 2 ")
    assert "robot_001 1.0 2.0 0.155 0 0 0 1" in line
    assert "robot_002 3.0 4.0 0.155 0 0 0 1" in line
    assert line.endswith("\n")
