"""A bounded delivery window over an immutable timed trajectory.

The MAPF planner owns the complete trajectory and its reservations.  This
module only controls how many future waypoint actions are handed to the
executor at once.  Refilling is an in-memory deque operation: it schedules no
event, advances no clock, and changes no robot or reservation state.
"""

from collections import deque
from dataclasses import dataclass
from fractions import Fraction


MOVE_ACTIONS = frozenset(("MOVE", "RETREAT"))
DEFAULT_LOOKAHEAD_CELLS = 6
DEFAULT_REFILL_CELLS = 2


@dataclass(frozen=True)
class BufferRefill:
    """Read-only diagnostic record; it is not a simulation event."""

    time: Fraction
    consumed_actions: int
    cells_before: int
    cells_after: int
    actions_added: int


class RollingExecutionBuffer:
    """Expose a full trajectory through a rolling waypoint window.

    ``WAIT`` and ``SERVICE`` actions stay in their exact place in the deque,
    but only ``MOVE`` / ``RETREAT`` targets count towards the cell lookahead.
    The current action remains at the front until it completes, so a refill is
    triggered immediately after a completion leaves at most two future cells.
    """

    def __init__(
        self,
        trajectory,
        lookahead_cells: int = DEFAULT_LOOKAHEAD_CELLS,
        refill_cells: int = DEFAULT_REFILL_CELLS,
        now: Fraction = Fraction(0),
    ) -> None:
        if lookahead_cells <= 0:
            raise ValueError("lookahead_cells must be positive")
        if refill_cells < 0 or refill_cells >= lookahead_cells:
            raise ValueError(
                "refill_cells must be non-negative and below lookahead_cells"
            )

        self.trajectory = trajectory
        self.lookahead_cells = lookahead_cells
        self.refill_cells = refill_cells

        self._actions = deque()
        self._next_unbuffered = 0
        self._consumed_actions = 0
        self.refill_log: list[BufferRefill] = []

        self._refill(now, force=True)

    @property
    def current_action(self):
        return self._actions[0] if self._actions else None

    @property
    def buffered_cells(self) -> int:
        return sum(
            action.kind in MOVE_ACTIONS for action in self._actions
        )

    @property
    def buffered_actions(self) -> int:
        return len(self._actions)

    @property
    def consumed_actions(self) -> int:
        return self._consumed_actions

    @property
    def exhausted(self) -> bool:
        return (
            not self._actions
            and self._next_unbuffered >= len(self.trajectory.actions)
        )

    def complete_current(self, now: Fraction) -> None:
        """Consume one finished action, then refill synchronously."""

        if not self._actions:
            raise RuntimeError("execution buffer has no current action")

        self._actions.popleft()
        self._consumed_actions += 1
        self._refill(now)

    def _refill(self, now: Fraction, force: bool = False) -> bool:
        before = self.buffered_cells

        if not force and before > self.refill_cells:
            return False

        added = 0
        source = self.trajectory.actions

        while (
            self._next_unbuffered < len(source)
            and self.buffered_cells < self.lookahead_cells
        ):
            self._actions.append(source[self._next_unbuffered])
            self._next_unbuffered += 1
            added += 1

        if added:
            self.refill_log.append(
                BufferRefill(
                    time=now,
                    consumed_actions=self._consumed_actions,
                    cells_before=before,
                    cells_after=self.buffered_cells,
                    actions_added=added,
                )
            )

        return bool(added)
