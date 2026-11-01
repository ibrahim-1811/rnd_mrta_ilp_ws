#!/usr/bin/env python3

"""Write one scenario's bundle: config, orders, Gazebo world, nav graph.

The reference config (config/warehouse.yaml) stays the one config for
W100 and is never touched here. Any further map is a *scenario*: this
step takes the map and writes everything that scenario needs, named by
its id, using the same tools as build_scenario.py:

    config/<id>.yaml                       the reference config text with this
                                           map, fleet size, order file and
                                           gazebo.world_name = <id>
    config/orders_<id>.csv                 orders generated from the map
    worlds/<id>.world                      Gazebo world (warehouse only)
    ../rmf_grid_traffic/config/<id>_nav_graph.yaml

    build_scenario_bundle.py --id my_map --map src/simulation_env/maps/my_map.xlsx
    build_scenario_bundle.py --sync

--sync gives every Excel file in maps/ a bundle and a registry entry, so a
file dropped there appears in the dashboard and in scenario:=<id>. A map that
cannot be built is reported and skipped.

A map outside maps/ is copied there as <id>.xlsx. Re-running rebuilds
the bundle from the map, so it is repeatable. The scenario is then
selected by adding an entry to the dashboard registry
(src/dashboard_GUI/config/scenarios.yaml) whose ``config`` points at
config/<id>.yaml; Gazebo takes it as ``scenario:=<id>``.

Only files for this id are written. Routing, traffic and battery
settings are copied verbatim from the reference config.
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from mrta_core.sim_config import load_sim_config, rewrite_scenario
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.fleet import home_bank


SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")

SRC = Path(__file__).resolve().parents[2]

REFERENCE_CONFIG = SRC / "simulation_env" / "config" / "warehouse.yaml"

BUILD_SCENARIO = Path(__file__).with_name("build_scenario.py")

REGISTRY = SRC / "dashboard_GUI" / "config" / "scenarios.yaml"


def bundle_config_text(reference_text: str, scenario_id: str, map_name: str, robots: int) -> str:
    """The reference config text switched to this scenario's files."""

    text = rewrite_scenario(reference_text, map_name=map_name, robot_count=robots)

    for pattern, replacement, name in (
        (r"^(  file:)[ \t]*\S.*$", rf"\g<1> orders_{scenario_id}.csv", "orders.file"),
        (r"^(  world_name:)[ \t]*\S.*$", rf"\g<1> {scenario_id}", "gazebo.world_name"),
    ):
        text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)

        if count != 1:
            raise ValueError(f"reference config has no {name} line to rewrite")

    note = (
        f"# Scenario config for '{scenario_id}', written by build_scenario_bundle.py\n"
        "# from config/warehouse.yaml. Do not edit by hand; rebuild instead.\n"
    )

    return note + text


def registered_map_stems(registry: Path) -> set[str]:
    """The map file stems the registry already serves."""

    stems = set()

    for entry in yaml.safe_load(registry.read_text()).get("scenarios") or []:
        config = (registry.parent / str(entry.get("config", ""))).resolve()

        if config.is_file():
            stems.add(load_sim_config(config).map_path.stem)

    return stems


def registry_entry(scenario_id: str) -> str:
    return (
        f"  - id: {scenario_id}\n"
        f"    label: {scenario_id}\n"
        f"    config: ../../simulation_env/config/{scenario_id}.yaml\n"
        "    reference: false\n"
        "    provenance: maps-folder\n"
    )


def sync_maps(maps_dir: Path, registry: Path, build) -> tuple[list[str], list[str]]:
    """Build and register every map without an entry; returns (added, failed)."""

    known = registered_map_stems(registry)
    added, failed = [], []

    for path in sorted(maps_dir.glob("*.xlsx")):
        if path.stem in known or path.name.startswith("~$"):
            continue

        if SAFE_ID.fullmatch(path.stem) is None or build(path.stem, path) != 0:
            failed.append(path.name)
            continue

        with registry.open("a") as stream:
            stream.write(registry_entry(path.stem))

        added.append(path.stem)

    return added, failed


def build_bundle(scenario_id: str, map_path: Path, orders: int = 2000, seed: int = 42) -> int:
    maps_dir = SRC / "simulation_env" / "maps"
    map_name = f"{scenario_id}.xlsx" if map_path.resolve().parent != maps_dir.resolve() else map_path.name
    target = maps_dir / map_name

    if map_path.resolve() != target.resolve():
        shutil.copyfile(map_path, target)

    robots = len(home_bank(load_warehouse_xlsx(target)))
    config = REFERENCE_CONFIG.with_name(f"{scenario_id}.yaml")
    config.write_text(
        bundle_config_text(REFERENCE_CONFIG.read_text(), scenario_id, map_name, robots)
    )

    print(f"[bundle] config: {config}  ({robots} parking cells)")

    # Same tool, same steps: check map, orders, world, nav graph, verify.
    # build_scenario.py resolves its relative output folders from the repo root.
    result = subprocess.run(
        [
            sys.executable,
            str(BUILD_SCENARIO),
            "--map", str(target),
            "--config", str(config),
            "--orders", str(orders),
            "--seed", str(seed),
        ],
        cwd=SRC.parent,
    )

    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--id", help="scenario id (letters, digits, _ -)")
    parser.add_argument("--map", type=Path, help="Excel map")
    parser.add_argument("--orders", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sync", action="store_true", help="bundle every new map in maps/")
    args = parser.parse_args()

    if args.sync:
        added, failed = sync_maps(
            SRC / "simulation_env" / "maps",
            REGISTRY,
            lambda scenario_id, path: build_bundle(scenario_id, path, args.orders, args.seed),
        )
        print(f"[maps] added: {added or 'none'}; could not build: {failed or 'none'}")
        return 0

    if args.id is None or args.map is None:
        raise SystemExit("give --id and --map, or --sync")

    if SAFE_ID.fullmatch(args.id) is None:
        raise SystemExit(f"--id {args.id!r}: use letters, digits, '_' or '-'")

    if args.id == "warehouse":
        raise SystemExit("--id warehouse is the reference config's own name")

    if not args.map.is_file():
        raise SystemExit(f"--map {args.map}: no such file")

    return build_bundle(args.id, args.map, args.orders, args.seed)


if __name__ == "__main__":
    raise SystemExit(main())
