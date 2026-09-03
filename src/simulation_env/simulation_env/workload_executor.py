"""Run a whole warehouse workload: FIFO -> allocator -> MAPF -> fleet.

This is the execution half of the milestone. It owns no allocation
policy of its own: it asks ``task_management`` who should take what,
and then drives the answer through the existing planner, the existing
space-time MAPF layer and the existing fleet engine.

    OrderQueue          which orders are eligible   (FIFO, capped at
                        min(available_robots, pending))
    BatchAllocationStrategy
                        which robot takes which of them   (the MRTA
                        decision, in task_management)
    plan_from_cell      the static route for the chosen shelf order
    MapfMissionPlanner  that route as a reservation-safe trajectory
    FleetSimulationEngine
                        execution on the one authoritative clock

Dispatch is continuous, the way a real fleet works. A robot that
finishes does not wait for the other seventeen and does not drive home
between orders: it stays where it stopped, in the aisle, and is offered
the next FIFO order on the very next tick. Only when the queue is
finally empty does it drive back to its own staging cell.

Reservations persist for the whole run. Each new trajectory is planned
against every other robot's live bookings, so a robot dispatched at
t=93 is routed around the seventeen already in motion.
"""

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from fractions import Fraction
from time import perf_counter

from mrta_core.order_queue import OrderQueue
from mrta_core.orders import OrderStatus

from simulation_env.fleet_engine import EventType, FleetSimulationEngine
from simulation_env.grid_robot import GridRobotStatus
from mrta_core.warehouse_routing import RouteCache, plan_from_cell


# An order visits at most three shelves and one drop-off.
MAX_ORDER_LEGS = 4


def workload_horizon_steps(
    warehouse,
    move_steps: int,
    service_steps: dict,
    robot_count: int,
    max_legs: int = MAX_ORDER_LEGS,
) -> int:
    """How deep the space-time search may look, in planner quanta.

    ``MapfMissionPlanner`` defaults to 4000 quanta -- roughly 2600
    simulated seconds at 1.5 m/s. That depth is never needed by a
    workload mission, but it is paid in full every time a robot is
    genuinely unroutable, because the search only stops when the
    horizon runs out. One such failure measured ~28 s of wall time.

    So bound it by what a mission can actually need:

        travel   a leg is at most a Manhattan crossing of the map, and
                 an order has at most ``max_legs`` of them
        service  one service hold per leg
        traffic  slack for yielding, scaled by fleet size: in the worst
                 case a robot waits behind every other robot once per
                 leg

    The result is generous -- several times the makespan a real mission
    needs -- so a route that exists is still found. What it removes is
    only the dead search *past* any plausible route, which is exactly
    the cost of discovering that no route exists right now.

    Derived from the map and fleet rather than hard-coded, so changing
    speed, service time, fleet size or map keeps it proportionate.
    """

    span = warehouse.grid.width + warehouse.grid.height

    travel = span * max_legs * move_steps

    service = max_legs * (max(service_steps.values()) if service_steps else 0)

    traffic = robot_count * max_legs * move_steps

    return int(travel + service + traffic)


@dataclass
class Dispatch:
    """One robot-order assignment as actually dispatched."""

    sim_time: float
    robot_id: str
    order_id: str
    static_cost_m: float          # C_static(r, o), the prediction
    shelf_sequence: tuple[str, ...]
    dropoff_id: str
    start_cell: tuple
    planned_distance_m: float
    planned_wait_s: float


@dataclass
class Completion:
    order_id: str
    robot_id: str
    sim_time: float


