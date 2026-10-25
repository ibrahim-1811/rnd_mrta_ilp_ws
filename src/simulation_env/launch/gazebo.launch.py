"""Gazebo with the warehouse and the fleet parked on its start cells.

Terminal 1 of a demo: start this once and keep it open, then start runs
in terminal 2 with ``warehouse_sim.launch.py start_gazebo:=false``. The
robots are visible straight away, standing on their start cells; a run
makes them move.

    ros2 launch simulation_env gazebo.launch.py robots:=100

Starting it again first stops the Gazebo already running this world, so
there is only ever one. ``robots:=0`` (the default) takes the fleet size
from the config. ``headless:=true`` runs the server with no window.

The world is the one world file for the config, plus the robots, written
to a temporary copy on every start (simulation_env/gazebo_world.py).
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction

from simulation_env.gazebo_world import stop_gazebo, world_file_for, write_fleet_world


def _source_tree(share):
    """src/simulation_env in this workspace, if the package was built here."""

    root = os.path.abspath(os.path.join(share, "..", "..", "..", ".."))
    source = os.path.join(root, "src", "simulation_env")

    return source if os.path.isdir(source) else None


def start_gazebo(context, *args, **kwargs):
    """Stop any Gazebo of this world, write the fleet world, start Gazebo."""

    share = get_package_share_directory("simulation_env")
    config = context.launch_configurations["config_file"]
    world = context.launch_configurations.get("world_file") or world_file_for(
        config, share
    )
    robots = int(context.launch_configurations.get("robots") or 0)
    headless = context.launch_configurations.get("headless", "false").lower() == "true"

    stopped = stop_gazebo(os.path.basename(world))

    if stopped:
        print(f"[gazebo] stopped the Gazebo already running this world: pid {stopped}")

    fleet_world = write_fleet_world(config, robots, world)

    print(f"[gazebo] world with the fleet parked: {fleet_world}")

    cmd = ["gz", "sim", "-s", "-r", fleet_world] if headless else ["gz", "sim", "-r", fleet_world]

    return [ExecuteProcess(cmd=cmd, output="screen")]


def generate_launch_description():

    share = get_package_share_directory("simulation_env")
    source = _source_tree(share)

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "config_file",
                default_value=os.path.join(
                    source if source else share, "config", "warehouse.yaml"
                ),
            ),
            # Empty means "derive it from gazebo.world_name in the config".
            DeclareLaunchArgument("world_file", default_value=""),
            # Robots to park; 0 keeps fleet.robot_count from the config.
            DeclareLaunchArgument("robots", default_value="0"),
            DeclareLaunchArgument("headless", default_value="false"),
            OpaqueFunction(function=start_gazebo),
        ]
    )
