#!/usr/bin/env python3
"""Exec the fixed ROS 2 warehouse launch after sourcing fixed setup files."""

import argparse
import os
from pathlib import Path
import subprocess


def sourced_environment(ros_setup, workspace_setup):
    ros_setup = Path(ros_setup).absolute()
    workspace_setup = Path(workspace_setup).absolute()
    if not ros_setup.is_file() or not workspace_setup.is_file():
        raise FileNotFoundError("ROS setup files are unavailable")
    script = 'source "$1"; source "$2"; env -0'
    done = subprocess.run(
        [
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            script,
            "dashboard-gazebo-environment",
            str(ros_setup),
            str(workspace_setup),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        env=dict(os.environ),
    )
    result = {}
    for item in done.stdout.split(b"\0"):
        if b"=" in item:
            key, value = item.split(b"=", 1)
            result[key.decode()] = value.decode()
    return result


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--ros-setup", required=True)
    value.add_argument("--workspace-setup", required=True)
    value.add_argument("--config", required=True)
    value.add_argument("--dataset", required=True)
    value.add_argument("--robots", type=int, required=True)
    value.add_argument("--orders", type=int, required=True)
    value.add_argument("--strategy", required=True)
    value.add_argument("--quantum", type=float)
    value.add_argument("--window")
    value.add_argument("--speed", type=float)
    value.add_argument("--progress", type=float, default=10)
    value.add_argument("--headless", action="store_true")
    value.add_argument("--verbose", action="store_true")
    return value


def ros_argv(args):
    command = [
        "ros2",
        "launch",
        "simulation_env",
        "warehouse_sim.launch.py",
        f"config_file:={args.config}",
        "orders:=true",
        "start_gazebo:=true",
        f"robots:={args.robots}",
        f"order_limit:={args.orders}",
        f"strategy:={args.strategy}",
        f"dataset:={args.dataset}",
        f"headless:={str(args.headless).lower()}",
        f"verbose:={str(args.verbose).lower()}",
        f"progress:={float(args.progress)}",
    ]
    if args.quantum is not None:
        command.append(f"quantum:={float(args.quantum)}")
    if args.window is not None:
        command.append(f"window:={args.window}")
    if args.speed is not None:
        command.append(f"speed:={float(args.speed)}")
    return command


def main():
    args = parser().parse_args()
    environment = sourced_environment(args.ros_setup, args.workspace_setup)
    if not args.headless and environment.get("XDG_SESSION_TYPE") == "wayland":
        environment.setdefault("QT_QPA_PLATFORM", "xcb")
    command = ros_argv(args)
    os.execvpe(command[0], command, environment)


if __name__ == "__main__":
    main()
