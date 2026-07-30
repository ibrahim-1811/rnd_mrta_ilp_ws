"""Full headless warehouse stack (no Gazebo, no Nav2).

    ros2 launch task_management headless_warehouse.launch.py \\
        num_robots:=6 strategy:=auction time_scale:=20.0 out_dir:=/tmp/mrta_run

Brings up: grid mock_fleet_manager, shared_memory_node, task_manager,
ilp_dataset_logger, metrics_logger. Drive it with the order_generator:

    ros2 run task_management order_generator --ros-args -p num_orders:=20 -p seed:=42
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    num_robots = LaunchConfiguration('num_robots')
    strategy = LaunchConfiguration('strategy')
    seed = LaunchConfiguration('seed')
    world_size = LaunchConfiguration('world_size_m')
    resolution = LaunchConfiguration('resolution_m')
    robot_speed = LaunchConfiguration('robot_speed_ms')
    time_scale = LaunchConfiguration('time_scale')
    out_dir = LaunchConfiguration('out_dir')
    shelves_csv = LaunchConfiguration('shelves_csv')

    common = [
        {'world_size_m': world_size},
        {'resolution_m': resolution},
        {'robot_speed_ms': robot_speed},
    ]

    return LaunchDescription([
        DeclareLaunchArgument('num_robots', default_value='6'),
        DeclareLaunchArgument('strategy', default_value='auction'),
        DeclareLaunchArgument('seed', default_value='0'),
        DeclareLaunchArgument('world_size_m', default_value='30.0'),
        DeclareLaunchArgument('resolution_m', default_value='1.0'),
        DeclareLaunchArgument('robot_speed_ms', default_value='2.0'),
        DeclareLaunchArgument('time_scale', default_value='20.0'),
        DeclareLaunchArgument('out_dir', default_value='/tmp/mrta_run'),
        DeclareLaunchArgument(
            'shelves_csv',
            default_value=PathJoinSubstitution([
                FindPackageShare('shared_memory_node'),
                'databases', 'headless_shelves_100.csv',
            ])),

        Node(
            package='simulation_env', executable='mock_fleet_manager',
            name='fleet_manager', output='screen',
            parameters=common + [
                {'num_robots': num_robots},
                {'time_scale': time_scale},
            ]),

        Node(
            package='shared_memory_node', executable='shared_memory_node',
            name='shared_memory_node', output='screen',
            parameters=[{'database_file': shelves_csv}]),

        Node(
            package='task_management', executable='task_manager_node',
            name='task_manager_node', output='screen',
            parameters=common + [
                {'allocation_strategy': strategy},
                {'allocator_seed': seed},
            ]),

        Node(
            package='task_management', executable='ilp_dataset_logger',
            name='ilp_dataset_logger', output='screen',
            parameters=[{'out_dir': PathJoinSubstitution([out_dir, 'ilp_logs'])}]),

        Node(
            package='task_management', executable='metrics_logger',
            name='metrics_logger', output='screen',
            parameters=[{'out_dir': out_dir}]),
    ])
