"""The one authoritative record of every robot's state of charge.

Battery V1. The ledger is written by the fleet engine only. Since B2
the executor reads SOC from it for eligibility, CHARGE_DUE and
CHARGE_NEEDED; since B3 a REQUEST robot drives to a charger. Nothing
here adds charge yet: SOC only ever falls.

It is booked from the engine's own events, at the very call sites that
already book the float travel / service / waiting counters on
GridRobot, with the same ``Fraction`` durations the clock used:

    a completed one-cell move    travel    any kind: aisle move, service
                                           retreat, yield side step, rejoin
    a completed pick or drop     service
    a closed traffic wait        waiting

Idle is never booked. It is the remainder: every simulated second a
robot exists it is moving, serving, traffic-waiting or idle, so

    idle_s = (now - registered_at) - moving_s - service_s - waiting_s

which is why this is a ledger over the engine's clock and not a second
clock of its own. A negative remainder can only mean some interval was
booked twice, and is raised rather than hidden.

Between events the split is provisional: a move, service or wait still
in progress reads as idle until it completes and is booked. The drain
rates for waiting and idle are equal, so a live SOC read mid-wait is
exact; mid-move or mid-service it lags by at most one move's or one
service's worth of drain. At the end of a run nothing is in progress,
so every category is exact.
"""

from dataclasses import dataclass
from fractions import Fraction

from mrta_core.battery import (
    BatteryConfig,
    service_energy,
    stationary_energy,
    travel_energy,
)


@dataclass
class _Account:
    initial_soc: Fraction
    registered_at: Fraction

    moving_s: Fraction = Fraction(0)
    service_s: Fraction = Fraction(0)
    waiting_s: Fraction = Fraction(0)

    travel: Fraction = Fraction(0)
    service: Fraction = Fraction(0)
    waiting: Fraction = Fraction(0)

    # Running totals so a live SOC read costs three operations, not a
    # full snapshot: booked_soc is initial minus every booked drain, and
    # booked_s is the time those bookings cover.
    booked_soc: Fraction = Fraction(0)
    booked_s: Fraction = Fraction(0)

    # Lowest SOC reached before a charge last began. SOC only falls
    # between charge sessions, so the running minimum is the current
    # SOC until charging exists; charging will latch it here when a
    # charge starts.
    latched_minimum: Fraction | None = None


@dataclass(frozen=True)
class BatterySnapshot:
    """One robot's battery account at one instant. Percentages of SOC."""

    robot_id: str
    time: Fraction
    registered_at: Fraction

    initial_soc: Fraction
    current_soc: Fraction
    minimum_soc: Fraction

    travel_energy: Fraction
    service_energy: Fraction
    waiting_energy: Fraction
    idle_energy: Fraction

    moving_s: Fraction
    service_s: Fraction
    waiting_s: Fraction
    idle_s: Fraction

    @property
    def total_energy_consumed(self) -> Fraction:
        return (
            self.travel_energy
            + self.service_energy
            + self.waiting_energy
            + self.idle_energy
        )


class BatteryLedger:
    """Per-robot SOC, booked from fleet engine events."""

    def __init__(self, config: BatteryConfig, resolution: Fraction) -> None:

        self.config = config

        # Every completed move is exactly one grid cell.
        self._move_energy = travel_energy(config, resolution)

        # SOC per second standing still, waiting or idle.
        self._idle_rate = stationary_energy(config, 1)

        self._accounts: dict[str, _Account] = {}

    # -- booking, called by the fleet engine only ---------------

    def register(self, robot_id: str, now: Fraction) -> None:

        if robot_id in self._accounts:
            raise ValueError(f"{robot_id} already has a battery account")

        initial = self.config.initial_soc_for(robot_id)

        self._accounts[robot_id] = _Account(
            initial_soc=initial,
            registered_at=now,
            booked_soc=initial,
        )

    def book_move(self, robot_id: str, duration: Fraction) -> None:
        account = self._accounts[robot_id]
        account.moving_s += duration
        account.travel += self._move_energy
        account.booked_s += duration
        account.booked_soc -= self._move_energy

    def book_service(self, robot_id: str, duration: Fraction) -> None:
        account = self._accounts[robot_id]
        energy = service_energy(self.config, duration)
        account.service_s += duration
        account.service += energy
        account.booked_s += duration
        account.booked_soc -= energy

    def book_wait(self, robot_id: str, duration: Fraction) -> None:
        account = self._accounts[robot_id]
        energy = stationary_energy(self.config, duration)
        account.waiting_s += duration
        account.waiting += energy
        account.booked_s += duration
        account.booked_soc -= energy

    # -- reading ------------------------------------------------

    @property
    def robot_ids(self) -> list[str]:
        return sorted(self._accounts)

    def snapshot(self, robot_id: str, now: Fraction) -> BatterySnapshot:

        account = self._accounts[robot_id]

        idle_s = (
            now
            - account.registered_at
            - account.moving_s
            - account.service_s
            - account.waiting_s
        )

        if idle_s < 0:
            raise RuntimeError(
                f"{robot_id} battery time partition is negative at "
                f"t={float(now):.4f}: idle {float(idle_s):.6f} s; some "
                f"interval was booked twice"
            )

        idle = stationary_energy(self.config, idle_s)

        current = (
            account.initial_soc
            - account.travel
            - account.service
            - account.waiting
            - idle
        )

        minimum = (
            current
            if account.latched_minimum is None
            else min(account.latched_minimum, current)
        )

        return BatterySnapshot(
            robot_id=robot_id,
            time=now,
            registered_at=account.registered_at,
            initial_soc=account.initial_soc,
            current_soc=current,
            minimum_soc=minimum,
            travel_energy=account.travel,
            service_energy=account.service,
            waiting_energy=account.waiting,
            idle_energy=idle,
            moving_s=account.moving_s,
            service_s=account.service_s,
            waiting_s=account.waiting_s,
            idle_s=idle_s,
        )

    def soc(self, robot_id: str, now: Fraction) -> Fraction:
        """Live SOC; the same value ``snapshot().current_soc`` returns."""

        account = self._accounts[robot_id]

        idle_s = now - account.registered_at - account.booked_s

        if idle_s < 0:
            # Let snapshot() raise with the full diagnosis.
            return self.snapshot(robot_id, now).current_soc

        return account.booked_soc - idle_s * self._idle_rate

    def snapshots(self, now: Fraction) -> list[BatterySnapshot]:
        return [
            self.snapshot(robot_id, now)
            for robot_id in self.robot_ids
        ]


