"""Complete warehouse simulation: shared memory, Gazebo, mission executor.

One config file drives every node:

    src/simulation_env/config/warehouse_sim.yaml

shared_memory_node owns order state. The executor asks it for orders and
reports transitions back. Gazebo is visualization only.
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
        DeclareLaunchArgument("run_mission", default_value="false"),
        DeclareLaunchArgument("demo", default_value="false"),

        # Authoritative dynamic state: orders, shelves, fleet cache.
        Node(
            package="shared_memory_node",
            executable="shared_memory_node",
            name="shared_memory_node",
            output="screen",
            emulate_tty=True,
            parameters=[{"config_file": config_file}],
        ),

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
                "run_demo_missions": LaunchConfiguration("demo"),
            }],
        ),

        # Order execution stays opt-in: run_mission:=true
        Node(
            package="simulation_env",
            executable="mission_executor",
            name="mrta_mission_executor",
            output="screen",
            emulate_tty=True,
            condition=IfCondition(LaunchConfiguration("run_mission")),
            parameters=[{
                "config_file": config_file,
                "wait_for_gazebo": 60.0,
                "wait_for_shared_memory": 60.0,
            }],
        ),
    ])
