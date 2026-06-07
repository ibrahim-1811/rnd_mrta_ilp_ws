from math import inf

from mrta_core.grid import PathFeatures
from mrta_core.models import RobotState, RobotStatus, TaskState
from mrta_core.symbolic import PredicateThresholds, as_prolog_facts, robot_task_predicates


def test_robot_task_predicates_for_good_candidate():
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
        pickup_x=5,
        pickup_y=0,
        drop_x=0,
        drop_y=0,
        priority=3,
        release_time=0.0,
    )

    features = PathFeatures(
        reachable=True,
        path_distance=5.0,
        travel_time=5.0,
        congestion_cost=0.1,
        energy_cost=0.05,
    )

    predicates = robot_task_predicates(robot, task, features)

    assert "robot(r1)" in predicates
    assert "task(t1)" in predicates
    assert "idle(r1)" in predicates
    assert "battery_high(r1)" in predicates
    assert "low_workload(r1)" in predicates
    assert "high_priority(t1)" in predicates
    assert "reachable(r1,t1)" in predicates
    assert "short_path(r1,t1)" in predicates
    assert "low_congestion(r1,t1)" in predicates
    assert "battery_feasible(r1,t1)" in predicates


def test_robot_task_predicates_for_blocked_path():
    robot = RobotState(
        robot_id="r2",
        x=0,
        y=0,
        battery=0.8,
        status=RobotStatus.IDLE,
        workload=0,
    )

    task = TaskState(
        task_id="t2",
        pickup_x=5,
        pickup_y=0,
        drop_x=0,
        drop_y=0,
        priority=1,
        release_time=0.0,
    )

    features = PathFeatures(
        reachable=False,
        path_distance=inf,
        travel_time=inf,
        congestion_cost=inf,
        energy_cost=inf,
    )

    predicates = robot_task_predicates(robot, task, features)

    assert "blocked_path(r2,t2)" in predicates
    assert "reachable(r2,t2)" not in predicates
    assert "battery_feasible(r2,t2)" not in predicates


def test_battery_insufficient_predicate():
    robot = RobotState(
        robot_id="r3",
        x=0,
        y=0,
        battery=0.22,
        status=RobotStatus.IDLE,
        workload=0,
    )

    task = TaskState(
        task_id="t3",
        pickup_x=20,
        pickup_y=0,
        drop_x=0,
        drop_y=0,
        priority=2,
        release_time=0.0,
    )

    features = PathFeatures(
        reachable=True,
        path_distance=20.0,
        travel_time=20.0,
        congestion_cost=0.0,
        energy_cost=0.05,
    )

    predicates = robot_task_predicates(robot, task, features)

    assert "battery_insufficient(r3,t3)" in predicates


def test_custom_thresholds_change_path_category():
    robot = RobotState(
        robot_id="r4",
        x=0,
        y=0,
        battery=0.8,
        status=RobotStatus.IDLE,
        workload=0,
    )

    task = TaskState(
        task_id="t4",
        pickup_x=8,
        pickup_y=0,
        drop_x=0,
        drop_y=0,
        priority=1,
        release_time=0.0,
    )

    features = PathFeatures(
        reachable=True,
        path_distance=8.0,
        travel_time=8.0,
        congestion_cost=0.0,
        energy_cost=0.01,
    )

    thresholds = PredicateThresholds(short_path=5.0, long_path=15.0)

    predicates = robot_task_predicates(robot, task, features, thresholds)

    assert "medium_path(r4,t4)" in predicates


def test_as_prolog_facts_adds_periods():
    predicates = ["idle(r1)", "short_path(r1,t1)"]

    facts = as_prolog_facts(predicates)

    assert facts == "idle(r1).\nshort_path(r1,t1)."