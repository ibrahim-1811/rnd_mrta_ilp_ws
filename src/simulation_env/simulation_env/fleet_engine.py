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
from heapq import heapify, heappop, heappush

from mrta_core.battery import BatteryConfig
from mrta_core.warehouse_map import WarehouseMap, ZoneType

from simulation_env.battery_ledger import BatteryLedger
from simulation_env.grid_robot import GridRobot, GridRobotStatus
from mrta_core.warehouse_routing import MissionPlan
from simulation_env.reservations import EDGE, VERTEX, ReservationTable
from simulation_env.local_traffic_controller import (
    LocalTrafficController,
    MovementIntent,
    TrafficDecision,
)
from simulation_env.local_yield_selector import select_local_yield_cell


class EventType(str, Enum):
    MOVE_COMPLETE = "MOVE_COMPLETE"
    WAIT_COMPLETE = "WAIT_COMPLETE"
    SERVICE_COMPLETE = "SERVICE_COMPLETE"
    CHARGE_COMPLETE = "CHARGE_COMPLETE"
    RETREAT_COMPLETE = "RETREAT_COMPLETE"
    YIELD_COMPLETE = "YIELD_COMPLETE"
    REJOIN_COMPLETE = "REJOIN_COMPLETE"
    MISSION_COMPLETE = "MISSION_COMPLETE"
    # A pure clock wake-up with no effect on any robot. It exists so the
    # executor can ask to be called again at a chosen instant -- the
    # dispatch quantum (S0b) -- without the clock running dry first.
    # Deliberately NOT a WAIT: a robot held for allocation batching is
    # idle, not blocked by traffic, and must not book waiting time.
    DISPATCH_WINDOW = "DISPATCH_WINDOW"


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


