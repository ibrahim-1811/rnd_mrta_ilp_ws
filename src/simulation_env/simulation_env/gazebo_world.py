"""Start Gazebo with the fleet already parked: the world, and nothing else.

Gazebo starts from the one world file, which holds the warehouse only.
Robots created after Gazebo is up arrive late, and a window still
loading the warehouse can miss them altogether. So starting Gazebo
writes a temporary copy of the world with every robot of the run on its
start cell, exactly where the fleet node will put it, and Gazebo loads
the warehouse and the fleet together. The fleet node then finds the
robots there and creates none.

The copy lives in the temporary directory and is rewritten on every
start. It is not a scenario world: the repo keeps one world per map.

Starting also stops a Gazebo already running this world on this
GZ_PARTITION, because two servers for one world split the pose stream
between them and the fleet node then refuses to draw at all.

Visualization only. Nothing here touches the simulation.
"""

import os
import signal
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import yaml

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx

from simulation_env.fleet import build_fleet
from simulation_env.gazebo_bridge import (
    DEFAULT_ROBOT_Z,
    ROBOT_NAME_RE,
    FleetPose,
    robot_model,
)
from simulation_env.gazebo_view import grid_to_gazebo


# How long a stopped Gazebo gets to exit before it is killed.
STOP_GRACE_S = 10.0


def world_name_of(config_path) -> str:
    """The Gazebo world name the config asks for."""

    with open(config_path) as handle:
        document = yaml.safe_load(handle) or {}

    return document.get("gazebo", {}).get("world_name") or "warehouse"


def world_file_for(config_path, share_dir) -> str:
    """The one world file for this config.

    ``worlds/<gazebo.world_name>.world`` next to the config first (the
    source tree), then the installed copy, so a map rebuilt by
    build_scenario.py is used without a colcon rebuild.
    """

    file_name = f"{world_name_of(config_path)}.world"

    beside = os.path.join(
        os.path.dirname(os.path.abspath(config_path)), "..", "worlds", file_name
    )

    if os.path.isfile(beside):
        return os.path.normpath(beside)

    return os.path.join(share_dir, "worlds", file_name)


def fleet_poses(config_path, robots: int = 0) -> list[FleetPose]:
    """Each robot of the run on its start cell, as the fleet node places it.

    ``robots`` is the run's fleet size; 0 keeps fleet.robot_count. The
    cells come from the same build_fleet the node uses, and the XY from
    the same cell-to-Gazebo transform, so the robots load exactly where
    the node's first frame will put them.
    """

    config = load_sim_config(config_path)

    if robots > 0:
        config = replace(config, robot_count=robots)

    warehouse = load_warehouse_xlsx(config.map_path)
    grid = warehouse.grid

    return [
        FleetPose(
            robot.robot_id,
            *grid_to_gazebo(robot.cell, config.resolution_m, grid.origin_x, grid.origin_y),
            DEFAULT_ROBOT_Z,
        )
        for robot in build_fleet(config, warehouse)
    ]


def with_fleet(world_text: str, poses) -> str:
    """The world with one robot model per pose, added before </world>.

    Everything else in the world is left exactly as it was.
    """

    if any(ROBOT_NAME_RE.match(name) for name in model_names(world_text)):
        raise ValueError(
            "the world file already holds robot models; a world holds "
            "the warehouse only (regenerate it with build_scenario.py)"
        )

    end = world_text.rindex("</world>")

    robots = "".join(f"    {robot_model(pose.name, pose)}\n" for pose in poses)

    return world_text[:end] + "\n" + robots + "\n  " + world_text[end:]


def model_names(world_text: str) -> list[str]:
    """Names of the <model> elements in a world file, in order."""

    names = []
    start = 0

    while (start := world_text.find('<model name="', start)) != -1:
        start += len('<model name="')
        names.append(world_text[start : world_text.index('"', start)])

    return names


def write_fleet_world(config_path, robots: int, world_file, out_dir=None) -> str:
    """Write the world plus the parked fleet to a temporary file.

    The file keeps the world's own file name, so a later start
    recognises the Gazebo running it, and lives in its own directory
    under the temporary directory, rewritten on every start.
    """

    if out_dir is None:
        out_dir = os.path.join(tempfile.gettempdir(), f"mrta_gazebo_{os.getuid()}")

    os.makedirs(out_dir, exist_ok=True)

    path = os.path.join(out_dir, os.path.basename(world_file))

    text = with_fleet(Path(world_file).read_text(), fleet_poses(config_path, robots))

    # Written beside, then renamed, so a reader never sees half a world.
    partial = path + ".partial"
    Path(partial).write_text(text)
    os.replace(partial, path)

    return path


# ----------------------------------------------------------------------
# Stopping an earlier Gazebo of the same world
# ----------------------------------------------------------------------


def is_gazebo_for(argv, world_file_name: str) -> bool:
    """Is this command line a ``gz sim`` running a world of this file name?

    Gazebo's ruby front end rewrites its process title, so the command
    line can be one string ("gz sim -r path") rather than separate
    arguments. A shell that merely mentions gz sim (``bash -c "gz sim
    ..."``) is not Gazebo and is never matched.
    """

    words = [word for arg in argv for word in arg.split()]

    if len(words) >= 3 and os.path.basename(words[0]).startswith("ruby"):
        words = words[1:]

    if len(words) < 2 or os.path.basename(words[0]) != "gz" or words[1] != "sim":
        return False

    return any(os.path.basename(word) == world_file_name for word in words[2:])


def partition_of(environ: bytes) -> str:
    """GZ_PARTITION from a /proc/<pid>/environ blob ('' when unset)."""

    for entry in environ.split(b"\0"):
        if entry.startswith(b"GZ_PARTITION="):
            return entry.split(b"=", 1)[1].decode(errors="replace")

    return ""


def gazebo_pids(
    world_file_name: str,
    partition: str | None = None,
    proc: Path = Path("/proc"),
) -> list[int]:
    """This user's gz sim processes running this world on this partition.

    Another partition is another set of Gazebo servers that cannot see
    this one, so it is left alone. ``proc`` is only ever changed by a
    test.
    """

    if partition is None:
        partition = os.environ.get("GZ_PARTITION", "")

    pids = []

    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue

        try:
            if entry.stat().st_uid != os.getuid():
                continue

            argv = (entry / "cmdline").read_bytes().decode(errors="replace")
            environ = (entry / "environ").read_bytes()

        except OSError:
            # Gone already, or not ours to read.
            continue

        if not is_gazebo_for(argv.split("\0"), world_file_name):
            continue

        if partition_of(environ) == partition:
            pids.append(int(entry.name))

    return sorted(pids)


def stop_gazebo(world_file_name: str, grace_s: float = STOP_GRACE_S) -> list[int]:
    """Stop every Gazebo running this world here; returns the pids stopped."""

    pids = gazebo_pids(world_file_name)

    for pid in pids:
        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + grace_s

    while time.monotonic() < deadline and any(_alive(pid) for pid in pids):
        time.sleep(0.2)

    for pid in pids:
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    return pids


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False

    # A zombie has exited; it only waits for its parent to reap it.
    return state != "Z"
