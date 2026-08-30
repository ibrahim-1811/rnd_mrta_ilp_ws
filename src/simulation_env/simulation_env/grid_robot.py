"""Lightweight deterministic grid robot.

This robot has no physics and no Nav2.

It moves cell by cell through a precomputed grid path and holds
position for deterministic service durations.

The robot does not own the simulation clock. ``advance_cell`` performs
the state transition and ``move_duration`` says how long it takes;
deciding *when* it completes belongs to whoever owns the clock, which
for a fleet is FleetSimulationEngine. ``sim_time`` here is a reported
timestamp, written by the clock owner, not a private counter.

``step`` and ``serve`` remain as single-robot conveniences that do both
at once, for the one-robot executor. They must not be used to drive a
fleet: eighteen private clocks are not a warehouse time.
"""

from dataclasses import dataclass, field
from enum import Enum

from mrta_core.grid import Cell


class GridRobotStatus(str, Enum):
    """Execution state of the deterministic grid robot.

    Deliberately separate from :class:`mrta_core.models.RobotStatus`,
    which is the coarser planning view (IDLE / BUSY / FAILED) consumed
    by the symbolic predicate layer. These are the finer states the
    executor drives.
    """

    IDLE = "IDLE"
    MOVING = "MOVING"
    PICKING = "PICKING"
    DROPPING = "DROPPING"
    CHARGING = "CHARGING"
    WAITING = "WAITING"
    FAILED = "FAILED"


DEFAULT_SPEED_MPS = 1.5


@dataclass
class GridRobot:
    robot_id: str
    cell: Cell

    speed_mps: float = DEFAULT_SPEED_MPS
    battery_percent: float = 100.0

    status: GridRobotStatus = GridRobotStatus.IDLE
    sim_time: float = 0.0

    path: list[Cell] = field(
        default_factory=list
    )

    path_index: int = 0

    distance_m: float = 0.0

    # Per-robot accounting. With a fleet these are the robot's own
    # totals, while sim_time is the shared warehouse clock.
    travel_time_s: float = 0.0
    service_time_s: float = 0.0

    def move_duration(self, resolution: float) -> float:
        """Simulated seconds for one grid edge. Pure."""
        return resolution / self.speed_mps

    def set_sim_time(self, sim_time: float) -> None:
        """Stamp the robot with the authoritative clock's time."""
        self.sim_time = sim_time

    def book_travel(self, duration_s: float) -> None:
        self.travel_time_s += duration_s

    def book_service(
        self,
        duration_s: float,
        status: "GridRobotStatus",
    ) -> None:
        """Account for a stationary service. No clock advance."""

        if duration_s < 0.0:
            raise ValueError(
                "service duration must not be negative"
            )

        self.status = status
        self.service_time_s += duration_s

    def advance_cell(self, resolution: float) -> Cell:
        """Move one grid edge. State transition only, no time.

        Raises if the edge is not a Manhattan step.
        """

        if self.finished:
            raise RuntimeError(
                f"{self.robot_id} has no edge left to advance"
            )

        current = self.path[self.path_index]
        next_cell = self.path[self.path_index + 1]

        if (
            abs(next_cell[0] - current[0])
            + abs(next_cell[1] - current[1])
        ) != 1:
            raise ValueError(
                f"non-Manhattan move {current} -> {next_cell}"
            )

        self.cell = next_cell
        self.path_index += 1
        self.distance_m += resolution
        self.status = GridRobotStatus.MOVING

        return self.cell

    def assign_path(
        self,
        path: list[Cell],
    ) -> None:

        if not path:
            raise ValueError(
                "path must not be empty"
            )

        if path[0] != self.cell:
            raise ValueError(
                f"path starts at {path[0]} "
                f"but robot is at {self.cell}"
            )

        self.path = list(path)
        self.path_index = 0

        if len(path) > 1:
            self.status = GridRobotStatus.MOVING
        else:
            self.status = GridRobotStatus.IDLE

    @property
    def goal_cell(self) -> Cell | None:

        if not self.path:
            return None

        return self.path[-1]

    @property
    def finished(self) -> bool:

        if not self.path:
            return True

        return (
            self.path_index
            >= len(self.path) - 1
        )

    # -- single-robot conveniences --------------------------------
    #
    # These advance the robot's own sim_time, which is only correct
    # when it is the sole robot. A fleet must be driven by
    # FleetSimulationEngine instead.

    def step(
        self,
        resolution: float,
    ) -> bool:
        """Move one grid edge and advance this robot's own clock."""

        if self.finished:
            return False

        duration = self.move_duration(resolution)

        self.advance_cell(resolution)

        self.book_travel(duration)

        self.sim_time += duration

        return True

    def serve(
        self,
        duration_s: float,
        status: GridRobotStatus,
    ) -> None:
        """Hold position and advance this robot's own clock."""

        self.book_service(duration_s, status)

        self.sim_time += duration_s
