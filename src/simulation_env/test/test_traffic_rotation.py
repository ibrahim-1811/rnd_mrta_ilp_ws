"""Rotation right-of-way: a full ring moves as one, never deadlocks.

A loop of robots, each wanting the cell the next one is leaving, can
all move in the same instant. Before this rule the controller could
hand one ring cell to a higher-priority robot from outside the ring;
then the ring could not move, so the outsider's target was never
vacated, and the identical decision repeated forever (W100, 6-cell
ring; V2, 4-cell rotary). docs/DECISIONS.md, "Rotation Right-of-Way".

What is pinned here:

  * a 4-cell and a 6-cell ring rotate, even against a higher-priority
    outsider, and the outsider waits
  * two independent rings rotate in one batch
  * contention outside any loop is decided exactly as before
  * a ring with a robot not moving in this batch never rotates
  * a two-robot swap is still a head-on conflict, not a rotation
  * the reservation table accepts a valid rotation and rejects an
    invalid one: it stays the authority on safety
"""

import random
from fractions import Fraction

import pytest

from simulation_env.local_traffic_controller import (
    LocalTrafficController,
    MovementIntent,
    TrafficDecision,
)
from simulation_env.reservations import ReservationTable


MOVE = TrafficDecision.MOVE
WAIT = TrafficDecision.WAIT


def intent(robot_id, source, target, remaining=50, busy=True):
    return MovementIntent(
        robot_id=robot_id,
        source=source,
        target=target,
        busy=busy,
        remaining_distance=remaining,
    )


def ring(cells, prefix, remaining=50):
    """One robot per cell, each wanting the next cell round the ring."""

    return [
        intent(f"{prefix}{i:02d}", cell, cells[(i + 1) % len(cells)], remaining)
        for i, cell in enumerate(cells)
    ]


# The 4-cell V2 rotary and the 6-cell W100 ring that deadlocked.
SQUARE = [(13, 3), (14, 3), (14, 4), (13, 4)]
RING_6 = [(4, 18), (3, 18), (2, 18), (2, 19), (3, 19), (4, 19)]


def resolve(intents, extra_occupied=None):
    occupied = {i.source: i.robot_id for i in intents}
    occupied.update(extra_occupied or {})
    return LocalTrafficController().resolve(intents, occupied_by=occupied)


@pytest.mark.parametrize("cells", [SQUARE, RING_6], ids=["4-cell", "6-cell"])
def test_a_full_ring_rotates(cells):
    decisions = resolve(ring(cells, "robot_0"))

    assert {d.decision for d in decisions.values()} == {MOVE}


@pytest.mark.parametrize("cells", [SQUARE, RING_6], ids=["4-cell", "6-cell"])
def test_a_ring_member_beats_a_higher_priority_outsider(cells):
    """The W100 stall, reduced: an outsider closer to its goal wants the
    cell a ring member rotates into."""

    members = ring(cells, "robot_0", remaining=55)
    contested = cells[1]
    outsider = intent("robot_066", (contested[0] + 10, contested[1] + 10), contested, remaining=21)

    # The plain priority rule would hand the cell to the outsider.
    assert LocalTrafficController.winner([members[0], outsider]) == outsider

    decisions = resolve([*members, outsider])

    assert all(decisions[m.robot_id].decision is MOVE for m in members)
    assert decisions["robot_066"].decision is WAIT
    assert decisions[members[0].robot_id].reason == f"rotating into {contested}"


def test_the_traced_w100_stall_now_rotates():
    """The eight intents traced at t=450.67 (hungarian, 100 robots)."""

    intents = [
        intent("robot_005", (3, 19), (4, 19), 55),
        intent("robot_022", (4, 19), (4, 18), 19),
        intent("robot_028", (3, 18), (2, 18), 10),
        intent("robot_031", (2, 19), (3, 19), 26),
        intent("robot_042", (5, 18), (4, 18), 21),
        intent("robot_066", (4, 20), (4, 19), 21),
        intent("robot_082", (2, 18), (2, 19), 14),
        intent("robot_093", (4, 18), (3, 18), 23),
    ]
    decisions = resolve(intents)

    ring_ids = {"robot_005", "robot_022", "robot_028", "robot_031", "robot_082", "robot_093"}

    assert {r for r, d in decisions.items() if d.decision is MOVE} == ring_ids
    assert decisions["robot_042"].decision is WAIT
    assert decisions["robot_066"].decision is WAIT


def test_two_independent_rings_rotate_in_one_batch():
    west = ring(RING_6, "robot_1")
    east = ring([(x + 3, y) for x, y in RING_6], "robot_2")
    outsider = intent("robot_300", (9, 17), east[0].target, remaining=1)

    decisions = resolve([*west, *east, outsider])

    assert all(decisions[i.robot_id].decision is MOVE for i in (*west, *east))
    assert decisions["robot_300"].decision is WAIT


