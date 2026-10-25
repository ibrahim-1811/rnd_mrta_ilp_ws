"""Central simulation configuration.

One YAML file is authoritative for map, fleet, battery, service
durations, orders and Gazebo. Kept here in ``mrta_core`` -- plain
Python, no ROS --
so the workload and simulation components read the same file
through the same code instead of each carrying its own constants.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from mrta_core.battery import BatteryConfig


@dataclass(frozen=True)
class SimConfig:
    config_path: Path

    map_path: Path
    resolution_m: float

    robot_count: int
    robot_prefix: str
    initial_position_mode: str
    speed_mps: float

    battery: BatteryConfig

    shelf_pick_s: float
    dropoff_s: float

    allocation_window: str
    dispatch_quantum_s: float

    orders_path: Path
    orders_mode: str

    deterministic: bool
    seed: int
    visualization_time_scale: float
    visualization_min_wall_hold_s: float

    robot_states_topic: str
    order_states_topic: str
    robot_state_publish_hz: float

    gazebo_enabled: bool
    gazebo_world_name: str

    def wall_for(self, sim_seconds: float) -> float:
        """Wall-clock seconds to spend showing ``sim_seconds``.

        Playback only. This never feeds back into simulation time.
        """
        if self.visualization_time_scale <= 0.0:
            return 0.0

        return max(
            sim_seconds / self.visualization_time_scale,
            self.visualization_min_wall_hold_s,
        )

    def robot_id(self, index: int = 0) -> str:
        """Deterministic robot id, 1-based: robot_001."""
        return f"{self.robot_prefix}{index + 1:03d}"

    def window_width(self) -> int | None:
        """Resolve ``allocation_window`` to a width, or None for legacy.

        ``fleet_size`` is the frozen default: the allocator may choose
        among as many orders as the fleet has robots, however many of
        them happen to be free at that instant. ``legacy`` restores the
        pre-S0 ``min(available_robots, pending)`` rule and exists only
        so the frozen pre-S0 baselines stay reproducible.

        See docs/DECISIONS.md, "Allocation Candidate Window".
        """

        if self.allocation_window == "legacy":
            return None

        if self.allocation_window == "fleet_size":
            return self.robot_count

        return int(self.allocation_window)


def _resolve(
    name: str,
    config_dir: Path,
    extra_dirs: tuple[Path, ...] = (),
) -> Path:
    """Find a file named in the config, relative to the config itself."""

    candidate = Path(name)

    if candidate.is_absolute():
        return candidate

    for directory in (config_dir, *extra_dirs):
        resolved = directory / name

        if resolved.exists():
            return resolved

    searched = ", ".join(str(d) for d in (config_dir, *extra_dirs))

    raise FileNotFoundError(f"{name!r} not found; looked in: {searched}")


def resolve_map_path(path: str | Path) -> Path:
    """The map a config names, without loading the rest of the config.

    ``load_sim_config`` needs the order file to exist. Generating that
    file is exactly what needs to know the map first, so this resolves
    the map alone, by the same rule ``load_sim_config`` uses.
    """

    path = Path(path)

    with path.open() as handle:
        raw = yaml.safe_load(handle) or {}

    warehouse = raw.get("warehouse") or {}

    return _resolve(
        warehouse.get("map", "W100.xlsx"),
        path.parent,
        (path.parent.parent / "maps",),
    )


def load_sim_config(path: str | Path) -> SimConfig:

    path = Path(path)

    with path.open() as handle:
        raw = yaml.safe_load(handle) or {}

    config_dir = path.parent

    def section(name: str) -> dict:
        return raw.get(name) or {}

    warehouse = section("warehouse")
    fleet = section("fleet")
    service = section("service")
    orders = section("orders")
    simulation = section("simulation")
    visualization = section("visualization")
    ros = section("ros")
    gazebo = section("gazebo")
    allocation = section("allocation")

    # The allocation window is a scientific parameter, so a typo must
    # not quietly fall back to a default that changes every result.
    window = str(allocation.get("window", "fleet_size"))

    if window not in ("fleet_size", "legacy"):
        if not window.isdigit() or int(window) < 1:
            raise ValueError(
                f"{path}: allocation.window must be 'fleet_size', 'legacy' "
                f"or a positive integer, got {window!r}"
            )

    quantum = float(allocation.get("dispatch_quantum_s", 0.0))

    if quantum < 0.0:
        raise ValueError(
            f"{path}: allocation.dispatch_quantum_s must be >= 0, got {quantum}"
        )

    # Initial SOC used to live here. It moved into the battery section
    # so there is one source of truth; refuse the old key rather than
    # silently ignore a value someone expected to take effect.
    if "initial_battery_percent" in fleet:
        raise ValueError(
            f"{path}: fleet.initial_battery_percent has moved to "
            f"battery.initial_soc_percent"
        )

    # maps/ sits beside config/ in both the source tree and the
    # installed share directory.
    map_path = _resolve(
        warehouse.get("map", "W100.xlsx"),
        config_dir,
        (config_dir.parent / "maps",),
    )

    orders_path = _resolve(
        orders.get("file", "orders.csv"),
        config_dir,
    )

    return SimConfig(
        config_path=path,
        map_path=map_path,
        resolution_m=float(warehouse.get("resolution_m", 1.0)),
        robot_count=int(fleet.get("robot_count", 1)),
        robot_prefix=str(fleet.get("robot_prefix", "robot_")),
        initial_position_mode=str(fleet.get("initial_position_mode", "staging")),
        speed_mps=float(fleet.get("speed_mps", 1.5)),
        battery=BatteryConfig.from_mapping(section("battery")),
        shelf_pick_s=float(service.get("shelf_pick_s", 2.0)),
        dropoff_s=float(service.get("dropoff_s", 2.0)),
        allocation_window=window,
        dispatch_quantum_s=quantum,
        orders_path=orders_path,
        orders_mode=str(orders.get("mode", "replay")),
        deterministic=bool(simulation.get("deterministic", True)),
        seed=int(simulation.get("seed", 42)),
        visualization_time_scale=float(visualization.get("time_scale", 1.0)),
        visualization_min_wall_hold_s=float(visualization.get("min_wall_hold_s", 0.0)),
        robot_states_topic=str(ros.get("robot_states_topic", "/mrta/robot_states")),
        order_states_topic=str(ros.get("order_states_topic", "/mrta/order_states")),
        robot_state_publish_hz=float(ros.get("robot_state_publish_hz", 2.0)),
        gazebo_enabled=bool(gazebo.get("enabled", True)),
        gazebo_world_name=str(gazebo.get("world_name", "warehouse_grid")),
    )


def rewrite_scenario(text: str, *, map_name: str, robot_count: int) -> str:
    """The config text with its map and fleet size switched to a new map.

    Only the ``warehouse.map`` and ``fleet.robot_count`` lines change;
    every comment and every other setting is kept exactly. Used when a
    new map becomes the current one, so the one config always describes
    the map the one world was built from.
    """

    import re

    replacements = (
        (r"^(  map:)[ \t]*\S.*$", rf"\g<1> {map_name}", "warehouse.map"),
        (r"^(  robot_count:)[ \t]*\S.*$", rf"\g<1> {robot_count}", "fleet.robot_count"),
    )

    for pattern, replacement, name in replacements:
        text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)

        if count != 1:
            raise ValueError(f"config has no {name} line to rewrite")

    return text
