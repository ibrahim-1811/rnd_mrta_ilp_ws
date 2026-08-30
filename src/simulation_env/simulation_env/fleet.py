"""Build a robot fleet from configuration.

Pure logic, no ROS, so the world generator and the fleet node build the
same fleet from the same config and cannot disagree about where a robot
starts.

Robots are generated programmatically from ``fleet.robot_count``; the
count is never written out by hand.

STAGING is where robots are deployed and may idle. It is not an order
pickup or drop-off point, and nothing here makes it a mandatory return
destination.
"""

from mrta_core.sim_config import SimConfig
from mrta_core.warehouse_map import WarehouseMap, ZoneType

from simulation_env.grid_robot import GridRobot, GridRobotStatus


def staging_stations(warehouse: WarehouseMap) -> list:
    """Staging zones in deterministic id order: staging_001 first."""

    return sorted(
        warehouse.zones_of_type(ZoneType.STAGING),
        key=lambda zone: zone.zone_id,
    )


def build_fleet(
    config: SimConfig,
    warehouse: WarehouseMap,
) -> list[GridRobot]:
    """Create one GridRobot per configured robot, at its staging cell.

    robot_001 -> staging_001, robot_002 -> staging_002, and so on.
    """

    mode = config.initial_position_mode

    if mode != "staging":
        raise ValueError(
            f"unsupported fleet.initial_position_mode {mode!r}; "
            f"only 'staging' is implemented"
        )

    stations = staging_stations(warehouse)

    if config.robot_count < 1:
        raise ValueError(
            f"fleet.robot_count must be >= 1, "
            f"got {config.robot_count}"
        )

    if config.robot_count > len(stations):
        raise ValueError(
            f"fleet.robot_count is {config.robot_count} but the map "
            f"has only {len(stations)} staging station(s); either add "
            f"staging cells to the XLSX or lower robot_count"
        )

    grid = warehouse.grid.grid_map()

    robots: list[GridRobot] = []

    taken: dict[tuple[int, int], str] = {}

    for index in range(config.robot_count):

        station = stations[index]

        cell = min(station.cells)

        robot_id = config.robot_id(index)

        if not grid.in_bounds(cell):
            raise ValueError(
                f"{robot_id}: {station.zone_id} cell {cell} "
                f"is out of bounds"
            )

        if cell in warehouse.grid.obstacles:
            raise ValueError(
                f"{robot_id}: {station.zone_id} cell {cell} "
                f"is a red obstacle"
            )

        if not warehouse.is_traversable(cell):
            raise ValueError(
                f"{robot_id}: {station.zone_id} cell {cell} "
                f"is not traversable"
            )

        if warehouse.zone_type_at(cell) is not ZoneType.STAGING:
            raise ValueError(
                f"{robot_id}: cell {cell} is not a staging cell"
            )

        if cell in taken:
            raise ValueError(
                f"{robot_id} and {taken[cell]} would both start "
                f"on cell {cell}"
            )

        taken[cell] = robot_id

        robots.append(
            GridRobot(
                robot_id=robot_id,
                cell=cell,
                speed_mps=config.speed_mps,
                battery_percent=config.initial_battery_percent,
                status=GridRobotStatus.IDLE,
            )
        )

    return robots


def home_stations(
    config: SimConfig,
    warehouse: WarehouseMap,
) -> dict[str, str]:
    """robot_id -> the staging station it was deployed at."""

    stations = staging_stations(warehouse)

    return {
        config.robot_id(index): stations[index].zone_id
        for index in range(config.robot_count)
    }
