from dataclasses import dataclass
from enum import Enum


class RobotStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    FAILED = "failed"


@dataclass(frozen=True)
class RobotState:
    robot_id: str
    x: int
    y: int
    battery: float
    status: RobotStatus
    workload: int = 0

    def is_available(self) -> bool:
        return self.status == RobotStatus.IDLE and self.battery > 0.2


@dataclass(frozen=True)
class TaskState:
    task_id: str
    pickup_x: int
    pickup_y: int
    drop_x: int
    drop_y: int
    priority: int
    release_time: float
    deadline: float | None = None
