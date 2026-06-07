from dataclasses import dataclass
from math import isinf

from mrta_core.grid import PathFeatures
from mrta_core.models import RobotState, RobotStatus, TaskState


@dataclass(frozen=True)
class PredicateThresholds:
    battery_high: float = 0.70
    battery_low: float = 0.30

    short_path: float = 10.0
    long_path: float = 25.0

    low_congestion: float = 0.30
    high_congestion: float = 0.70

    low_workload: int = 1
    high_workload: int = 3

    min_battery_after_task: float = 0.20


def robot_state_predicates(robot: RobotState, thresholds: PredicateThresholds) -> list[str]:
    predicates: list[str] = []

    if robot.status == RobotStatus.IDLE:
        predicates.append(f"idle({robot.robot_id})")
    elif robot.status == RobotStatus.BUSY:
        predicates.append(f"busy({robot.robot_id})")
    elif robot.status == RobotStatus.FAILED:
        predicates.append(f"failed({robot.robot_id})")

    if robot.battery >= thresholds.battery_high:
        predicates.append(f"battery_high({robot.robot_id})")
    elif robot.battery >= thresholds.battery_low:
        predicates.append(f"battery_medium({robot.robot_id})")
    else:
        predicates.append(f"battery_low({robot.robot_id})")

    if robot.workload <= thresholds.low_workload:
        predicates.append(f"low_workload({robot.robot_id})")
    elif robot.workload < thresholds.high_workload:
        predicates.append(f"medium_workload({robot.robot_id})")
    else:
        predicates.append(f"high_workload({robot.robot_id})")

    return predicates


def task_state_predicates(task: TaskState) -> list[str]:
    predicates: list[str] = []

    if task.priority >= 3:
        predicates.append(f"high_priority({task.task_id})")
    elif task.priority == 2:
        predicates.append(f"medium_priority({task.task_id})")
    else:
        predicates.append(f"low_priority({task.task_id})")

    return predicates


def path_feature_predicates(
    robot: RobotState,
    task: TaskState,
    features: PathFeatures,
    thresholds: PredicateThresholds,
) -> list[str]:
    predicates: list[str] = []

    robot_id = robot.robot_id
    task_id = task.task_id

    if not features.reachable or isinf(features.path_distance):
        predicates.append(f"blocked_path({robot_id},{task_id})")
        return predicates

    predicates.append(f"reachable({robot_id},{task_id})")

    if features.path_distance < thresholds.short_path:
        predicates.append(f"short_path({robot_id},{task_id})")
    elif features.path_distance < thresholds.long_path:
        predicates.append(f"medium_path({robot_id},{task_id})")
    else:
        predicates.append(f"long_path({robot_id},{task_id})")

    if features.congestion_cost < thresholds.low_congestion:
        predicates.append(f"low_congestion({robot_id},{task_id})")
    elif features.congestion_cost < thresholds.high_congestion:
        predicates.append(f"medium_congestion({robot_id},{task_id})")
    else:
        predicates.append(f"high_congestion({robot_id},{task_id})")

    battery_after_task = robot.battery - features.energy_cost

    if battery_after_task >= thresholds.min_battery_after_task:
        predicates.append(f"battery_feasible({robot_id},{task_id})")
    else:
        predicates.append(f"battery_insufficient({robot_id},{task_id})")

    return predicates


def robot_task_predicates(
    robot: RobotState,
    task: TaskState,
    features: PathFeatures,
    thresholds: PredicateThresholds | None = None,
) -> list[str]:
    if thresholds is None:
        thresholds = PredicateThresholds()

    predicates: list[str] = []

    predicates.append(f"robot({robot.robot_id})")
    predicates.append(f"task({task.task_id})")

    predicates.extend(robot_state_predicates(robot, thresholds))
    predicates.extend(task_state_predicates(task))
    predicates.extend(path_feature_predicates(robot, task, features, thresholds))

    return predicates


def as_prolog_facts(predicates: list[str]) -> str:
    return "\n".join(f"{predicate}." for predicate in predicates)