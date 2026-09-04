"""Warehouse workload simulation: fleet execution and Gazebo.

One config file drives the system: map, fleet size, order file, speed
and Gazebo world name all come from it. The fleet node runs the
selected MRTA workload; Gazebo remains visualization only.

The world file is derived from ``gazebo.world_name`` in the config, so
pointing ``config_file`` at a different scenario automatically picks up
that scenario's world instead of silently reusing the default one --
which is how a ten-robot config used to end up rendered in an
eighteen-robot world.

Regenerate a scenario's artefacts with::

    python3 src/simulation_env/scripts/build_scenario.py --config <config>
"""

import os

import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    OpaqueFunction,
)
from launch.conditions import IfCondition
from launch.substitutions import (
    AndSubstitution,
    LaunchConfiguration,
    NotSubstitution,
)
from launch_ros.actions import Node



def resolve_world_file(context, *args, **kwargs):
    """Fill in world_file from the config unless it was given.

    Keeping this in one place stops the world and the config drifting:
    a scenario built by build_scenario.py writes
    ``worlds/<gazebo.world_name>.world``, and this reads the same name
    back out.
    """

    if context.launch_configurations.get("world_file"):
        return []

    share = get_package_share_directory("simulation_env")

    config_path = context.launch_configurations["config_file"]

    with open(config_path) as handle:
        document = yaml.safe_load(handle) or {}

    world_name = (
        document.get("gazebo", {}).get("world_name")
        or "warehouse_grid"
    )

    context.launch_configurations["world_file"] = os.path.join(
        share, "worlds", f"{world_name}.world"
    )

    return []


def generate_launch_description():

    share = get_package_share_directory("simulation_env")

    default_config = os.path.join(
        share, "config", "warehouse_sim.yaml"
    )

    config_file = LaunchConfiguration("config_file")
    world_file = LaunchConfiguration("world_file")
    start_gazebo = LaunchConfiguration("start_gazebo")
    headless = LaunchConfiguration("headless")

    return LaunchDescription([

        DeclareLaunchArgument("config_file", default_value=default_config),
        # Empty means "derive it from gazebo.world_name in the config".
        DeclareLaunchArgument("world_file", default_value=""),
        OpaqueFunction(function=resolve_world_file),
        DeclareLaunchArgument("start_gazebo", default_value="true"),
        DeclareLaunchArgument("headless", default_value="false"),
        DeclareLaunchArgument("orders", default_value="false"),
        # Allocation strategy for the order workload. Naming one
        # implies orders:=true.
        DeclareLaunchArgument(
            "strategy", default_value="nearest_astar"
        ),
        # 0.0 keeps fleet.speed_mps from the YAML; any positive
        # value overrides it for this run only.
        DeclareLaunchArgument("speed", default_value="0.0"),


        # GUI server.
        ExecuteProcess(
            cmd=["gz", "sim", "-r", world_file],
            output="screen",
            condition=IfCondition(
                AndSubstitution(start_gazebo, NotSubstitution(headless))
            ),
        ),

        # Headless server, for CI or a machine with no display.
        ExecuteProcess(
            cmd=["gz", "sim", "-s", "-r", world_file],
            output="screen",
            condition=IfCondition(
                AndSubstitution(start_gazebo, headless)
            ),
        ),

        # Fleet deployment and live state publishing.
        Node(
            package="simulation_env",
            executable="fleet_node",
            name="mrta_fleet",
            output="screen",
            emulate_tty=True,
            parameters=[{
                # Only the config path: every simulation constant
                # lives in the YAML, never duplicated here.
                "config_file": config_file,
                "wait_for_gazebo": 60.0,
                "load_orders": LaunchConfiguration("orders"),
                "run_workload": LaunchConfiguration("orders"),
                "strategy": LaunchConfiguration("strategy"),
                "speed_mps": LaunchConfiguration("speed"),
            }],
        ),

    ])
