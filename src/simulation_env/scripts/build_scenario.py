#!/usr/bin/env python3

"""Make every generated artefact agree with one config file.

The config is the single source of truth. The map, the fleet size, the
cell size, the order file and the Gazebo world name all live there, and
three artefacts are derived from it:

    <config>.warehouse.map          the hand-authored XLSX
            |
            +--> worlds/<gazebo.world_name>.world     Gazebo geometry
            |                                         + fleet.robot_count robots
            +--> <nav graph yaml>                     RMF lanes

Run this whenever any of those change. It regenerates both artefacts
and then verifies them, so a scenario cannot be half-updated -- which
is what leaves eighteen robot models in a world the config says has
ten.

    build_scenario.py
    build_scenario.py --config src/simulation_env/config/warehouse_sim_10.yaml
"""

import argparse
import subprocess
import sys
from pathlib import Path

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.fleet import build_fleet, staging_stations


DEFAULT_CONFIG = "src/simulation_env/config/warehouse_sim.yaml"

WORLDS_DIR = Path("src/simulation_env/worlds")

NAV_GRAPH_DIR = Path("src/rmf_grid_traffic/config")

WORLD_GENERATOR = Path(
    "src/simulation_env/scripts/generate_gazebo_grid_world.py"
)

NAV_GENERATOR = Path(
    "src/rmf_grid_traffic/scripts/generate_nav_graph.py"
)

NAV_VERIFIER = Path(
    "src/rmf_grid_traffic/scripts/verify_nav_graph.py"
)

TOPOLOGY_VALIDATOR = Path(
    "src/simulation_env/scripts/validate_directional_highways.py"
)


def run(step: str, command: list[str]) -> None:
    """Run one generation step, failing loudly."""

    print()
    print("=" * 72)
    print(step)
    print("=" * 72)

    result = subprocess.run(
        [sys.executable, *command],
    )

    if result.returncode != 0:
        raise SystemExit(
            f"{step} failed with exit code {result.returncode}"
        )


def main() -> int:

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        type=Path,
    )

    parser.add_argument(
        "--nav-graph",
        default=None,
        type=Path,
        help="output nav graph "
             "(default: config/<gazebo.world_name>_nav_graph.yaml)",
    )

    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="generate only, do not run the topology gates",
    )

    args = parser.parse_args()

    config = load_sim_config(args.config)
    warehouse = load_warehouse_xlsx(config.map_path)

    world_path = WORLDS_DIR / f"{config.gazebo_world_name}.world"

    nav_graph_path = (
        args.nav_graph
        if args.nav_graph is not None
        else NAV_GRAPH_DIR / "warehouse_nav_graph.yaml"
    )

    stations = staging_stations(warehouse)

    # Fail before generating anything, rather than writing a world the
    # fleet cannot actually be placed in.
    if config.robot_count > len(stations):
        raise SystemExit(
            f"fleet.robot_count is {config.robot_count} but "
            f"{config.map_path.name} has only {len(stations)} staging "
            f"cell(s). Add STAGE cells to the map or lower robot_count."
        )

    fleet = build_fleet(config, warehouse)

    print()
    print("=" * 72)
    print("SCENARIO")
    print("=" * 72)
    print(f"  config       : {args.config}")
    print(f"  map          : {config.map_path}")
    print(f"  grid         : {warehouse.grid.width} x "
          f"{warehouse.grid.height} @ {config.resolution_m} m")
    print(f"  robots       : {config.robot_count} "
          f"(map allows up to {len(stations)})")
    print(f"  speed        : {config.speed_mps} m/s")
    print(f"  orders       : {config.orders_path}")
    print(f"  world name   : {config.gazebo_world_name}")
    print(f"  world file   : {world_path}")
    print(f"  nav graph    : {nav_graph_path}")
    print()
    print("  fleet placement:")

    for robot in fleet[:3]:
        print(f"    {robot.robot_id} -> {robot.cell}")

    if len(fleet) > 3:
        print(f"    ... {len(fleet) - 3} more")

    run(
        "GAZEBO WORLD",
        [
            str(WORLD_GENERATOR),
            "--config", str(args.config),
            "--output", str(world_path),
        ],
    )

    run(
        "RMF NAVIGATION GRAPH",
        [
            str(NAV_GENERATOR),
            "--config", str(args.config),
            "--output", str(nav_graph_path),
        ],
    )

    if args.skip_verify:
        print()
        print("verification skipped")
        return 0

    run(
        "TOPOLOGY GATE",
        [
            str(TOPOLOGY_VALIDATOR),
            "--config", str(args.config),
        ],
    )

    run(
        "NAV GRAPH MIRRORS THE LANE POLICY",
        [
            str(NAV_VERIFIER),
            "--config", str(args.config),
            "--graph", str(nav_graph_path),
        ],
    )

    print()
    print("=" * 72)
    print("SCENARIO READY")
    print("=" * 72)
    print(f"  robots in world : {config.robot_count}")
    print(f"  world           : {world_path}")
    print(f"  nav graph       : {nav_graph_path}")
    print()
    print("  colcon build --packages-select simulation_env "
          "rmf_grid_traffic")
    print("  source install/setup.bash")
    print(
        f"  ros2 launch simulation_env warehouse_sim.launch.py "
        f"config_file:=$PWD/{args.config} orders:=true"
    )
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