# ----------------------------------------------------------------------
# Reporting. Floats from here on; the ledger itself stays exact.
# ----------------------------------------------------------------------


def fleet_summary(snapshots: list[BatterySnapshot]) -> dict:
    """Fleet-level battery figures at the snapshots' instant."""

    finals = [s.current_soc for s in snapshots]

    return {
        "robots": len(snapshots),
        "min_final_soc": min(finals),
        "mean_final_soc": sum(finals) / len(finals),
        "max_final_soc": max(finals),
        "lowest_minimum_soc": min(s.minimum_soc for s in snapshots),
        "total_energy_consumed": sum(
            (s.total_energy_consumed for s in snapshots),
            Fraction(0),
        ),
        "below_zero": [
            s.robot_id for s in snapshots if s.minimum_soc < 0
        ],
    }


def cross_check(snapshot: BatterySnapshot, robot, config: BatteryConfig) -> float:
    """|ledger SOC - SOC re-derived from the robot's own float counters|.

    GridRobot keeps distance, travel time, service time and waiting time
    as floats, booked by the engine independently of this ledger. SOC
    rebuilt from those, with idle as the remainder of elapsed time, must
    agree with the ledger to float precision. A real disagreement means
    an event was booked into one account and not the other.
    """

    elapsed = float(snapshot.time - snapshot.registered_at)

    stationary = elapsed - robot.travel_time_s - robot.service_time_s

    rebuilt = (
        float(snapshot.initial_soc)
        - robot.distance_m * float(config.travel_percent_per_20m) / 20
        - robot.service_time_s * float(config.service_percent_per_60s) / 60
        - stationary * float(config.waiting_idle_percent_per_300s) / 300
    )

    return abs(float(snapshot.current_soc) - rebuilt)


def battery_report_lines(
    ledger: BatteryLedger,
    now: Fraction,
    robots: dict,
) -> list[str]:
    """The end-of-workload battery section, for any front end.

    ``robots`` is robot_id -> GridRobot, for the independent cross
    check against the engine's own counters.
    """

    config = ledger.config
    snapshots = ledger.snapshots(now)

    lines = [
        "BATTERY (SOC ledger; drains only -- no charging before B4)",
        f"  model            : {float(config.travel_percent_per_20m):g}% "
        f"/ 20 m travel, "
        f"{float(config.service_percent_per_60s):g}% / 60 s service, "
        f"{float(config.waiting_idle_percent_per_300s):g}% / 300 s "
        f"waiting or idle",
        f"  thresholds       : reserve {float(config.reserve_percent):g}%, "
        f"charge due <= {float(config.charge_due_percent):g}%; charge "
        f"{float(config.charging_percent_per_second):g}%/s not yet used",
        f"  at               : t={float(now):.4f} s",
        "",
        "  robot        initial     final   minimum    travel   service"
        "   waiting      idle     total",
    ]

    for s in snapshots:
        lines.append(
            f"  {s.robot_id:<11}"
            + "".join(
                f"{float(value):10.4f}"
                for value in (
                    s.initial_soc,
                    s.current_soc,
                    s.minimum_soc,
                    s.travel_energy,
                    s.service_energy,
                    s.waiting_energy,
                    s.idle_energy,
                    s.total_energy_consumed,
                )
            )
        )

    summary = fleet_summary(snapshots)

    worst = max(
        cross_check(s, robots[s.robot_id], config)
        for s in snapshots
    )

    lines += [
        "",
        f"  final SOC        : min {float(summary['min_final_soc']):.4f}"
        f"  mean {float(summary['mean_final_soc']):.4f}"
        f"  max {float(summary['max_final_soc']):.4f}",
        f"  lowest minimum   : {float(summary['lowest_minimum_soc']):.4f}",
        f"  fleet energy     : "
        f"{float(summary['total_energy_consumed']):.4f} % SOC in total",
        "  SOC identity     : final = initial - travel - service"
        " - waiting - idle, exact (rational arithmetic)",
        f"  cross-check      : SOC rebuilt from the engine's own counters"
        f" differs by at most {worst:.3e} %",
        "  below 0 % SOC    : "
        + (", ".join(summary["below_zero"]) or "none"),
    ]

    return lines
