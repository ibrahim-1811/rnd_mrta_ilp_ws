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

Events sharing a timestamp are applied in two phases: every robot
first releases what it is leaving, and only then does any robot claim
what it is entering. Without that split, a robot whose event happens
to be handled first sees a cell that a simultaneous robot is about to
vacate as still occupied, and reports a conflict that does not exist.

Every move is gated by a ReservationTable. A robot that cannot move
safely becomes WAITING, keeps its cell, and is retried whenever the
occupancy changes -- in sorted robot order, so arbitration between
simultaneous conflicts is reproducible.

Nothing here sleeps, publishes or talks to Gazebo. Callbacks let an
owner observe; observation never advances the clock.
"""

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from heapq import heappop, heappush

from mrta_core.warehouse_map import WarehouseMap, ZoneType

from simulation_env.execution_buffer import RollingExecutionBuffer
from simulation_env.grid_robot import GridRobot, GridRobotStatus
from mrta_core.warehouse_routing import MissionPlan
from simulation_env.mapf import MOVE, RETREAT, SERVICE, WAIT
from simulation_env.reservations import EDGE, VERTEX, ReservationTable


class EventType(str, Enum):
    MOVE_COMPLETE = "MOVE_COMPLETE"
    WAIT_COMPLETE = "WAIT_COMPLETE"
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
    # Parking runs are follow-up work, not the mission itself, so
    # they must not overwrite the mission's completion time.
    is_parking: bool = False


class DeadlockError(RuntimeError):
    """Every remaining robot is waiting for another. Nothing can move.

    Reservations prevent collisions; they do not resolve deadlock.
    Detecting it loudly beats silently ending a run early.
    """


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
        on_mission_complete=None,
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

        # Called when a robot finishes. Returning a MissionPlan sends
        # it straight on, which is how the idle-parking policy works.
        # Returning None leaves the robot idle where it stands. The
        # policy lives in the caller, never in the clock.
        self.on_mission_complete = on_mission_complete

        self.time = Fraction(0)

        self.robots: dict[str, GridRobot] = {}
        self.assignments: dict[str, _Assignment] = {}
        self.mission_completion: dict[str, Fraction] = {}
        self.park_completion: dict[str, Fraction] = {}

        self._queue: list = []
        self._seq = 0

        self.processed = 0

        self.reservations = ReservationTable()

        # robot_id -> (event kind it is waiting to schedule, since)
        self._waiting: dict[str, tuple] = {}

        # The complete timed trajectory remains owned by each rolling buffer;
        # only a six-cell delivery window is exposed to execution.
        self._trajectories: dict[str, RollingExecutionBuffer] = {}

        # Robots that finished an action this timestamp and must pick
        # their next one, once every release in the batch is applied.
        self._pending: list = []

        # Called when a planned move is refused at runtime. Must
        # return a fresh Trajectory from the robot's current cell and
        # the current time, or None to give up. Replanning, never a
        # blind retry loop.
        self.replanner = None

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
        self.reservations.occupy(robot.cell, robot.robot_id, self.time)

    def assign_mission(
        self,
        robot_id: str,
        plan: MissionPlan,
        is_parking: bool = False,
    ) -> None:

        robot = self.robots[robot_id]

        if plan.path[0] != robot.cell:
            raise ValueError(
                f"{robot_id} is at {robot.cell} but its mission "
                f"starts at {plan.path[0]}"
            )

        self.assignments[robot_id] = _Assignment(
            plan=plan, is_parking=is_parking
        )

        self._begin_segment(robot_id)

    # -- MAPF trajectory execution ----------------------

    ACTION_EVENT = {
        MOVE: EventType.MOVE_COMPLETE,
        RETREAT: EventType.RETREAT_COMPLETE,
        WAIT: EventType.WAIT_COMPLETE,
        SERVICE: EventType.SERVICE_COMPLETE,
    }

    def assign_trajectory(self, robot_id: str, trajectory) -> None:
        """Execute a pre-planned timed path instead of a raw mission."""

        robot = self.robots[robot_id]

        if trajectory.start_cell != robot.cell:
            raise ValueError(
                f"{robot_id} is at {robot.cell} but its trajectory "
                f"starts at {trajectory.start_cell}"
            )

        robot.planned_wait_time_s += float(trajectory.planned_wait)
        robot.space_time_expansions += trajectory.expansions

        self._trajectories[robot_id] = RollingExecutionBuffer(
            trajectory, now=self.time
        )

        self._start_action(robot_id)

    def _start_action(self, robot_id: str) -> None:

        robot = self.robots[robot_id]

        execution = self._trajectories[robot_id]
        action = execution.current_action

        if action is None:
            self._schedule(
                robot_id, EventType.MISSION_COMPLETE, Fraction(0)
            )
            return

        delay = action.end - self.time

        if delay < 0:
            delay = Fraction(0)

        if action.kind in (MOVE, RETREAT):

            conflict = self.reservations.check_move(
                robot_id, robot.cell, action.cell
            )

            if conflict is not None:
                self._replan(robot_id, conflict)
                return

            self.reservations.begin_move(
                robot_id,
                robot.cell,
                action.cell,
                self.time,
                delay,
            )

            robot.status = GridRobotStatus.MOVING

        elif action.kind == WAIT:
            # A planned wait: stationary, still holding its cell.
            robot.status = GridRobotStatus.WAITING

        elif action.kind == SERVICE:
            zone_type = self.warehouse.zone_by_id(
                action.zone
            ).zone_type
            robot.status = SERVICE_STATUS[zone_type]

        self._schedule(
            robot_id,
            self.ACTION_EVENT[action.kind],
            delay,
            detail=action.zone,
        )

    def _replan(self, robot_id: str, conflict: str) -> None:
        """The environment moved under a planned trajectory."""

        robot = self.robots[robot_id]

        if conflict == VERTEX:
            robot.vertex_conflict_count += 1
        elif conflict == EDGE:
            robot.edge_conflict_count += 1

        robot.replan_count += 1

        if self.replanner is None:
            raise RuntimeError(
                f"{robot_id} hit a {conflict} conflict at "
                f"{robot.cell} t={self.sim_time:.4f} and no "
                f"replanner is configured"
            )

        trajectory = self.replanner(robot, self.time)

        if trajectory is None:
            raise DeadlockError(
                f"{robot_id} could not be replanned from "
                f"{robot.cell} at t={self.sim_time:.4f}"
            )

        self._trajectories[robot_id] = RollingExecutionBuffer(
            trajectory, now=self.time
        )

        robot.space_time_expansions += trajectory.expansions
        robot.planned_wait_time_s += float(trajectory.planned_wait)

        self._start_action(robot_id)

    def _advance_trajectory(self, event: Event) -> None:
        """Apply one finished action. Claims happen later in the batch."""

        robot = self.robots[event.robot_id]

        execution = self._trajectories[event.robot_id]
        action = execution.current_action

        if action is None:
            raise RuntimeError(
                f"{event.robot_id} completed an action with an empty buffer"
            )

        duration = float(action.duration)

        if action.kind in (MOVE, RETREAT):
            source = robot.cell
            robot.move_to(action.cell, float(self.resolution))
            robot.book_travel(duration)
            self.reservations.complete_move(
                event.robot_id, source, robot.cell, self.time
            )

        elif action.kind == WAIT:
            robot.book_wait(duration)

        elif action.kind == SERVICE:
            robot.book_service(duration, robot.status)

        # Refill happens before phase two starts the next action.  It adds no
        # event or delay, so consecutive cell waypoints remain continuous
        # MOVING rather than becoming implicit stop commands.
        execution.complete_current(self.time)

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
            self._try_move(
                robot_id, EventType.MOVE_COMPLETE, segment.to_zone
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

    def _try_move(
        self,
        robot_id: str,
        kind: EventType,
        detail: str = "",
    ) -> bool:
        """Schedule one move if the reservation table allows it.

        Returns False if the robot must wait. A waiting robot keeps
        its cell, so it goes on blocking others, which is what makes
        the guarantees hold.
        """

        robot = self.robots[robot_id]

        source = robot.cell
        target = robot.path[robot.path_index + 1]

        conflict = self.reservations.check_move(
            robot_id, source, target
        )

        if conflict is not None:

            if conflict == VERTEX:
                robot.vertex_conflict_count += 1
            elif conflict == EDGE:
                robot.edge_conflict_count += 1

            if robot_id not in self._waiting:
                robot.wait_event_count += 1
                self._waiting[robot_id] = (kind, detail, self.time)

            robot.status = GridRobotStatus.WAITING

            return False

        # Clearing a wait: bill the stationary time, not travel.
        pending = self._waiting.pop(robot_id, None)

        if pending is not None:
            robot.book_wait(float(self.time - pending[2]))

        duration = self._move_duration(robot)

        self.reservations.begin_move(
            robot_id, source, target, self.time, duration
        )

        robot.status = GridRobotStatus.MOVING

        self._schedule(robot_id, kind, duration, detail=detail)

        return True

    def _retry_waiting(self) -> None:
        """Wake blocked robots in a stable order.

        Sorted by robot_id so that when several robots are freed by
        the same event, the winner is always the same.
        """

        while True:

            blocked = sorted(self._waiting)

            if not blocked:
                return

            progressed = False

            for robot_id in blocked:

                if robot_id not in self._waiting:
                    continue

                kind, detail, _ = self._waiting[robot_id]

                if self._try_move(robot_id, kind, detail):
                    progressed = True

            if not progressed:
                return

    def _handle(self, event: Event) -> None:

        robot = self.robots[event.robot_id]

        if event.robot_id in self._trajectories:

            if event.kind is EventType.MISSION_COMPLETE:
                robot.status = GridRobotStatus.IDLE
                self.mission_completion[event.robot_id] = self.time
                return

            self._advance_trajectory(event)
            self._pending.append((event.robot_id, event.kind))
            return

        assignment = self.assignments.get(event.robot_id)

        if event.kind in (
            EventType.MOVE_COMPLETE,
            EventType.RETREAT_COMPLETE,
        ):
            duration = self._move_duration(robot)

            source = robot.cell

            robot.advance_cell(float(self.resolution))
            robot.book_travel(float(duration))

            self.reservations.complete_move(
                event.robot_id, source, robot.cell, self.time
            )

            # Deciding the next move is deferred so every release in
            # this batch lands first.
            self._pending.append((event.robot_id, event.kind))

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

            assignment.retreating = True

            robot.assign_path([robot.cell, segment.exit_cell])

            self._pending.append((event.robot_id, event.kind))

        elif event.kind is EventType.MISSION_COMPLETE:

            robot.status = GridRobotStatus.IDLE

            assignment.completion_time = self.time

            if assignment.is_parking:
                self.park_completion[event.robot_id] = self.time
                return

            self.mission_completion[event.robot_id] = self.time

            # No work left: the caller may send the robot home.
            if self.on_mission_complete is None:
                return

            follow_up = self.on_mission_complete(robot)

            if follow_up is not None:
                self.assign_mission(
                    event.robot_id, follow_up, is_parking=True
                )

    # -- driving ----------------------------------------

    def _continue_pending(self) -> None:
        """Second phase: everyone picks their next action.

        Sorted so that when two robots contend for the same freed
        cell, the winner is always the same.
        """

        pending, self._pending = self._pending, []

        for robot_id, kind in sorted(pending):

            if robot_id in self._trajectories:
                self._start_action(robot_id)
                continue

            assignment = self.assignments.get(robot_id)

            if assignment is None:
                continue

            robot = self.robots[robot_id]

            if kind is EventType.SERVICE_COMPLETE:
                segment = assignment.plan.segments[
                    assignment.segment_index
                ]
                self._try_move(
                    robot_id,
                    EventType.RETREAT_COMPLETE,
                    segment.to_zone,
                )

            elif kind is EventType.RETREAT_COMPLETE:
                assignment.segment_index += 1
                self._begin_segment(robot_id)

            elif robot.finished:
                self._arrive(robot_id)

            else:
                segment = assignment.plan.segments[
                    assignment.segment_index
                ]
                self._try_move(
                    robot_id,
                    EventType.MOVE_COMPLETE,
                    segment.to_zone,
                )

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

        # Phase two: now that every release in this batch has landed,
        # robots may claim what they are entering.
        self._continue_pending()

        # Occupancy changed, so blocked robots may now be able to go.
        self._retry_waiting()

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

        if self._waiting:
            stuck = ", ".join(
                f"{rid}@{self.robots[rid].cell}"
                for rid in sorted(self._waiting)
            )
            raise DeadlockError(
                f"no events left but {len(self._waiting)} robot(s) "
                f"still waiting at t={self.sim_time:.4f}: {stuck}"
            )

    # -- reporting --------------------------------------

    def completion_time(self, robot_id: str) -> float | None:
        """When the robot finished its mission, excluding parking."""

        done = self.mission_completion.get(robot_id)

        return None if done is None else float(done)

    def parked_time(self, robot_id: str) -> float | None:
        """When the robot reached its park zone, if it parked."""

        done = self.park_completion.get(robot_id)

        return None if done is None else float(done)

    def blocked_by_others(self, robot_id: str) -> frozenset:
        """Cells currently held by other robots."""
        return self.reservations.cells_held_by_others(robot_id)

    def busy_robots(self) -> list[str]:
        return [
            robot_id
            for robot_id, robot in self.robots.items()
            if robot.status is not GridRobotStatus.IDLE
        ]
