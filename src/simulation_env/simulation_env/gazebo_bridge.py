"""Gazebo transport helpers.

Gazebo is visualization only and is not a ROS system here, so poses go
out over gz-transport. Shared by the fleet node and the mission
executor so there is one implementation of the retry and
multiple-server checks.
"""

import queue
import re
import subprocess
import threading


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
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.:/-]+$")


def gz(args: list[str], timeout: float = 10.0):
    return subprocess.run(
        ["gz"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def provider_count(world: str, service: str = "set_pose") -> int:
    """How many gz sim servers advertise a world pose service.

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
                f"/world/{world}/{service}",
                "--info",
            ]
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0

    return info.count("tcp://")


class FleetPoseSink:
    """Nonblocking Python front-end to one persistent transport process.

    The child owns one ``gz::transport::Node`` and sends one ``Pose_V`` request
    per fleet frame.  A latest-only queue prevents a slow renderer from ever
    back-pressuring scientific execution; intermediate visual frames may be
    dropped, but the newest complete fleet frame is retained.
    """

    def __init__(self, world: str, command: list[str] | None = None) -> None:
        self.world = world
        self._command = command or [
            "ros2",
            "run",
            "gazebo_fleet_pose_transport",
            "gazebo_fleet_pose_transport",
            "--world",
            world,
        ]
        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._frames: queue.Queue = queue.Queue(maxsize=1)
        self._next_frame = 0
        self._problem: Exception | None = None
        self._closed = False
        self._worker = threading.Thread(
            target=self._write_frames,
            name="gazebo-fleet-pose-writer",
            daemon=True,
        )
        self._worker.start()

    @property
    def command(self) -> tuple[str, ...]:
        return tuple(self._command)

    def send(self, poses) -> None:
        """Queue one complete fleet frame without waiting for Gazebo."""

        if self._closed:
            raise RuntimeError("fleet pose sink is closed")
        if self._problem is not None:
            raise RuntimeError(
                f"fleet pose transport failed: {self._problem}"
            ) from self._problem
        if self._process.poll() is not None:
            raise RuntimeError(
                "fleet pose transport exited with code "
                f"{self._process.returncode}"
            )

        frame = (self._next_frame, tuple(poses))
        self._next_frame += 1

        try:
            self._frames.put_nowait(frame)
        except queue.Full:
            # The old item is purely visual and has not been written yet.
            try:
                self._frames.get_nowait()
            except queue.Empty:
                pass
            self._frames.put_nowait(frame)

    @staticmethod
    def _frame_line(frame_id: int, poses) -> str:
        fields = ["FRAME", str(frame_id), str(len(poses))]

        for pose in poses:
            if not SAFE_NAME_RE.fullmatch(pose.name):
                raise ValueError(
                    f"Gazebo entity name cannot contain whitespace: "
                    f"{pose.name!r}"
                )
            fields.extend((
                pose.name,
                repr(float(pose.x)),
                repr(float(pose.y)),
                repr(float(pose.z)),
                "0",
                "0",
                "0",
                "1",
            ))

        return " ".join(fields) + "\n"

    def _write_frames(self) -> None:
        try:
            while True:
                item = self._frames.get()

                if item is None:
                    if self._process.stdin is not None:
                        self._process.stdin.write("QUIT\n")
                        self._process.stdin.flush()
                    return

                frame_id, poses = item
                line = self._frame_line(frame_id, poses)

                if self._process.stdin is None:
                    raise RuntimeError("transport stdin is unavailable")

                self._process.stdin.write(line)
                self._process.stdin.flush()

        except (BrokenPipeError, OSError, RuntimeError, ValueError) as error:
            self._problem = error

    def close(self, timeout: float = 2.0) -> None:
        if self._closed:
            return

        self._closed = True

        try:
            self._frames.put_nowait(None)
        except queue.Full:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                pass
            self._frames.put_nowait(None)

        self._worker.join(timeout=timeout)

        if self._process.poll() is None:
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._process.terminate()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


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
