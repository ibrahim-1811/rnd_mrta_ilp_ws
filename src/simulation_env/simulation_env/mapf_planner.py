"""Prioritized fleet planning: missions -> timed trajectories.

One shared traffic layer. Every allocation strategy will later hand
its missions to this, so routing behaviour stays identical no matter
which allocator produced the assignment.

Robots are planned in sorted id order, each against the bookings of
those planned before it. Prioritized planning is fast and
deterministic but incomplete: a robot planned late may find no path.
That is raised, not hidden.
"""

from dataclasses import dataclass, field
from fractions import Fraction

from mrta_core.warehouse_map import WarehouseMap, ZoneType

from simulation_env.mapf import (
    MOVE,
    RETREAT,
    SERVICE,
    MapfFailure,
    PlanAction,
    SpaceTimeAStar,
    TimedReservations,
    Trajectory,
    frac_gcd,
)
from simulation_env.mission_planner import (
    MissionPlan,
    navigation_grid,
    service_cells,
)


SERVICE_STATUS_ZONE = (ZoneType.SHELF, ZoneType.DROPOFF)


@dataclass
class FleetPlan:
    trajectories: dict = field(default_factory=dict)
    expansions: int = 0
    failures: dict = field(default_factory=dict)
    reservations: TimedReservations = field(
        default_factory=TimedReservations
    )

    @property
    def makespan(self) -> Fraction:
        if not self.trajectories:
            return Fraction(0)
        return max(t.end_time for t in self.trajectories.values())


def timing(
    resolution: float,
    speed_mps: float,
    service_seconds: dict,
) -> tuple:
    """Exact quantum, move length and service length, all in steps."""

    move = (
        Fraction(resolution).limit_denominator(10**6)
        / Fraction(speed_mps).limit_denominator(10**6)
    )

    quantum = move

    for seconds in service_seconds.values():
        quantum = frac_gcd(
            quantum, Fraction(seconds).limit_denominator(10**6)
        )

    move_steps = move / quantum

    if move_steps.denominator != 1:
        raise ValueError(
            f"move duration {move} is not a whole number of "
            f"quanta ({quantum})"
        )

    service_steps = {}

    for zone_type, seconds in service_seconds.items():
        steps = (
            Fraction(seconds).limit_denominator(10**6) / quantum
        )
        if steps.denominator != 1:
            raise ValueError(
                f"service {seconds}s is not a whole number of "
                f"quanta ({quantum})"
            )
        service_steps[zone_type] = int(steps)

    return quantum, int(move_steps), service_steps


