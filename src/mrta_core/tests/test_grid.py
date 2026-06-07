from math import inf

from mrta_core.grid import GridMap, astar_path, extract_path_features
from mrta_core.models import RobotState, RobotStatus, TaskState


def test_astar_finds_direct_path_without_obstacles():
    grid = GridMap(width=5, height=5)

    result = astar_path(grid, start=(0, 0), goal=(2, 0))

    assert result.found
    assert result.distance == 2.0
    assert result.path == [(0, 0), (1, 0), (2, 0)]


def test_astar_avoids_obstacle():
    grid = GridMap(
        width=5,
        height=5,
        obstacles=frozenset({(1, 0)}),
    )

    result = astar_path(grid, start=(0, 0), goal=(2, 0))

    assert result.found
    assert (1, 0) not in result.path
    assert result.distance > 2.0


def test_astar_reports_unreachable_goal():
    grid = GridMap(
        width=3,
        height=3,
        obstacles=frozenset({(1, 0), (0, 1)}),
    )

    result = astar_path(grid, start=(0, 0), goal=(2, 2))

    assert not result.found
    assert result.distance == inf
    assert result.path == []


def test_path_features_from_robot_to_task_pickup():
    grid = GridMap(width=10, height=10)

    robot = RobotState(
        robot_id="r1",
        x=0,
        y=0,
        battery=0.8,
        status=RobotStatus.IDLE,
        workload=0,
    )

    task = TaskState(
        task_id="t1",
        pickup_x=3,
        pickup_y=0,
        drop_x=0,
        drop_y=0,
        priority=1,
        release_time=0.0,
    )

    features = extract_path_features(
        grid=grid,
        robot=robot,
        task=task,
        robot_speed=1.0,
        energy_per_cell=0.01,
    )

    assert features.reachable
    assert features.path_distance == 3.0
    assert features.travel_time == 3.0
    assert features.energy_cost == 0.03


def test_congestion_increases_path_cost():
    grid = GridMap(
        width=5,
        height=5,
        congestion={(1, 0): 5.0},
    )

    result = astar_path(grid, start=(0, 0), goal=(2, 0))

    assert result.found
    assert result.total_cost >= result.distance