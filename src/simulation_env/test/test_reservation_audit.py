"""The reservation audit finds every conflict, and finishes on a long run.

The audit re-derives the traffic guarantees after a run: no two robots
hold one cell at once, and no two robots swap across one edge. It used
to compare every logged move with every other move. After 100 robots
and 2000 orders that is about 240,000 moves and hours of work, and the
final report waited on it, so a finished run printed nothing.

all_pairs_audit() below is that original scan, kept as the reference:
the audit must report exactly what it reports, in the same order.
"""

import random
from fractions import Fraction
from time import perf_counter

from simulation_env.reservations import (
    CellInterval,
    EdgeInterval,
    ReservationTable,
)


def overlaps(a_start, a_end, b_start, b_end) -> bool:
    if a_end is not None and a_end <= b_start:
        return False

    if b_end is not None and b_end <= a_start:
        return False

    return True


def all_pairs_audit(cell_log, edge_log) -> list:
    problems = []
    by_cell: dict = {}

    for interval in cell_log:
        by_cell.setdefault(interval.cell, []).append(interval)

    for cell, intervals in by_cell.items():
        for i, first in enumerate(intervals):
            for second in intervals[i + 1 :]:
                if first.robot_id != second.robot_id and overlaps(
                    first.start, first.end, second.start, second.end
                ):
                    problems.append(
                        f"cell {cell} held by {first.robot_id} "
                        f"[{float(first.start):.4f}, "
                        f"{first.end}) and {second.robot_id} "
                        f"[{float(second.start):.4f}, "
                        f"{second.end})"
                    )

    for i, first in enumerate(edge_log):
        for second in edge_log[i + 1 :]:
            if (
                first.robot_id != second.robot_id
                and first.source == second.target
                and first.target == second.source
                and overlaps(first.start, first.end, second.start, second.end)
            ):
                problems.append(
                    f"edge swap {first.source}->{first.target} "
                    f"by {first.robot_id} against "
                    f"{second.robot_id}"
                )

    return problems


def random_logs(rng):
    """A tiny world where overlaps and swaps are common on purpose."""

    cells = [(x, y) for x in range(3) for y in range(2)]

    def start():
        return Fraction(rng.randint(0, 12), rng.choice((1, 2, 3)))

    def robot():
        return f"robot_{rng.randint(1, 4):03d}"

    cell_log = []

    for _ in range(rng.randint(0, 25)):
        begin = start()
        end = None if rng.random() < 0.1 else begin + Fraction(rng.randint(0, 4), 2)
        cell_log.append(CellInterval(rng.choice(cells), robot(), begin, end))

    edge_log = []

    for _ in range(rng.randint(0, 25)):
        source = rng.choice(cells)
        neighbours = [
            cell
            for cell in cells
            if abs(cell[0] - source[0]) + abs(cell[1] - source[1]) == 1
        ]
        target = source if rng.random() < 0.15 else rng.choice(neighbours)
        begin = start()
        edge_log.append(
            EdgeInterval(
                source, target, robot(), begin, begin + Fraction(rng.randint(0, 3), 2)
            )
        )

    return cell_log, edge_log


def test_audit_reports_exactly_what_the_all_pairs_scan_reports():
    rng = random.Random(7)
    found = 0

    for _ in range(300):
        cell_log, edge_log = random_logs(rng)
        table = ReservationTable(cell_log=cell_log, edge_log=edge_log)
        expected = all_pairs_audit(cell_log, edge_log)

        assert table.audit() == expected

        found += len(expected)

    # The comparison only means something if conflicts were present.
    assert found > 500


def test_touching_intervals_are_not_a_conflict():
    table = ReservationTable(
        cell_log=[
            CellInterval((0, 0), "robot_001", Fraction(0), Fraction(1)),
            CellInterval((0, 0), "robot_002", Fraction(1), Fraction(2)),
        ],
        edge_log=[
            EdgeInterval((0, 0), (1, 0), "robot_001", Fraction(0), Fraction(1)),
            EdgeInterval((1, 0), (0, 0), "robot_002", Fraction(1), Fraction(2)),
        ],
    )

    assert table.audit() == []


def test_a_swap_and_a_shared_cell_are_reported():
    table = ReservationTable(
        cell_log=[
            CellInterval((0, 0), "robot_001", Fraction(0), Fraction(2)),
            CellInterval((0, 0), "robot_002", Fraction(1), None),
        ],
        edge_log=[
            EdgeInterval((0, 0), (1, 0), "robot_001", Fraction(0), Fraction(2)),
            EdgeInterval((1, 0), (0, 0), "robot_002", Fraction(1), Fraction(3)),
        ],
    )

    assert table.audit() == [
        "cell (0, 0) held by robot_001 [0.0000, 2) and robot_002 [1.0000, None)",
        "edge swap (0, 0)->(1, 0) by robot_001 against robot_002",
    ]


def test_a_long_run_is_audited_in_seconds():
    """40,000 one-way moves: the all-pairs scan needs minutes."""

    cell_log = []
    edge_log = []
    length = 200

    for lap in range(200):
        robot = f"robot_{lap % 100:03d}"

        for step in range(length):
            begin = Fraction(lap * length + step)
            source, target = (step, 0), (step + 1, 0)
            edge_log.append(EdgeInterval(source, target, robot, begin, begin + 1))
            cell_log.append(CellInterval(source, robot, begin, begin + 1))

    table = ReservationTable(cell_log=cell_log, edge_log=edge_log)

    started = perf_counter()
    problems = table.audit()

    assert problems == []
    assert perf_counter() - started < 5.0
