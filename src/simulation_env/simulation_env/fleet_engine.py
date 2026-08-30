"""Event-driven fleet simulation with one authoritative clock.

A robot must not own simulation time. With eighteen private clocks
``robot_001.sim_time = 20`` and ``robot_002.sim_time = 12`` describe no
warehouse state at all. This engine owns the single warehouse clock;
robots carry a reported timestamp written by it.

Time is kept as :class:`fractions.Fraction`, not float. One grid edge
at 1.5 m/s is exactly 2/3 s, so two robots that both start moving at
t=0 land on *identical* timestamps and are processed as one concurrent
batch. With floats they would differ in the last bits after enough
accumulation and silently stop being concurrent.

Concurrency rule: events sharing a timestamp advance the clock once.
Two 2/3 s moves starting together take the clock 0 -> 2/3, never 4/3.

Nothing here sleeps, publishes or talks to Gazebo. Callbacks let an
owner observe; observation never advances the clock.
"""

from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction
from heapq import heappop, heappush

from mrta_core.warehouse_map import WarehouseMap, ZoneType

from simulation_env.grid_robot import GridRobot, GridRobotStatus
from simulation_env.mission_planner import MissionPlan


class EventType(str, Enum):
    MOVE_COMPLETE = "MOVE_COMPLETE"
    SERVICE_COMPLETE = "SERVICE_COMPLETE"
    RETREAT_COMPLETE = "RETREAT_COMPLETE"
    MISSION_COMPLETE = "MISSION_COMPLETE"


SERVICE_STATUS = {
    ZoneType.SHELF: GridRobotStatus.PICKING,
    ZoneType.DROPOFF: GridRobotStatus.DROPPING,
}


@dataclass(frozen=True)
class Event:
    time: Fraction
    seq: int
    robot_id: str
    kind: EventType
    detail: str = ""

    @property
    def sim_time(self) -> float:
        return float(self.time)


@dataclass
class _Assignment:
    """Where a robot is within its mission."""

    plan: MissionPlan
    segment_index: int = 0
    retreating: bool = False
    completion_time: Fraction | None = None


