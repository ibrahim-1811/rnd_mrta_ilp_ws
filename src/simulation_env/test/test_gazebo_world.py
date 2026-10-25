"""Gazebo starts with the fleet parked, and only one Gazebo per world.

Robots created after Gazebo is up arrived late, and in a window still
loading the warehouse they could be missing altogether. Starting Gazebo
now writes a temporary copy of the one world file with every robot of
the run on its start cell, so the warehouse and the fleet load together
and the fleet node finds the robots already there.

Visualization only: nothing here touches the simulation.
"""

import os
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import pytest

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import HOME_TYPES
from simulation_env.fleet import build_fleet
from simulation_env.gazebo_bridge import DEFAULT_ROBOT_Z, FleetPose, robot_model_sdf
from simulation_env.gazebo_view import grid_to_gazebo
from simulation_env.gazebo_world import (
    fleet_poses,
    gazebo_pids,
    is_gazebo_for,
    model_names,
    partition_of,
    with_fleet,
    world_file_for,
    world_name_of,
    write_fleet_world,
)


PACKAGE = Path(__file__).resolve().parents[1]
ONE_CONFIG = PACKAGE / "config" / "warehouse.yaml"


@pytest.fixture(scope="module")
def world_file():
    return world_file_for(ONE_CONFIG, str(PACKAGE))


@pytest.fixture(scope="module")
def world_text(world_file):
    return Path(world_file).read_text()


def robots_in(text):
    root = ET.fromstring(text)

    return {
        model.get("name"): model
        for model in root.iter("model")
        if model.get("name").startswith("robot_")
    }


# ----------------------------------------------------------------------
# The fleet world.
# ----------------------------------------------------------------------


def test_the_world_file_is_the_one_world_of_the_config(world_file):
    assert os.path.basename(world_file) == f"{world_name_of(ONE_CONFIG)}.world"
    assert os.path.isfile(world_file)


def test_every_robot_is_parked_on_its_start_cell(world_text):
    """Exactly where the fleet node's first frame puts each robot."""

    config = replace(load_sim_config(ONE_CONFIG), robot_count=30)
    warehouse = load_warehouse_xlsx(config.map_path)
    grid = warehouse.grid

    robots = robots_in(with_fleet(world_text, fleet_poses(ONE_CONFIG, 30)))

    assert sorted(robots) == [f"robot_{n:03d}" for n in range(1, 31)]

    for robot in build_fleet(config, warehouse):
        assert warehouse.zone_type_at(robot.cell) in HOME_TYPES

        x, y = grid_to_gazebo(
            robot.cell, config.resolution_m, grid.origin_x, grid.origin_y
        )
        pose = [float(v) for v in robots[robot.robot_id].find("pose").text.split()]

        assert pose == [x, y, DEFAULT_ROBOT_Z, 0, 0, 0]


def test_robots_zero_takes_the_fleet_size_from_the_config():
    config = load_sim_config(ONE_CONFIG)

    assert len(fleet_poses(ONE_CONFIG, 0)) == config.robot_count
    assert len(fleet_poses(ONE_CONFIG, 100)) == 100


def test_the_rest_of_the_world_is_untouched(world_text):
    fleet = with_fleet(world_text, fleet_poses(ONE_CONFIG, 10))

    root = ET.fromstring(fleet)

    assert root.find("world").get("name") == world_name_of(ONE_CONFIG)

    names = model_names(fleet)
    warehouse = [name for name in names if not name.startswith("robot_")]

    assert warehouse == model_names(world_text)
    # The robots are added at the end; nothing before them moved.
    assert fleet.startswith(world_text[: world_text.rindex("</world>")])


def test_a_parked_robot_is_the_same_model_as_a_created_one():
    pose = FleetPose("robot_007", 1.0, 2.0, DEFAULT_ROBOT_Z)

    parked = robots_in(with_fleet('<sdf><world name="w"></world></sdf>', [pose]))
    created = ET.fromstring(robot_model_sdf("robot_007")).find("model")

    placed = parked["robot_007"]
    placed.remove(placed.find("pose"))
    # The line break after it in the world file is not part of it.
    placed.tail = None

    assert ET.tostring(placed) == ET.tostring(created)


def test_a_world_that_already_holds_robots_is_refused(world_text):
    fleet = with_fleet(world_text, fleet_poses(ONE_CONFIG, 2))

    with pytest.raises(ValueError, match="already holds robot models"):
        with_fleet(fleet, fleet_poses(ONE_CONFIG, 2))


def test_the_fleet_world_keeps_the_world_file_name(world_file, tmp_path):
    path = write_fleet_world(ONE_CONFIG, 5, world_file, out_dir=tmp_path)

    assert os.path.basename(path) == os.path.basename(world_file)
    assert len(robots_in(Path(path).read_text())) == 5

    # Rewritten on every start, never appended to.
    write_fleet_world(ONE_CONFIG, 3, world_file, out_dir=tmp_path)

    assert len(robots_in(Path(path).read_text())) == 3
    assert sorted(os.listdir(tmp_path)) == [os.path.basename(world_file)]


# ----------------------------------------------------------------------
# Only the Gazebo of this world, on this partition, is stopped.
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["gz", "sim", "-r", "/a/b/warehouse.world"], True),
        (["gz", "sim", "-s", "-r", "/tmp/mrta/warehouse.world"], True),
        # The ruby front end rewrites its title into one string.
        (["gz sim -r /a/b/warehouse.world"], True),
        (["ruby", "/usr/bin/gz", "sim", "-r", "warehouse.world"], True),
        (["gz", "sim", "-r", "/a/b/other.world"], False),
        (["gz", "sim", "-g"], False),
        (["gz", "topic", "-l"], False),
        # A shell that only mentions gz sim is not Gazebo.
        (["bash", "-c", "gz sim -r warehouse.world"], False),
        (["/usr/bin/python3", "fleet_node", "warehouse.world"], False),
    ],
)
def test_which_processes_are_this_worlds_gazebo(argv, expected):
    assert is_gazebo_for(argv, "warehouse.world") is expected


def test_the_partition_is_read_from_the_environment():
    assert partition_of(b"HOME=/h\0GZ_PARTITION=demo\0PATH=/bin\0") == "demo"
    assert partition_of(b"HOME=/h\0PATH=/bin\0") == ""


def test_only_this_partitions_gazebo_is_found(tmp_path):
    def process(pid, argv, environ=b""):
        entry = tmp_path / str(pid)
        entry.mkdir()
        (entry / "cmdline").write_bytes("\0".join(argv).encode() + b"\0")
        (entry / "environ").write_bytes(environ)

    process(101, ["gz", "sim", "-r", "/w/warehouse.world"])
    process(102, ["gz", "sim", "-r", "/w/warehouse.world"], b"GZ_PARTITION=other\0")
    process(103, ["gz", "sim", "-r", "/w/other.world"])
    process(104, ["bash", "-c", "gz sim -r /w/warehouse.world"])
    (tmp_path / "self").mkdir()

    assert gazebo_pids("warehouse.world", "", proc=tmp_path) == [101]
    assert gazebo_pids("warehouse.world", "other", proc=tmp_path) == [102]
