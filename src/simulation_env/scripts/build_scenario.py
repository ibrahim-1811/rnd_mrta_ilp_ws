#!/usr/bin/env python3

"""Build everything for the current map: config, orders, Gazebo world.

One map, one config, one world. Switching to a new map is one command:

    build_scenario.py --map src/simulation_env/maps/<your map>.xlsx

which, in order:

    1. checks the map (map_tool check) and stops on any error
    2. rewrites the one config, src/simulation_env/config/warehouse.yaml:
       warehouse.map -> the new map, fleet.robot_count -> its parking cells
    3. regenerates config/orders.csv from the new map's own stations
    4. rebuilds the one Gazebo world, worlds/warehouse.world (the
       warehouse only; robots are created when a run starts)
    5. rebuilds the RMF nav graph from the map's lanes and verifies it

Without --map it rebuilds the world and nav graph for the map the
config already names (for example after editing that map in Excel; add
--orders to regenerate the orders too).

    build_scenario.py
    build_scenario.py --map src/simulation_env/maps/W100.xlsx --orders 2000
"""

import argparse
import subprocess
import sys
from pathlib import Path

from mrta_core.sim_config import load_sim_config, rewrite_scenario
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.fleet import home_bank


DEFAULT_CONFIG = "src/simulation_env/config/warehouse.yaml"

WORLDS_DIR = Path("src/simulation_env/worlds")

NAV_GRAPH_DIR = Path("src/rmf_grid_traffic/config")

WORLD_GENERATOR = Path("src/simulation_env/scripts/generate_gazebo_grid_world.py")

NAV_GENERATOR = Path("src/rmf_grid_traffic/scripts/generate_nav_graph.py")

NAV_VERIFIER = Path("src/rmf_grid_traffic/scripts/verify_nav_graph.py")

MAP_TOOL = Path("src/simulation_env/scripts/map_tool.py")

ORDER_GENERATOR = Path("src/simulation_env/scripts/generate_orders.py")


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
        raise SystemExit(f"{step} failed with exit code {result.returncode}")


def check_map(map_path: Path) -> None:
    """The drawing check; stop before touching the config on any error."""

    result = subprocess.run(
        [sys.executable, str(MAP_TOOL), "check", "--map", str(map_path)],
    )

    if result.returncode != 0:
        raise SystemExit(
            f"{map_path} has errors (listed above). Fix them in Excel and "
            "run this again; nothing was changed."
        )


def main() -> int:

    # This script's own lines and its sub-steps' output share a terminal;
    # line buffering keeps them in the order they happen.
    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--map",
        default=None,
        type=Path,
        help="make this Excel map the current one (rewrites the config "
        "and the orders); default: keep the config's map",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        type=Path,
        help="the one config (default: %(default)s)",
    )
    parser.add_argument(
        "--orders",
        type=int,
        default=None,
        help="number of orders to generate (default: 2000 with --map, "
        "otherwise the order file is left alone)",
    )
    parser.add_argument("--seed", type=int, default=42, help="order seed")
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="generate only, do not run the nav graph check",
    )

    args = parser.parse_args()

    if args.map is not None:
        map_path = args.map.resolve()

        if not map_path.is_file():
            raise SystemExit(f"--map {args.map}: no such file")

        check_map(map_path)

        # A map in maps/ is named by file name, like every other config
        # path; one elsewhere keeps its absolute path.
        maps_dir = (args.config.resolve().parent.parent / "maps").resolve()
        map_name = (
            map_path.name if map_path.parent == maps_dir else str(map_path)
        )

        parking = len(home_bank(load_warehouse_xlsx(map_path)))

        text = args.config.read_text()
        args.config.write_text(
            rewrite_scenario(text, map_name=map_name, robot_count=parking)
        )

        print()
        print("=" * 72)
        print("CONFIG")
        print("=" * 72)
        print(f"  {args.config}")
        print(f"    warehouse.map     -> {map_name}")
        print(f"    fleet.robot_count -> {parking}  (every parking cell; "
              "pick fewer per run with --robots or robots:=N)")

        if args.orders is None:
            args.orders = 2000

    if args.orders is not None:
        orders_path = resolve_orders_target(args.config)

        run(
            f"ORDERS ({args.orders}, seed {args.seed})",
            [
                str(ORDER_GENERATOR),
                "--config",
                str(args.config),
                "--orders",
                str(args.orders),
                "--seed",
                str(args.seed),
                "--output",
                str(orders_path),
            ],
        )

    config = load_sim_config(args.config)
    warehouse = load_warehouse_xlsx(config.map_path)

    world_path = WORLDS_DIR / f"{config.gazebo_world_name}.world"
    nav_graph_path = NAV_GRAPH_DIR / f"{config.gazebo_world_name}_nav_graph.yaml"

    stations = home_bank(warehouse)

    if config.robot_count > len(stations):
        raise SystemExit(
            f"fleet.robot_count is {config.robot_count} but "
            f"{config.map_path.name} has only {len(stations)} parking "
            f"cell(s). Add P cells to the map or lower robot_count."
        )

    print()
    print("=" * 72)
    print("SCENARIO")
    print("=" * 72)
    print(f"  config       : {args.config}")
    print(f"  map          : {config.map_path}")
    print(
        f"  grid         : {warehouse.grid.width} x "
        f"{warehouse.grid.height} @ {config.resolution_m} m"
    )
    print(f"  robots       : up to {len(stations)} (one per parking cell)")
    print(f"  orders       : {config.orders_path}")
    print(f"  world        : {world_path}")
    print(f"  nav graph    : {nav_graph_path}")

    run(
        "GAZEBO WORLD",
        [
            str(WORLD_GENERATOR),
            "--config",
            str(args.config),
            "--output",
            str(world_path),
        ],
    )

    run(
        "RMF NAVIGATION GRAPH",
        [
            str(NAV_GENERATOR),
            "--config",
            str(args.config),
            "--output",
            str(nav_graph_path),
        ],
    )

    if not args.skip_verify:
        run(
            "NAV GRAPH MIRRORS THE LANE POLICY",
            [
                str(NAV_VERIFIER),
                "--config",
                str(args.config),
                "--graph",
                str(nav_graph_path),
            ],
        )

    print()
    print("=" * 72)
    print("READY")
    print("=" * 72)
    print(f"  map    : {config.map_path.name}")
    print(f"  world  : {world_path}  (warehouse only; robots are added when Gazebo starts)")
    print(f"  robots : up to {len(stations)}")
    print()
    print("  Run it (plain terminal, ROS sourced):")
    print("    ros2 launch simulation_env warehouse_sim.launch.py orders:=true robots:=20")
    print()

    return 0


def resolve_orders_target(config_path: Path) -> Path:
    """Where the config's orders.file points, without requiring it to exist."""

    import yaml

    raw = yaml.safe_load(config_path.read_text()) or {}
    name = (raw.get("orders") or {}).get("file", "orders.csv")

    target = Path(name)

    return target if target.is_absolute() else config_path.parent / target


if __name__ == "__main__":
    raise SystemExit(main())
