"""Scenario selection for Gazebo and the bundle step (any map, one registry).

A launch given scenario:=<id> reads only the ``config`` path of that id in
the dashboard registry; the map, orders, world and fleet follow from that
config. These tests pin the resolver, the generated config, and that the
registered scenario's bundle is the one its map produces. The reference
config (W100) is checked to be what the launch uses by default.
"""

import subprocess
import sys
from pathlib import Path

import importlib.util

import pytest
import yaml

from mrta_core.sim_config import load_sim_config
from simulation_env.gazebo_bridge import ROBOT_NAME_RE
from simulation_env.gazebo_world import (
    model_names,
    world_file_for,
    world_name_of,
    write_fleet_world,
)
from simulation_env.scenario_registry import (
    config_for_scenario,
    find_registry,
    resolve_config,
)

PACKAGE = Path(__file__).resolve().parents[1]
SCRIPTS = PACKAGE / "scripts"
REFERENCE = PACKAGE / "config" / "warehouse.yaml"
REGISTRY = find_registry(PACKAGE)


def test_reference_scenario_resolves_to_the_reference_config():
    assert config_for_scenario("W100", REGISTRY) == REFERENCE.resolve()


def test_launch_defaults_to_the_reference_config():
    assert resolve_config("", "", PACKAGE, str(REFERENCE)) == str(REFERENCE)


def test_scenario_id_selects_that_scenarios_config():
    chosen = Path(resolve_config("", "my_map", PACKAGE, str(REFERENCE)))

    assert chosen == (PACKAGE / "config" / "my_map.yaml").resolve()
    assert load_sim_config(chosen).map_path.name == "my_map.xlsx"


@pytest.mark.parametrize("bad", ["../warehouse", "a/b", "nope", "W100 "])
def test_unknown_or_path_like_ids_are_refused(bad):
    with pytest.raises(ValueError):
        config_for_scenario(bad, REGISTRY)


def test_config_file_and_scenario_together_are_refused():
    with pytest.raises(ValueError):
        resolve_config(str(REFERENCE), "my_map", PACKAGE, str(REFERENCE))


def test_every_registered_scenario_has_its_own_world_and_orders():
    """A third map needs only a registry entry plus its generated bundle."""

    import yaml

    entries = yaml.safe_load(REGISTRY.read_text())["scenarios"]
    worlds = set()

    for entry in entries:
        config = config_for_scenario(entry["id"], REGISTRY)
        sim = load_sim_config(config)
        world = Path(world_file_for(config, "unused-share"))

        assert world.is_file(), entry["id"]
        assert world_name_of(config) == sim.gazebo_world_name
        assert sim.orders_path.is_file()
        worlds.add(world)

    assert len(worlds) == len(entries)


def test_bundle_config_differs_from_the_reference_in_four_lines_only():
    sys.path.insert(0, str(SCRIPTS))
    from build_scenario_bundle import bundle_config_text

    reference = REFERENCE.read_text()
    text = bundle_config_text(reference, "demo", "demo.xlsx", 7)
    new = text.splitlines()[2:]  # drop the two-line provenance note
    old = reference.splitlines()
    changed = [(a.strip(), b.strip()) for a, b in zip(old, new) if a != b]

    assert len(new) == len(old)
    assert changed == [
        ("map: W100.xlsx", "map: demo.xlsx"),
        ("robot_count: 100", "robot_count: 7"),
        ("file: orders.csv", "file: orders_demo.csv"),
        ("world_name: warehouse", "world_name: demo"),
    ]


def test_registered_world_is_what_the_generator_builds(tmp_path):
    """my_map.world is the generator's output for my_map.yaml, not hand-made."""

    config = PACKAGE / "config" / "my_map.yaml"
    out = tmp_path / "my_map.world"
    subprocess.run(
        [sys.executable, str(SCRIPTS / "generate_gazebo_grid_world.py"),
         "--config", str(config), "--output", str(out)],
        check=True, capture_output=True,
    )

    assert out.read_bytes() == (PACKAGE / "worlds" / "my_map.world").read_bytes()


@pytest.mark.parametrize("scenario_id", ["W100", "my_map"])
def test_fleet_world_parks_100_robots_in_that_scenarios_world(scenario_id, tmp_path):
    config = config_for_scenario(scenario_id, REGISTRY)
    world = world_file_for(config, "unused-share")
    path = write_fleet_world(config, 100, world, out_dir=str(tmp_path))
    text = Path(path).read_text()
    robots = [n for n in model_names(text) if ROBOT_NAME_RE.match(n)]

    assert len(robots) == 100
    assert f'<world name="{load_sim_config(config).gazebo_world_name}">' in text


def test_sync_lists_every_new_map_once_and_skips_a_broken_one(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "build_scenario_bundle", PACKAGE / "scripts" / "build_scenario_bundle.py"
    )
    bundle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bundle)

    maps = tmp_path / "maps"
    maps.mkdir()
    for name in ("new_map.xlsx", "broken.xlsx", "~$open.xlsx", "bad name.xlsx"):
        (maps / name).touch()
    registry = tmp_path / "scenarios.yaml"
    registry.write_text("version: 1\nscenarios:\n")

    built = []

    def build(scenario_id, path):
        built.append(scenario_id)
        return 1 if scenario_id == "broken" else 0

    assert bundle.sync_maps(maps, registry, build) == (["new_map"], ["bad name.xlsx", "broken.xlsx"])
    assert built == ["broken", "new_map"]
    assert [e["id"] for e in yaml.safe_load(registry.read_text())["scenarios"]] == ["new_map"]
