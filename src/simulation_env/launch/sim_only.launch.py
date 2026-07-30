"""Minimal sim bring-up: shared_memory_node + grid mock_fleet_manager.

Used to develop/verify the simulator before the task manager exists. The full
headless stack (task_manager, loggers) is added in headless_warehouse.launch.py.

    ros2 launch simulation_env sim_only.launch.py num_robots:=6 time_scale:=20.0
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    num_robots = LaunchConfiguration('num_robots')
    world_size = LaunchConfiguration('world_size_m')
    resolution = LaunchConfiguration('resolution_m')
    robot_speed = LaunchConfiguration('robot_speed_ms')
    time_scale = LaunchConfiguration('time_scale')
    shelves_csv = LaunchConfiguration('shelves_csv')

    return LaunchDescription([
        DeclareLaunchArgument('num_robots', default_value='4'),
        DeclareLaunchArgument('world_size_m', default_value='30.0'),
        DeclareLaunchArgument('resolution_m', default_value='1.0'),
        DeclareLaunchArgument('robot_speed_ms', default_value='2.0'),
        DeclareLaunchArgument('time_scale', default_value='1.0'),
        DeclareLaunchArgument(
            'shelves_csv',
            default_value=PathJoinSubstitution([
                FindPackageShare('shared_memory_node'),
                'databases',
                'headless_shelves_100.csv',
            ]),
        ),

        Node(
            package='simulation_env',
            executable='mock_fleet_manager',
            name='fleet_manager',
            output='screen',
            parameters=[{
                'num_robots': num_robots,
                'world_size_m': world_size,
                'resolution_m': resolution,
                'robot_speed_ms': robot_speed,
                'time_scale': time_scale,
            }],
        ),

        Node(
            package='shared_memory_node',
            executable='shared_memory_node',
            name='shared_memory_node',
            output='screen',
            parameters=[{'database_file': shelves_csv}],
        ),
    ])
