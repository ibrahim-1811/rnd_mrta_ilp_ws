"""Physics-based robot motion and energy model.

Shared single source of truth for travel time and energy so the simulator drains
the *same* battery the allocators estimate. A grid path is a sequence of cells; the
robot drives straight runs joined by in-place 90 degree turns (4-connected grid).

Per straight run of length L (start and end at rest):
  * time  : trapezoidal velocity profile with (v_max, a_max)
  * energy: rolling-resistance work over L, plus idle electronics power over the run
            time, divided by drivetrain efficiency.
Per turn:
  * time  : (pi/2) / omega_max
  * energy: turn power (+ idle) over the turn time.

Energies are in joules internally, exposed in watt-hours and as a percentage of
battery capacity, so the simulator can keep working in battery-percent.

Defaults model a warehouse AMR (robot + payload), calibrated so a typical task
drains a few percent -- battery stays a meaningful, discriminating feature (the
old system's flat-per-metre model made it nearly constant).
"""

from dataclasses import dataclass
from math import pi, sqrt

from mrta_core.grid import Cell

GRAVITY = 9.81


@dataclass(frozen=True)
class RobotModel:
    # kinematics
    v_max: float = 2.0          # m/s cruise speed (matches simulator robot_speed_ms)
    a_max: float = 0.8          # m/s^2 linear acceleration
    omega_max: float = 2.84     # rad/s angular speed (TurtleBot3-class)
    # mass / resistance
    mass: float = 60.0          # kg, robot chassis
    payload: float = 20.0       # kg, carried load
    rolling_resistance: float = 0.08   # effective coefficient (wheels + drivetrain)
    # power / energy
    idle_power: float = 30.0    # W, electronics + sensors baseline
    turn_power: float = 40.0    # W, drawn while rotating in place
    drivetrain_efficiency: float = 0.75
    battery_capacity_wh: float = 10.0  # Wh usable

    def straight_time(self, length: float) -> float:
        """Trapezoidal-profile time to drive a straight run of ``length`` metres."""
        if length <= 0.0:
            return 0.0
        v, a = self.v_max, self.a_max
        d_acc = v * v / (2.0 * a)
        if 2.0 * d_acc <= length:
            t_acc = v / a
            d_cruise = length - 2.0 * d_acc
            return 2.0 * t_acc + d_cruise / v
        v_peak = sqrt(a * length)
        return 2.0 * v_peak / a

    def turn_time(self, angle: float = pi / 2.0) -> float:
        return angle / self.omega_max if self.omega_max > 0 else 0.0

    def straight_energy_j(self, length: float) -> float:
        if length <= 0.0:
            return 0.0
        f_roll = self.rolling_resistance * (self.mass + self.payload) * GRAVITY
        work = f_roll * length
        t = self.straight_time(length)
        return work / self.drivetrain_efficiency + self.idle_power * t

    def turn_energy_j(self, angle: float = pi / 2.0) -> float:
        t = self.turn_time(angle)
        return (self.turn_power + self.idle_power) * t


def _runs_and_turns(path: list[Cell]) -> tuple[list[int], int]:
    """Split a cell path into straight-run cell-counts and count 90 degree turns."""
    if len(path) < 2:
        return [], 0
    dirs = [
        (path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1])
        for i in range(len(path) - 1)
    ]
    runs: list[int] = []
    turns = 0
    current = dirs[0]
    count = 1
    for d in dirs[1:]:
        if d == current:
            count += 1
        else:
            runs.append(count)
            turns += 1
            current = d
            count = 1
    runs.append(count)
    return runs, turns


def path_time(path: list[Cell], resolution: float, model: RobotModel) -> float:
    runs, turns = _runs_and_turns(path)
    total = sum(model.straight_time(run * resolution) for run in runs)
    total += turns * model.turn_time()
    return total


def path_energy_j(path: list[Cell], resolution: float, model: RobotModel) -> float:
    runs, turns = _runs_and_turns(path)
    total = sum(model.straight_energy_j(run * resolution) for run in runs)
    total += turns * model.turn_energy_j()
    return total


def path_energy_wh(path: list[Cell], resolution: float, model: RobotModel) -> float:
    return path_energy_j(path, resolution, model) / 3600.0


def path_energy_percent(path: list[Cell], resolution: float, model: RobotModel) -> float:
    if model.battery_capacity_wh <= 0.0:
        return 0.0
    return 100.0 * path_energy_wh(path, resolution, model) / model.battery_capacity_wh
