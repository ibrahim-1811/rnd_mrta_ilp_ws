"""Time-indexed cell and directed-edge reservations.

Robots hold cells over time intervals rather than over discrete
timesteps, because service holds (2.0 s) and moves (2/3 s) do not share
a common tick: two robots can easily be out of phase.

Occupancy model
---------------
A robot always holds the cell it stands on. While traversing A -> B it
holds *both*: it keeps A until it arrives and claims B the moment it
sets off. Holding both is what stops a third robot slipping into B
mid-transit, or into A before the mover has cleared it.

That single rule gives all four required guarantees:

* two robots never share a cell        -- ownership is exclusive
* two never enter a cell together      -- the first claim wins
* no A->B / B->A swap                  -- checked against live transits
* no entering a cell held by a robot that is PICKING, DROPPING,
  WAITING or IDLE -- a stationary robot never releases its cell

Every claim and release is also logged, so a finished run can be
audited independently of the live bookkeeping.
"""

from dataclasses import dataclass, field
from fractions import Fraction

from mrta_core.grid import Cell


VERTEX = "vertex"
EDGE = "edge"


@dataclass(frozen=True)
class CellInterval:
    cell: Cell
    robot_id: str
    start: Fraction
    end: Fraction | None = None   # None = still held


@dataclass(frozen=True)
class EdgeInterval:
    source: Cell
    target: Cell
    robot_id: str
    start: Fraction
    end: Fraction


@dataclass
class ReservationTable:

    # Live ownership: cell -> robot currently entitled to it.
    _owner: dict = field(default_factory=dict)
    _since: dict = field(default_factory=dict)

    # Live traversals: robot -> (source, target, start, end).
    _transit: dict = field(default_factory=dict)

    # Completed history, for auditing.
    cell_log: list = field(default_factory=list)
    edge_log: list = field(default_factory=list)

    # -- queries ----------------------------------------

    def owner(self, cell: Cell) -> str | None:
        return self._owner.get(cell)

    def held_cells(self, robot_id: str) -> list:
        return sorted(
            cell
            for cell, holder in self._owner.items()
            if holder == robot_id
        )

    def cells_held_by_others(self, robot_id: str) -> frozenset:
        """Cells another robot is standing on or driving into.

        Idle and servicing robots never release their cell, so a
        planner may treat these as static obstacles.
        """

        return frozenset(
            cell
            for cell, holder in self._owner.items()
            if holder != robot_id
        )

    def check_move(
        self,
        robot_id: str,
        source: Cell,
        target: Cell,
    ) -> str | None:
        """Why this move is unsafe right now, or None if it is safe."""

        holder = self._owner.get(target)

        if holder is not None and holder != robot_id:
            return VERTEX

        # A head-on swap: someone is already driving target -> source.
        for other, (o_src, o_tgt, _, _) in self._transit.items():

            if other == robot_id:
                continue

            if o_src == target and o_tgt == source:
                return EDGE

        return None

    # -- mutation ---------------------------------------

    def occupy(
        self,
        cell: Cell,
        robot_id: str,
        now: Fraction,
    ) -> None:
        """Initial placement of a robot."""

        existing = self._owner.get(cell)

        if existing is not None and existing != robot_id:
            raise ValueError(
                f"{robot_id} cannot start on {cell}: "
                f"already held by {existing}"
            )

        self._owner[cell] = robot_id
        self._since[cell] = now

    def begin_move(
        self,
        robot_id: str,
        source: Cell,
        target: Cell,
        now: Fraction,
        duration: Fraction,
    ) -> None:
        """Claim the target and open the edge traversal."""

        conflict = self.check_move(robot_id, source, target)

        if conflict is not None:
            raise RuntimeError(
                f"{robot_id} {source}->{target} is unsafe "
                f"({conflict}); check_move first"
            )

        self._owner[target] = robot_id
        self._since[target] = now

        self._transit[robot_id] = (
            source,
            target,
            now,
            now + duration,
        )

    def complete_move(
        self,
        robot_id: str,
        source: Cell,
        target: Cell,
        now: Fraction,
    ) -> None:
        """Arrive at the target and release the cell left behind."""

        transit = self._transit.pop(robot_id, None)

        if transit is not None:
            self.edge_log.append(
                EdgeInterval(
                    source=transit[0],
                    target=transit[1],
                    robot_id=robot_id,
                    start=transit[2],
                    end=now,
                )
            )

        self.release(source, robot_id, now)

    def release(
        self,
        cell: Cell,
        robot_id: str,
        now: Fraction,
    ) -> None:

        if self._owner.get(cell) != robot_id:
            return

        self.cell_log.append(
            CellInterval(
                cell=cell,
                robot_id=robot_id,
                start=self._since.get(cell, now),
                end=now,
            )
        )

        del self._owner[cell]
        self._since.pop(cell, None)

    def close(self, now: Fraction) -> None:
        """Close every open interval, for auditing after a run."""

        for cell, robot_id in list(self._owner.items()):
            self.cell_log.append(
                CellInterval(
                    cell=cell,
                    robot_id=robot_id,
                    start=self._since.get(cell, now),
                    end=None,
                )
            )

        self._owner.clear()
        self._since.clear()

    # -- auditing ---------------------------------------

    def audit(self) -> list:
        """Re-derive the guarantees from the logged intervals."""

        problems = []

        def overlaps(a_start, a_end, b_start, b_end) -> bool:
            a_end = a_end if a_end is not None else None
            b_end = b_end if b_end is not None else None

            # Touching intervals do not overlap: one robot may enter a
            # cell exactly when another leaves it.
            if a_end is not None and a_end <= b_start:
                return False

            if b_end is not None and b_end <= a_start:
                return False

            return True

        by_cell: dict = {}

        for interval in self.cell_log:
            by_cell.setdefault(interval.cell, []).append(interval)

        for cell, intervals in by_cell.items():
            for i, first in enumerate(intervals):
                for second in intervals[i + 1:]:

                    if first.robot_id == second.robot_id:
                        continue

                    if overlaps(
                        first.start, first.end,
                        second.start, second.end,
                    ):
                        problems.append(
                            f"cell {cell} held by {first.robot_id} "
                            f"[{float(first.start):.4f}, "
                            f"{first.end}) and {second.robot_id} "
                            f"[{float(second.start):.4f}, "
                            f"{second.end})"
                        )

        for i, first in enumerate(self.edge_log):
            for second in self.edge_log[i + 1:]:

                if first.robot_id == second.robot_id:
                    continue

                if (
                    first.source == second.target
                    and first.target == second.source
                    and overlaps(
                        first.start, first.end,
                        second.start, second.end,
                    )
                ):
                    problems.append(
                        f"edge swap {first.source}->{first.target} "
                        f"by {first.robot_id} against "
                        f"{second.robot_id}"
                    )

        return problems
