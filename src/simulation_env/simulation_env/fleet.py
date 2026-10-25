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


# How a fleet smaller than the parking bank is placed at t=0.
#
#   home, staging   robot k on the k-th home cell in id order. The
#                   original rule; every frozen baseline uses it.
#   spread          robots shared out across every parking bank and
#                   evenly spaced along each. For fleet-size sweeps on
#                   one map: without it, 10 robots on a 100-cell bank
#                   all start bunched in one corner, so the start
#                   condition would change in kind across the sweep.
#
# Either way it is fixed infrastructure: the same cells for every
# strategy, and only the starting position -- parking at the end is
# always the nearest free cell (stage_parking).
SPAWN_MODES = ("home", "staging", "spread")


def _banks(stations) -> list[list]:
    """Home stations grouped into banks of touching cells, in id order."""

    cell_of = {station.zone_id: min(station.cells) for station in stations}
    by_cell = {cell_of[station.zone_id]: station for station in stations}

    banks = []
    seen = set()

    for station in stations:
        if station.zone_id in seen:
            continue

        bank = []
        stack = [station]
        seen.add(station.zone_id)

        while stack:
            current = stack.pop()
            bank.append(current)
            x, y = cell_of[current.zone_id]

            for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                other = by_cell.get(neighbour)

                if other is not None and other.zone_id not in seen:
                    seen.add(other.zone_id)
                    stack.append(other)

        banks.append(sorted(bank, key=lambda zone: zone.zone_id))

    return banks


def _spread(stations, count: int) -> list:
    """``count`` stations spaced evenly over every bank.

    Robots are shared out across banks in proportion to bank size
    (largest remainder, ties to the earlier bank), then each bank's
    share is centred at even intervals along it. A full fleet gets
    every cell. Returned in id order, so robot_001 takes the first.
    """

    banks = _banks(stations)
    total = len(stations)

    exact = [count * len(bank) / total for bank in banks]
    share = [int(value) for value in exact]

    order = sorted(
        range(len(banks)),
        key=lambda index: (-(exact[index] - share[index]), index),
    )

    for index in order[: count - sum(share)]:
        share[index] += 1

    picked = []

    for bank, k in zip(banks, share):
        size = len(bank)
        picked.extend(bank[((2 * j + 1) * size) // (2 * k)] for j in range(k))

    return sorted(picked, key=lambda zone: zone.zone_id)


def spawn_stations(config: SimConfig, warehouse: WarehouseMap) -> list:
    """The home station each robot starts on, robot_001 first."""

    stations = home_bank(warehouse)
    count = config.robot_count

    if config.initial_position_mode == "spread" and 0 < count <= len(stations):
        return _spread(stations, count)

    return stations[:count]


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
    if mode not in SPAWN_MODES:
        raise ValueError(
            f"unsupported fleet.initial_position_mode {mode!r}; "
            f"use 'home' (or its legacy alias 'staging') or 'spread'"
        )

    stations = home_bank(warehouse)

    if config.robot_count < 1:
        raise ValueError(f"fleet.robot_count must be >= 1, got {config.robot_count}")

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
    fleet_ids = {config.robot_id(index) for index in range(config.robot_count)}

    stray = sorted(set(config.battery.initial_soc_overrides) - fleet_ids)

    if stray:
        raise ValueError(
            f"battery.initial_soc_overrides names {stray}, which "
            f"{'is' if len(stray) == 1 else 'are'} not in this "
            f"{config.robot_count}-robot fleet"
        )

    grid = warehouse.grid.grid_map()

    robots: list[GridRobot] = []

    taken: dict[tuple[int, int], str] = {}

    starts = spawn_stations(config, warehouse)

    for index in range(config.robot_count):
        station = starts[index]

        cell = min(station.cells)

        robot_id = config.robot_id(index)

        if not grid.in_bounds(cell):
            raise ValueError(
                f"{robot_id}: {station.zone_id} cell {cell} is out of bounds"
            )

        if cell in warehouse.grid.obstacles:
            raise ValueError(
                f"{robot_id}: {station.zone_id} cell {cell} is a red obstacle"
            )

        if not warehouse.is_traversable(cell):
            raise ValueError(
                f"{robot_id}: {station.zone_id} cell {cell} is not traversable"
            )

        if warehouse.zone_type_at(cell) not in HOME_TYPES:
            raise ValueError(f"{robot_id}: cell {cell} is not a home cell")

        if cell in taken:
            raise ValueError(
                f"{robot_id} and {taken[cell]} would both start on cell {cell}"
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

    starts = spawn_stations(config, warehouse)

    return {
        config.robot_id(index): starts[index].zone_id
        for index in range(config.robot_count)
    }
