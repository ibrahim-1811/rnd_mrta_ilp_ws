"""Warehouse workload simulation: fleet execution and Gazebo.

One config file drives the system: map, fleet size, order file, speed
and Gazebo world name all come from it. The fleet node runs the
selected MRTA workload; Gazebo remains visualization only.

The world file is derived from ``gazebo.world_name`` in the config, so
pointing ``config_file`` at a different scenario automatically picks up
that scenario's world instead of silently reusing the default one --
which is how a ten-robot config used to end up rendered in an
eighteen-robot world.

One config, one map, one world. Build them for a map with::

    python3 src/simulation_env/scripts/build_scenario.py --map <map.xlsx>

The world file holds the warehouse only. Gazebo is started by
gazebo.launch.py (included here unless start_gazebo:=false), which
stops any Gazebo already running this world and loads the world with
the run's robots (robots:=N) parked on their start cells, so they are
visible at once. The fleet node finds them there and makes them move.
The config and the world are read from the source tree when the
workspace has one, so a rebuilt map is used without a colcon rebuild.

Two terminals, Gazebo kept open between runs:

    ros2 launch simulation_env gazebo.launch.py robots:=100
    ros2 launch simulation_env warehouse_sim.launch.py \
        start_gazebo:=false orders:=true robots:=100
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _source_tree(share):
    """src/simulation_env in this workspace, if the package was built here.

    install/simulation_env/share/simulation_env is four levels below the
    workspace root. Reading the config and the world from the source
    tree means build_scenario.py's output is used straight away, with no
    colcon rebuild in between.
    """

    root = os.path.abspath(os.path.join(share, "..", "..", "..", ".."))
    source = os.path.join(root, "src", "simulation_env")

    return source if os.path.isdir(source) else None


def generate_launch_description():

    share = get_package_share_directory("simulation_env")

    # The one config: the source tree's, so edits apply without a
    # rebuild; the installed copy only if there is no source tree.
    source = _source_tree(share)
    default_config = os.path.join(
        source if source else share, "config", "warehouse.yaml"
    )

    config_file = LaunchConfiguration("config_file")
    world_file = LaunchConfiguration("world_file")
    start_gazebo = LaunchConfiguration("start_gazebo")
    headless = LaunchConfiguration("headless")

    return LaunchDescription(
        [
            DeclareLaunchArgument("config_file", default_value=default_config),
            # Empty means "derive it from gazebo.world_name in the config".
            DeclareLaunchArgument("world_file", default_value=""),
            DeclareLaunchArgument("start_gazebo", default_value="true"),
            DeclareLaunchArgument("headless", default_value="false"),
            # The workload switch: it sets both load_orders and
            # run_workload below. Without orders:=true the node deploys
            # the fleet, publishes it and spins -- no order is loaded and
            # no robot moves.
            DeclareLaunchArgument("orders", default_value="false"),
            # Allocation strategy for the order workload. This does NOT
            # imply orders:=true; naming a strategy without it still runs
            # no workload.
            DeclareLaunchArgument("strategy", default_value="nearest_astar"),
            # 0.0 keeps fleet.speed_mps from the YAML; any positive
            # value overrides it for this run only.
            DeclareLaunchArgument("speed", default_value="0.0"),
            # Allocation candidate window: fleet_size, legacy, or a
            # positive integer. Empty keeps allocation.window from the
            # YAML, which is authoritative for it.
            DeclareLaunchArgument("window", default_value=""),
            # Dispatch quantum in simulated seconds. Negative keeps
            # allocation.dispatch_quantum_s from the config, so 0.0
            # stays a real override meaning continuous dispatch.
            DeclareLaunchArgument("quantum", default_value="-1.0"),
            # Run only the first N logical orders of the order file,
            # never splitting one; 0 (the default) runs them all. Only
            # used with orders:=true, which is what switches the
            # workload on. The order file itself is never rewritten.
            DeclareLaunchArgument("order_limit", default_value="0"),
            # Phase 8 dataset directory. Empty = do not record.
            DeclareLaunchArgument("dataset", default_value=""),
            # true prints the full trace to the terminal: every
            # assignment, robot event, battery event and per-robot
            # table. By default the terminal shows one status line per
            # `progress` simulated seconds and a short summary; the
            # detail is on /mrta/fleet_status, /mrta/robot_states and
            # /mrta/events.
            DeclareLaunchArgument("verbose", default_value="false"),
            DeclareLaunchArgument("progress", default_value="60.0"),
            # Fleet size for this run; 0 keeps fleet.robot_count from the
            # config. Gazebo is given exactly this many robots at start.
            DeclareLaunchArgument("robots", default_value="0"),
            # Gazebo with this run's robots parked on their start cells.
            # It stops a Gazebo already running this world first, and
            # finishes before the fleet node below starts.
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    os.path.join(share, "launch", "gazebo.launch.py")
                ),
                launch_arguments={
                    "config_file": config_file,
                    "world_file": world_file,
                    "robots": LaunchConfiguration("robots"),
                    "headless": headless,
                }.items(),
                condition=IfCondition(start_gazebo),
            ),
            # Fleet deployment and live state publishing.
            Node(
                package="simulation_env",
                executable="fleet_node",
                name="mrta_fleet",
                output="screen",
                emulate_tty=True,
                parameters=[
                    {
                        # Only the config path: every simulation constant
                        # lives in the YAML, never duplicated here.
                        "config_file": config_file,
                        "wait_for_gazebo": 60.0,
                        "load_orders": LaunchConfiguration("orders"),
                        "run_workload": LaunchConfiguration("orders"),
                        "strategy": LaunchConfiguration("strategy"),
                        "speed_mps": LaunchConfiguration("speed"),
                        "window": LaunchConfiguration("window"),
                        "dispatch_quantum_s": LaunchConfiguration("quantum"),
                        "dataset": LaunchConfiguration("dataset"),
                        "order_limit": LaunchConfiguration("order_limit"),
                        "verbose": LaunchConfiguration("verbose"),
                        "progress_interval_s": LaunchConfiguration("progress"),
                        "robots": LaunchConfiguration("robots"),
                    }
                ],
            ),
        ]
    )