class MapfMissionPlanner:
    """Turns MissionPlans into reservation-safe timed trajectories."""

    def __init__(
        self,
        warehouse: WarehouseMap,
        resolution: float,
        speed_mps: float,
        service_seconds: dict,
        horizon_steps: int = 4000,
    ) -> None:

        self.warehouse = warehouse
        self.resolution = resolution
        self.service_seconds = service_seconds

        self.quantum, self.move_steps, self.service_steps = timing(
            resolution, speed_mps, service_seconds
        )

        self.search = SpaceTimeAStar(
            timestep=self.quantum,
            move_steps=self.move_steps,
            horizon_steps=horizon_steps,
        )

        self._service_cells = service_cells(warehouse)

    def _grid(self, start, goal, blocked=frozenset()):
        """Nav view: obstacles plus every service cell but the target.

        ``blocked`` adds cells held by robots that are standing still.
        They must be obstacles in the *grid*, not merely reservations:
        a stationary robot's booking has no end time, so a search that
        is allowed to consider its cell explores every timestep out to
        the horizon before conceding. As an obstacle it is excluded
        from the heuristic map instead, and an unreachable goal is
        reported at once.

        Moving robots are deliberately not included: their bookings
        end, and the space-time layer is exactly what resolves them.
        """

        return navigation_grid(
            self.warehouse,
            occupied=self._service_cells | frozenset(blocked),
            start=start,
            goal=goal,
        )

    def plan_mission(
        self,
        robot_id: str,
        start_cell,
        plan: MissionPlan,
        start_time: Fraction,
        reservations: TimedReservations,
        hold_forever: bool = True,
        blocked_cells: frozenset = frozenset(),
    ) -> Trajectory:
        """Legs, services and retreats as one continuous trajectory.

        ``blocked_cells`` names cells occupied by stationary robots;
        see :meth:`_grid`.
        """

        actions: list = []
        expansions = 0

        cell = start_cell
        now = start_time

        segments = list(plan.segments)

        for index, segment in enumerate(segments):

            last = index == len(segments) - 1

            servicing = segment.exit_cell is not None

            zone_type = (
                self.warehouse.zone_by_id(segment.to_zone).zone_type
                if servicing
                else None
            )

            if servicing:
                hold = self.service_steps[zone_type]
            elif last:
                hold = None if hold_forever else 0
            else:
                hold = 0

            leg = self.search.plan(
                robot_id=robot_id,
                start_cell=cell,
                goal_cell=segment.goal_cell,
                start_time=now,
                grid=self._grid(
                    cell, segment.goal_cell, blocked_cells
                ),
                reservations=reservations,
                goal_hold_steps=hold,
                zone=segment.to_zone,
            )

            actions.extend(leg.actions)
            expansions += leg.expansions

            cell = segment.goal_cell
            now = leg.end_time

            if not servicing:
                continue

            # Stationary service, holding the cell throughout.
            duration = (
                self.service_steps[zone_type] * self.quantum
            )

            actions.append(
                PlanAction(
                    kind=SERVICE,
                    cell=cell,
                    start=now,
                    end=now + duration,
                    zone=segment.to_zone,
                )
            )

            now = now + duration

            # The retreat is reservation-planned like any other move.
            retreat = self.search.plan(
                robot_id=robot_id,
                start_cell=cell,
                goal_cell=segment.exit_cell,
                start_time=now,
                grid=self._grid(
                    cell, segment.exit_cell, blocked_cells
                ),
                reservations=reservations,
                goal_hold_steps=(
                    (None if hold_forever else 0) if last else 0
                ),
                zone=segment.to_zone,
                kind=RETREAT,
            )

            actions.extend(retreat.actions)
            expansions += retreat.expansions

            cell = segment.exit_cell
            now = retreat.end_time

        return Trajectory(
            robot_id=robot_id,
            start_cell=start_cell,
            actions=actions,
            expansions=expansions,
        )

    def plan_fleet(
        self,
        robots: dict,
        missions: dict,
        start_time: Fraction = Fraction(0),
        reservations: TimedReservations | None = None,
        hold_forever: bool = True,
    ) -> FleetPlan:
        """Plan every mission in sorted robot order."""

        result = FleetPlan(
            reservations=reservations or TimedReservations()
        )

        # Every robot is a real obstacle where it stands, whether or
        # not it has a mission yet. Without this an earlier-planned
        # robot happily routes through one that has not moved, and
        # the conflict only surfaces at execution.
        for robot_id in sorted(robots):
            result.reservations.reserve_cell(
                robots[robot_id].cell, start_time, None, robot_id
            )

        for robot_id in sorted(missions):

            robot = robots[robot_id]

            # Lift this robot's placeholder: its own trajectory says
            # when it really vacates.
            result.reservations.drop(robot_id)

            try:
                trajectory = self.plan_mission(
                    robot_id=robot_id,
                    start_cell=robot.cell,
                    plan=missions[robot_id],
                    start_time=start_time,
                    reservations=result.reservations,
                    hold_forever=hold_forever,
                )

            except MapfFailure as exc:
                result.failures[robot_id] = str(exc)
                # Still holds its current cell.
                result.reservations.reserve_cell(
                    robot.cell, start_time, None, robot_id
                )
                continue

            result.reservations.commit(trajectory, hold_forever)

            result.trajectories[robot_id] = trajectory
            result.expansions += trajectory.expansions

        return result
