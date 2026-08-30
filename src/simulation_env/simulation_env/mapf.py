"""Prioritized MAPF with Space-Time A* over the reservation model.

The spatial A* in ``mrta_core.grid`` still answers "how far is it?" for
static cost. This module answers the different question "when may this
robot be where?", which is what multi-robot execution needs.

Timebase
--------
Search runs on integer steps of an exact rational quantum. With the
default 1.5 m/s and 1.0 m cells a move is exactly 2/3 s, and a 2.0 s
service is exactly three of those, so the quantum is 2/3 and the
search is classic timestep MAPF. The quantum is derived as the gcd of
the move and service durations, so unusual settings still land on an
exact grid instead of drifting in floating point.

Occupancy
---------
Same rule as the runtime table: a robot traversing A -> B holds both
cells for the whole traversal. Planning against the identical rule is
what lets a planned trajectory survive the runtime safety checks.

Prioritized planning is incomplete: a robot planned late can find the
corridor already booked and fail. That is reported, never silently
ignored.
"""

from collections import deque
from dataclasses import dataclass, field
from fractions import Fraction
from heapq import heappop, heappush
from math import gcd, lcm

from mrta_core.grid import Cell, GridMap


MOVE = "MOVE"
WAIT = "WAIT"
SERVICE = "SERVICE"
RETREAT = "RETREAT"


def frac_gcd(a: Fraction, b: Fraction) -> Fraction:
    """Largest rational dividing both exactly."""
    return Fraction(
        gcd(a.numerator, b.numerator),
        lcm(a.denominator, b.denominator),
    )


class MapfFailure(RuntimeError):
    """No space-time path exists within the horizon."""


@dataclass(frozen=True)
class PlanAction:
    kind: str
    cell: Cell          # cell the robot is in once the action ends
    start: Fraction
    end: Fraction
    zone: str = ""

    @property
    def duration(self) -> Fraction:
        return self.end - self.start


@dataclass
class Trajectory:
    robot_id: str
    start_cell: Cell
    actions: list = field(default_factory=list)
    expansions: int = 0

    @property
    def end_time(self) -> Fraction:
        return (
            self.actions[-1].end
            if self.actions
            else Fraction(0)
        )

    @property
    def end_cell(self) -> Cell:
        return (
            self.actions[-1].cell
            if self.actions
            else self.start_cell
        )

    @property
    def planned_wait(self) -> Fraction:
        return sum(
            (a.duration for a in self.actions if a.kind == WAIT),
            Fraction(0),
        )

    @property
    def move_count(self) -> int:
        return sum(
            1 for a in self.actions if a.kind in (MOVE, RETREAT)
        )


# ------------------------------------------------------
# Time-indexed reservations, for planning
# ------------------------------------------------------


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    """Half-open intervals; ``None`` end means forever."""

    if a_end is not None and a_end <= b_start:
        return False

    if b_end is not None and b_end <= a_start:
        return False

    return True


@dataclass
class TimedReservations:
    """What is booked, and when. Queried during search."""

    cells: dict = field(default_factory=dict)
    edges: dict = field(default_factory=dict)

    def cell_free(
        self,
        cell: Cell,
        start: Fraction,
        end,
        robot_id: str,
    ) -> bool:

        for r_start, r_end, holder in self.cells.get(cell, ()):

            if holder == robot_id:
                continue

            if _overlaps(start, end, r_start, r_end):
                return False

        return True

    def edge_free(
        self,
        source: Cell,
        target: Cell,
        start: Fraction,
        end: Fraction,
        robot_id: str,
    ) -> bool:
        """False when someone is driving target -> source meanwhile."""

        for r_start, r_end, holder in self.edges.get(
            (target, source), ()
        ):
            if holder == robot_id:
                continue

            if _overlaps(start, end, r_start, r_end):
                return False

        return True

    def drop(self, robot_id: str) -> None:
        """Forget everything booked for a robot.

        Used to lift a robot's placeholder just before it is planned,
        so its own start cell does not block it.
        """

        for cell, bookings in list(self.cells.items()):
            kept = [b for b in bookings if b[2] != robot_id]
            if kept:
                self.cells[cell] = kept
            else:
                del self.cells[cell]

        for edge, bookings in list(self.edges.items()):
            kept = [b for b in bookings if b[2] != robot_id]
            if kept:
                self.edges[edge] = kept
            else:
                del self.edges[edge]

    def reserve_cell(self, cell, start, end, robot_id) -> None:
        self.cells.setdefault(cell, []).append(
            (start, end, robot_id)
        )

    def reserve_edge(self, source, target, start, end, robot_id) -> None:
        self.edges.setdefault((source, target), []).append(
            (start, end, robot_id)
        )

    def commit(self, trajectory: Trajectory, hold_forever: bool) -> None:
        """Turn a trajectory into bookings for later planners."""

        robot_id = trajectory.robot_id

        cell = trajectory.start_cell

        held_since = (
            trajectory.actions[0].start
            if trajectory.actions
            else Fraction(0)
        )

        for action in trajectory.actions:

            if action.kind in (MOVE, RETREAT):
                # Hold the old cell until arrival, claim the new one
                # from departure: the hold-both rule.
                self.reserve_cell(
                    cell, held_since, action.end, robot_id
                )
                self.reserve_edge(
                    cell, action.cell,
                    action.start, action.end, robot_id,
                )
                cell = action.cell
                held_since = action.start

        self.reserve_cell(
            cell,
            held_since,
            None if hold_forever else trajectory.end_time,
            robot_id,
        )


