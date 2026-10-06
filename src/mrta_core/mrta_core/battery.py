"""Battery V1: a normalized, deterministic state-of-charge model.

Fixed infrastructure, identical for every MRTA strategy. This is not a
hardware-calibrated electrical model. SOC is a percentage drained by
three things and nothing else:

    travel             travel_percent_per_20m          per 20 m driven
    known service      service_percent_per_60s         per 60 s of pick or drop
    waiting and idle   waiting_idle_percent_per_300s   per 300 s standing still

It is raised only by charging (``charging_percent_per_second``), which
is configured here but not implemented yet.

The arithmetic is exact. Distances and durations arrive as the same
``Fraction`` values the fleet engine's clock uses, so an energy total is
a rational number with no rounding drift, and a threshold such as
SOC <= 10 can later be decided exactly. Floats appear only in reports.

This module is pure: no robot state, no clock. The execution ledger
(``simulation_env.battery_ledger``) books energy with these functions,
and the allocator's decision-time estimate will use the same ones, so
the energy a strategy predicts and the energy a robot is charged cannot
drift apart.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from fractions import Fraction


TRAVEL_REFERENCE_M = 20
SERVICE_REFERENCE_S = 60
STATIONARY_REFERENCE_S = 300


def exact(value) -> Fraction:
    """A config or clock number as an exact rational.

    Fractions pass through untouched. Ints and floats are read the way
    the engine reads configured durations, so a YAML ``12.3`` becomes
    123/10 rather than the nearest binary float.
    """

    if isinstance(value, Fraction):
        return value

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"expected a number, got {value!r}")

    return Fraction(value).limit_denominator(10**6)


@dataclass(frozen=True)
class BatteryConfig:
    """The ``battery:`` section of the simulation config."""

    enabled: bool = True
    initial_soc_percent: Fraction = Fraction(100)
    travel_percent_per_20m: Fraction = Fraction(1)
    service_percent_per_60s: Fraction = Fraction(1)
    waiting_idle_percent_per_300s: Fraction = Fraction(1)
    charging_percent_per_second: Fraction = Fraction(1)
    reserve_percent: Fraction = Fraction(10)
    charge_due_percent: Fraction = Fraction(10)

    # robot_id -> initial SOC, for controlled tests only. The
    # scientific baseline configs never set this.
    initial_soc_overrides: dict = field(default_factory=dict)

    def __post_init__(self) -> None:

        for name in (
            "travel_percent_per_20m",
            "service_percent_per_60s",
            "waiting_idle_percent_per_300s",
            "charging_percent_per_second",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"battery.{name} must be > 0")

        for name in (
            "initial_soc_percent",
            "reserve_percent",
            "charge_due_percent",
        ):
            if not 0 <= getattr(self, name) <= 100:
                raise ValueError(
                    f"battery.{name} must be within 0..100"
                )

        for robot_id, soc in self.initial_soc_overrides.items():
            if not 0 <= soc <= 100:
                raise ValueError(
                    f"battery.initial_soc_overrides.{robot_id} must "
                    f"be within 0..100, got {float(soc)}"
                )

    def initial_soc_for(self, robot_id: str) -> Fraction:
        return self.initial_soc_overrides.get(
            robot_id,
            self.initial_soc_percent,
        )

    @classmethod
    def from_mapping(cls, raw: dict | None) -> "BatteryConfig":
        """Parse the YAML section. Unknown keys are refused.

        A misspelt key would otherwise silently fall back to its
        default, and the run would drain at a rate nobody asked for.
        """

        raw = dict(raw or {})

        known = {
            "enabled",
            "initial_soc_percent",
            "travel_percent_per_20m",
            "service_percent_per_60s",
            "waiting_idle_percent_per_300s",
            "charging_percent_per_second",
            "reserve_percent",
            "charge_due_percent",
            "initial_soc_overrides",
        }

        unknown = sorted(set(raw) - known)

        if unknown:
            raise ValueError(
                f"unknown battery config key(s) {unknown}; "
                f"known: {sorted(known)}"
            )

        enabled = raw.pop("enabled", True)

        if not isinstance(enabled, bool):
            raise ValueError("battery.enabled must be true or false")

        overrides = raw.pop("initial_soc_overrides", None) or {}

        if not isinstance(overrides, dict):
            raise ValueError(
                "battery.initial_soc_overrides must map "
                "robot_id -> initial SOC percent"
            )

        return cls(
            enabled=enabled,
            initial_soc_overrides={
                str(robot_id): exact(soc)
                for robot_id, soc in overrides.items()
            },
            **{name: exact(value) for name, value in raw.items()},
        )


def travel_energy(config: BatteryConfig, distance_m) -> Fraction:
    """SOC percent spent driving ``distance_m``."""

    return (
        exact(distance_m)
        * config.travel_percent_per_20m
        / TRAVEL_REFERENCE_M
    )


def service_energy(config: BatteryConfig, seconds) -> Fraction:
    """SOC percent spent on ``seconds`` of pick or drop service."""

    return (
        exact(seconds)
        * config.service_percent_per_60s
        / SERVICE_REFERENCE_S
    )


def stationary_energy(config: BatteryConfig, seconds) -> Fraction:
    """SOC percent spent standing still, waiting or idle, for ``seconds``."""

    return (
        exact(seconds)
        * config.waiting_idle_percent_per_300s
        / STATIONARY_REFERENCE_S
    )


# ----------------------------------------------------------------------
# Decision-time energy eligibility (Battery V1, increment B2).
# ----------------------------------------------------------------------
#
# A robot may be given an order only if it can do the whole order and
# still reach a charger with the reserve left:
#
#     required = travel(order route) + service(order) + travel(to charger)
#                + reserve
#     eligible = SOC >= required          (equality is eligible)
#
# Every term is a static prediction from the production directed A*
# router. Predicted traffic waiting is deliberately not a term: waiting
# is a realized outcome, and it drains the battery in the execution
# ledger when it actually happens.


@dataclass(frozen=True)
class EnergyRequirement:
    """Decision-time battery features of one robot-order pair.

    Everything here is known at the instant of the decision. Nothing is
    a realized outcome of executing the order.
    """

    current_soc_percent: Fraction
    predicted_order_distance_m: Fraction
    predicted_order_travel_energy_percent: Fraction
    predicted_service_energy_percent: Fraction
    predicted_nearest_charger_distance_m: Fraction
    predicted_charger_energy_percent: Fraction
    reserve_percent: Fraction
    required_soc_percent: Fraction
    energy_margin_percent: Fraction
    battery_eligible: bool

    # Traceability only: which charger the nearest-charger term used.
    nearest_charger_id: str


def energy_requirement(
    config: BatteryConfig,
    *,
    current_soc,
    order_distance_m,
    service_s,
    charger_distance_m,
    charger_id: str,
) -> EnergyRequirement:
    """The eligibility equation, evaluated exactly."""

    soc = exact(current_soc)

    travel = travel_energy(config, order_distance_m)
    service = service_energy(config, service_s)
    to_charger = travel_energy(config, charger_distance_m)

    required = travel + service + to_charger + config.reserve_percent
    margin = soc - required

    return EnergyRequirement(
        current_soc_percent=soc,
        predicted_order_distance_m=exact(order_distance_m),
        predicted_order_travel_energy_percent=travel,
        predicted_service_energy_percent=service,
        predicted_nearest_charger_distance_m=exact(charger_distance_m),
        predicted_charger_energy_percent=to_charger,
        reserve_percent=config.reserve_percent,
        required_soc_percent=required,
        energy_margin_percent=margin,
        battery_eligible=margin >= 0,
        nearest_charger_id=charger_id,
    )


@dataclass
class BatteryDecisionContext:
    """What an allocator may know about batteries at one decision.

    Built by the executor for one decision instant and handed to the
    strategy. ``soc`` is each offered robot's SOC at that instant.
    ``nearest_charger(cell)`` returns ``(charger_id, distance_m)`` of the
    charger with the cheapest directed-A* route from ``cell``.

    Every strategy computes eligibility through ``requirement()``, so
    all of them obey the same battery feasibility contract. The counters
    record how many robot-order pairs were evaluated and how many of
    those were ineligible, for reporting only.
    """

    config: BatteryConfig
    soc: dict
    shelf_service_s: Fraction
    dropoff_service_s: Fraction
    nearest_charger: Callable

    evaluated: int = 0
    ineligible: int = 0

    def requirement(
        self,
        robot_id: str,
        *,
        order_distance_m,
        shelf_services: int,
        end_cell,
    ) -> EnergyRequirement:
        """Eligibility of one robot for one planned order route.

        ``order_distance_m`` is the whole static route: robot cell,
        every shelf in the chosen order, the dropoff, and the retreat
        out of it. ``end_cell`` is that post-dropoff retreat cell, the
        aisle cell the charger leg starts from -- never the inside of
        the dropoff.
        """

        charger_id, charger_distance_m = self.nearest_charger(end_cell)

        result = energy_requirement(
            self.config,
            current_soc=self.soc[robot_id],
            order_distance_m=order_distance_m,
            service_s=(
                shelf_services * self.shelf_service_s
                + self.dropoff_service_s
            ),
            charger_distance_m=charger_distance_m,
            charger_id=charger_id,
        )

        self.evaluated += 1

        if not result.battery_eligible:
            self.ineligible += 1

        return result
