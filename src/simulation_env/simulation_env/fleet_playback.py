"""Read-only smooth playback of complete timed fleet trajectories.

This is deliberately downstream of the scientific simulation.  It copies the
timed actions into an immutable visual track, samples one global simulation
time for the whole fleet, and emits interpolated Gazebo poses.  It has no
reference to ``GridRobot`` and cannot alter motion, metrics, reservations, or
the authoritative clock.
"""

from dataclasses import dataclass
from math import ceil
from time import perf_counter, sleep

from simulation_env.gazebo_view import grid_to_gazebo


MOVE_ACTIONS = frozenset(("MOVE", "RETREAT"))
STATIONARY_ACTIONS = frozenset(("WAIT", "SERVICE"))
DEFAULT_PLAYBACK_HZ = 20.0


@dataclass(frozen=True)
class FleetPose:
    name: str
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class _VisualAction:
    kind: str
    source: tuple[int, int]
    target: tuple[int, int]
    start: float
    end: float


@dataclass(frozen=True)
class _VisualTrack:
    start_cell: tuple[int, int]
    actions: tuple[_VisualAction, ...]


class FleetTrajectoryPlayback:
    """Interpolate every robot against the same sampled simulation time."""

    def __init__(
        self,
        trajectories: dict,
        resolution: float,
        z: float,
        frame_hz: float = DEFAULT_PLAYBACK_HZ,
    ) -> None:
        if resolution <= 0.0:
            raise ValueError("resolution must be positive")
        if frame_hz <= 0.0:
            raise ValueError("frame_hz must be positive")

        self.resolution = float(resolution)
        self.z = float(z)
        self.frame_hz = float(frame_hz)
        self._tracks = {
            robot_id: self._copy_track(trajectory)
            for robot_id, trajectory in sorted(trajectories.items())
        }

    # -- dynamic workloads ----------------------------------
    #
    # A fixed demo hands over every trajectory up front. A continuous
    # workload does not: robots are dispatched as they free up, so the
    # visual track for a robot arrives while the fleet is already
    # moving. These two methods let the same playback object follow
    # that, without touching interpolation, timing or the clock.

    def place(self, robot_id: str, cell) -> None:
        """Show a robot standing still at ``cell``.

        Used before a robot's first dispatch, and for one parked at the
        end, so every robot has a pose from t=0 onwards.
        """

        self._tracks[robot_id] = _VisualTrack(
            start_cell=(int(cell[0]), int(cell[1])),
            actions=(),
        )

        self._resort()

    def set_trajectory(self, robot_id: str, trajectory) -> None:
        """Replace one robot's visual track with a newly planned one.

        Safe because playback only ever samples forward: for any time
        before the new trajectory's first action, ``_pose_at`` falls
        back to its start cell, which is exactly where the robot is
        standing while it waits to set off.
        """

        self._tracks[robot_id] = self._copy_track(trajectory)

        self._resort()

    def _resort(self) -> None:
        """Keep emission order deterministic as robots are added."""

        self._tracks = dict(sorted(self._tracks.items()))

    @staticmethod
    def _copy_track(trajectory) -> _VisualTrack:
        cell = trajectory.start_cell
        copied = []

        for action in trajectory.actions:
            if action.end < action.start:
                raise ValueError(
                    f"{trajectory.robot_id}: action ends before it starts"
                )

            if action.kind in STATIONARY_ACTIONS and action.cell != cell:
                raise ValueError(
                    f"{trajectory.robot_id}: {action.kind} changes cell "
                    f"{cell} -> {action.cell}"
                )

            copied.append(
                _VisualAction(
                    kind=action.kind,
                    source=cell,
                    target=action.cell,
                    start=float(action.start),
                    end=float(action.end),
                )
            )
            cell = action.cell

        return _VisualTrack(
            start_cell=trajectory.start_cell,
            actions=tuple(copied),
        )

    def poses_at(self, sim_time: float) -> tuple[FleetPose, ...]:
        """Return one fleet-wide pose batch at ``sim_time``."""

        return tuple(
            self._pose_at(robot_id, track, float(sim_time))
            for robot_id, track in self._tracks.items()
        )

    def _pose_at(
        self,
        robot_id: str,
        track: _VisualTrack,
        sim_time: float,
    ) -> FleetPose:
        x, y = track.start_cell
        grid_x, grid_y = float(x), float(y)

        for action in track.actions:
            if sim_time < action.start:
                break

            if sim_time >= action.end:
                grid_x, grid_y = map(float, action.target)
                continue

            if action.kind in MOVE_ACTIONS and action.end > action.start:
                progress = (
                    (sim_time - action.start) / (action.end - action.start)
                )
                grid_x = action.source[0] + progress * (
                    action.target[0] - action.source[0]
                )
                grid_y = action.source[1] + progress * (
                    action.target[1] - action.source[1]
                )
            else:
                # WAITING, PICKING and DROPPING all remain at the source cell.
                grid_x, grid_y = map(float, action.source)

            break

        world_x, world_y = grid_to_gazebo(
            (grid_x, grid_y), self.resolution
        )
        return FleetPose(robot_id, world_x, world_y, self.z)

    def sample_times(
        self,
        start_sim: float,
        end_sim: float,
        wall_seconds: float,
    ) -> tuple[float, ...]:
        """Global timestamps for one playback interval, including its end."""

        if end_sim < start_sim:
            raise ValueError("playback interval cannot move backwards")

        frame_count = max(1, ceil(max(0.0, wall_seconds) * self.frame_hz))
        delta = end_sim - start_sim

        return tuple(
            start_sim + delta * frame / frame_count
            for frame in range(1, frame_count + 1)
        )

    def play_interval(
        self,
        start_sim: float,
        end_sim: float,
        wall_seconds: float,
        emit,
        sleep_fn=sleep,
        clock_fn=perf_counter,
    ) -> None:
        """Pace and emit batched poses; never feed anything back upstream."""

        times = self.sample_times(start_sim, end_sim, wall_seconds)
        begun = clock_fn()

        for index, sim_time in enumerate(times, start=1):
            deadline = begun + wall_seconds * index / len(times)
            remaining = deadline - clock_fn()

            if remaining > 0.0:
                sleep_fn(remaining)

            emit(sim_time, self.poses_at(sim_time))
