"""Gazebo transport helpers.

Gazebo is visualization only and is not a ROS system here, so poses go
out over gz-transport. Shared by the fleet node and the mission
executor so there is one implementation of the retry and
multiple-server checks.
"""

import re
import subprocess


# Matches the robot pose the world generator writes.
DEFAULT_ROBOT_Z = 0.155

# A GUI server renders every station tile, so it answers slower than a
# headless one and an occasional call overruns. Retry rather than
# abandoning the robot mid-aisle.
SET_POSE_TIMEOUT_MS = 5000
SET_POSE_ATTEMPTS = 4

POSE_RE = re.compile(
    r"\[\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s*\]"
)


def gz(args: list[str], timeout: float = 10.0):
    return subprocess.run(
        ["gz"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def provider_count(world: str) -> int:
    """How many gz sim servers advertise this world's set_pose.

    Must be exactly one. Two servers running the same world both
    advertise the service, and gz-transport hands each request to one
    of them, so pose updates get split and the robot appears to stall
    part way through the mission.
    """

    try:
        info = gz(
            [
                "service",
                "-s",
                f"/world/{world}/set_pose",
                "--info",
            ]
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0

    return info.count("tcp://")


def read_robot_z(name: str, default: float) -> float:
    """Reuse whatever Z the world already gave the robot."""

    try:
        out = gz(["model", "-m", name, "-p"]).stdout
    except (OSError, subprocess.SubprocessError):
        return default

    match = POSE_RE.search(out)

    return float(match.group(3)) if match else default


def set_pose(
    world: str,
    name: str,
    x: float,
    y: float,
    z: float,
) -> None:

    request = (
        f'name: "{name}", '
        f"position: {{x: {x}, y: {y}, z: {z}}}, "
        f"orientation: {{x: 0, y: 0, z: 0, w: 1}}"
    )

    call = [
        "service",
        "-s",
        f"/world/{world}/set_pose",
        "--reqtype",
        "gz.msgs.Pose",
        "--reptype",
        "gz.msgs.Boolean",
        "--timeout",
        str(SET_POSE_TIMEOUT_MS),
        "--req",
        request,
    ]

    budget = SET_POSE_TIMEOUT_MS / 1000.0 + 5.0

    problem = "no response"

    for _ in range(SET_POSE_ATTEMPTS):

        try:
            result = gz(call, timeout=budget)

        except subprocess.TimeoutExpired:
            problem = "gz service did not return"
            continue

        if "true" in result.stdout:
            return

        problem = (
            f"{result.stdout.strip()} {result.stderr.strip()}"
        ).strip() or "no response"

    raise RuntimeError(
        f"set_pose failed for {name} at ({x}, {y}) after "
        f"{SET_POSE_ATTEMPTS} attempts: {problem}"
    )
