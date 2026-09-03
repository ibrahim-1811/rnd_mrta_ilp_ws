#!/usr/bin/env python3

from simulation_env.local_traffic_controller import (
    LocalTrafficController,
    MovementIntent,
    TrafficDecision,
)


controller = LocalTrafficController()


def show(title, intents, expected):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)

    decisions = controller.resolve(intents)

    for intent in intents:
        result = decisions[intent.robot_id]

        print(
            f"{intent.robot_id:10s} "
            f"{intent.source} -> {intent.target}   "
            f"{result.decision.value:14s}   "
            f"{result.reason}"
        )

    actual = {
        robot_id: result.decision
        for robot_id, result in decisions.items()
    }

    if actual != expected:
        print()
        print("status: FAILED")
        print("expected:", expected)
        print("actual  :", actual)
        raise SystemExit(1)

    print()
    print("status: OK")


# =====================================================================
# CASE 1
#
# Same-direction chain.
#
# robot_003 -> robot_002 -> robot_001 -> free cell
#
# All robots should move together.
# =====================================================================

show(
    "CASE 1: SAME-DIRECTION CHAIN",
    [
        MovementIntent(
            robot_id="robot_001",
            source=(2, 1),
            target=(3, 1),
            busy=True,
            remaining_distance=3,
        ),
        MovementIntent(
            robot_id="robot_002",
            source=(1, 1),
            target=(2, 1),
            busy=True,
            remaining_distance=4,
        ),
        MovementIntent(
            robot_id="robot_003",
            source=(0, 1),
            target=(1, 1),
            busy=True,
            remaining_distance=5,
        ),
    ],
    {
        "robot_001": TrafficDecision.MOVE,
        "robot_002": TrafficDecision.MOVE,
        "robot_003": TrafficDecision.MOVE,
    },
)


# =====================================================================
# CASE 2
#
# Two robots want the same free cell.
#
# Both busy.
# robot_001 is closer to destination.
# robot_001 wins.
# =====================================================================

show(
    "CASE 2: SAME-CELL CONFLICT",
    [
        MovementIntent(
            robot_id="robot_001",
            source=(2, 1),
            target=(3, 1),
            busy=True,
            remaining_distance=2,
        ),
        MovementIntent(
            robot_id="robot_002",
            source=(3, 2),
            target=(3, 1),
            busy=True,
            remaining_distance=5,
        ),
    ],
    {
        "robot_001": TrafficDecision.MOVE,
        "robot_002": TrafficDecision.WAIT,
    },
)


# =====================================================================
# CASE 3
#
# Head-on edge swap.
#
# robot_001 wants robot_002's current cell.
# robot_002 wants robot_001's current cell.
#
# Both are equally busy and equally far from destination.
# Lower robot ID has right-of-way.
#
# But robot_001 still CANNOT move into robot_002's occupied cell.
# robot_002 must first find a local yield cell.
# =====================================================================

show(
    "CASE 3: HEAD-ON EDGE SWAP",
    [
        MovementIntent(
            robot_id="robot_001",
            source=(2, 1),
            target=(3, 1),
            busy=True,
            remaining_distance=4,
        ),
        MovementIntent(
            robot_id="robot_002",
            source=(3, 1),
            target=(2, 1),
            busy=True,
            remaining_distance=4,
        ),
    ],
    {
        "robot_001": TrafficDecision.WAIT,
        "robot_002": TrafficDecision.YIELD_REQUIRED,
    },
)


print()
print("=" * 72)
print("LOCAL TRAFFIC CONTROLLER VALIDATION")
print("=" * 72)
print("same-direction chain : PASS")
print("same-cell conflict   : PASS")
print("head-on conflict     : PASS")
print()
print("status               : OK")
