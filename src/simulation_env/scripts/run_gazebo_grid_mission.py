#!/usr/bin/env python3

"""Deprecated shim.

The mission executor is a ROS 2 node now:

    ros2 run simulation_env mission_executor
    ros2 launch simulation_env grid_mission.launch.py

This forwards to it so older invocations keep working. Pass node
settings as ROS parameters, e.g.

    ros2 run simulation_env mission_executor \\
        --ros-args -p speed_mps:=1.5 -p use_gazebo:=false
"""

from simulation_env.mission_executor import main

if __name__ == "__main__":
    raise SystemExit(main())
