"""Central simulation configuration.

One YAML file is authoritative for map, fleet, service durations,
orders and Gazebo. Kept here in ``mrta_core`` -- plain Python, no ROS --
so ``shared_memory_node`` and ``simulation_env`` read the same file
through the same code instead of each carrying its own constants.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SimConfig:
    config_path: Path

    map_path: Path
    resolution_m: float

    robot_count: int
    robot_prefix: str
    initial_position_mode: str
    speed_mps: float
    initial_battery_percent: float

    shelf_pick_s: float
    dropoff_s: float

    orders_path: Path
    orders_mode: str

    start_zone: str
    park_when_idle: bool
    park_zone_mode: str

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

    searched = ", ".join(
        str(d) for d in (config_dir, *extra_dirs)
    )

    raise FileNotFoundError(
        f"{name!r} not found; looked in: {searched}"
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
    mission = section("mission")
    simulation = section("simulation")
    visualization = section("visualization")
    ros = section("ros")
    gazebo = section("gazebo")

    # maps/ sits beside config/ in both the source tree and the
    # installed share directory.
    map_path = _resolve(
        warehouse.get("map", "CSV_To_Gazebo.xlsx"),
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
        initial_position_mode=str(
            fleet.get("initial_position_mode", "staging")
        ),
        speed_mps=float(fleet.get("speed_mps", 1.5)),
        initial_battery_percent=float(
            fleet.get("initial_battery_percent", 100.0)
        ),
        shelf_pick_s=float(service.get("shelf_pick_s", 2.0)),
        dropoff_s=float(service.get("dropoff_s", 2.0)),
        orders_path=orders_path,
        orders_mode=str(orders.get("mode", "replay")),
        start_zone=str(mission.get("start_zone", "collection_001")),
        park_when_idle=bool(mission.get("park_when_idle", True)),
        park_zone_mode=str(mission.get("park_zone_mode", "home")),
        deterministic=bool(simulation.get("deterministic", True)),
        seed=int(simulation.get("seed", 42)),
        visualization_time_scale=float(
            visualization.get("time_scale", 1.0)
        ),
        visualization_min_wall_hold_s=float(
            visualization.get("min_wall_hold_s", 0.0)
        ),
        robot_states_topic=str(
            ros.get("robot_states_topic", "/mrta/robot_states")
        ),
        order_states_topic=str(
            ros.get("order_states_topic", "/mrta/order_states")
        ),
        robot_state_publish_hz=float(
            ros.get("robot_state_publish_hz", 2.0)
        ),
        gazebo_enabled=bool(gazebo.get("enabled", True)),
        gazebo_world_name=str(
            gazebo.get("world_name", "warehouse_grid")
        ),
    )