class PerfBudget:
    """Wall-clock accounting for one dispatch, for profiling only.

    Every number here is CPU time, never simulated time. Nothing in
    this class is read by the allocator, the planner, the clock or any
    metric: a slower machine produces the same warehouse results with
    larger numbers printed. That separation is the whole point --
    planning cost must never leak into the science.
    """

    __slots__ = ("sim_time", "spans", "_open")

    # Only report a dispatch that actually stalled the loop.
    THRESHOLD_S = 0.100

    STAGES = (
        "allocator",
        "mission_build",
        "plan_fleet",
        "recovery",
    )

    def __init__(self, sim_time: float) -> None:
        self.sim_time = sim_time
        self.spans = dict.fromkeys(self.STAGES, 0.0)
        self._open = None

    @contextmanager
    def stage(self, name: str):
        """Time one stage. Non-reentrant: the outermost stage wins.

        Recovery calls straight through to mission building and fleet
        planning. Timing both levels would count the same seconds
        twice and make ``total`` meaningless, so a nested stage is
        absorbed by the one already running.
        """

        if self._open is not None:
            yield
            return

        self._open = name
        started = perf_counter()

        try:
            yield
        finally:
            self._open = None
            self.spans[name] = self.spans.get(name, 0.0) + (
                perf_counter() - started
            )

    @property
    def total(self) -> float:
        return sum(self.spans.values())

    def slow(self) -> bool:
        return self.total >= self.THRESHOLD_S

    def lines(self) -> tuple[str, ...]:
        return (
            f"[PERF t={self.sim_time:.3f}]",
            *(
                f"  {name}={self.spans[name]:.3f}s"
                for name in self.STAGES
                if self.spans.get(name, 0.0) > 0.0
            ),
            f"  total={self.total:.3f}s",
        )


@dataclass(frozen=True)
class Progress:
    """A periodic snapshot of the workload. Read-only reporting.

    The five order counts are the five OrderStatus values, so they
    always sum to the total. ``deferred_now`` is a *subset* of
    ``assigned`` -- assignments whose robot is chosen but which are
    still waiting for a MAPF plan -- and must never be added to the
    total separately.
    """

    sim_time: float
    total: int
    pending: int
    assigned: int
    in_progress: int
    completed: int
    failed: int
    deferred_now: int
    cumulative_deferrals: int
    busy: int
    available: int
    returning: int
    parked: int

    def lines(self) -> tuple[str, ...]:
        return (
            f"[PROGRESS t={self.sim_time:.1f}]",
            f"  orders: {self.completed}/{self.total} completed | "
            f"{self.in_progress} active | {self.assigned} assigned | "
            f"{self.pending} pending | {self.failed} failed",
            f"  deferred now: {self.deferred_now} | "
            f"cumulative deferrals: {self.cumulative_deferrals}",
            f"  robots: {self.busy} busy | {self.available} available | "
            f"{self.returning} returning | {self.parked} parked",
        )


@dataclass
class WorkloadResult:
    dispatches: list = field(default_factory=list)
    completions: list = field(default_factory=list)
    returns: dict = field(default_factory=dict)
    deferrals: int = 0
    plan_failures: int = 0

    # Peaks, for the final report.
    peak_in_progress: int = 0
    max_deferred_now: int = 0

    # Wall-clock planning cost, by stage. Profiling only: none of it
    # can change a simulated outcome.
    perf: dict = field(
        default_factory=lambda: dict.fromkeys(PerfBudget.STAGES, 0.0)
    )
    slow_dispatches: int = 0
    worst_dispatch_s: float = 0.0
    static_route_reuses: int = 0