def test_contention_outside_any_loop_is_unchanged():
    """A chain C -> B -> A -> free cell, and an outsider for that cell.

    No loop, so the plain rule decides: the outsider (closer) wins and
    the whole chain waits, exactly as before the rotation rule.
    """

    chain = [
        intent("robot_003", (0, 0), (1, 0), 40),
        intent("robot_002", (1, 0), (2, 0), 40),
        intent("robot_001", (2, 0), (3, 0), 40),
    ]
    outsider = intent("robot_009", (3, 1), (3, 0), 5)

    decisions = resolve([*chain, outsider])

    assert decisions["robot_009"].decision is MOVE
    assert decisions["robot_009"].reason == "won local contention for (3, 0)"
    assert all(decisions[i.robot_id].decision is WAIT for i in chain)

    # And with the chain ahead on priority, the chain moves as one.
    decisions = resolve([*chain, intent("robot_009", (3, 1), (3, 0), 90)])

    assert all(decisions[i.robot_id].decision is MOVE for i in chain)
    assert decisions["robot_009"].decision is WAIT


@pytest.mark.parametrize("cells", [SQUARE, RING_6], ids=["4-cell", "6-cell"])
def test_a_ring_with_a_robot_not_moving_never_rotates(cells):
    """One ring cell is held by a robot outside the batch: servicing,
    charging, mid-transit or otherwise stationary. Nobody may enter its
    cell, so nothing in the ring moves."""

    members = ring(cells, "robot_0")
    parked = members.pop()

    decisions = resolve(members, extra_occupied={parked.source: parked.robot_id})

    assert all(d.decision is WAIT for d in decisions.values())
    assert LocalTrafficController.rotation_members(
        members, {m.source: m.robot_id for m in members} | {parked.source: parked.robot_id}
    ) == frozenset()


def test_a_robot_required_to_side_step_is_never_a_member():
    members = ring(RING_6, "robot_0")
    occupancy = {m.source: m.robot_id for m in members}

    assert LocalTrafficController.rotation_members(members, occupancy) == frozenset(
        m.robot_id for m in members
    )
    assert (
        LocalTrafficController.rotation_members(
            members, occupancy, excluded=frozenset({members[2].robot_id})
        )
        == frozenset()
    )


def test_a_holder_leaving_another_cell_does_not_close_a_loop():
    """A robot holding two cells (mid-transit) is not vacating the one
    asked for, so a loop through it is not a rotation."""

    members = ring(SQUARE, "robot_0")
    occupancy = {m.source: m.robot_id for m in members}
    # robot_001 also holds a second cell it is not leaving.
    moved = intent(members[1].robot_id, (20, 20), members[1].target)

    assert (
        LocalTrafficController.rotation_members(
            [members[0], moved, members[2], members[3]], occupancy
        )
        == frozenset()
    )


def test_a_two_robot_swap_is_still_a_head_on_conflict():
    a = intent("robot_001", (5, 5), (6, 5), 10)
    b = intent("robot_002", (6, 5), (5, 5), 20)

    decisions = resolve([a, b])

    assert decisions["robot_002"].decision is TrafficDecision.YIELD_REQUIRED
    assert decisions["robot_001"].decision is WAIT


def test_decisions_do_not_depend_on_intent_order():
    intents = [*ring(RING_6, "robot_0", 55), intent("robot_066", (4, 20), (4, 19), 21)]
    expected = resolve(intents)

    for seed in range(10):
        shuffled = intents[:]
        random.Random(seed).shuffle(shuffled)

        assert resolve(shuffled) == expected


# -- the reservation table stays the authority --------------------------


def table_with(cells, robots):
    table = ReservationTable()

    for cell, robot_id in zip(cells, robots):
        table.occupy(cell, robot_id, Fraction(0))

    return table


@pytest.mark.parametrize("cells", [SQUARE, RING_6], ids=["4-cell", "6-cell"])
def test_the_reservation_table_accepts_a_valid_rotation(cells):
    robots = [f"robot_{i:03d}" for i in range(len(cells))]
    table = table_with(cells, robots)
    moves = [(r, cells[i], cells[(i + 1) % len(cells)]) for i, r in enumerate(robots)]

    table.begin_moves_batch(moves, now=Fraction(0), duration=Fraction(2, 3))

    for robot_id, source, target in moves:
        table.complete_move(robot_id, source, target, Fraction(2, 3))

    assert table.audit() == []

    for robot_id, _source, target in moves:
        assert table.owner(target) == robot_id


def test_the_reservation_table_rejects_a_rotation_through_a_stationary_robot():
    robots = [f"robot_{i:03d}" for i in range(len(SQUARE))]
    table = table_with(SQUARE, robots)
    moves = [(r, SQUARE[i], SQUARE[(i + 1) % 4]) for i, r in enumerate(robots)][:-1]

    with pytest.raises(RuntimeError, match="stationary robot_003 still owns it"):
        table.begin_moves_batch(moves, now=Fraction(0), duration=Fraction(2, 3))


def test_the_reservation_table_rejects_a_two_robot_swap():
    table = table_with([(0, 0), (1, 0)], ["robot_001", "robot_002"])

    with pytest.raises(RuntimeError, match="head-on edge swap rejected"):
        table.begin_moves_batch(
            [("robot_001", (0, 0), (1, 0)), ("robot_002", (1, 0), (0, 0))],
            now=Fraction(0),
            duration=Fraction(2, 3),
        )
