"""Backwards-compatible alias for the full warehouse simulation.

The mission is order-driven now, so this simply includes
``warehouse_sim.launch.py``. Prefer launching that directly.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource


def generate_launch_description():

    share = get_package_share_directory("simulation_env")

    return LaunchDescription([
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(share, "launch", "warehouse_sim.launch.py")
            )
        ),
    ])