class WorkloadExecutor:
    """Drives a whole OrderQueue across a fleet, end to end."""

    def __init__(
        self,
        warehouse,
        robots,
        queue: OrderQueue,
        allocator,
        homes: dict,
        *,
        resolution: float,
        speed_mps: float,
        service_seconds: dict,
        horizon_steps: int | None = None,
        on_advance=None,
        on_tick=None,
        on_dispatch=None,
        on_order_status=None,
        on_trajectory=None,
        on_progress=None,
        progress_interval_s: float = 10.0,
        on_perf=None,
    ) -> None:

        self.warehouse = warehouse
        self.queue = queue
        self.allocator = allocator
        self.homes = homes

        self.on_dispatch = on_dispatch

        # Visualization only. Called as (robot_id, trajectory) each
        # time a robot is given a new timed trajectory, so a playback
        # layer can follow a continuously dispatched fleet. It is
        # never read back: nothing downstream of this may influence
        # motion, metrics, reservations or the clock.
        self.on_trajectory = on_trajectory

        # Periodic reporting, in simulated seconds.
        # Profiling only. Called with a PerfBudget whenever one
        # dispatch spends more than PerfBudget.THRESHOLD_S of CPU.
        self.on_perf = on_perf
        self._perf: PerfBudget | None = None

        self.on_progress = on_progress
        self.progress_interval_s = float(progress_interval_s)
        self._next_progress = 0.0

        # Called as (order_id, OrderStatus, sim_time, robot_id) on every
        # lifecycle transition, so an owner can mirror it into shared
        # memory. This class never stores order state itself: the
        # OrderQueue's Order objects are the only copy.
        self.on_order_status = on_order_status

        self._external_tick = on_tick

        self.engine = FleetSimulationEngine(
            warehouse=warehouse,
            resolution=resolution,
            service_seconds=service_seconds,
            on_advance=on_advance,
            on_tick=self._tick,
            on_event=self._event,
        )

        for robot in robots:
            self.engine.add_robot(robot)

        # One memo for the whole run: the map and the service-cell
        # rule never change, so a leg costed for the first dispatch is
        # still valid for the hundredth.
        self.routes = RouteCache(warehouse)

        self.fifo_position = {
            order_id: index
            for index, order_id in enumerate(queue.order_ids)
        }

        # robot_id -> order_id it is currently executing.
        self.active: dict[str, str] = {}

        # robot_id -> services still to complete before the drop-off
        # service. Counting them is how the drop-off is recognised
        # without reaching into the trajectory.
        self._services_left: dict[str, int] = {}

        # Robots driving home at end of workload.
        self.returning: set[str] = set()
        self.parked: set[str] = set()

        # robot_id -> the OrderAssignment the allocator made for it,
        # still waiting for a MAPF plan. Prioritized MAPF is
        # incomplete: a robot can be genuinely unroutable right now
        # because others hold the aisle it needs. The MRTA decision is
        # made once and kept -- a deferred order is never quietly
        # handed to a different robot. Its order is already ASSIGNED,
        # so FIFO keeps flowing past it to later orders.
        self.deferred: dict = {}

        # Bumped whenever traffic meaningfully changes: a robot
        # finishes a mission, or a robot starts moving and will vacate
        # its cell. A deferred plan is retried only when this differs
        # from the value it last failed at, so one blocked robot
        # cannot re-pay a full space-time search on every engine tick.
        self._traffic_version = 0
        self._deferred_at: dict = {}

        # (robot, cell, order, shelf sequence, blocked cells) tuples
        # already known to have no static route. Bounded by the
        # states actually visited, and only ever consulted for a
        # verdict that is a pure function of that key.
        self._unroutable: set = set()

        # Robots driving to their staging cell to get out of the
        # way, not because the workload is over. They come back
        # into the available pool on arrival.
        self.repositioning: set[str] = set()

        self.result = WorkloadResult()

    # -- lifecycle ------------------------------------------

    def _set_status(self, order, status: OrderStatus, robot_id="") -> None:

        order.status = status

        if status is OrderStatus.ASSIGNED:
            order.assigned_robot = robot_id

        now = self.engine.sim_time

        if status is OrderStatus.IN_PROGRESS:
            order.start_time = now

        if status is OrderStatus.COMPLETED:
            order.completion_time = now

        if self.on_order_status is not None:
            self.on_order_status(
                order.order_id, status, now, order.assigned_robot
            )

    # -- availability ---------------------------------------

    def available_robots(self) -> list:
        """Robots standing idle with nothing assigned, in id order.

        A robot is available the moment it is IDLE: it has finished its
        retreat into the aisle and is free to take work from where it
        stands. It is not sent home first.
        """

        return [
            robot_id
            for robot_id in sorted(self.engine.robots)
            if self.engine.robots[robot_id].status is GridRobotStatus.IDLE
            and robot_id not in self.active
            and robot_id not in self.returning
            and robot_id not in self.repositioning
            and robot_id not in self.parked
            and robot_id not in self.deferred
        ]

    def stationary_cells(self, mover: str) -> frozenset:
        """Cells held by robots that are standing still.

        A robot with no trajectory in flight is not going to move out
        of the way, so it is a wall -- to the static route and to the
        space-time search alike. Robots that are driving are left out:
        their bookings expire, and resolving them is what the MAPF
        layer is for.
        """

        return frozenset(
            robot.cell
            for robot_id, robot in self.engine.robots.items()
            if robot_id != mover
            and robot_id not in self.active
            and robot_id not in self.returning
        )

    # -- dispatch -------------------------------------------

    def retry_deferred(self) -> int:
        """Re-attempt assignments that could not be planned earlier.

        Only assignments whose traffic has moved since they failed are
        retried, and they are retried *together*, as one batch through
        the fleet planner. The rest keep their pairing and wait:
        re-running the same search against the same occupancy returns
        the same answer and costs a full horizon to learn it.
        """

        if not self.deferred:
            return 0

        ready = [
            self.deferred[robot_id]
            for robot_id in sorted(self.deferred)
            if self._deferred_at.get(robot_id) != self._traffic_version
            and self.engine.robots[robot_id].status
            is GridRobotStatus.IDLE
        ]

        return self.launch_batch(ready)

    def dispatch(self) -> int:
        """Offer work to every free robot. Returns how many started."""

        outer = self._perf is None

        if outer:
            self._perf = PerfBudget(self.engine.sim_time)

        try:
            return self._dispatch()

        finally:
            if outer:
                self._close_perf()

    def _close_perf(self) -> None:
        """Retire the current budget and report it if it was slow."""

        budget = self._perf
        self._perf = None

        if budget is None:
            return

        for name, spent in budget.spans.items():
            self.result.perf[name] = (
                self.result.perf.get(name, 0.0) + spent
            )

        total = budget.total

        self.result.worst_dispatch_s = max(
            self.result.worst_dispatch_s, total
        )

        if not budget.slow():
            return

        self.result.slow_dispatches += 1

        if self.on_perf is not None:
            self.on_perf(budget)

    def _stage(self, name: str):
        """Time one stage, when a budget is open."""

        if self._perf is None:
            return nullcontext()

        return self._perf.stage(name)

    def _dispatch(self) -> int:

        started = self.retry_deferred()

        free = self.available_robots()

        if not free:
            return started

        pending = self.queue.pending()

        if not pending:
            # Nothing left to allocate. A robot with no assignment
            # heads for its staging cell -- both because that is where
            # the run must end, and because an idle robot loitering in
            # an aisle is exactly what blocks a deferred assignment.
            # Robots still owing a deferred plan are not in ``free``,
            # so their pairing is untouched.
            return started + self._send_home(free)

        # FIFO decides the candidate set; the allocator decides pairing.
        eligible = self.queue.eligible(len(free))

        with self._stage("allocator"):
            assignments = self.allocator.assign(
                self.warehouse,
                [(rid, self.engine.robots[rid].cell) for rid in free],
                eligible,
                self.fifo_position,
                self.routes,
            )

        if not assignments:
            self.result.deferrals += 1
            return started

        for assignment in assignments:

            # The MRTA decision is recorded before any planning, so a
            # deferral retries this exact pairing rather than
            # re-allocating the order to somebody else. ASSIGNED also
            # takes the order out of pending(), which is what lets FIFO
            # keep flowing to later orders while this one waits.
            self._set_status(
                self.queue.order(assignment.order_id),
                OrderStatus.ASSIGNED,
                assignment.robot_id,
            )

            self.deferred[assignment.robot_id] = assignment
            self._deferred_at[assignment.robot_id] = -1

        # One allocation decision, one fleet MAPF planning operation.
        return started + self.launch_batch(assignments)

    def _clear_deferred(self, robot_id: str) -> None:
        """This robot no longer owes a deferred plan."""

        self.deferred.pop(robot_id, None)
        self._deferred_at.pop(robot_id, None)

    def _bump_traffic(self) -> None:
        """Occupancy changed enough to be worth re-planning against."""

        self._traffic_version += 1

    def _build_mission(self, assignment):
        """Build the static mission chosen by the MRTA allocator.

        Dynamic robot occupancy is deliberately excluded here.

        The static A* layer answers where the mission should travel.
        FleetSimulationEngine and LocalTrafficController decide when
        each cell may actually be entered.
        """

        robot_id = assignment.robot_id
        robot = self.engine.robots[robot_id]

        try:
            return plan_from_cell(
                self.warehouse,
                robot.cell,
                list(assignment.zone_sequence),
                start_label=robot_id,
            )

        except (RuntimeError, ValueError):
            return None

    def launch_batch(self, assignments) -> int:
        """Build and launch one MRTA allocation batch.

        The allocator owns robot-order assignment.

        Static A* builds each MissionPlan. The complete batch is then
        handed directly to FleetSimulationEngine, where local traffic
        arbitration controls execution cell by cell.
        """

        assignments = list(assignments)

        if not assignments:
            return 0

        by_robot = {}
        missions = {}

        for assignment in assignments:

            with self._stage("mission_build"):
                mission = self._build_mission(assignment)

            if mission is None:
                self.result.plan_failures += 1

                self._deferred_at[
                    assignment.robot_id
                ] = self._traffic_version

                continue

            by_robot[assignment.robot_id] = assignment
            missions[assignment.robot_id] = mission

        if not missions:
            return 0

        # --------------------------------------------------
        # Register workload state before starting movement.
        #
        # A robot that immediately has to WAIT is still executing its
        # assigned order. Traffic waiting is not an MRTA deferral.
        # --------------------------------------------------

        for robot_id in sorted(missions):

            assignment = by_robot[robot_id]
            order = self.queue.order(
                assignment.order_id
            )

            self._clear_deferred(robot_id)

            self.active[robot_id] = order.order_id

            # Every shelf service plus the final drop-off service.
            self._services_left[robot_id] = (
                len(
                    assignment.route.shelf_sequence
                )
                + 1
            )

            self._set_status(
                order,
                OrderStatus.IN_PROGRESS,
                robot_id,
            )

        # --------------------------------------------------
        # Scientific execution.
        #
        # All initial movement intentions enter the local traffic
        # controller together.
        # --------------------------------------------------

        self.engine.assign_missions_batch(
            missions
        )

        # --------------------------------------------------
        # Dispatch records.
        #
        # There is no pre-planned traffic wait anymore. Waiting is
        # measured only from what actually occurs during execution.
        # --------------------------------------------------

        for robot_id in sorted(missions):

            assignment = by_robot[robot_id]
            plan = missions[robot_id]

            order = self.queue.order(
                assignment.order_id
            )

            record = Dispatch(
                sim_time=self.engine.sim_time,
                robot_id=robot_id,
                order_id=order.order_id,
                static_cost_m=assignment.cost,
                shelf_sequence=(
                    assignment.route.shelf_sequence
                ),
                dropoff_id=order.dropoff_id,
                start_cell=plan.path[0],
                planned_distance_m=(
                    plan.total_distance_m
                ),
                planned_wait_s=0.0,
            )

            self.result.dispatches.append(
                record
            )

            if self.on_dispatch is not None:
                self.on_dispatch(record)

        return len(missions)

    def _send_home(self, free) -> int:
        """No work left: return free robots to their staging cells.

        Home routes are static MissionPlans. Dynamic robot interaction
        is resolved by FleetSimulationEngine and LocalTrafficController,
        exactly like normal order execution.
        """

        missions = {}
        parking = set()

        for robot_id in free:

            robot = self.engine.robots[robot_id]

            home = self.homes[robot_id]

            home_cell = min(
                self.warehouse.zone_by_id(home).cells
            )

            # Already parked.
            if robot.cell == home_cell:

                self.parked.add(robot_id)

                self.result.returns[
                    robot_id
                ] = self.engine.sim_time

                continue

            try:

                plan = plan_from_cell(
                    self.warehouse,
                    robot.cell,
                    [home],
                    start_label=robot_id,
                )

            except (RuntimeError, ValueError):

                self.result.plan_failures += 1
                continue

            missions[robot_id] = plan
            parking.add(robot_id)

        if not missions:
            return 0

        # Mark them before execution begins so workload availability
        # cannot treat them as free robots.
        self.returning.update(missions)

        try:

            self.engine.assign_missions_batch(
                missions,
                parking=parking,
            )

        except Exception:

            # Keep executor state consistent if assignment itself
            # fails before execution begins.
            for robot_id in missions:
                self.returning.discard(robot_id)

            raise

        return len(missions)

    # -- engine callbacks -----------------------------------

    def _event(self, event) -> None:
        """Watch for the drop-off service that completes an order."""

        if event.kind is not EventType.SERVICE_COMPLETE:
            return

        robot_id = event.robot_id

        left = self._services_left.get(robot_id)

        if left is None:
            return

        left -= 1
        self._services_left[robot_id] = left

        if left > 0:
            return

        # That was the drop-off service: the order is delivered. The
        # robot still has its retreat into the aisle to drive before it
        # is free again.
        order_id = self.active.get(robot_id)

        if order_id is None:
            return

        self._set_status(
            self.queue.order(order_id), OrderStatus.COMPLETED, robot_id
        )

        self.result.completions.append(
            Completion(
                order_id=order_id,
                robot_id=robot_id,
                sim_time=self.engine.sim_time,
            )
        )

    def _retire(self, robot_id: str) -> None:
        """Retire one finished mission.

        FleetSimulationEngine's live ReservationTable already leaves
        the robot owning the cell where it stopped.

        No trajectory reservations need to be collapsed or rebuilt.
        """

        # Traffic changed because this robot completed its mission and
        # may immediately become available for another assignment.
        self._bump_traffic()

    def snapshot(self, sim_time: float | None = None) -> Progress:
        """Current counts. Pure reporting: changes nothing."""

        if sim_time is None:
            sim_time = self.engine.sim_time

        counts = {status: 0 for status in OrderStatus}

        for order in self.queue.orders:
            counts[order.status] += 1

        robots = self.engine.robots

        available = sum(
            1
            for robot_id, robot in robots.items()
            if robot.status is GridRobotStatus.IDLE
            and robot_id not in self.active
            and robot_id not in self.returning
            and robot_id not in self.repositioning
            and robot_id not in self.parked
        )

        return Progress(
            sim_time=sim_time,
            total=len(self.queue),
            pending=counts[OrderStatus.PENDING],
            assigned=counts[OrderStatus.ASSIGNED],
            in_progress=counts[OrderStatus.IN_PROGRESS],
            completed=counts[OrderStatus.COMPLETED],
            failed=counts[OrderStatus.FAILED],
            deferred_now=len(self.deferred),
            cumulative_deferrals=self.result.plan_failures,
            busy=len(self.active),
            available=available,
            returning=len(self.returning) + len(self.repositioning),
            parked=len(self.parked),
        )

    def _report_progress(self, sim_time: float) -> None:
        """Emit a snapshot every ``progress_interval_s`` simulated seconds."""

        current = self.snapshot(sim_time)

        # Peaks are tracked every tick, not only when a line is
        # printed, so they do not depend on the reporting interval.
        self.result.peak_in_progress = max(
            self.result.peak_in_progress, current.in_progress
        )
        self.result.max_deferred_now = max(
            self.result.max_deferred_now, current.deferred_now
        )

        if self.on_progress is None or self.progress_interval_s <= 0:
            # Peaks are still tracked above; only reporting is off.
            return

        if sim_time < self._next_progress:
            return

        # Skip whole intervals the clock jumped over rather than
        # emitting a burst of catch-up lines.
        step = self.progress_interval_s

        self._next_progress = (int(sim_time / step) + 1) * step

        self.on_progress(current)

    def _tick(self, sim_time, batch) -> None:
        """After every timestamp: retire finishers, then dispatch."""


        for robot_id, robot in self.engine.robots.items():

            if robot.status is not GridRobotStatus.IDLE:
                continue

            if robot_id in self.active:
                # Retreat done; the order was completed at drop-off.
                self.active.pop(robot_id)
                self._services_left.pop(robot_id, None)
                self._retire(robot_id)

            if robot_id in self.returning:
                self.returning.discard(robot_id)
                self.parked.add(robot_id)
                self.result.returns[robot_id] = sim_time
                self._retire(robot_id)

            if robot_id in self.repositioning:
                # Out of the lane and available again. Its deferred
                # assignment is deliberately kept: repositioning moved
                # the robot, it did not cancel the MRTA decision, and
                # _retire bumps traffic so the pairing is retried from
                # where the robot now stands.
                self.repositioning.discard(robot_id)
                self._retire(robot_id)

        self.dispatch()

        self._report_progress(sim_time)

        if self._external_tick is not None:
            self._external_tick(sim_time, batch)

    def _yield_blockers(self) -> int:
        """Legacy MAPF recovery is disabled.

        Local traffic conflicts are resolved inside
        FleetSimulationEngine. There is no full-horizon traffic plan
        to discard or rebuild.
        """

        return 0

    def finished(self) -> bool:
        return (
            not self.queue.pending()
            and not self.deferred
            and not self.active
            and not self.returning
            and len(self.parked) == len(self.engine.robots)
        )

    def _recover(self) -> int:
        """Break a standstill without touching any MRTA pairing.

        The clock has run dry with work still owed. That means every
        deferred assignment is blocked by a robot standing still, and
        no robot is moving to change that -- the circular wait this
        executor used to stall on.

        Recovery only ever moves robots that owe nothing:

          1. retry the deferred batch, in case traffic did move
          2. send idle unassigned robots to their staging cells, which
             is where they belong at the end anyway and clears the
             aisles the deferred robots need
          3. as a last resort, move a robot that is itself deferred out
             of the lane, so it stops blocking its own neighbours

        No order is re-allocated and no pairing is dropped at any step.
        """

        self._bump_traffic()

        started = self.retry_deferred()

        if started:
            return started

        idle = [
            robot_id
            for robot_id in sorted(self.engine.robots)
            if self.engine.robots[robot_id].status
            is GridRobotStatus.IDLE
            and robot_id not in self.active
            and robot_id not in self.returning
            and robot_id not in self.repositioning
            and robot_id not in self.parked
            and robot_id not in self.deferred
        ]

        if idle:

            started = self._send_home(idle)

            if started:
                return started

        return self._yield_blockers()

    def run(self, max_rounds: int = 500) -> WorkloadResult:
        """Execute the whole workload, then bring the fleet home."""

        for _ in range(max_rounds):

            # Seeds the first batch, and restarts the clock whenever a
            # round ended with robots still owed work.
            self.dispatch()

            self.engine.run()

            if self.finished():
                return self.result

            # The clock ran dry with work outstanding. Never give up
            # without actually trying to unblock it.
            self._perf = PerfBudget(self.engine.sim_time)

            try:
                with self._stage("recovery"):
                    recovered = self._recover()

            finally:
                self._close_perf()

            if not recovered:
                break

        if not self.finished():
            raise RuntimeError(
                f"workload stalled at t={self.engine.sim_time:.4f}: "
                f"{len(self.queue.pending())} pending, "
                f"{len(self.deferred)} deferred, "
                f"{len(self.active)} in flight, "
                f"{len(self.parked)}/{len(self.engine.robots)} parked"
            )

        return self.result

    # -- reporting ------------------------------------------

    def totals(self) -> dict:

        robots = self.engine.robots.values()

        return {
            "makespan_s": self.engine.sim_time,
            "distance_m": sum(r.distance_m for r in robots),
            "travel_time_s": sum(r.travel_time_s for r in robots),
            "waiting_time_s": sum(r.waiting_time_s for r in robots),
            "service_time_s": sum(r.service_time_s for r in robots),
            "replans": sum(r.replan_count for r in robots),
            "conflicts": len(self.engine.reservations.audit()),
            "expansions": sum(
                r.space_time_expansions for r in robots
            ),
            "events": self.engine.processed,
        }