# ------------------------------------------------------
# Space-Time A*
# ------------------------------------------------------


def heuristic_map(grid: GridMap, goal: Cell) -> dict:
    """Backward BFS from the goal: exact free-space distance.

    Admissible and obstacle-aware, so the search never explores
    around a wall it cannot pass.
    """

    dist = {goal: 0}
    queue = deque([goal])

    while queue:
        cell = queue.popleft()
        for neighbour in grid.neighbors(cell):
            if neighbour not in dist:
                dist[neighbour] = dist[cell] + 1
                queue.append(neighbour)

    return dist


@dataclass
class SpaceTimeAStar:
    """Plans one robot's timed path against existing bookings."""

    timestep: Fraction
    move_steps: int
    horizon_steps: int = 4000

    def plan(
        self,
        robot_id: str,
        start_cell: Cell,
        goal_cell: Cell,
        start_time: Fraction,
        grid: GridMap,
        reservations: TimedReservations,
        goal_hold_steps: int | None = 0,
        zone: str = "",
        kind: str = MOVE,
    ) -> Trajectory:
        """Find the earliest safe arrival at ``goal_cell``.

        ``goal_hold_steps`` is how long the goal must stay free after
        arrival: the service duration for a station, or ``None`` for a
        robot that parks there and never leaves.
        """

        if not grid.is_free(start_cell):
            raise MapfFailure(
                f"{robot_id}: start {start_cell} is not free"
            )

        if not grid.is_free(goal_cell):
            raise MapfFailure(
                f"{robot_id}: goal {goal_cell} is not free"
            )

        heuristic = heuristic_map(grid, goal_cell)

        if start_cell not in heuristic:
            raise MapfFailure(
                f"{robot_id}: no static path {start_cell} -> "
                f"{goal_cell}"
            )

        def at(step: int) -> Fraction:
            return start_time + step * self.timestep

        def h(cell: Cell) -> int:
            return heuristic.get(cell, 10**9) * self.move_steps

        open_heap = [(h(start_cell), 0, 0, start_cell, 0)]
        best = {(start_cell, 0): 0}
        parent = {}
        counter = 1
        expansions = 0

        while open_heap:

            _, g, _, cell, step = heappop(open_heap)

            if g > best.get((cell, step), 10**9):
                continue

            expansions += 1

            if cell == goal_cell:

                hold_end = (
                    None
                    if goal_hold_steps is None
                    else at(step + goal_hold_steps)
                )

                if reservations.cell_free(
                    cell, at(step), hold_end, robot_id
                ):
                    return self._rebuild(
                        robot_id, start_cell, parent,
                        cell, step, at, expansions, zone, kind,
                    )

            if step >= self.horizon_steps:
                continue

            # WAIT: same cell, one quantum later.
            wait_end = at(step + 1)

            if reservations.cell_free(
                cell, at(step), wait_end, robot_id
            ):
                self._relax(
                    open_heap, best, parent,
                    cell, step, cell, step + 1,
                    g + 1, h, counter,
                )
                counter += 1

            # MOVE to a 4-connected neighbour.
            for neighbour in grid.neighbors(cell):

                arrive = step + self.move_steps

                begin, finish = at(step), at(arrive)

                if not reservations.cell_free(
                    neighbour, begin, finish, robot_id
                ):
                    continue

                # Under hold-both the robot still occupies the cell it
                # is leaving for the whole traversal. Checking only
                # the target would let it book a cell it holds past
                # arrival while someone else already owns that window.
                # Entry, wait and exit checks together cover the full
                # holding interval.
                if not reservations.cell_free(
                    cell, begin, finish, robot_id
                ):
                    continue

                if not reservations.edge_free(
                    cell, neighbour, begin, finish, robot_id
                ):
                    continue

                self._relax(
                    open_heap, best, parent,
                    cell, step, neighbour, arrive,
                    g + self.move_steps, h, counter,
                )
                counter += 1

        raise MapfFailure(
            f"{robot_id}: no space-time path {start_cell} -> "
            f"{goal_cell} within {self.horizon_steps} steps "
            f"({expansions} expansions)"
        )

    def _relax(
        self, heap, best, parent,
        cell, step, next_cell, next_step, cost, h, counter,
    ) -> None:

        key = (next_cell, next_step)

        if cost >= best.get(key, 10**9):
            return

        best[key] = cost
        parent[key] = (cell, step)

        heappush(
            heap,
            (cost + h(next_cell), cost, counter, next_cell, next_step),
        )

    def _rebuild(
        self, robot_id, start_cell, parent,
        cell, step, at, expansions, zone, kind,
    ) -> Trajectory:

        chain = [(cell, step)]

        while (cell, step) in parent:
            cell, step = parent[(cell, step)]
            chain.append((cell, step))

        chain.reverse()

        actions = []

        for (prev_cell, prev_step), (next_cell, next_step) in zip(
            chain, chain[1:]
        ):
            actions.append(
                PlanAction(
                    kind=WAIT if next_cell == prev_cell else kind,
                    cell=next_cell,
                    start=at(prev_step),
                    end=at(next_step),
                    zone=zone,
                )
            )

        return Trajectory(
            robot_id=robot_id,
            start_cell=start_cell,
            actions=actions,
            expansions=expansions,
        )
