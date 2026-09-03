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

    def resolve(
        self,
        intents: list[MovementIntent],
        occupied_by: dict[Cell, str] | None = None,
    ) -> dict[str, MovementDecision]:

        if len({i.robot_id for i in intents}) != len(intents):
            raise ValueError("duplicate robot_id in movement batch")

        if len({i.source for i in intents}) != len(intents):
            raise ValueError("two robots cannot own the same source cell")

        by_id = {
            intent.robot_id: intent
            for intent in intents
        }

        occupancy = (
            dict(occupied_by)
            if occupied_by is not None
            else {
                intent.source: intent.robot_id
                for intent in intents
            }
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

                if (
                    a.target == b.source
                    and b.target == a.source
                ):
                    handled_pairs.add(pair)

                    priority_winner = self.winner((a, b))

                    yielder = (
                        b
                        if priority_winner.robot_id == a.robot_id
                        else a
                    )

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
        # The highest-priority robot wins if the cell can actually
        # become available in this batch.
        # ------------------------------------------------------------

        by_target: dict[Cell, list[MovementIntent]] = {}

        for intent in intents:
            if (
                decisions[intent.robot_id].decision
                is TrafficDecision.YIELD_REQUIRED
            ):
                continue

            by_target.setdefault(
                intent.target,
                [],
            ).append(intent)

        for target, contenders in by_target.items():

            if len(contenders) <= 1:
                continue

            priority_winner = self.winner(contenders)

            for contender in contenders:

                if contender.robot_id == priority_winner.robot_id:
                    decisions[contender.robot_id] = MovementDecision(
                        robot_id=contender.robot_id,
                        decision=TrafficDecision.MOVE,
                        reason=(
                            f"won local contention for {target}"
                        ),
                    )

                else:
                    decisions[contender.robot_id] = MovementDecision(
                        robot_id=contender.robot_id,
                        decision=TrafficDecision.WAIT,
                        reason=(
                            f"yielded {target} to "
                            f"{priority_winner.robot_id}"
                        ),
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
                            f"target {intent.target} occupied by "
                            f"stationary {holder}"
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
                        reason=(
                            f"target {intent.target} remains occupied "
                            f"by {holder}"
                        ),
                    )

                    changed = True

        return decisions