class FleetSimulationEngine:
    """Advances a fleet through missions on one shared clock."""

    def __init__(
        self,
        warehouse: WarehouseMap,
        resolution: float,
        service_seconds: dict,
        on_advance=None,
        on_tick=None,
        on_event=None,
    ) -> None:

        self.warehouse = warehouse

        self.resolution = Fraction(resolution).limit_denominator(10**6)

        self.service_seconds = {
            zone_type: Fraction(seconds).limit_denominator(10**6)
            for zone_type, seconds in service_seconds.items()
        }

        # Called once per global time advancement, with the simulated
        # delta. Intended for visualization pacing only.
        self.on_advance = on_advance

        # Called once per processed timestamp, after all events at
        # that time have been applied.
        self.on_tick = on_tick

        # Called per individual event, for tracing.
        self.on_event = on_event

        self.time = Fraction(0)

        self.robots: dict[str, GridRobot] = {}
        self.assignments: dict[str, _Assignment] = {}

        self._queue: list = []
        self._seq = 0

        self.processed = 0

    # -- clock ------------------------------------------

    @property
    def sim_time(self) -> float:
        """Authoritative warehouse time, as a float for reporting."""
        return float(self.time)

    def _move_duration(self, robot: GridRobot) -> Fraction:
        speed = Fraction(robot.speed_mps).limit_denominator(10**6)
        return self.resolution / speed

    def _schedule(
        self,
        robot_id: str,
        kind: EventType,
        delay: Fraction,
        detail: str = "",
    ) -> Event:

        if delay < 0:
            raise ValueError("cannot schedule into the past")

        event = Event(
            time=self.time + delay,
            seq=self._seq,
            robot_id=robot_id,
            kind=kind,
            detail=detail,
        )

        self._seq += 1

        # (time, seq) is a total order, so ties never fall through to
        # comparing events and the ordering is reproducible.
        heappush(self._queue, (event.time, event.seq, event))

        return event

    # -- fleet ------------------------------------------

    def add_robot(self, robot: GridRobot) -> None:
        self.robots[robot.robot_id] = robot
        robot.set_sim_time(self.sim_time)

    def assign_mission(self, robot_id: str, plan: MissionPlan) -> None:

        robot = self.robots[robot_id]

        if plan.path[0] != robot.cell:
            raise ValueError(
                f"{robot_id} is at {robot.cell} but its mission "
                f"starts at {plan.path[0]}"
            )

        self.assignments[robot_id] = _Assignment(plan=plan)

        self._begin_segment(robot_id)

    # -- mission progression ----------------------------

    def _begin_segment(self, robot_id: str) -> None:

        assignment = self.assignments[robot_id]
        robot = self.robots[robot_id]

        if assignment.segment_index >= len(assignment.plan.segments):
            self._schedule(
                robot_id, EventType.MISSION_COMPLETE, Fraction(0)
            )
            return

        segment = assignment.plan.segments[assignment.segment_index]

        assignment.retreating = False

        robot.assign_path(list(segment.path))

        if robot.finished:
            self._arrive(robot_id)
        else:
            self._schedule(
                robot_id,
                EventType.MOVE_COMPLETE,
                self._move_duration(robot),
                detail=segment.to_zone,
            )

    def _arrive(self, robot_id: str) -> None:
        """Robot reached this segment's target station."""

        assignment = self.assignments[robot_id]
        robot = self.robots[robot_id]

        segment = assignment.plan.segments[assignment.segment_index]

        if segment.exit_cell is None:
            # Not a service station: straight on to the next leg.
            assignment.segment_index += 1
            self._begin_segment(robot_id)
            return

        zone_type = self.warehouse.zone_by_id(
            segment.to_zone
        ).zone_type

        robot.status = SERVICE_STATUS[zone_type]

        self._schedule(
            robot_id,
            EventType.SERVICE_COMPLETE,
            self.service_seconds[zone_type],
            detail=segment.to_zone,
        )

    def _handle(self, event: Event) -> None:

        robot = self.robots[event.robot_id]
        assignment = self.assignments.get(event.robot_id)

        if event.kind in (
            EventType.MOVE_COMPLETE,
            EventType.RETREAT_COMPLETE,
        ):
            duration = self._move_duration(robot)

            robot.advance_cell(float(self.resolution))
            robot.book_travel(float(duration))

            if event.kind is EventType.RETREAT_COMPLETE:
                assignment.segment_index += 1
                self._begin_segment(event.robot_id)

            elif robot.finished:
                self._arrive(event.robot_id)

            else:
                segment = assignment.plan.segments[
                    assignment.segment_index
                ]
                self._schedule(
                    event.robot_id,
                    EventType.MOVE_COMPLETE,
                    duration,
                    detail=segment.to_zone,
                )

        elif event.kind is EventType.SERVICE_COMPLETE:

            segment = assignment.plan.segments[
                assignment.segment_index
            ]

            zone_type = self.warehouse.zone_by_id(
                segment.to_zone
            ).zone_type

            robot.book_service(
                float(self.service_seconds[zone_type]),
                robot.status,
            )

            # Back out one cell into the aisle.
            assignment.retreating = True

            robot.assign_path([robot.cell, segment.exit_cell])

            self._schedule(
                event.robot_id,
                EventType.RETREAT_COMPLETE,
                self._move_duration(robot),
                detail=segment.to_zone,
            )

        elif event.kind is EventType.MISSION_COMPLETE:
            robot.status = GridRobotStatus.IDLE
            assignment.completion_time = self.time

    # -- driving ----------------------------------------

    def _stamp_all(self) -> None:
        """Every robot reports the one authoritative timestamp."""

        for robot in self.robots.values():
            robot.set_sim_time(self.sim_time)

    def step_once(self) -> list[Event]:
        """Process every event at the next timestamp. One advance."""

        if not self._queue:
            return []

        target = self._queue[0][0]

        if target < self.time:
            raise RuntimeError(
                f"clock would move backwards: "
                f"{float(target)} < {self.sim_time}"
            )

        delta = target - self.time

        self.time = target

        # One wall-clock pause for the whole advancement, never one
        # per robot that happens to act at this instant.
        if self.on_advance is not None and delta > 0:
            self.on_advance(float(delta))

        batch: list[Event] = []

        while self._queue and self._queue[0][0] == target:
            batch.append(heappop(self._queue)[2])

        for event in batch:
            self.processed += 1
            if self.on_event is not None:
                self.on_event(event)
            self._handle(event)

        self._stamp_all()

        if self.on_tick is not None:
            self.on_tick(self.sim_time, batch)

        return batch

    def run(self, max_events: int = 1_000_000) -> None:

        self._stamp_all()

        while self._queue:

            self.step_once()

            if self.processed > max_events:
                raise RuntimeError(
                    f"event budget exceeded ({max_events}); "
                    f"the simulation is probably looping"
                )

    # -- reporting --------------------------------------

    def completion_time(self, robot_id: str) -> float | None:
        assignment = self.assignments.get(robot_id)

        if assignment is None or assignment.completion_time is None:
            return None

        return float(assignment.completion_time)

    def busy_robots(self) -> list[str]:
        return [
            robot_id
            for robot_id, robot in self.robots.items()
            if robot.status is not GridRobotStatus.IDLE
        ]