@dataclass
class _YieldState:
    """Temporary state while a robot leaves and rejoins its route."""

    winner_id: str

    # Mission path before the temporary side step.
    original_path: list
    original_index: int

    # Cell on the original corridor from which the robot yielded.
    corridor_cell: tuple

    # Temporary one-cell side position.
    yield_cell: tuple

    # Start of stationary waiting in the yield cell.
    wait_since: Fraction | None = None


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
        battery: BatteryConfig | None = None,
    ) -> None:

        self.warehouse = warehouse

        self.resolution = Fraction(resolution).limit_denominator(10**6)

        # The one authoritative battery record, booked from this
        # engine's own events. Observation only: nothing in the engine
        # reads it back. None when battery accounting is disabled.
        self.battery = (
            BatteryLedger(battery, self.resolution)
            if battery is not None and battery.enabled
            else None
        )

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
        self.local_traffic = LocalTrafficController()

        # robot_id -> (event kind it is waiting to schedule, since)
        self._waiting: dict[str, tuple] = {}

        # Monotonic execution-progress counter.
        #
        # Unlike mission_completion, which is keyed by robot_id,
        # this counts every completed mission during the run.
        # It is used only for livelock diagnostics.
        self._mission_complete_events = 0

        # Robots temporarily displaced one cell to resolve a local
        # head-on conflict.
        self._yielding: dict[str, _YieldState] = {}

        # Duration and exact completion event for each open session.
        # The watchdog may exempt a dwell only while that event is queued.
        self._charging: dict[str, tuple[Fraction, Event]] = {}

        # The complete timed trajectory remains owned by each rolling buffer;
        # only a six-cell delivery window is exposed to execution.

        # Robots that finished an action this timestamp and must pick
        # their next one, once every release in the batch is applied.
        self._pending: list = []

        # Called when a planned move is refused at runtime. Must
        # return a fresh Trajectory from the robot's current cell and
        # the current time, or None to give up. Replanning, never a
        # blind retry loop.

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

    def schedule_timer(self, delay: Fraction, detail: str = "") -> Event:
        """Ask to be ticked again in ``delay`` simulated seconds.

        A neutral wake-up: it moves the clock, books the interval the
        way any other advance does (idle robots drain at the idle rate,
        which is exactly what they are doing), produces a tick, and
        changes nothing else. The executor uses it for the dispatch
        quantum.
        """

        return self._schedule("", EventType.DISPATCH_WINDOW, delay, detail)

    # -- fleet ------------------------------------------

    def add_robot(self, robot: GridRobot) -> None:
        self.robots[robot.robot_id] = robot
        robot.set_sim_time(self.sim_time)
        self.reservations.occupy(robot.cell, robot.robot_id, self.time)

        if self.battery is not None:
            self.battery.register(robot.robot_id, self.time)

    # -- accounting ---------------------------------------
    #
    # Every travel, service and wait booking goes through these, so the
    # robot's float counters and the battery ledger are written by the
    # same events with the same durations and can never disagree about
    # what happened.

    def _book_travel(self, robot: GridRobot, duration: Fraction) -> None:
        """One completed one-cell move, whatever kind of move it was."""

        robot.book_travel(float(duration))

        if self.battery is not None:
            self.battery.book_move(robot.robot_id, duration)

    def _book_wait(self, robot: GridRobot, duration: Fraction) -> None:
        """One closed stationary traffic wait."""

        robot.book_wait(float(duration))

        if self.battery is not None:
            self.battery.book_wait(robot.robot_id, duration)

    def _book_service(
        self,
        robot: GridRobot,
        duration: Fraction,
        status: GridRobotStatus,
    ) -> None:
        """One completed pick or drop."""

        robot.book_service(float(duration), status)

        if self.battery is not None:
            self.battery.book_service(robot.robot_id, duration)

    def _book_charge(self, robot: GridRobot, duration: Fraction) -> None:
        """One completed charge session. The only booking that adds SOC."""

        robot.book_charge(float(duration))

        if self.battery is not None:
            self.battery.book_charge(robot.robot_id, duration)

    # -- charging (B4, cut short by B5 preemption) --------
    #
    # A charge is a timed dwell on the robot's own reserved charger
    # cell, exactly like a pick or a drop is a timed dwell on a station:
    # the clock advances through it, the cell stays reserved, and the
    # energy is booked when it completes. The charging *policy* -- who
    # charges, for how long, and what happens next -- stays in the
    # executor; this only runs the dwell.

    def begin_charge(self, robot_id: str, duration: Fraction) -> Event:
        """Hold ``robot_id`` on its cell, charging, for ``duration``.

        The robot must be standing still with no mission in flight: a
        charge is only ever started once it has arrived on its reserved
        charger. Returns the scheduled CHARGE_COMPLETE event.
        """

        robot = self.robots[robot_id]

        # A finished assignment is left in place rather than deleted,
        # so the test is whether the robot has driven its path out, not
        # whether an assignment object exists.
        if not robot.finished:
            raise RuntimeError(f"{robot_id} cannot charge with a mission in flight")

        if robot.status is not GridRobotStatus.IDLE:
            raise RuntimeError(
                f"{robot_id} cannot charge from status {robot.status.value}"
            )

        if duration < 0:
            raise ValueError("charge duration must not be negative")

        if robot_id in self._charging:
            raise RuntimeError(f"{robot_id} is already charging")

        if self.battery is not None:
            self.battery.begin_charge(robot_id, self.time)

        robot.status = GridRobotStatus.CHARGING

        event = self._schedule(
            robot_id,
            EventType.CHARGE_COMPLETE,
            duration,
            detail="charging",
        )
        self._charging[robot_id] = (duration, event)
        return event

    def end_charge_early(self, robot_id: str) -> Fraction:
        """Close an open charge session now and return the interval booked.

        Preemption (B5). The *policy* -- who may be preempted, and by
        whom -- stays in the executor; this only cuts the dwell short,
        books exactly the time that really elapsed, and hands the robot
        back IDLE on its charger cell, the same way a completed session
        does. The scheduled completion is dropped, because a completion
        without an open session is a bug the handler refuses.
        """

        session = self._charging.get(robot_id)

        if session is None:
            raise RuntimeError(f"{robot_id} has no open charge session to end")

        duration, event = session

        elapsed = self.time - (event.time - duration)

        if elapsed < 0:
            raise RuntimeError(
                f"{robot_id} charge session began at "
                f"t={float(event.time - duration):.4f}, after now "
                f"t={float(self.time):.4f}"
            )

        del self._charging[robot_id]

        self._queue = [entry for entry in self._queue if entry[2] is not event]
        heapify(self._queue)

        robot = self.robots[robot_id]

        self._book_charge(robot, elapsed)

        robot.status = GridRobotStatus.IDLE

        return elapsed

    def _prepare_mission_start(self, robot_id: str):
        """Prepare the first movement request for one raw mission.

        No move is claimed here. This lets several newly assigned
        robots enter local traffic arbitration together.
        """

        assignment = self.assignments[robot_id]
        robot = self.robots[robot_id]

        while True:
            if assignment.segment_index >= len(assignment.plan.segments):
                self._schedule(
                    robot_id,
                    EventType.MISSION_COMPLETE,
                    Fraction(0),
                )
                return None

            segment = assignment.plan.segments[assignment.segment_index]

            assignment.retreating = False

            robot.assign_path(list(segment.path))

            if not robot.finished:
                return (
                    robot_id,
                    EventType.MOVE_COMPLETE,
                    segment.to_zone,
                )

            # Already at a service target.
            if segment.exit_cell is not None:
                self._arrive(robot_id)
                return None

            # Zero-length non-service leg. Continue to the next leg.
            assignment.segment_index += 1

    def assign_missions_batch(
        self,
        missions: dict[str, MissionPlan],
        parking=frozenset(),
    ) -> set[str]:
        """Assign several raw missions at one simulation instant.

        Initial movement intentions are resolved together by the
        LocalTrafficController.
        """

        missions = dict(missions)
        parking = set(parking)

        unknown = parking - set(missions)

        if unknown:
            raise ValueError(f"parking robots missing missions: {sorted(unknown)}")

        # Validate the whole batch before mutating engine state.
        for robot_id in sorted(missions):
            if robot_id not in self.robots:
                raise KeyError(f"unknown robot {robot_id}")

            robot = self.robots[robot_id]
            plan = missions[robot_id]

            if plan.path[0] != robot.cell:
                raise ValueError(
                    f"{robot_id} is at {robot.cell} but its mission "
                    f"starts at {plan.path[0]}"
                )

        # Install every assignment first.
        for robot_id in sorted(missions):
            self.assignments[robot_id] = _Assignment(
                plan=missions[robot_id],
                is_parking=(robot_id in parking),
            )

        requests = []

        # Existing waiting robots should compete in the same local
        # traffic decision as newly dispatched robots.
        for robot_id in sorted(self._waiting):
            kind, detail, _since = self._waiting[robot_id]

            requests.append(
                (
                    robot_id,
                    kind,
                    detail,
                )
            )

        # Prepare all new first moves without claiming any cells yet.
        for robot_id in sorted(missions):
            request = self._prepare_mission_start(robot_id)

            if request is not None:
                requests.append(request)

        if not requests:
            return set()

        return self._try_moves_batch(requests)

    def assign_mission(
        self,
        robot_id: str,
        plan: MissionPlan,
        is_parking: bool = False,
    ) -> None:
        """Assign one raw mission through the same local traffic path."""

        self.assign_missions_batch(
            {robot_id: plan},
            parking=({robot_id} if is_parking else frozenset()),
        )

    def _begin_segment(self, robot_id: str) -> None:

        assignment = self.assignments[robot_id]
        robot = self.robots[robot_id]

        if assignment.segment_index >= len(assignment.plan.segments):
            self._schedule(robot_id, EventType.MISSION_COMPLETE, Fraction(0))
            return

        segment = assignment.plan.segments[assignment.segment_index]

        assignment.retreating = False

        robot.assign_path(list(segment.path))

        if robot.finished:
            self._arrive(robot_id)
        else:
            self._try_move(robot_id, EventType.MOVE_COMPLETE, segment.to_zone)

    def _arrive(self, robot_id: str) -> None:
        """Robot reached this segment's target station."""

        assignment = self.assignments[robot_id]
        robot = self.robots[robot_id]

        segment = assignment.plan.segments[assignment.segment_index]

        # A service or terminal arrival is valid only at the
        # segment's actual planned goal.
        if robot.cell != segment.goal_cell:
            raise RuntimeError(
                f"{robot_id} arrived at wrong cell: "
                f"physical={robot.cell}, "
                f"expected_goal={segment.goal_cell}, "
                f"segment={segment.to_zone}, "
                f"retreating={assignment.retreating}, "
                f"path={robot.path}, "
                f"path_index={robot.path_index}"
            )

        if segment.exit_cell is None:
            # Not a service station: straight on to the next leg.
            assignment.segment_index += 1
            self._begin_segment(robot_id)
            return

        zone_type = self.warehouse.zone_by_id(segment.to_zone).zone_type

        robot.status = SERVICE_STATUS[zone_type]

        self._schedule(
            robot_id,
            EventType.SERVICE_COMPLETE,
            self.service_seconds[zone_type],
            detail=segment.to_zone,
        )

    def _traffic_busy(self, robot_id: str) -> bool:
        """Whether the robot is doing productive order work.

        Normal order missions have right-of-way over parking missions.
        Robot ID is only the final deterministic tie breaker.
        """

        assignment = self.assignments.get(robot_id)

        if assignment is None:
            return False

        return not assignment.is_parking

    def _remaining_to_destination(self, robot_id: str) -> int:
        """Grid edges remaining to the current destination."""

        robot = self.robots[robot_id]

        return max(
            0,
            len(robot.path) - robot.path_index - 1,
        )

    def _service_protected_move(
        self,
        robot_id: str,
        kind: EventType,
    ) -> str | None:
        """Classify movement that needs exclusive service clearance.

        Returns:
            "entry"
                Final aisle -> shelf/dropoff move.

            "retreat"
                Shelf/dropoff -> protected aisle exit.

            None
                Ordinary traffic movement.
        """

        assignment = self.assignments.get(robot_id)

        if assignment is None:
            return None

        if assignment.segment_index >= len(assignment.plan.segments):
            return None

        robot = self.robots[robot_id]

        if robot.finished:
            return None

        segment = assignment.plan.segments[assignment.segment_index]

        if segment.exit_cell is None:
            return None

        source = robot.cell
        target = robot.path[robot.path_index + 1]

        if (
            kind is EventType.MOVE_COMPLETE
            and not assignment.retreating
            and source == segment.exit_cell
            and target == segment.goal_cell
        ):
            return "entry"

        if (
            kind is EventType.RETREAT_COMPLETE
            and assignment.retreating
            and source == segment.goal_cell
            and target == segment.exit_cell
        ):
            return "retreat"

        return None

    def _try_moves_batch(self, requests) -> set[str]:
        """Resolve simultaneous one-cell movement requests.

        Ordinary safe moves are committed atomically.

        For a direct head-on conflict, the lower right-of-way robot
        moves one orthogonal cell aside when a safe local yield cell
        exists. The winner waits until that side step finishes.
        """

        requests = list(requests)

        if not requests:
            return set()

        request_by_robot = {}

        for robot_id, kind, detail in requests:
            if robot_id in request_by_robot:
                raise RuntimeError(f"duplicate movement request for {robot_id}")

            request_by_robot[robot_id] = (
                kind,
                detail,
            )

        # ----------------------------------------------------
        # Service station clearance.
        #
        # Normal aisle movement uses atomic same-direction batches.
        # The final service entry and its retreat are different:
        # the approach cell must remain exclusive to the servicing
        # robot until it has backed out again.
        # ----------------------------------------------------

        started = set()
        ordinary_requests = []

        for robot_id, kind, detail in requests:
            mode = self._service_protected_move(
                robot_id,
                kind,
            )

            if mode is None:
                ordinary_requests.append(
                    (
                        robot_id,
                        kind,
                        detail,
                    )
                )
                continue

            robot = self.robots[robot_id]

            source = robot.cell
            target = robot.path[robot.path_index + 1]

            conflict = self.reservations.check_move(
                robot_id,
                source,
                target,
            )

            if conflict is not None:
                if robot_id not in self._waiting:
                    robot.wait_event_count += 1

                    if conflict == EDGE:
                        robot.edge_conflict_count += 1
                    else:
                        robot.vertex_conflict_count += 1

                    self._waiting[robot_id] = (
                        kind,
                        detail,
                        self.time,
                    )

                robot.status = GridRobotStatus.WAITING

                continue

            pending_wait = self._waiting.pop(
                robot_id,
                None,
            )

            if pending_wait is not None:
                self._book_wait(
                    robot,
                    self.time - pending_wait[2],
                )

            duration = self._move_duration(robot)

            # Conservative single-edge reservation.
            #
            # Unlike begin_moves_batch(), this does NOT release
            # the source when movement begins.
            self.reservations.begin_move(
                robot_id,
                source,
                target,
                self.time,
                duration,
            )

            robot.status = GridRobotStatus.MOVING

            self._schedule(
                robot_id,
                kind,
                duration,
                detail=detail,
            )

            started.add(robot_id)

        requests = ordinary_requests

        if not requests:
            return started

        intents = []

        for robot_id, _kind, _detail in requests:
            robot = self.robots[robot_id]

            if robot.finished:
                continue

            intents.append(
                MovementIntent(
                    robot_id=robot_id,
                    source=robot.cell,
                    target=robot.path[robot.path_index + 1],
                    busy=self._traffic_busy(robot_id),
                    remaining_distance=(self._remaining_to_destination(robot_id)),
                )
            )

        if not intents:
            return set()

        occupied = {}

        for robot_id in self.robots:
            for cell in self.reservations.held_cells(robot_id):
                occupied[cell] = robot_id

        decisions = self.local_traffic.resolve(
            intents,
            occupied_by=occupied,
        )

        intent_by_robot = {intent.robot_id: intent for intent in intents}

        # ----------------------------------------------------
        # Identify explicit head-on pairs.
        # ----------------------------------------------------

        head_on = set()
        yield_partner = {}

        for index, first in enumerate(intents):
            for second in intents[index + 1 :]:
                if not (
                    first.source == second.target and first.target == second.source
                ):
                    continue

                head_on.add(first.robot_id)
                head_on.add(second.robot_id)

                first_decision = decisions[first.robot_id].decision

                second_decision = decisions[second.robot_id].decision

                if first_decision is TrafficDecision.YIELD_REQUIRED:
                    yield_partner[first.robot_id] = second.robot_id

                if second_decision is TrafficDecision.YIELD_REQUIRED:
                    yield_partner[second.robot_id] = first.robot_id

        # ----------------------------------------------------
        # Helper for stationary waiting.
        # ----------------------------------------------------

        def register_wait(robot_id: str, edge: bool) -> None:

            robot = self.robots[robot_id]

            if robot_id not in self._waiting:
                robot.wait_event_count += 1

                if edge:
                    robot.edge_conflict_count += 1
                else:
                    robot.vertex_conflict_count += 1

                kind, detail = request_by_robot[robot_id]

                self._waiting[robot_id] = (
                    kind,
                    detail,
                    self.time,
                )

            robot.status = GridRobotStatus.WAITING

        # ----------------------------------------------------
        # Ordinary approved movement.
        # ----------------------------------------------------

        approved = []
        yield_required = []

        for robot_id in sorted(intent_by_robot):
            intent = intent_by_robot[robot_id]
            decision = decisions[robot_id]

            kind, _detail = request_by_robot[robot_id]

            if decision.decision is TrafficDecision.MOVE:
                approved.append(
                    (
                        robot_id,
                        intent.source,
                        intent.target,
                    )
                )

                continue

            # A yielder that is trying to rejoin simply remains in
            # its side cell if the corridor cannot clear yet.
            if kind is EventType.REJOIN_COMPLETE:
                self.robots[robot_id].status = GridRobotStatus.WAITING
                continue

            if (
                decision.decision is TrafficDecision.YIELD_REQUIRED
                and robot_id in yield_partner
            ):
                yield_required.append(robot_id)
                continue

            register_wait(
                robot_id,
                edge=(robot_id in head_on),
            )

        # ----------------------------------------------------
        # Convert YIELD_REQUIRED into an actual one-cell side
        # step when the real warehouse offers a safe cell.
        # ----------------------------------------------------

        # A temporary yield cell must not interfere with any
        # movement already being requested at this timestamp.
        reserved_targets = {intent.target for intent in intents}

        # Existing yielding robots intend to return to these cells.
        # Do not use those corridor cells as new yield bays.
        reserved_targets |= {state.corridor_cell for state in self._yielding.values()}

        yield_moves = []

        for robot_id in sorted(yield_required):
            if robot_id in self._yielding:
                continue

            intent = intent_by_robot[robot_id]

            winner_id = yield_partner[robot_id]
            winner = self.robots[winner_id]

            forbidden = {
                intent.source,
                intent.target,
            }

            # ------------------------------------------------
            # Protect the winner's escape cell.
            #
            # For:
            #
            # A -> [conflict] <- B
            #
            # if B yields sideways, that yield cell must not
            # be the cell A needs immediately after passing.
            #
            # Otherwise:
            #
            # A: corridor -> yield_cell
            # B: yield_cell -> corridor
            #
            # becomes a second head-on swap.
            # ------------------------------------------------

            next_index = winner.path_index + 2

            if next_index < len(winner.path):
                forbidden.add(winner.path[next_index])

            # Do not interfere with any existing temporary
            # yield corridor either.
            forbidden |= {state.corridor_cell for state in self._yielding.values()}

            yield_cell = select_local_yield_cell(
                warehouse=self.warehouse,
                source=intent.source,
                intended_target=intent.target,
                occupied=frozenset(occupied),
                reserved_targets=frozenset(reserved_targets),
                forbidden=frozenset(forbidden),
            )

            if yield_cell is None:
                register_wait(
                    robot_id,
                    edge=True,
                )

                continue

            robot = self.robots[robot_id]

            # Count the head-on conflict once. If this robot had
            # already been stationary waiting for the same problem,
            # the conflict was already counted there.
            if robot_id not in self._waiting:
                robot.edge_conflict_count += 1

            pending_wait = self._waiting.pop(
                robot_id,
                None,
            )

            if pending_wait is not None:
                self._book_wait(
                    robot,
                    self.time - pending_wait[2],
                )

            self._yielding[robot_id] = _YieldState(
                winner_id=yield_partner[robot_id],
                original_path=list(robot.path),
                original_index=robot.path_index,
                corridor_cell=intent.source,
                yield_cell=yield_cell,
            )

            # Temporary path used only for the explicit side step.
            robot.assign_path(
                [
                    intent.source,
                    yield_cell,
                ]
            )

            yield_moves.append(
                (
                    robot_id,
                    intent.source,
                    yield_cell,
                )
            )

            reserved_targets.add(yield_cell)

        # ``started`` already contains any protected service
        # entry or retreat movements started above.

        # ----------------------------------------------------
        # Start ordinary simultaneous movement.
        # ----------------------------------------------------

        if approved:
            durations = {
                self._move_duration(self.robots[robot_id])
                for robot_id, _source, _target in approved
            }

            if len(durations) != 1:
                raise RuntimeError(
                    "simultaneous movement requires one common grid move duration"
                )

            duration = durations.pop()

            self.reservations.begin_moves_batch(
                approved,
                now=self.time,
                duration=duration,
            )

            for robot_id, _source, _target in approved:
                pending_wait = self._waiting.pop(
                    robot_id,
                    None,
                )

                if pending_wait is not None:
                    self._book_wait(
                        self.robots[robot_id],
                        self.time - pending_wait[2],
                    )

                kind, detail = request_by_robot[robot_id]

                # Rejoining robots have waited in their side cell
                # since YIELD_COMPLETE.
                if kind is EventType.REJOIN_COMPLETE and robot_id in self._yielding:
                    state = self._yielding[robot_id]

                    if state.wait_since is not None:
                        self._book_wait(
                            self.robots[robot_id],
                            self.time - state.wait_since,
                        )

                        state.wait_since = None

                robot = self.robots[robot_id]
                robot.status = GridRobotStatus.MOVING

                self._schedule(
                    robot_id,
                    kind,
                    duration,
                    detail=detail,
                )

                started.add(robot_id)

        # ----------------------------------------------------
        # Start side steps.
        #
        # Here we deliberately use the conservative single-move
        # reservation. It holds both the corridor source and the
        # yield cell until the side step completes. Therefore the
        # winner cannot enter too early.
        # ----------------------------------------------------

        for robot_id, source, target in yield_moves:
            robot = self.robots[robot_id]

            duration = self._move_duration(robot)

            self.reservations.begin_move(
                robot_id,
                source,
                target,
                self.time,
                duration,
            )

            robot.status = GridRobotStatus.MOVING

            self._schedule(
                robot_id,
                EventType.YIELD_COMPLETE,
                duration,
                detail="local_head_on_yield",
            )

            started.add(robot_id)

        return started

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

        conflict = self.reservations.check_move(robot_id, source, target)

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
            self._book_wait(robot, self.time - pending[2])

        duration = self._move_duration(robot)

        self.reservations.begin_move(robot_id, source, target, self.time, duration)

        robot.status = GridRobotStatus.MOVING

        self._schedule(robot_id, kind, duration, detail=detail)

        return True

    def _retry_waiting(self) -> None:
        """Retry all blocked movement requests as one batch."""

        if not self._waiting:
            return

        requests = []

        for robot_id in sorted(self._waiting):
            kind, detail, _since = self._waiting[robot_id]

            requests.append(
                (
                    robot_id,
                    kind,
                    detail,
                )
            )

        self._try_moves_batch(requests)

    def _handle(self, event: Event) -> None:

        # A timer, not a robot action: it has already done its whole job
        # by advancing the clock to this instant and producing a tick.
        if event.kind is EventType.DISPATCH_WINDOW:
            return

        robot = self.robots[event.robot_id]

        if event.kind is EventType.MISSION_COMPLETE:
            self._mission_complete_events += 1

        # --------------------------------------------------
        # Charge session finished (B4).
        #
        # A dwell on the robot's own reserved charger, like a service
        # dwell on a station: book the interval, hand the robot back
        # IDLE on the same cell. What happens next -- vacating the slot
        # and rejoining the fleet -- is the executor's policy, driven
        # off this event.
        # --------------------------------------------------

        if event.kind is EventType.CHARGE_COMPLETE:
            session = self._charging.get(event.robot_id)

            if session is None or session[1] is not event:
                raise RuntimeError(
                    f"{event.robot_id} completed a charge without an open session"
                )

            duration, _ = self._charging.pop(event.robot_id)
            self._book_charge(robot, duration)

            robot.status = GridRobotStatus.IDLE

            return

        # --------------------------------------------------
        # Temporary local head-on side step completed.
        # --------------------------------------------------

        if event.kind is EventType.YIELD_COMPLETE:
            state = self._yielding.get(event.robot_id)

            if state is None:
                raise RuntimeError(
                    f"{event.robot_id} completed a yield without yield state"
                )

            duration = self._move_duration(robot)

            source = robot.cell

            robot.advance_cell(float(self.resolution))

            self._book_travel(robot, duration)

            self.reservations.complete_move(
                event.robot_id,
                source,
                robot.cell,
                self.time,
            )

            # Prepare the one-cell return path, but remain stationary
            # until the winner actually leaves the corridor cell.
            robot.assign_path(
                [
                    robot.cell,
                    state.corridor_cell,
                ]
            )

            robot.status = GridRobotStatus.WAITING

            state.wait_since = self.time

            robot.wait_event_count += 1

            return

        # --------------------------------------------------
        # Robot has returned from its temporary side cell to
        # the original corridor cell.
        # --------------------------------------------------

        if event.kind is EventType.REJOIN_COMPLETE:
            state = self._yielding.get(event.robot_id)

            if state is None:
                raise RuntimeError(
                    f"{event.robot_id} completed a rejoin without yield state"
                )

            duration = self._move_duration(robot)

            source = robot.cell

            robot.advance_cell(float(self.resolution))

            self._book_travel(robot, duration)

            self.reservations.complete_move(
                event.robot_id,
                source,
                robot.cell,
                self.time,
            )

            # Restore the exact mission route and progress point that
            # existed before the temporary side step.
            robot.path = list(state.original_path)

            robot.path_index = state.original_index

            robot.status = (
                GridRobotStatus.IDLE if robot.finished else GridRobotStatus.MOVING
            )

            del self._yielding[event.robot_id]

            self._pending.append(
                (
                    event.robot_id,
                    event.kind,
                )
            )

            return

        assignment = self.assignments.get(event.robot_id)

        if event.kind in (
            EventType.MOVE_COMPLETE,
            EventType.RETREAT_COMPLETE,
        ):
            duration = self._move_duration(robot)

            source = robot.cell

            robot.advance_cell(float(self.resolution))
            self._book_travel(robot, duration)

            retain_service_exit = False

            if (
                event.kind is EventType.MOVE_COMPLETE
                and assignment is not None
                and assignment.segment_index < len(assignment.plan.segments)
            ):
                segment = assignment.plan.segments[assignment.segment_index]

                retain_service_exit = (
                    not assignment.retreating
                    and segment.exit_cell is not None
                    and source == segment.exit_cell
                    and robot.cell == segment.goal_cell
                )

            self.reservations.complete_move(
                event.robot_id,
                source,
                robot.cell,
                self.time,
                release_source=(not retain_service_exit),
            )

            # Deciding the next move is deferred so every release in
            # this batch lands first.
            self._pending.append((event.robot_id, event.kind))

        elif event.kind is EventType.SERVICE_COMPLETE:
            segment = assignment.plan.segments[assignment.segment_index]

            zone_type = self.warehouse.zone_by_id(segment.to_zone).zone_type

            self._book_service(
                robot,
                self.service_seconds[zone_type],
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
                self.assign_mission(event.robot_id, follow_up, is_parking=True)

    # -- driving ----------------------------------------

    def _continue_pending(self) -> None:
        """Choose next actions after all same-time events finish.

        Mission state transitions happen first.

        Ordinary movement requests, blocked robots, and eligible
        yield rejoins are then resolved in one simultaneous batch.
        """

        pending, self._pending = self._pending, []

        move_requests = {}

        # Existing blocked robots participate in the same arbitration
        # as robots that became ready at this timestamp.
        for robot_id in sorted(self._waiting):
            kind, detail, _since = self._waiting[robot_id]

            move_requests[robot_id] = (
                robot_id,
                kind,
                detail,
            )

        for robot_id, kind in sorted(pending):
            assignment = self.assignments.get(robot_id)

            if assignment is None:
                continue

            robot = self.robots[robot_id]

            if kind is EventType.SERVICE_COMPLETE:
                segment = assignment.plan.segments[assignment.segment_index]

                move_requests[robot_id] = (
                    robot_id,
                    EventType.RETREAT_COMPLETE,
                    segment.to_zone,
                )

            elif kind is EventType.RETREAT_COMPLETE:
                assignment.segment_index += 1

                if assignment.segment_index >= len(assignment.plan.segments):
                    self._schedule(
                        robot_id,
                        EventType.MISSION_COMPLETE,
                        Fraction(0),
                    )
                    continue

                segment = assignment.plan.segments[assignment.segment_index]

                assignment.retreating = False

                robot.assign_path(list(segment.path))

                if robot.finished:
                    self._arrive(robot_id)

                else:
                    move_requests[robot_id] = (
                        robot_id,
                        EventType.MOVE_COMPLETE,
                        segment.to_zone,
                    )

            # ------------------------------------------------
            # A temporary yield/rejoin interrupted an existing
            # mission edge.
            #
            # The rejoin itself does NOT complete that original
            # mission edge. Resume it using its correct semantic
            # event type.
            #
            # This matters especially during service retreat:
            #
            #   SERVICE
            #      ↓
            #   RETREAT
            #      ↓ conflict
            #   YIELD
            #      ↓
            #   REJOIN
            #      ↓
            #   RETREAT_COMPLETE
            #
            # Treating that final edge as MOVE_COMPLETE makes the
            # engine call _arrive() at the retreat exit cell and
            # schedules service a second time.
            # ------------------------------------------------

            elif kind is EventType.REJOIN_COMPLETE:
                if robot.finished:
                    raise RuntimeError(
                        f"{robot_id} rejoined with no original "
                        f"mission edge left to resume"
                    )

                segment = assignment.plan.segments[assignment.segment_index]

                resume_kind = (
                    EventType.RETREAT_COMPLETE
                    if assignment.retreating
                    else EventType.MOVE_COMPLETE
                )

                move_requests[robot_id] = (
                    robot_id,
                    resume_kind,
                    segment.to_zone,
                )

            elif robot.finished:
                self._arrive(robot_id)

            else:
                segment = assignment.plan.segments[assignment.segment_index]

                move_requests[robot_id] = (
                    robot_id,
                    EventType.MOVE_COMPLETE,
                    segment.to_zone,
                )

        # --------------------------------------------------
        # RMF-style local corridor mutex for yield rejoining.
        #
        # A yielder never tries to re-enter a corridor while
        # another robot still owns it.
        #
        # Once the corridor becomes free, exactly ONE waiting
        # yielder is permitted to request that corridor.
        #
        # Multiple yielders for the same corridor are ordered by:
        #
        #   1. earliest wait_since
        #   2. robot_id as deterministic exact tie-break
        #
        # This removes reciprocal relationships such as:
        #
        #   robot_A waits for robot_B
        #   robot_B waits for robot_A
        #
        # and avoids simultaneous rejoin competition.
        # --------------------------------------------------

        corridor_waiters = {}

        for robot_id in sorted(self._yielding):
            state = self._yielding[robot_id]

            if state.wait_since is None:
                continue

            if robot_id in move_requests:
                continue

            corridor = state.corridor_cell

            # Mutex unavailable while any robot owns the corridor.
            if self.reservations.owner(corridor) is not None:
                continue

            candidate = (
                state.wait_since,
                robot_id,
            )

            current = corridor_waiters.get(corridor)

            if current is None or candidate < current:
                corridor_waiters[corridor] = candidate

        # Exactly one rejoin request per free corridor.
        for corridor in sorted(corridor_waiters):
            _wait_since, robot_id = corridor_waiters[corridor]

            move_requests[robot_id] = (
                robot_id,
                EventType.REJOIN_COMPLETE,
                "local_corridor_mutex_rejoin",
            )

        if move_requests:
            self._try_moves_batch(
                [move_requests[robot_id] for robot_id in sorted(move_requests)]
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
                f"clock would move backwards: {float(target)} < {self.sim_time}"
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

    def _only_scheduled_charging(self) -> bool:
        """Can this interval be excluded from the mission watchdog?

        Only charge-only intervals qualify. An unrelated unfinished
        mission or traffic wait keeps the watchdog running, even if
        another robot has a valid future charge completion.
        """
        if not self._charging or self._waiting or self._yielding:
            return False

        for robot_id, robot in self.robots.items():
            if robot_id not in self._charging:
                assignment = self.assignments.get(robot_id)
                if (
                    robot.status is not GridRobotStatus.IDLE
                    or not robot.finished
                    or (assignment is not None and assignment.completion_time is None)
                ):
                    return False

        queued = {id(event) for _, _, event in self._queue}
        return all(
            self.robots[robot_id].status is GridRobotStatus.CHARGING
            and event.kind is EventType.CHARGE_COMPLETE
            and event.robot_id == robot_id
            and isinstance(event.time, Fraction)
            and self.time <= event.time <= self.time + duration
            and id(event) in queued
            for robot_id, (duration, event) in self._charging.items()
        )

    def run(
        self,
        max_events: int = 1_000_000,
        max_no_progress_s: float = 600.0,
    ) -> None:

        self._stamp_all()

        last_progress_count = self._mission_complete_events

        last_progress_time = self.time

        while self._queue:
            charge_only = self._only_scheduled_charging()
            before = self.time
            self.step_once()

            # Inspect before handling: completion closes the session and
            # may start the exit mission in this same batch. Exclude only
            # the actual charge-only elapsed interval, never future time.
            if charge_only:
                last_progress_time += self.time - before

            if self._mission_complete_events != last_progress_count:
                last_progress_count = self._mission_complete_events
                last_progress_time = self.time

            no_progress_s = float(self.time - last_progress_time)

            if no_progress_s > max_no_progress_s:
                lines = [
                    "",
                    "NO-PROGRESS / LIVELOCK DIAGNOSTICS",
                    "=" * 72,
                    f"time: {self.sim_time:.4f}",
                    (
                        f"non-charging seconds without mission completion: {no_progress_s:.4f}"
                    ),
                    (f"mission completion events: {self._mission_complete_events}"),
                    f"waiting robots: {len(self._waiting)}",
                    f"yielding robots: {len(self._yielding)}",
                    "",
                    "ROBOT STATES",
                    "-" * 72,
                ]

                for robot_id in sorted(self.robots):
                    robot = self.robots[robot_id]

                    if robot.finished:
                        target = None
                    else:
                        target = robot.path[robot.path_index + 1]

                    assignment = self.assignments.get(robot_id)

                    segment_index = (
                        None if assignment is None else assignment.segment_index
                    )

                    retreating = None if assignment is None else assignment.retreating

                    waiting = robot_id in self._waiting

                    yielding = robot_id in self._yielding

                    lines.append(
                        f"{robot_id}: "
                        f"cell={robot.cell} "
                        f"next={target} "
                        f"status={robot.status.value} "
                        f"seg={segment_index} "
                        f"retreat={retreating} "
                        f"waiting={waiting} "
                        f"yielding={yielding}"
                    )

                lines.extend(
                    [
                        "",
                        "YIELD STATES",
                        "-" * 72,
                    ]
                )

                if not self._yielding:
                    lines.append("none")

                for robot_id in sorted(self._yielding):
                    state = self._yielding[robot_id]

                    lines.append(
                        f"{robot_id}: "
                        f"winner={state.winner_id} "
                        f"corridor={state.corridor_cell} "
                        f"yield_cell={state.yield_cell} "
                        f"wait_since={state.wait_since}"
                    )

                raise DeadlockError("\n".join(lines))

            if self.processed > max_events:
                raise RuntimeError(
                    f"event budget exceeded ({max_events}); "
                    f"the simulation is probably looping"
                )

        if self._charging:
            raise DeadlockError(
                f"open charge session(s) without queued completion: {sorted(self._charging)}"
            )

        if self._waiting:
            # ------------------------------------------------
            # Terminal wait diagnostics.
            #
            # With no events left, every waiting robot is
            # stationary. Therefore its requested next cell can
            # be mapped directly to the robot currently occupying
            # that cell.
            # ------------------------------------------------

            occupants = {
                robot.cell: robot_id for robot_id, robot in self.robots.items()
            }

            dependencies = {}
            lines = [
                "",
                "DEADLOCK DIAGNOSTICS",
                "=" * 72,
                f"time: {self.sim_time:.4f}",
                f"waiting robots: {len(self._waiting)}",
                "",
            ]

            for robot_id in sorted(self._waiting):
                robot = self.robots[robot_id]

                kind, detail, since = self._waiting[robot_id]

                if robot.finished:
                    target = None
                else:
                    target = robot.path[robot.path_index + 1]

                blocker = occupants.get(target) if target is not None else None

                if blocker == robot_id:
                    blocker = None

                dependencies[robot_id] = blocker if blocker in self._waiting else None

                lo = max(
                    0,
                    robot.path_index - 2,
                )

                hi = min(
                    len(robot.path),
                    robot.path_index + 5,
                )

                lines.extend(
                    [
                        robot_id,
                        f"  current      : {robot.cell}",
                        f"  requested    : {target}",
                        f"  occupied_by  : {blocker}",
                        f"  event        : {kind.value}",
                        f"  detail       : {detail}",
                        f"  waiting_since: {float(since):.4f}",
                        (f"  path_index   : {robot.path_index}/{len(robot.path) - 1}"),
                        (f"  path_slice   : {robot.path[lo:hi]}"),
                        "",
                    ]
                )

            # ------------------------------------------------
            # Detect closed wait dependencies.
            #
            # Example:
            #
            # R1 -> R2
            # R2 -> R3
            # R3 -> R1
            # ------------------------------------------------

            cycles = []
            globally_seen = set()

            for start in sorted(dependencies):
                if start in globally_seen:
                    continue

                order = []
                position = {}
                current = start

                while (
                    current is not None
                    and current in dependencies
                    and current not in globally_seen
                ):
                    if current in position:
                        cycle = order[position[current] :]

                        cycles.append(cycle)
                        break

                    position[current] = len(order)
                    order.append(current)

                    current = dependencies.get(current)

                globally_seen.update(order)

            lines.append("WAIT DEPENDENCIES")
            lines.append("-" * 72)

            for robot_id in sorted(dependencies):
                blocker = dependencies[robot_id]

                lines.append(f"  {robot_id} -> {blocker or 'NONE'}")

            lines.append("")

            # ------------------------------------------------
            # Show every robot that blocks a waiting robot,
            # even if that blocker is not itself registered in
            # _waiting.
            # ------------------------------------------------

            blocker_ids = set()

            for robot_id in sorted(self._waiting):
                robot = self.robots[robot_id]

                if robot.finished:
                    continue

                target = robot.path[robot.path_index + 1]

                blocker = occupants.get(target)

                if blocker is not None and blocker != robot_id:
                    blocker_ids.add(blocker)

            lines.append("BLOCKER STATES")
            lines.append("-" * 72)

            if not blocker_ids:
                lines.append("  none")

            for blocker_id in sorted(blocker_ids):
                blocker = self.robots[blocker_id]

                assignment = self.assignments.get(blocker_id)

                target = (
                    None if blocker.finished else blocker.path[blocker.path_index + 1]
                )

                lines.append(
                    f"{blocker_id}: "
                    f"cell={blocker.cell} "
                    f"next={target} "
                    f"status={blocker.status.value} "
                    f"waiting={blocker_id in self._waiting} "
                    f"yielding={blocker_id in self._yielding} "
                    f"finished={blocker.finished} "
                    f"segment={None if assignment is None else assignment.segment_index} "
                    f"retreating={None if assignment is None else assignment.retreating}"
                )

            lines.append("")

            lines.append("YIELD STATES")
            lines.append("-" * 72)

            if not self._yielding:
                lines.append("  none")

            for yielding_id in sorted(self._yielding):
                state = self._yielding[yielding_id]

                lines.append(
                    f"{yielding_id}: "
                    f"winner={state.winner_id} "
                    f"corridor={state.corridor_cell} "
                    f"yield_cell={state.yield_cell} "
                    f"original_index={state.original_index} "
                    f"wait_since={state.wait_since}"
                )

            lines.append("")

            lines.append("CYCLES")
            lines.append("-" * 72)

            if cycles:
                for cycle in cycles:
                    closed = cycle + [cycle[0]]

                    lines.append("  " + " -> ".join(closed))

            else:
                lines.append("  none detected")

            raise DeadlockError("\n".join(lines))

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
