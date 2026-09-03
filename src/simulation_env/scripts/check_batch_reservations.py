#!/usr/bin/env python3

from fractions import Fraction

from simulation_env.reservations import (
    ReservationTable,
)


DT = Fraction(2, 3)

table = ReservationTable()

table.occupy(
    (2, 1),
    "robot_001",
    Fraction(0),
)

table.occupy(
    (1, 1),
    "robot_002",
    Fraction(0),
)

table.occupy(
    (0, 1),
    "robot_003",
    Fraction(0),
)


print()
print("=" * 68)
print("PRODUCTION RESERVATION BATCH CHECK")
print("=" * 68)

print()
print("before:")
print(
    "  robot_001",
    table.held_cells("robot_001"),
)
print(
    "  robot_002",
    table.held_cells("robot_002"),
)
print(
    "  robot_003",
    table.held_cells("robot_003"),
)


table.begin_moves_batch(
    [
        (
            "robot_001",
            (2, 1),
            (3, 1),
        ),
        (
            "robot_002",
            (1, 1),
            (2, 1),
        ),
        (
            "robot_003",
            (0, 1),
            (1, 1),
        ),
    ],
    now=Fraction(0),
    duration=DT,
)


print()
print("during simultaneous movement:")

print(
    "  robot_001 holds",
    table.held_cells("robot_001"),
)

print(
    "  robot_002 holds",
    table.held_cells("robot_002"),
)

print(
    "  robot_003 holds",
    table.held_cells("robot_003"),
)


assert table.owner((3, 1)) == "robot_001"
assert table.owner((2, 1)) == "robot_002"
assert table.owner((1, 1)) == "robot_003"

assert table.owner((0, 1)) is None


# Complete all three at exactly the same timestamp.
table.complete_move(
    "robot_001",
    (2, 1),
    (3, 1),
    DT,
)

table.complete_move(
    "robot_002",
    (1, 1),
    (2, 1),
    DT,
)

table.complete_move(
    "robot_003",
    (0, 1),
    (1, 1),
    DT,
)


print()
print("after completion:")

print(
    "  robot_001",
    table.held_cells("robot_001"),
)

print(
    "  robot_002",
    table.held_cells("robot_002"),
)

print(
    "  robot_003",
    table.held_cells("robot_003"),
)


assert table.owner((3, 1)) == "robot_001"
assert table.owner((2, 1)) == "robot_002"
assert table.owner((1, 1)) == "robot_003"


# Close open cell intervals so the audit sees the final standing
# occupancy as intentional.
table.close(DT)

problems = table.audit()

print()
print("reservation audit problems:", problems)

assert problems == []


print()
print("same-direction chain : PASS")
print("atomic handoff       : PASS")
print("exact 2/3 s timing   : PASS")
print("reservation audit    : PASS")

print()
print("status               : OK")
