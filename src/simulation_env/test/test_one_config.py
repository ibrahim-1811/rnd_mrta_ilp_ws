"""One config, one map, one world.

The research setup is a single config (config/warehouse.yaml) naming a
single map, with a single Gazebo world built from that map. The world
holds the warehouse only; the robots a run uses are created in Gazebo
when the run starts. These tests pin the pieces that make that work
without a running Gazebo:

  * the one config loads, and its map, orders and fleet agree
  * switching maps rewrites only the map and fleet lines of the config
  * the robot create/remove plan makes Gazebo show exactly the fleet
  * the create request is well-formed for the gz command line
"""

from pathlib import Path

import pytest

from mrta_core.map_check import check_map
from mrta_core.orders import load_orders_csv
from mrta_core.sim_config import load_sim_config, rewrite_scenario
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.fleet import home_bank
from simulation_env.gazebo_bridge import (
    FleetPose,
    create_robots_request,
    fleet_changes,
    robot_model_sdf,
)


PACKAGE = Path(__file__).resolve().parents[1]
ONE_CONFIG = PACKAGE / "config" / "warehouse.yaml"


# ----------------------------------------------------------------------
# The one config
# ----------------------------------------------------------------------


def test_the_config_folder_holds_one_config():
    configs = sorted(p.name for p in (PACKAGE / "config").glob("*.yaml"))

    assert configs == ["warehouse.yaml"]


@pytest.fixture(scope="module")
def config():
    return load_sim_config(ONE_CONFIG)


@pytest.fixture(scope="module")
def warehouse(config):
    return load_warehouse_xlsx(config.map_path)


def test_the_config_names_a_map_in_the_maps_folder(config):
    """Whatever map is current, it lives with the others in maps/."""

    assert config.map_path.parent == PACKAGE / "maps"
    assert config.map_path.is_file()


def test_the_one_config_names_a_usable_map(warehouse):
    report = check_map(warehouse)

    assert report.ok, [(p.kind, p.cells[:2]) for p in report.errors]


def test_the_fleet_fits_the_map(config, warehouse):
    assert 0 < config.robot_count <= len(home_bank(warehouse))


def test_the_orders_belong_to_the_map(config, warehouse):
    """Every order names a shelf and a drop-off the current map has."""

    orders = load_orders_csv(config.orders_path, warehouse)

    assert len(orders) >= 1


def test_the_world_is_named_once(config):
    assert config.gazebo_world_name == "warehouse"


# ----------------------------------------------------------------------
# Switching maps rewrites the config, and nothing else in it
# ----------------------------------------------------------------------


def test_rewriting_changes_only_the_map_and_fleet_lines():
    """Whatever the config currently says, only those two lines move."""

    text = ONE_CONFIG.read_text()
    config = load_sim_config(ONE_CONFIG)

    rewritten = rewrite_scenario(text, map_name="NEW_MAP.xlsx", robot_count=42)

    before = text.splitlines()
    after = rewritten.splitlines()

    assert len(before) == len(after)

    changed = [(a, b) for a, b in zip(before, after) if a != b]

    assert changed == [
        (f"  map: {config.map_path.name}", "  map: NEW_MAP.xlsx"),
        (f"  robot_count: {config.robot_count}", "  robot_count: 42"),
    ]


def test_a_rewritten_config_still_loads(tmp_path):
    """The new config resolves its map like any other."""

    (tmp_path / "config").mkdir()
    (tmp_path / "maps").mkdir()

    current = load_sim_config(ONE_CONFIG).map_path

    text = rewrite_scenario(
        ONE_CONFIG.read_text(), map_name=current.name, robot_count=7
    )

    (tmp_path / "config" / "warehouse.yaml").write_text(text)
    (tmp_path / "config" / "orders.csv").write_text(
        (PACKAGE / "config" / "orders.csv").read_text()
    )
    (tmp_path / "maps" / current.name).write_bytes(current.read_bytes())

    config = load_sim_config(tmp_path / "config" / "warehouse.yaml")

    assert config.robot_count == 7
    assert config.map_path == tmp_path / "maps" / current.name


def test_rewriting_refuses_a_config_without_the_lines():
    with pytest.raises(ValueError, match="warehouse.map"):
        rewrite_scenario("fleet:\n  robot_count: 3\n", map_name="x", robot_count=1)


# ----------------------------------------------------------------------
# Gazebo shows exactly the fleet of this run
# ----------------------------------------------------------------------


def poses(*names):
    return [FleetPose(name, 1.0, 2.0, 0.155) for name in names]


def test_an_empty_world_gets_every_robot():
    create, remove = fleet_changes(set(), poses("robot_001", "robot_002"))

    assert [p.name for p in create] == ["robot_001", "robot_002"]
    assert remove == []


def test_a_larger_earlier_run_is_trimmed():
    """30 robots left in the world, a 10-robot run starts: remove 20."""

    existing = {f"robot_{n:03d}" for n in range(1, 31)}
    wanted = poses(*(f"robot_{n:03d}" for n in range(1, 11)))

    create, remove = fleet_changes(existing, wanted)

    assert create == []
    assert remove == [f"robot_{n:03d}" for n in range(11, 31)]


def test_a_smaller_earlier_run_is_topped_up():
    existing = {"robot_001", "robot_002"}
    wanted = poses("robot_001", "robot_002", "robot_003")

    create, remove = fleet_changes(existing, wanted)

    assert [p.name for p in create] == ["robot_003"]
    assert remove == []


def test_the_robot_model_is_a_drawn_cylinder():
    sdf = robot_model_sdf("robot_007")

    assert '<model name="robot_007">' in sdf
    assert "<static>true</static>" in sdf
    assert "<cylinder>" in sdf
    # Visual only: a robot has no collision, so Gazebo never pushes it.
    assert "<collision" not in sdf


def test_the_create_request_escapes_the_sdf():
    request = create_robots_request([FleetPose("robot_001", 3.0, 5.0, 0.155)])

    assert request.startswith("data: [{sdf: \"")
    assert 'name: "robot_001"' in request
    assert "position: {x: 3.0, y: 5.0, z: 0.155}" in request
    # Every quote inside the SDF is escaped for the protobuf text format.
    inner = request[len('data: [{sdf: "') : request.index('", name:')]
    assert '"' not in inner.replace('\\"', "")


def test_an_unsafe_name_is_refused():
    with pytest.raises(ValueError, match="unsafe"):
        create_robots_request([FleetPose('robot"; rm', 0.0, 0.0, 0.0)])
