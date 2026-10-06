"""Run a whole warehouse workload: FIFO -> allocator -> fleet.

This is the execution half of the milestone. It owns no allocation
policy of its own: it asks ``task_management`` who should take what,
and then drives the answer through the existing planner, the existing
reactive local traffic layer and the fleet engine.

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

from mrta_core.battery import BatteryDecisionContext, EnergyRequirement
from mrta_core.order_queue import OrderQueue
from mrta_core.orders import OrderStatus
from mrta_core.warehouse_map import ZoneType

from simulation_env.charging import (
    DESTINATION,
    SETTLED_STATES,
    TRAVEL_STATES,
    ChargeRecord,
    ChargeState,
    ChargingSaturationError,
    ResourceState,
    TerminalBank,
)
from simulation_env.fleet_engine import EventType, FleetSimulationEngine
from simulation_env.grid_robot import GridRobotStatus
from simulation_env.stage_parking import StageParking
from mrta_core.warehouse_routing import (
    RouteCache,
    nearest_reachable_zone,
    non_transit_cells,
    plan_from_cell,
)


# An order visits at most three shelves and one drop-off.
MAX_ORDER_LEGS = 4



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

    # Decision-time battery features of this pair, as the allocator saw
    # them when it decided. None without battery accounting.
    energy: EnergyRequirement | None = None


class IneligibleAssignmentError(RuntimeError):
    """An allocator returned a robot-order pair the battery forbids.

    Battery eligibility is a hard feasibility constraint shared by every
    MRTA strategy. The executor never executes such a pair and never
    quietly hands the order to a different robot instead: that would be
    the infrastructure making an allocation decision.
    """


@dataclass(frozen=True)
class BatteryEvent:
    """A battery state change the executor acted on. Reporting only."""

    sim_time: Fraction
    robot_id: str

    # CHARGE_DUE        SOC <= charge-due threshold
    # REQUEST           entered the charging workflow
    # CHARGE_DUE_AT_END finished its last order due, no pending work left
    # RESERVE           a charger, holding cell or fallback Stage Zone
    #                   was reserved and the robot set off (B3)
    # ARRIVE            the robot is on its reserved terminal (B3)
    # SATURATION        no charger or holding cell was free (B3)
    kind: str
    reason: str
    soc_percent: Fraction
    cell: tuple
    detail: str = ""


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
    still waiting for a route -- and must never be added to the
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
    charge_requests: int = 0

    def lines(self) -> tuple[str, ...]:
        waiting_for_charge = (
            f" | {self.charge_requests} in charge request"
            if self.charge_requests
            else ""
        )

        return (
            f"[PROGRESS t={self.sim_time:.1f}]",
            f"  orders: {self.completed}/{self.total} completed | "
            f"{self.in_progress} active | {self.assigned} assigned | "
            f"{self.pending} pending | {self.failed} failed",
            f"  deferred now: {self.deferred_now} | "
            f"cumulative deferrals: {self.cumulative_deferrals}",
            f"  robots: {self.busy} busy | {self.available} available | "
            f"{self.returning} returning | {self.parked} parked"
            + waiting_for_charge,
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

    # Battery eligibility, decision time. Reporting only.
    battery_events: list = field(default_factory=list)
    candidate_pairs: int = 0            # scored by the allocator
    ineligible_pairs: int = 0           # of those, battery-ineligible
    availability_pairs: int = 0         # scored by CHARGE_NEEDED checks
    assignments_validated: int = 0
    min_assigned_margin: Fraction | None = None

    # Charger travel (B3). Reporting only.
    saturation_events: int = 0
    yield_events: int = 0


class WorkloadExecutor:
    """Drives a whole OrderQueue across a fleet, end to end."""

    def __init__(
        self,
        warehouse,
        robots,
        queue: OrderQueue,
        allocator,
        *,
        resolution: float,
        speed_mps: float,
        service_seconds: dict,
        on_advance=None,
        on_tick=None,
        on_dispatch=None,
        on_order_status=None,
        on_trajectory=None,
        on_progress=None,
        progress_interval_s: float = 10.0,
        on_perf=None,
        battery=None,
        on_battery_event=None,
    ) -> None:

        self.warehouse = warehouse
        self.queue = queue
        self.allocator = allocator

        # Stage Zone cells are a shared fleet resource, claimed on the
        # way in. A robot's spawn cell gives it no title to anything.
        self.parking = StageParking(warehouse)

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

        # ``battery`` is a BatteryConfig. It switches on the engine's SOC
        # ledger and, with it, battery-aware dispatch: the eligibility
        # mask handed to the allocator, CHARGE_DUE and CHARGE_NEEDED.
        self.engine = FleetSimulationEngine(
            warehouse=warehouse,
            resolution=resolution,
            service_seconds=service_seconds,
            on_advance=on_advance,
            on_tick=self._tick,
            on_event=self._event,
            battery=battery,
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
        # still waiting for a route. Prioritized planning is
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

        # -- battery ------------------------------------------------
        #
        # The ledger the engine books. None without battery accounting,
        # in which case dispatch is exactly what it was before B2.
        self.battery = self.engine.battery
        self.on_battery_event = on_battery_event

        # robot_id -> the REQUEST event that took it out of the fleet.
        # A requesting robot takes no new order. It is sent at once to a
        # reserved charger, holding cell or, when both banks are full, a
        # Stage Zone, and it is never left standing on a transit cell.
        # B3 has no charging: it stays on that terminal for the run.
        self.requesting: dict = {}

        # robot_id -> its trip through the charging workflow.
        self.charge: dict[str, ChargeRecord] = {}
        self.chargers: TerminalBank | None = None
        self.holding: TerminalBank | None = None

        # robot_id -> the CHARGE_DUE event raised while it was busy. The
        # order is never interrupted; the flag is acted on only once
        # the whole order, retreat included, has finished.
        self._due_flag: dict = {}

        self._chargers: tuple[str, ...] = ()
        self._nearest_charger_memo: dict = {}

        if self.battery is not None:

            self._chargers = tuple(sorted(
                zone.zone_id
                for zone in warehouse.zones_of_type(ZoneType.CHARGING)
            ))

            if not self._chargers:
                raise ValueError(
                    "battery accounting is enabled but the map has no "
                    "CHARGING zone, so no order can ever be battery "
                    "eligible"
                )

            self.chargers = TerminalBank(
                warehouse, ZoneType.CHARGING, "charger"
            )
            self.holding = TerminalBank(
                warehouse, ZoneType.CHARGE_QUEUE, "holding"
            )

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
            and robot_id not in self.requesting
        ]

    # -- battery --------------------------------------------

    def _nearest_charger(self, cell) -> tuple[str, float]:
        """(charger_id, metres) of the cheapest directed-A* charger leg.

        Static geometry, independent of who is charging, so it is
        memoized per cell for the whole run.
        """

        found = self._nearest_charger_memo.get(cell)

        if found is None:

            found = nearest_reachable_zone(
                self.warehouse,
                cell,
                self._chargers,
                self.routes,
            )

            if found is None:
                raise RuntimeError(
                    f"no charger is reachable from {cell}; the map "
                    f"fails the topology gate"
                )

            self._nearest_charger_memo[cell] = found

        return found

    def _battery_context(self, robot_ids) -> BatteryDecisionContext:
        """Decision-time battery view for ``robot_ids``, at this instant."""

        now = self.engine.time
        service = self.engine.service_seconds

        return BatteryDecisionContext(
            config=self.battery.config,
            soc={
                robot_id: self.battery.soc(robot_id, now)
                for robot_id in robot_ids
            },
            shelf_service_s=service.get(ZoneType.SHELF, Fraction(0)),
            dropoff_service_s=service.get(ZoneType.DROPOFF, Fraction(0)),
            nearest_charger=self._nearest_charger,
        )

    def _battery_event(self, robot_id, kind, reason, detail="") -> BatteryEvent:

        now = self.engine.time

        event = BatteryEvent(
            sim_time=now,
            robot_id=robot_id,
            kind=kind,
            reason=reason,
            soc_percent=self.battery.soc(robot_id, now),
            cell=self.engine.robots[robot_id].cell,
            detail=detail,
        )

        self.result.battery_events.append(event)

        if self.on_battery_event is not None:
            self.on_battery_event(event)

        return event

    def _enter_request(self, robot_id, reason, detail) -> None:
        """Leave the available fleet for the charging workflow.

        The robot is only marked here. ``_evacuate`` reserves its
        terminal and starts it moving at this same instant, before any
        order is allocated.
        """

        event = self._battery_event(robot_id, "REQUEST", reason, detail)

        self.requesting[robot_id] = event

        self.charge[robot_id] = ChargeRecord(
            robot_id=robot_id,
            reason=reason,
            requested_at=event.sim_time,
            request_cell=event.cell,
            request_soc=event.soc_percent,
        )

    def _evacuate(self) -> int:
        """Send every robot in REQUEST to a reserved non-transit terminal.

        1. a FREE charger, cheapest by directed A*
        2. else a FREE holding cell, cheapest by directed A*
        3. else record saturation and take the nearest free Stage Zone

        Every reservation is made before the robot moves. Robots asking
        at the same instant are paired as one batch, so they reserve
        distinct cells. There is no queue promotion, preemption or
        charging here. Returns how many robots set off.
        """

        waiting = sorted(
            robot_id
            for robot_id, record in self.charge.items()
            if record.state is ChargeState.REQUEST
        )

        if not waiting:
            return 0

        now = self.engine.time
        robots = self.engine.robots

        cells = {robot_id: robots[robot_id].cell for robot_id in waiting}

        # Bank cells somebody outside this batch is standing on are not
        # free to give away.
        blocked = frozenset(
            robot.cell
            for robot_id, robot in robots.items()
            if robot_id not in cells
        )

        claims = {}

        for bank in (self.chargers, self.holding):

            left = {r: c for r, c in cells.items() if r not in claims}

            if not left:
                break

            found = bank.assign(
                left, now=now, blocked_cells=blocked, cache=self.routes
            )

            for robot_id, (zone_id, cost) in found.items():
                claims[robot_id] = (bank.kind, zone_id, bank.slots[zone_id].cell, cost)

        left = {r: c for r, c in cells.items() if r not in claims}

        if left:

            for robot_id in sorted(left):
                self.result.saturation_events += 1
                self._battery_event(
                    robot_id,
                    "SATURATION",
                    self.charge[robot_id].reason,
                    f"no free charger ({len(self.chargers.slots)}) or "
                    f"holding cell ({len(self.holding.slots)}); nearest "
                    f"free Stage Zone instead",
                )

            stage = self.parking.assign(
                left, blocked_cells=blocked, cache=self.routes
            )

            for robot_id, zone_id in stage.items():
                claims[robot_id] = (
                    "stage",
                    zone_id,
                    self.parking.cell_of[zone_id],
                    self.parking.route_cost(left[robot_id], zone_id, self.routes),
                )

            stranded = sorted(set(left) - set(stage))

            if stranded:
                raise ChargingSaturationError(
                    f"no charger, holding cell or Stage Zone is free or "
                    f"reachable for {stranded} at t={float(now):.4f}; a "
                    f"REQUEST robot is never left in an aisle"
                )

        missions = {}

        for robot_id in waiting:

            kind, zone_id, target, cost = claims[robot_id]
            record = self.charge[robot_id]

            record.kind = kind
            record.zone_id = zone_id
            record.target_cell = target
            record.route_cost_m = cost
            record.departed_at = now

            self._battery_event(
                robot_id,
                "RESERVE",
                record.reason,
                f"{kind} {zone_id} at {target}, directed A* {cost:.1f} m",
            )

            if cells[robot_id] == target:
                # Only a fallback Stage Zone can be the cell a robot
                # already stands on.
                self._settle(robot_id)
                continue

            # The same production router and non-transit view the cost
            # above came from, so this is the route that was costed.
            plan = plan_from_cell(
                self.warehouse,
                cells[robot_id],
                [zone_id],
                start_label=robot_id,
                cache=self.routes,
            )

            record.path = plan.path
            record.state = DESTINATION[kind][0]
            missions[robot_id] = plan

        if missions:
            # Recovery moves, like parking: they never outrank order
            # work in traffic and never count as mission completion.
            self.engine.assign_missions_batch(
                missions, parking=set(missions)
            )

        return len(missions)

    def _settle(self, robot_id) -> None:
        """The robot is on its reserved terminal: occupy it."""

        record = self.charge[robot_id]
        cell = self.engine.robots[robot_id].cell
        now = self.engine.time

        if cell != record.target_cell:
            raise RuntimeError(
                f"{robot_id} ended its {record.kind} trip at {cell}, not "
                f"at {record.zone_id} {record.target_cell}"
            )

        if record.kind == "charger":
            self.chargers.occupy(record.zone_id, robot_id, now, cell)

        elif record.kind == "holding":
            self.holding.occupy(record.zone_id, robot_id, now, cell)

        record.state = DESTINATION[record.kind][1]
        record.arrived_at = now
        record.arrival_soc = self.battery.soc(robot_id, now)

        self._battery_event(
            robot_id,
            "ARRIVE",
            record.reason,
            f"{record.state.value} {record.zone_id}",
        )

    def _has_eligible_order(self, robot_id, pending) -> bool:
        """Is any pending order battery-eligible for this robot?

        Checked against every pending order, in FIFO order, stopping at
        the first eligible one -- never against the allocator's batch
        alone. Scored through the allocator's own shared ``score()``,
        so the route and energy are exactly what allocation would use.
        """

        context = self._battery_context([robot_id])
        cell = self.engine.robots[robot_id].cell

        found = False

        for order in pending:

            (candidate,) = self.allocator.score(
                self.warehouse,
                [(robot_id, cell)],
                [order],
                self.fifo_position,
                self.routes,
                battery=context,
            )

            if candidate.eligible:
                found = True
                break

        self.result.availability_pairs += context.evaluated

        return found

    def _battery_gate(self, free, pending) -> list:
        """Take CHARGE_DUE and CHARGE_NEEDED robots out of the pool.

        Only called while pending work exists. With no pending order a
        robot is never CHARGE_NEEDED; end-of-work recovery is G6.
        """

        now = self.engine.time
        due = self.battery.config.charge_due_percent

        offered = []

        for robot_id in free:

            soc = self.battery.soc(robot_id, now)

            if soc <= due:
                self._battery_event(
                    robot_id, "CHARGE_DUE", "CHARGE_DUE", "while idle"
                )
                self._enter_request(robot_id, "CHARGE_DUE", "while idle")

            elif not self._has_eligible_order(robot_id, pending):
                self._enter_request(
                    robot_id,
                    "CHARGE_NEEDED",
                    f"no battery-eligible order among "
                    f"{len(pending)} pending",
                )

            else:
                offered.append(robot_id)

        return offered

    def _flag_charge_due(self, batch) -> None:
        """Raise the due flag on a busy robot the moment SOC <= 10%.

        Checked for robots that had an event at this timestamp; their
        SOC only drops in steps at their own moves and services. The
        order carries on regardless.
        """

        now = self.engine.time
        due = self.battery.config.charge_due_percent

        for robot_id in sorted({event.robot_id for event in batch}):

            if robot_id not in self.active or robot_id in self._due_flag:
                continue

            if self.battery.soc(robot_id, now) <= due:
                self._due_flag[robot_id] = self._battery_event(
                    robot_id,
                    "CHARGE_DUE",
                    "CHARGE_DUE",
                    f"while busy on {self.active[robot_id]}; "
                    f"the order continues",
                )

    def _after_order(self, robot_id) -> None:
        """A robot has finished its whole order, retreat included."""

        due = self.battery.config.charge_due_percent

        flagged = robot_id in self._due_flag

        if not flagged and self.battery.soc(robot_id, self.engine.time) <= due:
            # Cannot normally happen -- SOC only falls at the robot's own
            # events, and the completion is one -- but never miss it.
            self._battery_event(
                robot_id, "CHARGE_DUE", "CHARGE_DUE", "at order completion"
            )
            flagged = True

        self._due_flag.pop(robot_id, None)

        if not flagged:
            return

        if self.queue.pending():
            self._enter_request(robot_id, "CHARGE_DUE", "order finished")
            return

        # No work left: end-of-work recovery (G6) decides. Returning to
        # a Stage Zone is today's behaviour; charging first when a Stage
        # Zone is out of reach needs a charger, which B2 does not have.
        self._battery_event(
            robot_id,
            "CHARGE_DUE_AT_END",
            "CHARGE_DUE",
            "no pending work; parks as usual (G6 charge-first not "
            "implemented before charging exists)",
        )

    def _check_battery_feasibility(self, assignments, battery) -> None:
        """Re-derive every returned pair's eligibility; refuse any violation.

        Independent of the allocator's own bookkeeping: the energy is
        recomputed here from the returned route and this executor's SOC.
        """

        for assignment in assignments:

            if assignment.robot_id not in battery.soc:
                raise IneligibleAssignmentError(
                    f"allocator assigned {assignment.robot_id}, which was "
                    f"not offered at this decision"
                )

            route = assignment.route

            if route.plan is None:
                raise IneligibleAssignmentError(
                    f"allocator assigned {assignment.robot_id} -> "
                    f"{assignment.order_id} with no route"
                )

            requirement = battery.requirement(
                assignment.robot_id,
                order_distance_m=route.distance_m,
                shelf_services=len(route.shelf_sequence),
                end_cell=route.plan.path[-1],
            )

            if not requirement.battery_eligible:
                raise IneligibleAssignmentError(
                    f"allocator {getattr(self.allocator, 'name', '?')!r} "
                    f"assigned {assignment.robot_id} -> "
                    f"{assignment.order_id}, which is battery-ineligible: "
                    f"SOC {float(requirement.current_soc_percent):.4f}% < "
                    f"required {float(requirement.required_soc_percent):.4f}% "
                    f"(margin {float(requirement.energy_margin_percent):.4f}%). "
                    f"Battery eligibility is a hard constraint; the pair is "
                    f"refused, not re-assigned."
                )

            margin = requirement.energy_margin_percent

            if (
                self.result.min_assigned_margin is None
                or margin < self.result.min_assigned_margin
            ):
                self.result.min_assigned_margin = margin

        self.result.assignments_validated += len(assignments)

    def stationary_cells(self, mover: str) -> frozenset:
        """Cells held by robots that are standing still.

        A robot with no trajectory in flight is not going to move out
        of the way, so it is a wall -- to the static route and to the
        space-time search alike. Robots that are driving are left out:
        their bookings expire, and resolving them is what the traffic
        layer is for.
        """

        return frozenset(
            robot.cell
            for robot_id, robot in self.engine.robots.items()
            if robot_id != mover
            and robot_id not in self.active
            and robot_id not in self.returning
            and not (
                robot_id in self.charge
                and self.charge[robot_id].state in TRAVEL_STATES
            )
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

        pending = self.queue.pending()

        if self.battery is not None:

            # Battery first: a robot that is CHARGE_DUE, or CHARGE_NEEDED
            # against every pending order, is not offered work at all,
            # so the FIFO batch below is sized for robots that can take
            # it.
            if free and pending:
                free = self._battery_gate(free, pending)

            # Every robot in REQUEST -- from the gate, or from finishing
            # a flagged order this tick -- leaves the aisle now, before
            # any order is allocated.
            started += self._evacuate()

        if not free:
            return started

        if not pending:
            # Nothing left to allocate. A robot with no assignment
            # heads for its staging cell -- both because that is where
            # the run must end, and because an idle robot loitering in
            # an aisle is exactly what blocks a deferred assignment.
            # Robots still owing a deferred plan are not in ``free``,
            # so their pairing is untouched.
            return started + self._send_home(free)

        battery = None

        if self.battery is not None:
            battery = self._battery_context(free)

        # FIFO decides the candidate set; the allocator decides pairing.
        eligible = self.queue.eligible(len(free))

        with self._stage("allocator"):
            assignments = self.allocator.assign(
                self.warehouse,
                [(rid, self.engine.robots[rid].cell) for rid in free],
                eligible,
                self.fifo_position,
                self.routes,
                battery=battery,
            )

        if battery is not None:

            self.result.candidate_pairs += battery.evaluated
            self.result.ineligible_pairs += battery.ineligible

            # Before any order changes state: an ineligible pair stops
            # the run here rather than being executed or re-routed.
            self._check_battery_feasibility(assignments, battery)

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

        # One allocation decision, one fleet planning operation.
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
                energy=assignment.energy,
            )

            self.result.dispatches.append(
                record
            )

            if self.on_dispatch is not None:
                self.on_dispatch(record)

        return len(missions)

    def _send_home(self, free) -> int:
        """No work left: park free robots on the nearest free Stage Zone.

        The bank is a shared resource, so the cell is chosen here by
        directed A* cost and claimed before the mission is handed over.
        Two robots converging on the same bank therefore cannot pick
        the same cell, and a robot does not go back to the cell it
        spawned on unless that really is its cheapest free option.

        Parking routes are static MissionPlans. Dynamic robot
        interaction is resolved by FleetSimulationEngine and
        LocalTrafficController, exactly like normal order execution.
        """

        free = list(free)
        batch = set(free)

        # Bank cells a robot outside this batch is still standing on
        # are not free to give away.
        blocked = frozenset(
            robot.cell
            for robot_id, robot in self.engine.robots.items()
            if robot_id not in batch
        )

        claims = self.parking.assign(
            {
                robot_id: self.engine.robots[robot_id].cell
                for robot_id in free
            },
            blocked_cells=blocked,
            cache=self.routes,
        )

        missions = {}
        parking = set()

        for robot_id in free:

            robot = self.engine.robots[robot_id]

            zone_id = claims.get(robot_id)

            if zone_id is None:
                # Every bank cell is claimed or unreachable from here.
                # Nothing to do but leave the robot standing.
                self.result.plan_failures += 1
                continue

            stage_cell = self.parking.cell_of[zone_id]

            # Already standing on the cell it claimed.
            if robot.cell == stage_cell:

                self.parked.add(robot_id)

                self.result.returns[
                    robot_id
                ] = self.engine.sim_time

                continue

            try:

                plan = plan_from_cell(
                    self.warehouse,
                    robot.cell,
                    [zone_id],
                    start_label=robot_id,
                )

            except (RuntimeError, ValueError):

                # Hand the cell back, or it stays claimed by a robot
                # that never drove to it.
                self.parking.release(robot_id)

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

        if event.kind is EventType.YIELD_COMPLETE:
            self.result.yield_events += 1

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
            and robot_id not in self.requesting
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
            charge_requests=len(self.requesting),
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

        if self.battery is not None:
            self._flag_charge_due(batch)

        for robot_id, robot in self.engine.robots.items():

            if robot.status is not GridRobotStatus.IDLE:
                continue

            if robot_id in self.active:
                # Retreat done; the order was completed at drop-off.
                self.active.pop(robot_id)
                self._services_left.pop(robot_id, None)
                self._retire(robot_id)

                if self.battery is not None:
                    self._after_order(robot_id)

            if robot_id in self.returning:
                self.returning.discard(robot_id)
                self.parked.add(robot_id)
                self.result.returns[robot_id] = sim_time
                self._retire(robot_id)

            record = self.charge.get(robot_id)

            if record is not None and record.state in TRAVEL_STATES:
                self._settle(robot_id)
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

    def settled_charging(self) -> list[str]:
        """Robots standing on their reserved charging-workflow terminal."""

        return sorted(
            robot_id
            for robot_id, record in self.charge.items()
            if record.state in SETTLED_STATES
        )

    def finished(self) -> bool:
        # B3 has no charging, so a robot on its charger, holding cell or
        # fallback Stage Zone stays there. A robot still in REQUEST or
        # still driving to its terminal is not finished. Every other
        # robot must be parked.
        return (
            not self.queue.pending()
            and not self.deferred
            and not self.active
            and not self.returning
            and len(self.parked) + len(self.settled_charging())
            == len(self.engine.robots)
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
            and robot_id not in self.requesting
        ]

        if idle:

            started = self._send_home(idle)

            if started:
                return started

        # Nothing further to try: local traffic conflicts are resolved
        # inside FleetSimulationEngine, and there is no full-horizon
        # traffic plan left to discard and rebuild.
        return 0

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
                f"{len(self.parked)}/{len(self.engine.robots)} parked, "
                f"{len(self.requesting)} in charge request"
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
            "reservation_violations": len(
                self.engine.reservations.audit()
            ),
            "events": self.engine.processed,
        }


# ----------------------------------------------------------------------
# Reporting.
# ----------------------------------------------------------------------


def battery_event_line(event: BatteryEvent) -> str:
    return (
        f"  t={float(event.sim_time):8.3f}  {event.kind:<17s} "
        f"{event.robot_id}  SOC {float(event.soc_percent):8.4f}%  "
        f"cell={event.cell}  {event.reason}: {event.detail}"
    )


def eligibility_report_lines(executor: "WorkloadExecutor") -> list[str]:
    """Battery eligibility at decision time, for any front end."""

    result = executor.result
    events = result.battery_events

    due = [e for e in events if e.kind == "CHARGE_DUE"]

    due_busy = sum(1 for e in due if e.detail.startswith("while busy"))
    due_idle = sum(1 for e in due if e.detail == "while idle")

    needed = [
        e for e in events
        if e.kind == "REQUEST" and e.reason == "CHARGE_NEEDED"
    ]

    at_end = [e for e in events if e.kind == "CHARGE_DUE_AT_END"]

    margin = result.min_assigned_margin

    lines = [
        "BATTERY ELIGIBILITY (decision time; hard mask for every strategy)",
        f"  allocator pairs  : {result.candidate_pairs} evaluated, "
        f"{result.ineligible_pairs} battery-ineligible",
        f"  CHARGE_NEEDED    : {result.availability_pairs} pairs scanned "
        f"over all pending orders",
        f"  assignments      : {result.assignments_validated} validated by "
        f"the executor, 0 ineligible",
        "  lowest margin    : "
        + ("n/a" if margin is None else f"{float(margin):.4f} % of SOC "
           f"over an assigned pair"),
        f"  CHARGE_DUE       : {len(due)} "
        f"({due_busy} while busy, {due_idle} while idle; "
        f"{len(at_end)} with no work left)",
        f"  CHARGE_NEEDED    : {len(needed)}",
        "  charge request   : "
        + (", ".join(sorted(executor.requesting)) or "none"),
    ]

    return lines


def charging_report_lines(executor: "WorkloadExecutor") -> list[str]:
    """Charger and holding reservations, and every REQUEST trip (B3)."""

    if executor.chargers is None:
        return []

    def bank_lines(bank, every_row):

        counts = "  ".join(
            f"{state.value} {bank.count(state)}" for state in ResourceState
        )

        lines = [f"  {bank.kind:<8s} : {len(bank.slots)} cells | {counts}"]

        for slot in bank.slots.values():

            if slot.state is ResourceState.FREE and not every_row:
                continue

            line = f"    {slot.zone_id:<16s} {str(slot.cell):<9s} {slot.state.value:<9s}"

            if slot.robot_id is not None:
                line += (
                    f" {slot.robot_id}  A* {slot.route_cost_m:5.1f} m  "
                    f"reserved t={float(slot.reserved_at):.4f}"
                )

            if slot.occupied_at is not None:
                line += f"  occupied t={float(slot.occupied_at):.4f}"

            lines.append(line)

        return lines

    records = [executor.charge[r] for r in sorted(executor.charge)]
    terminals = non_transit_cells(executor.warehouse)

    def count(*states):
        return sum(1 for record in records if record.state in states)

    # Not driving, yet standing on a transit cell: must never happen.
    stuck = [
        record.robot_id
        for record in records
        if record.state not in TRAVEL_STATES
        and executor.engine.robots[record.robot_id].cell not in terminals
    ]

    lines = [
        "CHARGER TRAVEL (B3: evacuation only -- no charging, SOC never rises)",
        *bank_lines(executor.chargers, every_row=True),
        *bank_lines(executor.holding, every_row=False),
        f"  requests : {len(records)} | at charger "
        f"{count(ChargeState.OCCUPIED_CHARGER)} | in holding "
        f"{count(ChargeState.HOLDING)} | Stage Zone fallback "
        f"{count(ChargeState.STAGE_FALLBACK)} | still travelling "
        f"{count(*TRAVEL_STATES)} | still in REQUEST "
        f"{count(ChargeState.REQUEST)}",
        f"  saturation events : {executor.result.saturation_events}",
        f"  yield events      : {executor.result.yield_events} (whole run)",
    ]

    for record in records:

        arrived = (
            "not yet" if record.arrived_at is None
            else f"t={float(record.arrived_at):.4f} SOC "
            f"{float(record.arrival_soc):.4f}%"
        )

        lines.append(
            f"    {record.robot_id} {record.reason:<13s} "
            f"{str(record.request_cell):<9s} -> {record.kind:<7s} "
            f"{record.zone_id} {record.target_cell}  A* "
            f"{record.route_cost_m:5.1f} m  requested "
            f"t={float(record.requested_at):.4f} SOC "
            f"{float(record.request_soc):.4f}%  departed "
            f"t={float(record.departed_at):.4f}  arrived {arrived}"
        )

    waited = [
        record.departed_at - record.requested_at
        for record in records
        if record.departed_at is not None
    ]

    lines.append(
        f"  REQUEST -> departure : max "
        f"{float(max(waited, default=0)):.4f} s; REQUEST robots standing "
        f"on a transit cell now: {len(stuck)}"
        + (f" {stuck}" if stuck else "")
    )

    return lines
