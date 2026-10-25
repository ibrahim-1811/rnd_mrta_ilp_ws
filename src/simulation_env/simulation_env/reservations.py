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
    end: Fraction | None = None  # None = still held


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
            cell for cell, holder in self._owner.items() if holder == robot_id
        )

    def transits(self) -> dict:
        """Every move currently in flight: robot -> (source, target, start, end).

        Read-only view for a visualiser that needs to know where a robot
        is *between* two cells. Nothing here influences the simulation;
        the entry is created by ``begin_move`` and removed by
        ``complete_move``.
        """

        return dict(self._transit)

    def cells_held_by_others(self, robot_id: str) -> frozenset:
        """Cells another robot is standing on or driving into.

        Idle and servicing robots never release their cell, so a
        planner may treat these as static obstacles.
        """

        return frozenset(
            cell for cell, holder in self._owner.items() if holder != robot_id
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
                f"{robot_id} cannot start on {cell}: already held by {existing}"
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

        already_held = self._owner.get(target) == robot_id

        self._owner[target] = robot_id

        if not already_held:
            self._since[target] = now

        self._transit[robot_id] = (
            source,
            target,
            now,
            now + duration,
        )

    def begin_moves_batch(
        self,
        moves,
        now: Fraction,
        duration: Fraction,
    ) -> None:
        """Atomically begin multiple simultaneous one-cell moves.

        ``moves`` contains ``(robot_id, source, target)`` tuples.

        This is the runtime primitive for local synchronous traffic.
        A robot may enter a cell that another approved robot is
        vacating in the same batch.

        Guarantees:
        - target cells are unique
        - a target may only be occupied by a robot leaving in this batch
        - direct head-on edge swaps are rejected
        - all sources release before any target is claimed
        """

        moves = tuple(moves)

        if not moves:
            return

        robot_ids = [robot_id for robot_id, _, _ in moves]

        if len(set(robot_ids)) != len(robot_ids):
            raise ValueError("duplicate robot in simultaneous move batch")

        sources = {robot_id: source for robot_id, source, _ in moves}

        targets = {robot_id: target for robot_id, _, target in moves}

        if len(set(targets.values())) != len(targets):
            raise ValueError("simultaneous move batch has duplicate target cells")

        # Every robot must actually own its source before the move.
        for robot_id, source, _ in moves:
            holder = self._owner.get(source)

            if holder != robot_id:
                raise RuntimeError(
                    f"{robot_id} cannot leave {source}: owned by {holder!r}"
                )

        moving = set(robot_ids)

        # A target must either be free or currently owned by another
        # robot that is approved to vacate it in this same batch.
        for robot_id, _, target in moves:
            holder = self._owner.get(target)

            if holder is None or holder == robot_id:
                continue

            if holder not in moving:
                raise RuntimeError(
                    f"{robot_id} cannot enter {target}: "
                    f"stationary {holder} still owns it"
                )

            if sources[holder] != target:
                raise RuntimeError(
                    f"{robot_id} cannot enter {target}: "
                    f"{holder} is not leaving that cell"
                )

        # Never permit a direct A->B / B->A exchange.
        for robot_id, source, target in moves:
            for other_id, other_source, other_target in moves:
                if other_id <= robot_id:
                    continue

                if source == other_target and target == other_source:
                    raise RuntimeError(
                        f"head-on edge swap rejected: "
                        f"{robot_id} {source}->{target} against "
                        f"{other_id} {other_source}->{other_target}"
                    )

        # Check against a traversal that was already in progress before
        # this batch began.
        for robot_id, source, target in moves:
            for other, (o_src, o_tgt, _, _) in self._transit.items():
                if other == robot_id:
                    continue

                if o_src == target and o_tgt == source:
                    raise RuntimeError(
                        f"{robot_id} {source}->{target} conflicts "
                        f"with live reverse traversal by {other}"
                    )

        # Phase 1. Everyone leaves their source at the exact same
        # simulation instant.
        for robot_id, source, _ in moves:
            self.release(
                source,
                robot_id,
                now,
            )

        # Phase 2. Everyone claims the destination and begins transit.
        for robot_id, source, target in moves:
            if target in self._owner:
                raise RuntimeError(
                    f"batch target {target} unexpectedly still occupied "
                    f"by {self._owner[target]}"
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
        *,
        release_source: bool = True,
    ) -> None:
        """Complete one move.

        Normally the source is released on arrival.

        A service-entry move may deliberately retain its source.
        That source is the robot's guaranteed one-cell retreat path
        while PICKING or DROPPING.
        """

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

        if release_source:
            self.release(
                source,
                robot_id,
                now,
            )

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
        """Re-derive the guarantees from the logged intervals.

        Only intervals that can conflict are compared: two holds of one
        cell, and two moves over one pair of cells in opposite
        directions. Comparing every logged move with every other move
        took hours after a 100-robot, 2000-order run, and the run's
        final report waited on it. The problems found, and their order,
        are exactly those of that all-pairs scan.
        """

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
            # Sweep in start order: once a later hold starts at or after
            # this one ends, no hold after it can overlap this one.
            order = sorted(range(len(intervals)), key=lambda k: intervals[k].start)
            pairs = []

            for position, k in enumerate(order):
                held = intervals[k]

                for later in range(position + 1, len(order)):
                    q = order[later]
                    other = intervals[q]

                    if held.end is not None and held.end <= other.start:
                        break

                    if held.robot_id != other.robot_id and overlaps(
                        held.start,
                        held.end,
                        other.start,
                        other.end,
                    ):
                        pairs.append((min(k, q), max(k, q)))

            for i, j in sorted(pairs):
                first, second = intervals[i], intervals[j]

                problems.append(
                    f"cell {cell} held by {first.robot_id} "
                    f"[{float(first.start):.4f}, "
                    f"{first.end}) and {second.robot_id} "
                    f"[{float(second.start):.4f}, "
                    f"{second.end})"
                )

        # A swap needs one move each way over the same two cells, so
        # moves are grouped by their unordered cell pair and only the
        # two directions are compared. A move that stays on its cell is
        # both directions at once and is compared with its own group.
        by_edge: dict = {}

        for index, move in enumerate(self.edge_log):
            key = tuple(sorted((move.source, move.target)))
            ways = by_edge.setdefault(key, ([], []))
            ways[move.source != key[0]].append(index)

        pairs = []

        for key, (forward, backward) in by_edge.items():
            if key[0] == key[1]:
                candidates = [
                    (x, y) for n, x in enumerate(forward) for y in forward[n + 1 :]
                ]
            else:
                candidates = [(x, y) for x in forward for y in backward]

            for x, y in candidates:
                i, j = min(x, y), max(x, y)
                first, second = self.edge_log[i], self.edge_log[j]

                if first.robot_id == second.robot_id:
                    continue

                if (
                    first.source == second.target
                    and first.target == second.source
                    and overlaps(
                        first.start,
                        first.end,
                        second.start,
                        second.end,
                    )
                ):
                    pairs.append((i, j))

        for i, j in sorted(pairs):
            first, second = self.edge_log[i], self.edge_log[j]

            problems.append(
                f"edge swap {first.source}->{first.target} "
                f"by {first.robot_id} against "
                f"{second.robot_id}"
            )

        return problems
