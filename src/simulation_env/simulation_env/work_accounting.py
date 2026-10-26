"""Which part of a run was order work, and which was recovery (B6a).

docs/DECISIONS.md, "End of Workload", keeps order work and end-of-work
recovery apart: charging or parking after the final order must not
change ``order_makespan``. The same separation is needed for distance,
waiting and energy, or a strategy whose robots happen to finish far
from the Stage Zone bank is charged for its parking drive.

Every robot is in exactly one phase at every instant:

    support      not executing an order while orders are still owed:
                 from the start, between orders, charger trips during
                 the workload, liveness repositioning
    productive   executing an order mission, from launch at the
                 robot's cell to the end of its drop-off retreat
    recovery     after the workload is exhausted (the last order has
                 been allocated) and the robot has retired its last
                 order: final parking, and any charging done then

Transitions happen at three instants only, all of them already
decided by the executor:

    order launched                    -> productive
    order retired, orders still owed  -> support
    order retired, workload exhausted -> recovery
    workload exhausted                -> recovery, for every robot not
                                         holding an unfinished order

Measurement is the robots' own cumulative counters, the same ones the
run totals and the Phase 8 outcome rows read, snapshotted at each
transition and differenced. A phase is therefore a partition of the
run: support + productive + recovery is each counter's run total, and
the productive part is the sum of the per-order outcome rows.

A move or wait in flight at the exhaustion instant is attributed by
when the engine books it: distance when the move starts, travel and
waiting time when they close. Robots are stationary at launch and at
retirement, so per-order values are exact.

Observation only. Nothing here is read back by allocation, routing,
traffic, charging or battery feasibility.
"""

from dataclasses import dataclass, field


SUPPORT = "support"
PRODUCTIVE = "productive"
RECOVERY = "recovery"

PHASES = (PRODUCTIVE, SUPPORT, RECOVERY)

# GridRobot's cumulative counters, booked by FleetSimulationEngine.
ROBOT_COUNTERS = ("distance_m", "travel_time_s", "waiting_time_s", "service_time_s")

# BatteryLedger's, when the battery is enabled. Energy is the SOC spent
# (charging is a gain, not a cost); charging_s includes an open session.
BATTERY_COUNTERS = ("energy_percent", "charging_s")


def counters_of(robot, ledger, now) -> dict:
    """One robot's cumulative counters at ``now``. Pure."""

    values = {name: getattr(robot, name) for name in ROBOT_COUNTERS}

    if ledger is not None:
        snapshot = ledger.snapshot(robot.robot_id, now)

        values["energy_percent"] = snapshot.total_energy_consumed
        values["charging_s"] = snapshot.charging_s

    return values


def _difference(later: dict, earlier: dict) -> dict:
    return {name: later[name] - earlier[name] for name in later}


@dataclass
class WorkAccounting:
    """Per-robot phase meter. Fed by the executor, read by run_metrics."""

    # The instant the last pending order was allocated, or None while
    # orders are still owed.
    exhausted_at: object = None

    # robot_id -> its current phase, and the counters when it began.
    phase: dict = field(default_factory=dict)
    _mark: dict = field(default_factory=dict)

    # phase -> counter -> total over every robot, closed segments only.
    totals: dict = field(default_factory=dict)

    # robot_id -> when its recovery began.
    recovery_from: dict = field(default_factory=dict)

    def register(self, robot_id: str, values: dict) -> None:
        """A robot joins the run, in support until it is given work."""

        self.phase[robot_id] = SUPPORT
        self._mark[robot_id] = values

    def _enter(self, robot_id: str, phase: str, values: dict, now) -> dict:
        """Close the robot's current segment and open ``phase``.

        Returns what the closed segment spent.
        """

        spent = _difference(values, self._mark[robot_id])

        bucket = self.totals.setdefault(self.phase[robot_id], {})

        for name, value in spent.items():
            bucket[name] = bucket.get(name, 0) + value

        self.phase[robot_id] = phase
        self._mark[robot_id] = values

        if phase == RECOVERY:
            self.recovery_from[robot_id] = now

        return spent

    def launched(self, robot_id: str, values: dict, now) -> None:
        self._enter(robot_id, PRODUCTIVE, values, now)

    def retired(self, robot_id: str, values: dict, now) -> dict:
        """An order mission is fully driven. Returns what it spent."""

        after = SUPPORT if self.exhausted_at is None else RECOVERY

        return self._enter(robot_id, after, values, now)

    def exhaust(self, now, idle: dict) -> None:
        """No order is pending any more.

        ``idle`` is robot_id -> counters for every robot holding no
        unfinished order; each starts its recovery now. A robot still
        executing or owing an order starts it when that order retires.
        """

        self.exhausted_at = now

        for robot_id in sorted(idle):
            self._enter(robot_id, RECOVERY, idle[robot_id], now)

    def split(self, final: dict) -> dict:
        """phase -> counter -> run total, with every open segment closed.

        ``final`` is robot_id -> counters at the end of the run. Pure:
        the meter itself is not advanced, so it can be read twice.
        """

        result = {
            phase: dict(self.totals.get(phase, {})) for phase in PHASES
        }

        names = next(iter(final.values()), {}).keys()

        for phase in PHASES:
            for name in names:
                result[phase].setdefault(name, 0)

        for robot_id, values in final.items():
            bucket = result[self.phase[robot_id]]

            for name, value in _difference(values, self._mark[robot_id]).items():
                bucket[name] += value

        return result
