from mrta_core.models import RobotState, RobotStatus, TaskState


def test_idle_robot_with_enough_battery_is_available():
    robot = RobotState(
        robot_id="r1",
        x=0,
        y=0,
        battery=0.8,
        status=RobotStatus.IDLE,
        workload=0,
    )

    assert robot.is_available()


def test_busy_robot_is_not_available():
    robot = RobotState(
        robot_id="r2",
        x=1,
        y=1,
        battery=0.8,
        status=RobotStatus.BUSY,
        workload=1,
    )

    assert not robot.is_available()


def test_task_state_can_be_created():
    task = TaskState(
        task_id="t1",
        pickup_x=5,
        pickup_y=3,
        drop_x=0,
        drop_y=0,
        priority=2,
        release_time=0.0,
    )

    assert task.task_id == "t1"
    assert task.priority == 2
