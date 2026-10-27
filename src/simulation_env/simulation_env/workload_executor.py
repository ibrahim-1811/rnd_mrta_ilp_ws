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

from mrta_core.battery import (
    BatteryDecisionContext,
    EnergyRequirement,
    charge_seconds_to_full,
    travel_energy,
)
from mrta_core.order_queue import OrderQueue
from mrta_core.orders import OrderStatus
from mrta_core.warehouse_map import ZoneType

from simulation_env.charging import (
    DESTINATION,
    LEAVING_CHARGER_STATES,
    LEAVING_STATES,
    MOVING_STATES,
    PREEMPT_RECORD_SOC,
    SETTLED_STATES,
    TRAVEL_STATES,
    WAITING_STATES,
    ChargeRecord,
    ChargeState,
    ChargingSaturationError,
    ResourceState,
    TerminalBank,
)
from simulation_env.dataset import exact
from simulation_env.fleet_engine import EventType, FleetSimulationEngine
from simulation_env.grid_robot import GridRobotStatus
from simulation_env.stage_parking import StageParking
from simulation_env.work_accounting import WorkAccounting, counters_of
from task_management.features import DecisionTiming
from mrta_core.warehouse_routing import (
    MissionPlan,
    MissionSegment,
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
    static_cost_m: float  # C_static(r, o), the prediction
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
    # CHARGE_START      charging began on its own charger (B4)
    # CHARGE_FULL       reached 100%; the slot is VACATING (B4)
    # REJOIN            off the charger, access edge clear, available (B4)
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
            self.spans[name] = self.spans.get(name, 0.0) + (perf_counter() - started)

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
            f"{self.returning} returning | {self.parked} parked" + waiting_for_charge,
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
    perf: dict = field(default_factory=lambda: dict.fromkeys(PerfBudget.STAGES, 0.0))
    slow_dispatches: int = 0
    worst_dispatch_s: float = 0.0
    static_route_reuses: int = 0

    # Decision shape (S0). Reporting only, but the whole point of the
    # candidate window: a decision with robots x orders == 1 is forced,
    # and every strategy returns the same pair on it.
    decisions: int = 0
    choice_bearing: int = 0
    window_extensions: int = 0
    # Dispatch quantum (S0b): dispatch attempts deferred to batch more
    # robots. Dispatch is attempted on every engine tick, so this counts
    # ticks refused inside an open window, not decisions -- several per
    # window, and always more than `decisions`.
    quantum_holds: int = 0
    # (free robots, window orders) -> how many decisions had that shape.
    decision_shapes: dict = field(default_factory=dict)
    # (sim_time, free robots, window orders) per decision, in order.
    # Reporting and S0b sizing: the gaps between consecutive decisions
    # are the free-robot arrival distribution a dispatch quantum would
    # have to bridge.
    decision_log: list = field(default_factory=list)

    # Battery eligibility, decision time. Reporting only.
    battery_events: list = field(default_factory=list)
    candidate_pairs: int = 0  # scored by the allocator
    ineligible_pairs: int = 0  # of those, battery-ineligible
    availability_pairs: int = 0  # scored by CHARGE_NEEDED checks
    assignments_validated: int = 0
    min_assigned_margin: Fraction | None = None

    # Charger travel (B3). Reporting only.
    saturation_events: int = 0
    yield_events: int = 0

    # Charging (B4). Reporting only.
    charge_sessions: int = 0
    rejoins: int = 0

    # Queue promotion and preemption (B5). Reporting only.
    promotions: int = 0
    # Of those, robots promoted off a fallback Stage Zone.
    stage_promotions: int = 0
    preemptions: int = 0
    # Preempted robots that were energy-eligible and went straight back
    # to work, and those that had to wait in a holding cell instead.
    preempt_rejoins: int = 0
    preempt_to_holding: int = 0


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
        window: str | int = "fleet_size",
        dispatch_quantum_s: float = 0.0,
        recorder=None,
        strategy_name: str = "",
    ) -> None:

        self.warehouse = warehouse
        self.queue = queue
        self.allocator = allocator

        # The allocation candidate window (S0). ``fleet_size`` is the
        # frozen default; ``legacy`` is the pre-S0
        # ``min(available_robots, pending)`` rule, kept only so the
        # pre-S0 baselines stay reproducible. Infrastructure: the same
        # width is offered to every strategy.
        self.window = window
        self._window_width = self.resolve_window(window, len(robots))

        # The dispatch quantum (S0b). 0 is continuous dispatch: allocate
        # the instant a robot is free. A positive value holds a freed
        # robot up to that long so several are allocated in one decision,
        # which is what the batch-assignment strategies need in order to
        # differ from greedy at all. Fixed infrastructure: the same
        # quantum applies to every strategy.
        if dispatch_quantum_s < 0:
            raise ValueError(
                f"dispatch_quantum_s must be >= 0, got {dispatch_quantum_s}"
            )

        self.dispatch_quantum = Fraction(str(dispatch_quantum_s))

        # robot_id -> the instant it was first seen free, so a window is
        # measured from the robot that has waited longest, not from the
        # latest arrival (which could postpone a decision indefinitely).
        self._free_since: dict[str, Fraction] = {}
        self._quantum_armed: Fraction | None = None

        # Phase 8 dataset. A RunRecorder, or None to record nothing.
        # Recording is observation only: it must never change a
        # simulated outcome, which test_dataset.py asserts directly.
        self.recorder = recorder
        self.strategy_name = strategy_name or getattr(allocator, "name", "")

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

        # Order work vs end-of-work recovery, for the run metrics
        # (B6a). Observation only: nothing reads it back.
        self.accounting = WorkAccounting()

        for robot_id in self.engine.robots:
            self.accounting.register(robot_id, self._counters(robot_id))

        # One memo for the whole run: the map and the service-cell
        # rule never change, so a leg costed for the first dispatch is
        # still valid for the hundredth.
        self.routes = RouteCache(warehouse)

        self.fifo_position = {
            order_id: index for index, order_id in enumerate(queue.order_ids)
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
        # B4 removes this exclusion only after physical charger exit.
        # Holding and fallback robots remain excluded until B5.
        self.requesting: dict = {}

        # robot_id -> its current trip through the charging workflow.
        self.charge: dict[str, ChargeRecord] = {}

        # Every completed trip, in completion order. A robot may charge
        # more than once in a long run, and ``charge`` only holds its
        # latest trip, so the report reads this for the full history.
        self.charge_history: list[ChargeRecord] = []
        self.chargers: TerminalBank | None = None
        self.holding: TerminalBank | None = None

        # robot_id -> the CHARGE_DUE event raised while it was busy. The
        # order is never interrupted; the flag is acted on only once
        # the whole order, retreat included, has finished.
        self._due_flag: dict = {}

        self._chargers: tuple[str, ...] = ()
        self._nearest_charger_memo: dict = {}

        if self.battery is not None:
            self._chargers = tuple(
                sorted(
                    zone.zone_id for zone in warehouse.zones_of_type(ZoneType.CHARGING)
                )
            )

            if not self._chargers:
                raise ValueError(
                    "battery accounting is enabled but the map has no "
                    "CHARGING zone, so no order can ever be battery "
                    "eligible"
                )

            self.chargers = TerminalBank(warehouse, ZoneType.CHARGING, "charger")
            self.holding = TerminalBank(warehouse, ZoneType.CHARGE_QUEUE, "holding")

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
            self.on_order_status(order.order_id, status, now, order.assigned_robot)

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

    def _decision_timing(self, robot_ids) -> DecisionTiming:
        """Speeds and service durations the engine executes with.

        The same for every strategy; a strategy that ignores time ignores
        it. Nothing realized is in it.
        """

        service = self.engine.service_seconds

        return DecisionTiming(
            speed_mps={
                robot_id: float(self.engine.robots[robot_id].speed_mps)
                for robot_id in robot_ids
            },
            shelf_service_s=float(service.get(ZoneType.SHELF, 0)),
            dropoff_service_s=float(service.get(ZoneType.DROPOFF, 0)),
        )

    def _battery_context(self, robot_ids) -> BatteryDecisionContext:
        """Decision-time battery view for ``robot_ids``, at this instant."""

        now = self.engine.time
        service = self.engine.service_seconds

        return BatteryDecisionContext(
            config=self.battery.config,
            soc={robot_id: self.battery.soc(robot_id, now) for robot_id in robot_ids},
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

    def _evacuate(self, only=None, banks=None, *, preempt=True) -> int:
        """Send every robot in REQUEST to a reserved non-transit terminal.

        1. a FREE charger, cheapest by directed A*
        2. else a FREE holding cell, cheapest by directed A*
        3. else record saturation and take the nearest free Stage Zone

        Every reservation is made before the robot moves. Robots asking
        at the same instant are paired as one batch, so they reserve
        distinct cells. A CHARGE_DUE robot that got no charger may then
        preempt one (B5). Returns how many robots set off.

        ``only`` and ``banks`` restrict the pass to named robots and a
        shorter list of banks; with ``preempt=False`` that is the
        preempted-robot path, which must take a holding cell and must
        not itself preempt anybody.
        """

        waiting = sorted(
            robot_id
            for robot_id, record in self.charge.items()
            if record.state is ChargeState.REQUEST
            and (only is None or robot_id in only)
        )

        if not waiting:
            return 0

        now = self.engine.time
        robots = self.engine.robots

        cells = {robot_id: robots[robot_id].cell for robot_id in waiting}

        # Bank cells somebody outside this batch is standing on are not
        # free to give away.
        blocked = frozenset(
            robot.cell for robot_id, robot in robots.items() if robot_id not in cells
        )

        claims = {}

        for bank in banks or (self.chargers, self.holding):
            left = {r: c for r, c in cells.items() if r not in claims}

            if not left:
                break

            found = bank.assign(left, now=now, blocked_cells=blocked, cache=self.routes)

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

            stage = self.parking.assign(left, blocked_cells=blocked, cache=self.routes)

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
            self.engine.assign_missions_batch(missions, parking=set(missions))

        if preempt:
            self._preempt(claims)

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

        # The aisle cell it came from is the cell it will reverse into.
        if len(record.path) >= 2:
            record.access_cell = record.path[-2]

        self._battery_event(
            robot_id,
            "ARRIVE",
            record.reason,
            f"{record.state.value} {record.zone_id}",
        )

        if record.kind == "charger":
            self._begin_charge(robot_id)

    # -- charging (B4) --------------------------------------

    def _begin_charge(self, robot_id) -> None:
        """Start charging the robot standing on its reserved charger.

        The charge runs to exactly 100% (docs/DECISIONS.md, "Charge
        Target"). The duration is exact rational arithmetic, so the
        session lands on the target rather than near it.
        """

        record = self.charge[robot_id]
        now = self.engine.time

        soc = self.battery.soc(robot_id, now)

        duration = charge_seconds_to_full(self.battery.config, soc)

        record.state = ChargeState.CHARGING
        record.charge_started_at = now
        record.charge_duration_s = duration

        self.engine.begin_charge(robot_id, duration)

        self.result.charge_sessions += 1

        self._battery_event(
            robot_id,
            "CHARGE_START",
            record.reason,
            f"{record.zone_id}; to 100% in {float(duration):.4f} simulated s",
        )

    def _charge_complete(self, robot_id) -> None:
        """The robot is full: release the slot and reverse into the aisle.

        The slot goes OCCUPIED -> VACATING *before* the robot moves and
        stays unavailable until the robot has physically cleared the
        access edge, so an entering and a leaving robot can never meet
        on it.
        """

        record = self.charge[robot_id]
        now = self.engine.time

        record.charge_completed_at = now
        record.charge_gained = self.battery.soc(robot_id, now) - record.arrival_soc

        self.chargers.vacate(record.zone_id, robot_id)

        self._battery_event(
            robot_id,
            "CHARGE_FULL",
            record.reason,
            f"{record.zone_id}; gained "
            f"{float(record.charge_gained):.4f}% in "
            f"{float(record.charge_duration_s):.4f} s; vacating",
        )

        record.state = ChargeState.LEAVING_CHARGER
        record.left_charger_at = now

        self._reverse_off_terminal(robot_id, record)

    def _reverse_off_terminal(self, robot_id, record) -> None:
        """Drive one cell, off the terminal, back into the aisle it came from.

        Built here rather than planned: the destination is a bare aisle
        cell, not a zone, and the move is the exact reverse of the entry
        the topology gate already proves legal. The segment carries no
        exit_cell, so the engine drives it as a plain move and never
        treats the terminal as a service station.

        Shared by every exit from a terminal -- a full robot leaving its
        charger (B4), a preempted one, and a promoted one leaving its
        holding cell (B5) -- so the three cannot drift apart.
        """

        robot = self.engine.robots[robot_id]
        target = record.access_cell

        if target is None:
            raise RuntimeError(
                f"{robot_id} has no access cell to reverse into from "
                f"{record.zone_id}; the arrival path was not recorded"
            )

        resolution = self.warehouse.grid.resolution

        segment = MissionSegment(
            from_zone=record.zone_id,
            to_zone=record.zone_id,
            start_cell=robot.cell,
            goal_cell=target,
            path=(robot.cell, target),
            distance_m=resolution,
            cost=resolution,
            exit_cell=None,
        )

        plan = MissionPlan(
            zones=(record.zone_id,),
            segments=(segment,),
            path=(robot.cell, target),
            total_distance_m=resolution,
            total_cost=resolution,
        )

        # A recovery move, like parking: order work keeps right of way
        # and this never counts as an order completion.
        self.engine.assign_missions_batch({robot_id: plan}, parking={robot_id})

    def _assert_terminal_cleared(self, robot_id, record, states) -> None:
        """Refuse to release a slot before the robot has really left it."""

        robot = self.engine.robots[robot_id]

        if (
            record.state not in states
            or record.access_cell is None
            or robot.cell != record.access_cell
            or robot.status is not GridRobotStatus.IDLE
            or not robot.finished
        ):
            raise RuntimeError(
                f"{robot_id} cannot rejoin before physically clearing "
                f"{record.zone_id} into {record.access_cell}; at {robot.cell}"
            )

    def _rejoin(self, robot_id) -> None:
        """The robot has cleared the access edge: free the slot, rejoin.

        From here it is an ordinary robot again. The unchanged B2 gate
        re-tests it at its new SOC on the next dispatch, so it can take
        work, and nothing about its order history changed. Reached by a
        robot that charged to 100% (B4) and by a preempted robot that is
        still energy-eligible (B5).
        """

        record = self.charge[robot_id]
        now = self.engine.time

        self._assert_terminal_cleared(robot_id, record, LEAVING_CHARGER_STATES)

        self.chargers.cleared(record.zone_id, robot_id)

        record.state = ChargeState.REJOINED
        record.rejoined_at = now
        record.rejoin_soc = self.battery.soc(robot_id, now)

        self.requesting.pop(robot_id, None)

        self.charge_history.append(record)

        self.result.rejoins += 1

        self._battery_event(
            robot_id,
            "REJOIN",
            record.reason,
            f"off {record.zone_id}, access edge clear; available again",
        )

    # -- queue promotion and preemption (B5) ----------------

    def _queue_order(self, robot_ids) -> list:
        """Waiting robots in the frozen queue-discipline order.

        docs/DECISIONS.md, "Charger Queue Discipline": lowest SOC, then
        earliest charge request, then ``robot_id`` purely as the final
        deterministic engineering tie-break. It carries no scientific
        priority meaning.
        """

        now = self.engine.time

        return sorted(
            robot_ids,
            key=lambda robot_id: (
                self.battery.soc(robot_id, now),
                self.charge[robot_id].requested_at,
                robot_id,
            ),
        )

    def _promote(self) -> int:
        """Give every FREE charger to the robot that has waited best for it.

        One idempotent pass per dispatch, so it fires both when a
        charger is released and when a robot starts waiting. The pair
        (FREE reachable charger, waiting robot) therefore never survives
        a tick, which is what stops a robot sitting in holding, or on a
        Stage Zone, while a charger stands empty.

        A waiting robot is one in holding or, after saturation, on a
        Stage Zone; both wait in one queue (docs/DECISIONS.md, "Stage
        Zone Promotion"). Robot priority is the frozen queue discipline.
        The cell each promoted robot gets is its cheapest FREE charger
        by directed A* from the cell it will really set off from --
        never Manhattan -- among the chargers it can reach on the SOC it
        has now. Returns how many robots were promoted.
        """

        self._expire_promises()

        waiting = [
            robot_id
            for robot_id, record in self.charge.items()
            if record.state in WAITING_STATES
        ]

        if not waiting:
            return 0

        # Bank cells a robot is standing on are not free to give away.
        # A waiting robot stands on its holding cell or Stage Zone,
        # never on a charger, so no candidate excludes itself here.
        blocked = frozenset(robot.cell for robot in self.engine.robots.values())

        now = self.engine.time
        promoted = 0

        for robot_id in self._queue_order(waiting):
            candidates = self.chargers.free(blocked)

            # A slot promised to this robot by a preemption is not on
            # general offer, but it is exactly what this robot is here
            # for -- once its previous holder has physically left it.
            promise = self.chargers.promised(robot_id)

            if promise is not None:
                if self.chargers.slots[promise].state is ResourceState.FREE:
                    candidates = [promise, *candidates]

                elif not candidates:
                    # Still being vacated. Waiting one tick is correct:
                    # nobody else can take it.
                    continue

            if not candidates:
                break

            record = self.charge[robot_id]
            start, exit_m = self._promotion_start(robot_id, record)
            soc = self.battery.soc(robot_id, now)

            costs = {}

            for zone_id in candidates:
                cost = self.chargers.route_cost(start, zone_id, self.routes)

                if cost is None:
                    continue

                # Battery reachability: the robot must reach the charger
                # with charge left, by the route it would really drive.
                if travel_energy(self.battery.config, exit_m + cost) > soc:
                    continue

                costs[zone_id] = cost

            if not costs:
                if record.charger_out_of_reach_at is None:
                    record.charger_out_of_reach_at = now

                    self._battery_event(
                        robot_id,
                        "OUT_OF_REACH",
                        record.reason,
                        f"no free charger within {float(soc):.4f}% from "
                        f"{record.kind} {record.zone_id}; keeps waiting",
                    )

                continue

            zone_id = min(costs, key=lambda candidate: (costs[candidate], candidate))

            if promise is not None and promise != zone_id:
                # It found something nearer that nobody is leaving.
                self.chargers.cancel_promise(promise)

            self._grant_charger(robot_id, record, zone_id, costs[zone_id])

            promoted += 1

        return promoted

    def _promotion_start(self, robot_id, record):
        """Where a waiting robot's charger route begins, and the metres before it.

        A holding cell is non-transit and is left by reversing into its
        access cell, so the charger route starts there, one cell away. A
        Stage Zone is left the way every robot leaves one for its first
        order: by the directed A* route from the Stage Zone cell itself.
        """

        if record.kind == "holding":
            return record.access_cell, self.warehouse.grid.resolution

        return self.engine.robots[robot_id].cell, 0

    def _expire_promises(self) -> None:
        """Drop a preemption promise its robot can no longer collect.

        A promise is owed to a robot on its way to, or already in, a
        holding cell or a fallback Stage Zone. If it ended up somewhere
        else -- back in the fleet, or already on a charger -- the charger
        goes back on general offer rather than standing empty for
        nobody.
        """

        owed = frozenset(
            {
                ChargeState.TO_HOLDING,
                ChargeState.HOLDING,
                ChargeState.TO_STAGE_FALLBACK,
                ChargeState.STAGE_FALLBACK,
            }
        )

        for zone_id, slot in self.chargers.slots.items():
            if slot.promised_to is None:
                continue

            record = self.charge.get(slot.promised_to)

            if record is None or record.state not in owed:
                self.chargers.cancel_promise(zone_id)

    def _grant_charger(self, robot_id, record, zone_id, cost) -> None:
        """Reserve a FREE charger for a waiting robot and start it leaving.

        From holding, the holding slot goes OCCUPIED -> VACATING before
        the robot moves and is released only once it physically stands
        on its access cell, exactly as a charger is. So an entering and
        a leaving robot can never meet on a terminal access edge, and no
        second robot can be promoted into the cell being vacated.

        From a Stage Zone, the robot drives straight off by its charger
        route, and its Stage Zone claim is handed back. Nobody can be
        given that cell while the robot is still on it: a bank cell a
        robot stands on is never free (StageParking.free_zones).
        """

        now = self.engine.time

        # Raises rather than double-booking if the charger is already
        # held: one robot, one charger.
        self.chargers.reserve(zone_id, robot_id, now, cost)

        record.promoted_at = now

        self.result.promotions += 1

        self._battery_event(
            robot_id,
            "PROMOTE",
            record.reason,
            f"{record.kind} {record.zone_id} -> charger {zone_id}, "
            f"directed A* {cost:.1f} m",
        )

        if record.kind == "holding":
            # Raises rather than double-booking: one holding cell.
            self.holding.vacate(record.zone_id, robot_id)

            record.state = ChargeState.LEAVING_HOLDING

            self._reverse_off_terminal(robot_id, record)
            return

        self.result.stage_promotions += 1

        self.parking.release(robot_id)

        record.state = ChargeState.PROMOTED

        self.charge_history.append(record)

        self._drive_to_charger(robot_id, record, zone_id)

    def _leave_holding(self, robot_id) -> None:
        """A promoted robot is clear of holding: drive to its charger."""

        record = self.charge[robot_id]
        now = self.engine.time

        self._assert_terminal_cleared(
            robot_id,
            record,
            frozenset({ChargeState.LEAVING_HOLDING}),
        )

        zone_id = self.chargers.held_by(robot_id)

        if zone_id is None:
            raise RuntimeError(
                f"{robot_id} left holding {record.zone_id} without a "
                f"reserved charger"
            )

        self.holding.cleared(record.zone_id, robot_id)

        record.state = ChargeState.PROMOTED
        record.left_holding_at = now

        self.charge_history.append(record)

        self._drive_to_charger(robot_id, record, zone_id)

    def _drive_to_charger(self, robot_id, record, zone_id) -> None:
        """Start the charger trip of a robot promoted out of waiting.

        The waiting trip has ended and the charger trip is a new record,
        so every record still describes exactly one terminal. The
        original charge request is carried over rather than restarted,
        which is what keeps the queue discipline and the reporting
        honest about how long the robot really waited.
        """

        now = self.engine.time
        slot = self.chargers.slots[zone_id]

        trip = ChargeRecord(
            robot_id=robot_id,
            reason=record.reason,
            requested_at=record.requested_at,
            request_cell=record.request_cell,
            request_soc=record.request_soc,
            state=ChargeState.TO_CHARGER,
            kind="charger",
            zone_id=zone_id,
            target_cell=slot.cell,
            route_cost_m=slot.route_cost_m,
            departed_at=now,
            promoted_from=record.zone_id,
            promoted_from_kind=record.kind,
        )

        # The same production router and non-transit view the promotion
        # cost came from, so this is the route that was costed.
        plan = plan_from_cell(
            self.warehouse,
            self.engine.robots[robot_id].cell,
            [zone_id],
            start_label=robot_id,
            cache=self.routes,
        )

        trip.path = plan.path

        self.charge[robot_id] = trip

        self._battery_event(
            robot_id,
            "RESERVE",
            trip.reason,
            f"charger {zone_id} at {slot.cell}, directed A* "
            f"{slot.route_cost_m:.1f} m, promoted from {record.zone_id}",
        )

        self.engine.assign_missions_batch({robot_id: plan}, parking={robot_id})

    def _preempt(self, claims) -> int:
        """Let a CHARGE_DUE robot take a charger from a higher-SOC robot.

        docs/DECISIONS.md, "Charger Saturation and Preemption": only a
        CHARGE_DUE robot may preempt, the victim is the charging robot
        with the highest SOC, and only if that SOC is strictly above the
        requester's -- a lower-SOC charging robot is never replaced by a
        higher-SOC requester. ``victim_soc_ge_80`` is recorded, never
        branched on: the rule always selects the maximum.

        Preemption frees a charger; it does not hand it over. The slot
        stays unavailable until the victim has physically cleared the
        access edge, and the frozen queue discipline then decides who
        takes it. The requester is already on its way to a holding cell,
        so it never waits in an aisle, and it preempts once -- at the
        instant it requests -- never again from the queue.

        Returns how many charge sessions were cut short.
        """

        now = self.engine.time

        requesters = [
            robot_id
            for robot_id, (kind, *_) in claims.items()
            if kind != "charger" and self.charge[robot_id].reason == "CHARGE_DUE"
        ]

        if not requesters:
            return 0

        cut = 0

        for robot_id in self._queue_order(requesters):
            requester_soc = self.battery.soc(robot_id, now)

            # Recomputed per requester: a robot cut short above is no
            # longer CHARGING, so it cannot be preempted twice.
            found = self._select_victim(requester_soc)

            if found is None:
                continue

            victim_soc, victim = found

            self._cut_charge(victim, robot_id, victim_soc, requester_soc)

            cut += 1

        return cut

    def _select_victim(self, requester_soc):
        """The charging robot a requester at this SOC may displace, or None.

        The frozen rule, as one expression: the maximum-SOC charging
        robot, and only if its SOC is strictly above the requester's.
        ``robot_id`` breaks an exact SOC tie and nothing else.
        """

        now = self.engine.time

        victims = sorted(
            (
                (self.battery.soc(victim, now), victim)
                for victim, record in self.charge.items()
                if record.state is ChargeState.CHARGING
            ),
            key=lambda pair: (-pair[0], pair[1]),
        )

        if not victims or victims[0][0] <= requester_soc:
            return None

        return victims[0]

    def _cut_charge(self, robot_id, requester_id, victim_soc, requester_soc) -> None:
        """End one charge session early and send the robot off the charger."""

        record = self.charge[robot_id]
        now = self.engine.time

        elapsed = self.engine.end_charge_early(robot_id)

        record.preempted_at = now
        record.preempted_by = requester_id
        record.charge_elapsed_s = elapsed
        record.charge_gained = self.battery.soc(robot_id, now) - record.arrival_soc
        record.victim_soc_ge_80 = victim_soc >= PREEMPT_RECORD_SOC
        record.state = ChargeState.PREEMPTED_LEAVING_CHARGER
        record.left_charger_at = now

        self.chargers.vacate(record.zone_id, robot_id)

        # The charger is assigned to the requester from this instant,
        # though it may not enter until the victim has cleared the
        # access edge. Otherwise the victim -- standing one cell away
        # with the lowest SOC in the bank -- would simply take it back
        # and the preemption would achieve nothing.
        self.chargers.promise(record.zone_id, requester_id, now)

        self.result.preemptions += 1

        self._battery_event(
            robot_id,
            "PREEMPTED",
            record.reason,
            f"{record.zone_id} given up for {requester_id} at "
            f"{float(requester_soc):.4f}%; gained "
            f"{float(record.charge_gained):.4f}% in {float(elapsed):.4f} s; "
            f"victim_soc_ge_{int(PREEMPT_RECORD_SOC)}="
            f"{str(record.victim_soc_ge_80).lower()}; vacating",
        )

        self._reverse_off_terminal(robot_id, record)

    def _after_preempt(self, robot_id) -> None:
        """A preempted robot is clear of the charger: rejoin, or wait.

        docs/DECISIONS.md, "Preempted Robot": it rejoins the available
        fleet if it is energy-eligible for work, and otherwise waits in
        a dedicated holding cell. "Energy-eligible for work" is the
        unchanged CHARGE_NEEDED test, and with no pending work a robot
        is never CHARGE_NEEDED (B2), so it rejoins and parks as usual --
        end-of-work partial charging is G6, in B6.

        A robot still at or below the CHARGE_DUE threshold is never fit
        for work, whatever the pending orders look like: the B2 gate
        would pull it straight back out of the fleet on the same tick.
        It waits instead.
        """

        record = self.charge[robot_id]

        self._assert_terminal_cleared(
            robot_id,
            record,
            frozenset({ChargeState.PREEMPTED_LEAVING_CHARGER}),
        )

        soc = self.battery.soc(robot_id, self.engine.time)

        pending = self.queue.pending()

        eligible = soc > self.battery.config.charge_due_percent and (
            not pending or self._has_eligible_order(robot_id, pending)
        )

        record.preempt_rejoined = eligible

        if eligible:
            self.result.preempt_rejoins += 1
            self._rejoin(robot_id)
            return

        self.result.preempt_to_holding += 1

        self._wait_in_holding(robot_id, record)

    def _wait_in_holding(self, robot_id, record) -> None:
        """A preempted robot with no eligible work waits in a holding cell.

        It takes a holding cell, never the charger it has just given up:
        that slot is FREE and belongs to the queue discipline now, and
        the robot that displaced it has a strictly lower SOC. The charge
        request itself continues, as a fresh holding trip, so saturation
        falls back exactly as it does for any other requester.
        """

        now = self.engine.time

        self.chargers.cleared(record.zone_id, robot_id)

        record.state = ChargeState.PREEMPTED

        self.charge_history.append(record)

        self._battery_event(
            robot_id,
            "PREEMPT_WAIT",
            record.reason,
            f"off {record.zone_id} at {float(self.battery.soc(robot_id, now)):.4f}%, "
            f"no battery-eligible order; waits in holding",
        )

        self._enter_request(
            robot_id,
            record.reason,
            f"preempted off {record.zone_id} by {record.preempted_by}",
        )

        self._evacuate(only={robot_id}, banks=(self.holding,), preempt=False)

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
                self._battery_event(robot_id, "CHARGE_DUE", "CHARGE_DUE", "while idle")
                self._enter_request(robot_id, "CHARGE_DUE", "while idle")

            elif not self._has_eligible_order(robot_id, pending):
                self._enter_request(
                    robot_id,
                    "CHARGE_NEEDED",
                    f"no battery-eligible order among {len(pending)} pending",
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
                    f"while busy on {self.active[robot_id]}; the order continues",
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
        # a Stage Zone is today's behaviour. G6 -- charging only enough
        # to make final parking feasible -- is deliberately B6, not B4
        # (docs/DECISIONS.md, "Charge Target").
        self._battery_event(
            robot_id,
            "CHARGE_DUE_AT_END",
            "CHARGE_DUE",
            "no pending work; parks as usual (G6 partial charge is B6)",
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
                robot_id in self.charge and self.charge[robot_id].state in MOVING_STATES
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
            and self.engine.robots[robot_id].status is GridRobotStatus.IDLE
        ]

        return self.launch_batch(ready)

    def dispatch(self) -> int:
        """Offer work to every free robot. Returns how many started."""

        outer = self._perf is None

        if outer:
            self._perf = PerfBudget(self.engine.sim_time)

        try:
            started = self._dispatch()

            self._note_exhausted()

            return started

        finally:
            if outer:
                self._close_perf()

    def _note_exhausted(self) -> None:
        """Start end-of-work recovery once no order is pending (B6a).

        Checked after the dispatch that allocated the last order, at
        that same instant. A robot still executing or owing an order
        starts its recovery when that order retires instead.
        """

        if self.accounting.exhausted_at is not None or self.queue.pending():
            return

        self.accounting.exhaust(
            self.engine.time,
            {
                robot_id: self._counters(robot_id)
                for robot_id in self.engine.robots
                if robot_id not in self.active and robot_id not in self.deferred
            },
        )

    def _close_perf(self) -> None:
        """Retire the current budget and report it if it was slow."""

        budget = self._perf
        self._perf = None

        if budget is None:
            return

        for name, spent in budget.spans.items():
            self.result.perf[name] = self.result.perf.get(name, 0.0) + spent

        total = budget.total

        self.result.worst_dispatch_s = max(self.result.worst_dispatch_s, total)

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

    @staticmethod
    def resolve_window(window, fleet_size: int) -> int | None:
        """Width of the candidate window, or None for the legacy rule.

        A scientific parameter, so an unrecognised value is an error
        rather than a silent fallback to a default that would change
        every measured result.
        """

        if window == "legacy":
            return None

        if window == "fleet_size":
            return fleet_size

        width = int(window)

        if width < 1:
            raise ValueError(f"allocation window must be >= 1, got {width}")

        return width

    def _assignable(self, free, orders) -> bool:
        """Does any (free robot, order) pair in here stand up?

        Assignable means reachable and, with a battery, battery-eligible
        -- scored through the allocator's shared ``score()`` so the route
        and energy are exactly what allocation would use. This is a
        feasibility probe, not a decision: it asks whether a choice
        exists, never which one to take, so the answer is the same for
        every strategy.
        """

        context = None if self.battery is None else self._battery_context(free)

        candidates = self.allocator.score(
            self.warehouse,
            [(robot_id, self.engine.robots[robot_id].cell) for robot_id in free],
            list(orders),
            self.fifo_position,
            self.routes,
            battery=context,
        )

        if context is not None:
            self.result.availability_pairs += context.evaluated

        return any(candidate.eligible for candidate in candidates)

    def _candidate_window(self, free, pending) -> tuple:
        """The orders this allocation decision may choose from.

        Width is the configured window (S0), not the number of free
        robots: a lone free robot still chooses among the whole window,
        which is what gives a strategy anything to decide. The result is
        always a FIFO prefix of ``pending``.

        The FIFO liveness rule. A strict prefix can hold nothing any
        free robot can take -- a dear order at the head with the fleet
        low on charge -- while a feasible order sits just behind it. The
        allocator then returns nothing, recovery parks the robots
        because they owe no work, and the run stalls with orders
        outstanding. So when the prefix offers no assignable pair at
        all, the window is extended down the FIFO sequence until one
        appears, or the queue is exhausted.

        This is liveness, not priority. It widens the choice; it never
        reorders the queue, and it runs only when the choice would
        otherwise be empty, so FIFO preference is untouched. It is
        computed from routing and energy alone, so every strategy is
        handed the same window.

        See docs/DECISIONS.md, "Allocation Candidate Window" and "FIFO
        Liveness Rule".
        """

        if self._window_width is None:
            # Legacy: the pre-S0 rule exactly -- a strict prefix sized
            # by the free robots, with no liveness extension. Extending
            # it here would move the frozen baselines this mode exists
            # to reproduce, so legacy can still stall. That is the point
            # of keeping it: the stall stays reproducible.
            return self.queue.eligible(min(len(free), len(pending)))

        window = self.queue.eligible(min(self._window_width, len(pending)))

        if len(window) >= len(pending) or self._assignable(free, window):
            return window

        # Nothing in the prefix can be taken. Walk on down the sequence.
        for extra in range(len(window) + 1, len(pending) + 1):
            self.result.window_extensions += 1

            window = self.queue.eligible(extra)

            if self._assignable(free, window[-1:]):
                break

        return window

    def _quantum_hold(self, free, pending) -> bool:
        """Should this decision wait for more robots to come free?

        The dispatch quantum (S0b). The candidate window fixes the
        *order* side of a decision; this fixes the *robot* side. Under
        continuous dispatch robots come free one at a time -- the
        measured median gap on the 100-order workload is 2.0 s -- so
        almost every decision offers a single robot, and Hungarian and
        MILP reduce to argmin and collapse onto greedy nearest. Holding
        a freed robot briefly lets several be allocated together.

        The window is measured from the robot that has been free
        longest, so a steady trickle of arrivals cannot postpone a
        decision indefinitely: every robot is dispatched within one
        quantum of becoming free, whatever happens around it.

        Nothing is held when holding cannot pay:

          * no quantum configured, or nothing free
          * the window has already been open a full quantum
          * there are no more orders than robots already in hand
          * no robot is in flight on an order, so none can come free

        Infrastructure, not policy: the hold is computed from the clock
        and the free pool alone, identically for every strategy, and it
        never touches which robot gets which order.

        See docs/DECISIONS.md, "Dispatch Batching".
        """

        if not self.dispatch_quantum or not free:
            return False

        now = self.engine.time

        for robot_id in free:
            self._free_since.setdefault(robot_id, now)

        for robot_id in [r for r in self._free_since if r not in free]:
            del self._free_since[robot_id]

        if now - min(self._free_since[r] for r in free) >= self.dispatch_quantum:
            return False

        if len(free) >= len(pending):
            return False

        if not self.active:
            # Nothing is running, so no further robot can come free and
            # waiting would buy nothing but idle drain.
            return False

        self._arm_quantum(min(self._free_since[r] for r in free))

        return True

    def _arm_quantum(self, opened_at) -> None:
        """Ask the engine to tick again when this window closes."""

        closes_at = opened_at + self.dispatch_quantum

        if self._quantum_armed == closes_at:
            # Already armed for exactly this instant; a second timer
            # would only duplicate the tick.
            return

        self._quantum_armed = closes_at

        self.engine.schedule_timer(
            closes_at - self.engine.time, detail="dispatch quantum"
        )

    def _record_candidates(self, free, window, battery, assignments) -> None:
        """Write one dataset row per scored (robot, order) pair.

        The candidate set is scored *here*, by the executor, through the
        allocator's shared ``score()`` -- not reported by the strategy.
        A strategy therefore cannot under-report what it was offered,
        and the logged candidate set is identical for every strategy on
        the same state, which is what makes two strategies comparable at
        all.

        Every row is finalized before any mission is planned and never
        revisited, so nothing realized can reach it. Only ``selected``
        depends on the allocator, and a selection is a decision, not an
        outcome.

        See docs/DECISIONS.md, "Dataset Granularity and Labels" and
        "No-Leakage Enforcement".
        """

        now = self.engine.time

        candidates = self.allocator.score(
            self.warehouse,
            [(robot_id, self.engine.robots[robot_id].cell) for robot_id in free],
            list(window),
            self.fifo_position,
            self.routes,
            battery=battery,
        )

        chosen = {
            (assignment.robot_id, assignment.order_id)
            for assignment in (assignments or ())
        }

        decision_id = self.result.decisions - 1

        shared = {
            "run_id": self.recorder.run_id,
            "decision_id": decision_id,
            "sim_time": float(now),
            "sim_time_exact": exact(now),
            "strategy": self.strategy_name,
            "window": str(self.window),
            "window_width": self._window_width,
            "dispatch_quantum_s": float(self.dispatch_quantum),
            "free_robots": len(free),
            "window_orders": len(window),
            "choice_bearing": len(free) * len(window) > 1,
            "pending_orders": len(self.queue.pending()),
            "robots_busy": len(self.active),
            "robots_charging": len(self.charge),
        }

        for candidate in candidates:
            robot = self.engine.robots[candidate.robot_id]
            order = self.queue.order(candidate.order_id)
            energy = candidate.energy

            soc = None if self.battery is None else self.battery.soc(robot.robot_id, now)

            plan = candidate.route.plan

            row = dict(shared)
            row.update(
                {
                    "robot_id": candidate.robot_id,
                    "order_id": candidate.order_id,
                    "robot_x": int(robot.cell[0]),
                    "robot_y": int(robot.cell[1]),
                    "robot_soc_percent": None if soc is None else float(soc),
                    "robot_soc_exact": exact(soc),
                    "fifo_position": int(candidate.fifo_position),
                    "order_shelf_count": len(order.shelf_ids),
                    "order_dropoff_id": order.dropoff_id,
                    "c_static_m": float(candidate.cost),
                    "route_reachable": candidate.reachable,
                    "route_cells": 0 if plan is None else len(plan.path),
                    "battery_eligible": candidate.battery_eligible,
                    "eligible": candidate.eligible,
                    "selected": (candidate.robot_id, candidate.order_id) in chosen,
                }
            )

            if energy is not None:
                row.update(
                    {
                        "predicted_travel_energy_percent": float(
                            energy.predicted_order_travel_energy_percent
                        ),
                        "predicted_service_energy_percent": float(
                            energy.predicted_service_energy_percent
                        ),
                        "predicted_charger_distance_m": float(
                            energy.predicted_nearest_charger_distance_m
                        ),
                        "predicted_charger_energy_percent": float(
                            energy.predicted_charger_energy_percent
                        ),
                        "reserve_percent": float(energy.reserve_percent),
                        "required_soc_percent": float(energy.required_soc_percent),
                        "required_soc_exact": exact(energy.required_soc_percent),
                        "energy_margin_percent": float(energy.energy_margin_percent),
                        "energy_margin_exact": exact(energy.energy_margin_percent),
                        "nearest_charger_id": energy.nearest_charger_id,
                    }
                )

            self.recorder.decision(row)

    def _counters(self, robot_id: str) -> dict:
        """The robot's cumulative counters now, for WorkAccounting."""

        return counters_of(
            self.engine.robots[robot_id], self.engine.battery, self.engine.time
        )

    def _record_launch(self, assignment, record) -> None:
        """Note what one order's outcome row will need from its launch.

        Per-order realized cost is the difference of the robot's own
        cumulative counters across the mission. WorkAccounting takes
        that difference for the run totals (B6a) and hands it to
        _record_outcome, so the outcome rows and the run's productive
        totals are one calculation, not two. Sound because a robot runs
        one order at a time and never charges mid-order, and because
        travel to a charger or a Stage Zone falls between orders rather
        than inside one.

        See docs/DECISIONS.md, "Phase 8 Schema Gaps To Close".
        """

        now = self.engine.time

        self.recorder.launched(
            assignment.order_id,
            {
                "decision_id": self.result.decisions - 1,
                "robot_id": assignment.robot_id,
                "launched_at": now,
                "soc": None if self.battery is None else self.battery.soc(
                    assignment.robot_id, now
                ),
                "charge_sessions": self.result.charge_sessions,
                "yield_events": self.result.yield_events,
                "predicted_distance_m": record.planned_distance_m,
            },
        )

    def _record_outcome(self, order_id: str, robot_id: str, spent: dict) -> None:
        """Write one order's realized outcome.

        ``spent`` is the mission's counter difference, as WorkAccounting
        booked it to the productive phase.
        """

        start = self.recorder.snapshot_of(order_id)

        if start is None:
            # No launch snapshot: the order never ran under this
            # recorder, so there is nothing realized to report.
            return

        # The order was fulfilled at its drop-off; the clock has since
        # moved on by the retreat. Completion time is the drop-off, the
        # realized costs are the whole mission.
        now = self.engine.time
        completed_at = start.get("completed_at", now)

        soc = None if self.battery is None else self.battery.soc(robot_id, now)

        consumed = (
            None
            if soc is None or start["soc"] is None
            else start["soc"] - soc
        )

        self.recorder.outcome(
            {
                "run_id": self.recorder.run_id,
                "order_id": order_id,
                "decision_id": start["decision_id"],
                "robot_id": robot_id,
                "launched_at": float(start["launched_at"]),
                "launched_at_exact": exact(start["launched_at"]),
                "completed_at": float(completed_at),
                "completed_at_exact": exact(completed_at),
                "realized_distance_m": spent["distance_m"],
                "realized_travel_time_s": spent["travel_time_s"],
                "realized_waiting_s": spent["waiting_time_s"],
                "realized_service_s": spent["service_time_s"],
                "realized_duration_s": float(completed_at - start["launched_at"]),
                "retreat_finished_at": float(now),
                "energy_consumed_percent": (
                    None if consumed is None else float(consumed)
                ),
                "energy_consumed_exact": exact(consumed),
                "soc_at_launch_percent": (
                    None if start["soc"] is None else float(start["soc"])
                ),
                "soc_at_completion_percent": None if soc is None else float(soc),
                "charge_sessions_before": start["charge_sessions"],
                "yield_events_during": self.result.yield_events
                - start["yield_events"],
                "predicted_distance_m": start["predicted_distance_m"],
                "success": True,
            }
        )

    def _record_decision(self, free, window) -> None:
        """Record the shape of one allocation decision.

        ``robots x orders == 1`` is a *forced* decision: there is one
        pair and every strategy returns it. Only choice-bearing
        decisions can distinguish one strategy from another, so they are
        counted separately and are the denominator the distinctness gate
        uses (docs/DECISIONS.md, "Strategy Distinctness Gate").
        """

        shape = (len(free), len(window))

        self.result.decision_log.append((self.engine.time, shape[0], shape[1]))
        self.result.decisions += 1
        self.result.decision_shapes[shape] = (
            self.result.decision_shapes.get(shape, 0) + 1
        )

        if shape[0] * shape[1] > 1:
            self.result.choice_bearing += 1

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

            # A robot already waiting in holding has first claim on a
            # free charger, ahead of a robot requesting at this instant:
            # the queue discipline is lowest SOC first, and a waiter has
            # by definition been out of the fleet for longer (B5).
            started += self._promote()

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

        if self._quantum_hold(free, pending):
            self.result.quantum_holds += 1
            return started

        # FIFO decides the candidate set; the allocator decides pairing.
        eligible = self._candidate_window(free, pending)

        self._record_decision(free, eligible)

        with self._stage("allocator"):
            assignments = self.allocator.assign(
                self.warehouse,
                [(rid, self.engine.robots[rid].cell) for rid in free],
                eligible,
                self.fifo_position,
                self.routes,
                battery=battery,
                timing=self._decision_timing(free),
            )

        if battery is not None:
            self.result.candidate_pairs += battery.evaluated
            self.result.ineligible_pairs += battery.ineligible

            # Before any order changes state: an ineligible pair stops
            # the run here rather than being executed or re-routed.
            self._check_battery_feasibility(assignments, battery)

        # Dataset row per scored pair, built before launch_batch plans
        # anything or any robot moves. A deferral is still a decision and
        # is still recorded: "nothing was assignable" is a fact about the
        # state, and dropping it would bias the dataset towards decisions
        # that happened to succeed.
        if self.recorder is not None:
            self._record_candidates(free, eligible, battery, assignments)

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

                self._deferred_at[assignment.robot_id] = self._traffic_version

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
            order = self.queue.order(assignment.order_id)

            self._clear_deferred(robot_id)

            self.active[robot_id] = order.order_id

            # Every shelf service plus the final drop-off service.
            self._services_left[robot_id] = len(assignment.route.shelf_sequence) + 1

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

        self.engine.assign_missions_batch(missions)

        # --------------------------------------------------
        # Dispatch records.
        #
        # There is no pre-planned traffic wait anymore. Waiting is
        # measured only from what actually occurs during execution.
        # --------------------------------------------------

        for robot_id in sorted(missions):
            assignment = by_robot[robot_id]
            plan = missions[robot_id]

            order = self.queue.order(assignment.order_id)

            record = Dispatch(
                sim_time=self.engine.sim_time,
                robot_id=robot_id,
                order_id=order.order_id,
                static_cost_m=assignment.cost,
                shelf_sequence=(assignment.route.shelf_sequence),
                dropoff_id=order.dropoff_id,
                start_cell=plan.path[0],
                planned_distance_m=(plan.total_distance_m),
                planned_wait_s=0.0,
                energy=assignment.energy,
            )

            self.result.dispatches.append(record)

            self.accounting.launched(
                robot_id, self._counters(robot_id), self.engine.time
            )

            if self.recorder is not None:
                self._record_launch(assignment, record)

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
            {robot_id: self.engine.robots[robot_id].cell for robot_id in free},
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

                self.result.returns[robot_id] = self.engine.sim_time

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

        self._set_status(self.queue.order(order_id), OrderStatus.COMPLETED, robot_id)

        self.result.completions.append(
            Completion(
                order_id=order_id,
                robot_id=robot_id,
                sim_time=self.engine.sim_time,
            )
        )

        if self.recorder is not None:
            # The order is *fulfilled* here, at the drop-off service, so
            # this is its completion instant. Its realized cost is not
            # final yet: the robot still has the post-drop-off retreat
            # to drive, and C_static includes that retreat (the energy
            # equation requires it). Writing the row now would make
            # realized distance one cell short of predicted on every
            # single order. The row is written at retirement instead.
            snapshot = self.recorder.snapshot_of(order_id)

            if snapshot is not None:
                snapshot["completed_at"] = self.engine.time

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
                finished = self.active.pop(robot_id)
                self._services_left.pop(robot_id, None)

                # Now the whole mission is driven, retreat included, so
                # realized and predicted cover the same route.
                spent = self.accounting.retired(
                    robot_id, self._counters(robot_id), self.engine.time
                )

                if self.recorder is not None:
                    self._record_outcome(finished, robot_id, spent)

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
                # Arrived on its reserved terminal. For a charger this
                # also starts the charge, in this same instant.
                self._settle(robot_id)
                self._retire(robot_id)

            elif record is not None and record.state is ChargeState.CHARGING:
                # IDLE while CHARGING means the engine has just booked
                # the session: the robot is full.
                self._charge_complete(robot_id)
                self._retire(robot_id)

            elif record is not None and record.state in LEAVING_STATES:
                # Standing on the access aisle cell: the edge is clear,
                # the slot can be released, and what happens next
                # depends on why the robot left the terminal.
                if record.state is ChargeState.LEAVING_CHARGER:
                    self._rejoin(robot_id)

                elif record.state is ChargeState.PREEMPTED_LEAVING_CHARGER:
                    self._after_preempt(robot_id)

                else:
                    self._leave_holding(robot_id)

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
        # A robot in holding or on a fallback Stage Zone is settled
        # while no FREE charger is within its reach: promotion moves it
        # the instant one is (B5), so the run cannot end with a waiting
        # robot beside a charger it could take. A robot that is
        # in REQUEST, driving to a terminal, charging, or reversing off
        # a charger is NOT finished: its charge will complete and it
        # will rejoin the fleet. Every other robot must be parked.
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
            if self.engine.robots[robot_id].status is GridRobotStatus.IDLE
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
            "reservation_violations": len(self.engine.reservations.audit()),
            "events": self.engine.processed,
        }

    def phase_totals(self) -> dict:
        """phase -> counter -> run total: order work vs recovery (B6a).

        Read only: the open segments are closed on a copy, so this can
        be called mid-run or more than once.
        """

        return self.accounting.split(
            {robot_id: self._counters(robot_id) for robot_id in self.engine.robots}
        )


# ----------------------------------------------------------------------
# Reporting.
# ----------------------------------------------------------------------


def battery_event_line(event: BatteryEvent) -> str:
    return (
        f"  t={float(event.sim_time):8.3f}  {event.kind:<17s} "
        f"{event.robot_id}  SOC {float(event.soc_percent):8.4f}%  "
        f"cell={event.cell}  {event.reason}: {event.detail}"
    )


def decision_report_lines(executor) -> list[str]:
    """What shape the allocation decisions had (S0).

    The one number that decides whether a strategy comparison means
    anything: a decision offering a single robot a single order is
    answered identically by every strategy, so only the choice-bearing
    ones can tell two strategies apart.

    See docs/DECISIONS.md, "Allocation Candidate Window" and "Strategy
    Distinctness Gate".
    """

    result = executor.result

    total = result.decisions

    forced = sum(
        count
        for shape, count in result.decision_shapes.items()
        if shape[0] * shape[1] == 1
    )

    def share(count: int) -> str:
        return "n/a" if not total else f"{100.0 * count / total:.1f} %"

    width = executor._window_width

    lines = [
        "ALLOCATION DECISIONS (candidate window; same for every strategy)",
        f"  window           : {executor.window} "
        + ("(W = available robots)" if width is None else f"(W = {width})"),
        f"  decisions        : {total}",
        f"  choice-bearing   : {result.choice_bearing} ({share(result.choice_bearing)})"
        f"  -- robots x orders > 1",
        f"  forced 1x1       : {forced} ({share(forced)})"
        f"  -- every strategy agrees on these",
        f"  window extensions: {result.window_extensions} "
        f"(FIFO liveness: prefix held no assignable pair)",
        f"  dispatch quantum : {float(executor.dispatch_quantum):.4f} s"
        + (
            " (continuous dispatch)"
            if not executor.dispatch_quantum
            else (
                f", {result.quantum_holds} dispatch attempt(s) held "
                f"to batch robots"
            )
        ),
    ]

    shapes = sorted(result.decision_shapes.items(), key=lambda kv: (-kv[1], kv[0]))

    if shapes:
        lines.append("  shapes (robots x orders -> decisions):")

        for (robots, orders), count in shapes[:8]:
            lines.append(f"    {robots:>3} x {orders:<3} : {count}")

        if len(shapes) > 8:
            lines.append(f"    ... {len(shapes) - 8} further shape(s)")

    return lines


def eligibility_report_lines(executor: "WorkloadExecutor") -> list[str]:
    """Battery eligibility at decision time, for any front end."""

    result = executor.result
    events = result.battery_events

    due = [e for e in events if e.kind == "CHARGE_DUE"]

    due_busy = sum(1 for e in due if e.detail.startswith("while busy"))
    due_idle = sum(1 for e in due if e.detail == "while idle")

    needed = [e for e in events if e.kind == "REQUEST" and e.reason == "CHARGE_NEEDED"]

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
        + (
            "n/a"
            if margin is None
            else f"{float(margin):.4f} % of SOC over an assigned pair"
        ),
        f"  CHARGE_DUE       : {len(due)} "
        f"({due_busy} while busy, {due_idle} while idle; "
        f"{len(at_end)} with no work left)",
        f"  CHARGE_NEEDED    : {len(needed)}",
        "  charge request   : " + (", ".join(sorted(executor.requesting)) or "none"),
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

            line = (
                f"    {slot.zone_id:<16s} {str(slot.cell):<9s} {slot.state.value:<9s}"
            )

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

    # In the charging workflow, not driving, yet standing on a transit
    # cell: must never happen. A rejoined robot is deliberately exempt
    # -- it is back in the fleet and an aisle cell is where it belongs.
    stuck = [
        record.robot_id
        for record in records
        if record.state not in MOVING_STATES
        and record.state is not ChargeState.REJOINED
        and executor.engine.robots[record.robot_id].cell not in terminals
    ]

    result = executor.result

    # A record can be both current and in the history; count it once.
    trips = {id(r): r for r in (*executor.charge_history, *records)}.values()

    out_of_reach = sum(1 for r in trips if r.charger_out_of_reach_at is not None)

    lines = [
        "CHARGING (B4: charge to 100%, leave, rejoin; B5: promotion and "
        "preemption)",
        *bank_lines(executor.chargers, every_row=True),
        *bank_lines(executor.holding, every_row=False),
        f"  requests : {len(records)} | charging "
        f"{count(ChargeState.CHARGING)} | leaving "
        f"{count(*LEAVING_STATES)} | rejoined "
        f"{count(ChargeState.REJOINED)} | in holding "
        f"{count(ChargeState.HOLDING)} | Stage Zone fallback "
        f"{count(ChargeState.STAGE_FALLBACK)} | still travelling "
        f"{count(*TRAVEL_STATES)} | still in REQUEST "
        f"{count(ChargeState.REQUEST)}",
        f"  charge sessions   : {result.charge_sessions} started, "
        f"{result.rejoins} robot(s) back in the fleet",
        f"  promotions        : {result.promotions} onto a freed charger, "
        f"lowest SOC first ({result.promotions - result.stage_promotions} "
        f"from holding, {result.stage_promotions} from a Stage Zone)",
        f"  out of reach      : {out_of_reach} waiting robot(s) passed over "
        f"for a free charger they could not reach on their SOC",
        f"  preemptions       : {result.preemptions} charge(s) cut short "
        f"({result.preempt_rejoins} victim(s) rejoined, "
        f"{result.preempt_to_holding} went to holding)",
        f"  saturation events : {result.saturation_events}",
        f"  yield events      : {result.yield_events} (whole run)",
    ]

    for record in records:
        arrived = (
            "not yet"
            if record.arrived_at is None
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

    sessions = [
        record
        for record in (*executor.charge_history, *records)
        if record.charge_started_at is not None
    ]

    # charge_history holds completed trips; records holds current ones,
    # so a robot mid-charge appears once and a finished one once.
    seen: set = set()
    unique = []

    for record in sessions:
        key = (record.robot_id, record.charge_started_at)
        if key not in seen:
            seen.add(key)
            unique.append(record)

    if unique:
        lines.append(
            "  charge sessions (SOC rises at the net configured rate; target 100%)"
        )

        for record in sorted(unique, key=lambda r: (r.charge_started_at, r.robot_id)):
            if record.preempted_at is not None:
                done = (
                    f"PREEMPTED t={float(record.preempted_at):.4f} by "
                    f"{record.preempted_by} after "
                    f"{float(record.charge_elapsed_s):.4f} s"
                )

            elif record.charge_completed_at is None:
                done = "charging"

            else:
                done = f"t={float(record.charge_completed_at):.4f}"

            back = (
                "not yet"
                if record.rejoined_at is None
                else f"t={float(record.rejoined_at):.4f} SOC "
                f"{float(record.rejoin_soc):.4f}%"
            )

            lines.append(
                f"    {record.robot_id} {record.zone_id} "
                + (
                    ""
                    if record.promoted_from is None
                    else f"(promoted from {record.promoted_from}) "
                )
                + f"start t={float(record.charge_started_at):.4f} SOC "
                f"{float(record.arrival_soc):.4f}%  "
                f"{float(record.charge_duration_s):.4f} s  full {done}"
                + (
                    ""
                    if record.charge_gained is None
                    else f"  +{float(record.charge_gained):.4f}%"
                )
                + f"  rejoined {back}"
            )

    # The B3 invariant: a requester leaves the aisle at the instant it
    # asks, so this must stay 0. A promoted robot is deliberately not
    # counted here -- it departs when its holding cell is clear or as it
    # is promoted off a Stage Zone, and it waited on a terminal, never
    # in an aisle. Its wait is the queue
    # wait below, which is a B5 outcome rather than an invariant.
    waited = [
        record.departed_at - record.requested_at
        for record in records
        if record.departed_at is not None and record.promoted_from is None
    ]

    queued = [
        record.departed_at - record.requested_at
        for record in trips
        if record.departed_at is not None and record.promoted_from is not None
    ]

    lines.append(
        f"  REQUEST -> departure : max "
        f"{float(max(waited, default=0)):.4f} s; REQUEST robots standing "
        f"on a transit cell now: {len(stuck)}" + (f" {stuck}" if stuck else "")
    )

    if queued:
        lines.append(
            f"  request -> charger   : max "
            f"{float(max(queued)):.4f} s waited in holding or on a Stage "
            f"Zone over "
            f"{len(queued)} promoted trip(s)"
        )

    return lines
