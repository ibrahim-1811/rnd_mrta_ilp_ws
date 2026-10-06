"""Build a robot fleet from configuration.

Pure logic, no ROS, so the world generator and the fleet node build the
same fleet from the same config and cannot disagree about where a robot
starts.

Robots are generated programmatically from ``fleet.robot_count``; the
count is never written out by hand.

The home bank is where robots are deployed and may idle. It is not an
order pickup or drop-off point, and nothing here makes it a mandatory
return destination.

V1 maps label that bank STAGING; V2 renames it to COLLECTION. Both are
accepted, so the same fleet code builds either map. "Home" is the
stable concept and is what the helpers here are named after.
"""

from mrta_core.sim_config import SimConfig
from mrta_core.warehouse_map import WarehouseMap
from mrta_core.warehouse_routing import HOME_TYPES

from simulation_env.grid_robot import GridRobot, GridRobotStatus


def home_bank(warehouse: WarehouseMap) -> list:
    """Home zones in deterministic id order: ``*_001`` first.

    A map is expected to label its bank with one home type. If it
    carries both, COLLECTION wins and STAGING is ignored, so a
    half-migrated sheet fails loudly on the robot count rather than
    silently deploying onto a mixture of the two.
    """

    for zone_type in HOME_TYPES:

        zones = warehouse.zones_of_type(zone_type)

        if zones:
            return sorted(
                zones,
                key=lambda zone: zone.zone_id,
            )

    return []


# Legacy name, kept so V1-era scripts keep working.
staging_stations = home_bank


def build_fleet(
    config: SimConfig,
    warehouse: WarehouseMap,
    speed_mps: float | None = None,
) -> list[GridRobot]:
    """Create one GridRobot per configured robot, at its home cell.

    robot_001 -> the first home station, robot_002 -> the second, and
    so on: staging_001.. on a V1 map, collection_001.. on a V2 map.

    ``speed_mps`` overrides ``fleet.speed_mps`` for this fleet only,
    so a run can be re-timed from the command line without editing the
    config. Everything else still comes from the config.
    """

    speed = config.speed_mps if speed_mps is None else speed_mps

    mode = config.initial_position_mode

    # "home" is the stable name for this; "staging" is the V1 spelling
    # and keeps existing configs working.
    if mode not in ("home", "staging"):
        raise ValueError(
            f"unsupported fleet.initial_position_mode {mode!r}; "
            f"only 'home' (or its legacy alias 'staging') "
            f"is implemented"
        )

    stations = home_bank(warehouse)

    if config.robot_count < 1:
        raise ValueError(
            f"fleet.robot_count must be >= 1, "
            f"got {config.robot_count}"
        )

    if config.robot_count > len(stations):
        raise ValueError(
            f"fleet.robot_count is {config.robot_count} but the map "
            f"has only {len(stations)} home station(s); either add "
            f"home cells to the XLSX (labelled "
            f"{' or '.join(t.value for t in HOME_TYPES)}) "
            f"or lower robot_count"
        )

    # A per-robot SOC override naming a robot this fleet does not have
    # would silently do nothing, so a controlled test would quietly run
    # at the default instead.
    fleet_ids = {
        config.robot_id(index)
        for index in range(config.robot_count)
    }

    stray = sorted(
        set(config.battery.initial_soc_overrides) - fleet_ids
    )

    if stray:
        raise ValueError(
            f"battery.initial_soc_overrides names {stray}, which "
            f"{'is' if len(stray) == 1 else 'are'} not in this "
            f"{config.robot_count}-robot fleet"
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

        if warehouse.zone_type_at(cell) not in HOME_TYPES:
            raise ValueError(
                f"{robot_id}: cell {cell} is not a home cell"
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
                speed_mps=speed,
                status=GridRobotStatus.IDLE,
            )
        )

    return robots


def home_stations(
    config: SimConfig,
    warehouse: WarehouseMap,
) -> dict[str, str]:
    """robot_id -> the home station it was deployed at."""

    stations = home_bank(warehouse)

    return {
        config.robot_id(index): stations[index].zone_id
        for index in range(config.robot_count)
    }
