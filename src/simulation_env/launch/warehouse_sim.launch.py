"""Warehouse workload simulation: fleet execution and Gazebo.

One config file drives the system. The fleet node runs the selected MRTA
workload; Gazebo remains visualization only.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.conditions import IfCondition
from launch.substitutions import (
    AndSubstitution,
    LaunchConfiguration,
    NotSubstitution,
)
from launch_ros.actions import Node


def generate_launch_description():

    share = get_package_share_directory("simulation_env")

    default_config = os.path.join(
        share, "config", "warehouse_sim.yaml"
    )

    default_world = os.path.join(
        share, "worlds", "warehouse_grid.world"
    )

    config_file = LaunchConfiguration("config_file")
    world_file = LaunchConfiguration("world_file")
    start_gazebo = LaunchConfiguration("start_gazebo")
    headless = LaunchConfiguration("headless")

    return LaunchDescription([

        DeclareLaunchArgument("config_file", default_value=default_config),
        DeclareLaunchArgument("world_file", default_value=default_world),
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
