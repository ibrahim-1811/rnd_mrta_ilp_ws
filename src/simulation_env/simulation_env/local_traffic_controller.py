"""Local batch traffic arbitration for grid robots.

This controller does not allocate orders and does not plan missions.

It only decides which simultaneous one-cell movement intentions may
proceed safely at the current simulation instant.

Temporary right-of-way rule:

1. Busy robot beats non-busy robot.
2. Smaller remaining distance to current destination wins.
3. Lower numeric robot ID breaks an exact tie.

Head-on edge swaps are never approved directly. The lower-priority
robot must locally yield before the higher-priority robot may pass.

Rotations. Robots that each want the cell the next one is leaving,
closing a loop of three or more, can all move at once: every target is
vacated in the same batch. Such a loop member wins the contention for
the cell it rotates into; the rule above decides every other
contention. Without this a robot outside a full ring could win a ring
cell, stall the ring, and so wait forever for a cell the stalled ring
can never vacate (docs/DECISIONS.md, "Rotation Right-of-Way").
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from mrta_core.grid import Cell


class TrafficDecision(str, Enum):
    MOVE = "MOVE"
    WAIT = "WAIT"
    YIELD_REQUIRED = "YIELD_REQUIRED"


@dataclass(frozen=True)
class MovementIntent:
    robot_id: str
    source: Cell
    target: Cell
    busy: bool
    remaining_distance: int


@dataclass(frozen=True)
class MovementDecision:
    robot_id: str
    decision: TrafficDecision
    reason: str


class LocalTrafficController:
    """Resolve one simultaneous batch of one-cell movement intentions."""

    @staticmethod
    def _robot_tie_key(robot_id: str):
        match = re.search(r"(\d+)$", robot_id)

        if match is not None:
            return (0, int(match.group(1)))

        return (1, robot_id)

    @classmethod
    def priority_key(cls, intent: MovementIntent):
        """Smaller tuple has higher right-of-way."""

        return (
            0 if intent.busy else 1,
            int(intent.remaining_distance),
            cls._robot_tie_key(intent.robot_id),
        )

    @classmethod
    def winner(cls, intents):
        return min(intents, key=cls.priority_key)

    @staticmethod
    def rotation_members(intents, occupancy, excluded=frozenset()) -> frozenset:
        """Robots on a closed movement loop of three or more.

        Robot r points at robot h when h holds r's target and h is in
        this batch moving out of exactly that cell. Each robot has one
        target, so each points at most once, and a loop is a set of
        robots that can all move in the same instant: every target is
        vacated by its holder. Robots in ``excluded`` (a side step is
        required of them) cannot be part of one. A robot not in the
        batch -- servicing, charging, mid-transit, stationary -- is never
        pointed at, so no loop runs through it.

        A loop of two is a head-on swap and is never a rotation.
        """

        by_id = {i.robot_id: i for i in intents if i.robot_id not in excluded}
        points_at = {}

        for intent in by_id.values():
            holder = occupancy.get(intent.target)

            if holder is None or holder == intent.robot_id or holder not in by_id:
                continue

            if by_id[holder].source != intent.target:
                continue

            points_at[intent.robot_id] = holder

        members = set()
        settled = set()

        # Walk each chain once; robots are visited in sorted order so the
        # result never depends on dict or list order.
        for start in sorted(points_at):
            if start in settled:
                continue

            position = {}
            path = []
            robot = start

            while robot in points_at and robot not in position and robot not in settled:
                position[robot] = len(path)
                path.append(robot)
                robot = points_at[robot]

            if robot in position and len(path) - position[robot] >= 3:
                members.update(path[position[robot] :])

            settled.update(path)

        return frozenset(members)

    def resolve(
        self,
        intents: list[MovementIntent],
        occupied_by: dict[Cell, str] | None = None,
    ) -> dict[str, MovementDecision]:

        if len({i.robot_id for i in intents}) != len(intents):
            raise ValueError("duplicate robot_id in movement batch")

        if len({i.source for i in intents}) != len(intents):
            raise ValueError("two robots cannot own the same source cell")

        occupancy = (
            dict(occupied_by)
            if occupied_by is not None
            else {intent.source: intent.robot_id for intent in intents}
        )

        decisions = {
            intent.robot_id: MovementDecision(
                robot_id=intent.robot_id,
                decision=TrafficDecision.MOVE,
                reason="no local conflict",
            )
            for intent in intents
        }

        # ------------------------------------------------------------
        # 1. Head-on edge swaps.
        #
        # A -> B's current cell
        # B -> A's current cell
        #
        # Neither may directly execute that swap.
        # Lower-priority robot must first find a local yield cell.
        # ------------------------------------------------------------

        handled_pairs = set()

        for a in intents:
            for b in intents:
                if a.robot_id == b.robot_id:
                    continue

                pair = tuple(sorted((a.robot_id, b.robot_id)))

                if pair in handled_pairs:
                    continue

                if a.target == b.source and b.target == a.source:
                    handled_pairs.add(pair)

                    priority_winner = self.winner((a, b))

                    yielder = b if priority_winner.robot_id == a.robot_id else a

                    decisions[yielder.robot_id] = MovementDecision(
                        robot_id=yielder.robot_id,
                        decision=TrafficDecision.YIELD_REQUIRED,
                        reason=(
                            f"head-on edge swap with "
                            f"{priority_winner.robot_id}; "
                            f"lower right-of-way robot must move aside"
                        ),
                    )

                    decisions[priority_winner.robot_id] = MovementDecision(
                        robot_id=priority_winner.robot_id,
                        decision=TrafficDecision.WAIT,
                        reason=(
                            f"waiting for {yielder.robot_id} "
                            f"to clear local head-on conflict"
                        ),
                    )

        # ------------------------------------------------------------
        # 2. Multiple robots requesting the same destination cell.
        #
        # A rotation member wins the cell it rotates into: the loop
        # can move as a whole only if it keeps every one of its cells,
        # and an outsider handed one of them could never be released
        # (its target stays held by the stalled loop). At most one
        # member can want a given cell. Every other contention goes
        # to the highest-priority robot, as before.
        # ------------------------------------------------------------

        rotating = self.rotation_members(
            intents,
            occupancy,
            excluded=frozenset(
                robot_id
                for robot_id, decision in decisions.items()
                if decision.decision is TrafficDecision.YIELD_REQUIRED
            ),
        )

        by_target: dict[Cell, list[MovementIntent]] = {}

        for intent in intents:
            if decisions[intent.robot_id].decision is TrafficDecision.YIELD_REQUIRED:
                continue

            by_target.setdefault(
                intent.target,
                [],
            ).append(intent)

        for target, contenders in by_target.items():
            if len(contenders) <= 1:
                continue

            members = [c for c in contenders if c.robot_id in rotating]

            if len(members) > 1:
                raise RuntimeError(
                    f"two rotations claim {target}: "
                    f"{sorted(m.robot_id for m in members)}"
                )

            if members:
                priority_winner = members[0]
                won = f"rotating into {target}"
            else:
                priority_winner = self.winner(contenders)
                won = f"won local contention for {target}"

            for contender in contenders:
                if contender.robot_id == priority_winner.robot_id:
                    decisions[contender.robot_id] = MovementDecision(
                        robot_id=contender.robot_id,
                        decision=TrafficDecision.MOVE,
                        reason=won,
                    )

                else:
                    decisions[contender.robot_id] = MovementDecision(
                        robot_id=contender.robot_id,
                        decision=TrafficDecision.WAIT,
                        reason=(f"yielded {target} to {priority_winner.robot_id}"),
                    )

        # ------------------------------------------------------------
        # 3. Occupancy dependency propagation.
        #
        # This is what permits:
        #
        # C -> B -> A -> free cell
        #
        # All three may move simultaneously.
        #
        # But if B is stationary, C may not enter B's cell.
        # ------------------------------------------------------------

        changed = True

        while changed:
            changed = False

            for intent in intents:
                current = decisions[intent.robot_id]

                if current.decision is not TrafficDecision.MOVE:
                    continue

                holder = occupancy.get(intent.target)

                if holder is None or holder == intent.robot_id:
                    continue

                holder_decision = decisions.get(holder)

                # Target belongs to a robot outside this movement batch.
                if holder_decision is None:
                    decisions[intent.robot_id] = MovementDecision(
                        robot_id=intent.robot_id,
                        decision=TrafficDecision.WAIT,
                        reason=(
                            f"target {intent.target} occupied by stationary {holder}"
                        ),
                    )

                    changed = True
                    continue

                # Same-direction chaining is allowed only when the
                # current holder is also approved to leave.
                if holder_decision.decision is not TrafficDecision.MOVE:
                    decisions[intent.robot_id] = MovementDecision(
                        robot_id=intent.robot_id,
                        decision=TrafficDecision.WAIT,
                        reason=(f"target {intent.target} remains occupied by {holder}"),
                    )

                    changed = True

        return decisions
