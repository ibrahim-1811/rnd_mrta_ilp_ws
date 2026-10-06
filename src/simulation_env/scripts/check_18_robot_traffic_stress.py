#!/usr/bin/env python3

from dataclasses import replace
from itertools import combinations

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import plan_from_cell

from simulation_env.fleet import (
    home_bank,
    build_fleet,
    home_stations,
)
from simulation_env.fleet_engine import (
    FleetSimulationEngine,
)


CONFIG = "src/simulation_env/config/warehouse_sim.yaml"


def synchronous_conflict_score(plans):
    """Static score used only to select a difficult test scenario.

    This does not control execution.

    Score:
      +1 simultaneous same-cell request
      +2 direct opposite-edge request

    The real FleetSimulationEngine remains the authority.
    """

    paths = {
        robot_id: list(plan.path)
        for robot_id, plan in plans.items()
    }

    horizon = max(
        len(path)
        for path in paths.values()
    )

    vertex = 0
    edge = 0

    for tick in range(1, horizon):

        previous = {}
        current = {}

        for robot_id, path in paths.items():

            previous[robot_id] = path[
                min(tick - 1, len(path) - 1)
            ]

            current[robot_id] = path[
                min(tick, len(path) - 1)
            ]

        for a, b in combinations(
            sorted(paths),
            2,
        ):

            if current[a] == current[b]:
                vertex += 1

            if (
                previous[a] == current[b]
                and previous[b] == current[a]
                and previous[a] != previous[b]
            ):
                edge += 1

    return vertex + 2 * edge, vertex, edge


config = load_sim_config(CONFIG)
warehouse = load_warehouse_xlsx(config.map_path)

# Maximum density: one robot on every home cell the map provides,
# independent of fleet.robot_count. A traffic stress test wants the
# busiest fleet the warehouse can hold, and pinning it to a literal
# meant widening the home bank broke the test rather than exercising
# it.
FLEET_SIZE = len(home_bank(warehouse))

if FLEET_SIZE < 2:
    raise RuntimeError(
        f"traffic stress needs at least 2 home cells, "
        f"map provides {FLEET_SIZE}"
    )

robots = build_fleet(
    replace(config, robot_count=FLEET_SIZE),
    warehouse,
)

homes = home_stations(
    replace(config, robot_count=FLEET_SIZE),
    warehouse,
)

robot_ids = sorted(
    robot.robot_id
    for robot in robots
)

home_zones = [
    homes[robot_id]
    for robot_id in robot_ids
]


# -----------------------------------------------------------------
# Deterministic candidate target permutations.
#
# No random stress scenario. Every run should select the same case.
# -----------------------------------------------------------------

n = len(robot_ids)

permutations = []

for shift in (
    1,
    2,
    3,
    4,
    5,
    7,
    9,
):
    permutations.append(
        (
            f"rotate_{shift}",
            home_zones[shift:]
            + home_zones[:shift],
        )
    )

reversed_homes = list(
    reversed(home_zones)
)

permutations.append(
    (
        "reverse",
        reversed_homes,
    )
)

for shift in (
    1,
    3,
    5,
):
    targets = (
        reversed_homes[shift:]
        + reversed_homes[:shift]
    )

    permutations.append(
        (
            f"reverse_rotate_{shift}",
            targets,
        )
    )


# Deterministic mid-mission pick targets.
#
# A stride that is coprime with the shelf count spreads the robots
# over the whole racking instead of clustering them in one aisle.
SHELF_IDS = [
    zone.zone_id
    for zone in warehouse.zones_of_type(ZoneType.SHELF)
]

SHELF_STRIDE = 23


best = None


for name, targets in permutations:

    missions = {}
    valid = True

    for index, (robot_id, target_zone) in enumerate(
        zip(
            robot_ids,
            targets,
        )
    ):

        robot = next(
            robot
            for robot in robots
            if robot.robot_id == robot_id
        )

        if target_zone == homes[robot_id]:
            valid = False
            break

        # Park-to-park moves alone stopped contending once the aisles
        # became one-way: robots simply spread out along lanes running
        # the same way and never met. A pick leg in the middle of the
        # warehouse puts them back across each other's routes, which is
        # what this test exists to stress.
        cross_shelf = SHELF_IDS[
            (index * SHELF_STRIDE) % len(SHELF_IDS)
        ]

        try:

            missions[robot_id] = plan_from_cell(
                warehouse,
                robot.cell,
                [cross_shelf, target_zone],
                start_label=robot_id,
            )

        except (RuntimeError, ValueError):

            valid = False
            break

    if not valid:
        continue

    score, vertex, edge = (
        synchronous_conflict_score(
            missions
        )
    )

    candidate = (
        score,
        vertex,
        edge,
        name,
        targets,
        missions,
    )

    if best is None or candidate[:4] > best[:4]:
        best = candidate


if best is None:
    raise RuntimeError(
        f"could not construct a valid {FLEET_SIZE}-robot traffic scenario"
    )


(
    predicted_score,
    predicted_vertex,
    predicted_edge,
    scenario,
    targets,
    missions,
) = best


print()
print("=" * 76)
print(f"{FLEET_SIZE} ROBOT REAL WAREHOUSE TRAFFIC STRESS TEST")
print("=" * 76)

print()
print(f"scenario                    : {scenario}")
print(f"robots                      : {len(robots)}")
print(f"predicted interaction score : {predicted_score}")
print(f"predicted vertex conflicts  : {predicted_vertex}")
print(f"predicted edge swaps        : {predicted_edge}")


print()
print("missions:")

for robot_id, target_zone in zip(
    robot_ids,
    targets,
):

    plan = missions[robot_id]

    print(
        f"  {robot_id}  "
        f"{plan.path[0]} -> {target_zone:12s} "
        f"distance={plan.total_distance_m:5.1f} m"
    )


# -----------------------------------------------------------------
# Actual production engine.
# -----------------------------------------------------------------

engine = FleetSimulationEngine(
    warehouse=warehouse,
    resolution=config.resolution_m,
    service_seconds={
        ZoneType.SHELF: config.shelf_pick_s,
        ZoneType.DROPOFF: config.dropoff_s,
    },
)


for robot in robots:

    robot.speed_mps = config.speed_mps

    engine.add_robot(
        robot
    )


started = engine.assign_missions_batch(
    missions,
    parking=set(missions),
)


print()
print("initially moving             :", len(started))
print("initially blocked/yielding   :", FLEET_SIZE - len(started))


try:

    engine.run()

except Exception as exc:

    print()
    print("STRESS TEST FAILED DURING EXECUTION")
    print(type(exc).__name__, ":", exc)

    print()
    print("robot states:")

    for robot_id in robot_ids:

        robot = engine.robots[robot_id]

        print(
            f"  {robot_id} "
            f"cell={robot.cell} "
            f"status={robot.status.value} "
            f"wait={robot.waiting_time_s:.3f} "
            f"vertex={robot.vertex_conflict_count} "
            f"edge={robot.edge_conflict_count}"
        )

    print()
    print("=" * 76)
    print("DEADLOCK DEPENDENCY DIAGNOSTICS")
    print("=" * 76)

    print()
    print("WAITING ROBOTS")

    wait_for = {}

    for robot_id in sorted(engine._waiting):

        robot = engine.robots[robot_id]

        if robot.finished:
            target = None
            holder = None
        else:
            target = robot.path[
                robot.path_index + 1
            ]
            holder = engine.reservations.owner(
                target
            )

        kind, detail, since = engine._waiting[
            robot_id
        ]

        wait_for[robot_id] = holder

        print(
            f"  {robot_id} "
            f"cell={robot.cell} "
            f"next={target} "
            f"blocked_by={holder} "
            f"kind={kind.value} "
            f"waiting_since={float(since):.3f}"
        )

    print()
    print("ACTIVE YIELD STATES")

    if not engine._yielding:
        print("  none")

    for robot_id in sorted(engine._yielding):

        state = engine._yielding[robot_id]

        robot = engine.robots[robot_id]
        winner = engine.robots.get(
            state.winner_id
        )

        winner_next = None

        if (
            winner is not None
            and not winner.finished
        ):
            winner_next = winner.path[
                winner.path_index + 1
            ]

        print(
            f"  {robot_id} "
            f"yield_cell={robot.cell} "
            f"corridor={state.corridor_cell} "
            f"winner={state.winner_id} "
            f"winner_cell={None if winner is None else winner.cell} "
            f"winner_next={winner_next} "
            f"wait_since={None if state.wait_since is None else float(state.wait_since):}"
        )

    print()
    print("WAIT-FOR EDGES")

    for robot_id in sorted(wait_for):

        holder = wait_for[robot_id]

        print(
            f"  {robot_id} -> "
            f"{holder if holder is not None else 'FREE'}"
        )

    # ---------------------------------------------------------
    # Detect closed cycles among robots currently in _waiting.
    # ---------------------------------------------------------

    cycles = []
    canonical = set()

    for start_robot in sorted(wait_for):

        order = []
        positions = {}

        current = start_robot

        while current is not None:

            if current in positions:

                cycle = order[
                    positions[current]:
                ]

                if cycle:
                    rotations = [
                        tuple(
                            cycle[i:]
                            + cycle[:i]
                        )
                        for i in range(len(cycle))
                    ]

                    key = min(rotations)

                    if key not in canonical:
                        canonical.add(key)
                        cycles.append(cycle)

                break

            if current not in wait_for:
                break

            positions[current] = len(order)
            order.append(current)

            current = wait_for[current]

    print()
    print("CLOSED WAIT CYCLES")

    if not cycles:
        print("  none")

    for index, cycle in enumerate(
        cycles,
        start=1,
    ):
        print(
            f"  cycle {index}: "
            + " -> ".join(
                cycle + [cycle[0]]
            )
        )

    print()
    print(
        f"_waiting count  : "
        f"{len(engine._waiting)}"
    )

    print(
        f"_yielding count : "
        f"{len(engine._yielding)}"
    )

    raise


# -----------------------------------------------------------------
# Results.
# -----------------------------------------------------------------

total_wait = sum(
    robot.waiting_time_s
    for robot in robots
)

wait_events = sum(
    robot.wait_event_count
    for robot in robots
)

vertex_conflicts = sum(
    robot.vertex_conflict_count
    for robot in robots
)

edge_conflicts = sum(
    robot.edge_conflict_count
    for robot in robots
)

distance = sum(
    robot.distance_m
    for robot in robots
)

parked = len(
    engine.park_completion
)

problems = engine.reservations.audit()


print()
print("=" * 76)
print("RESULT")
print("=" * 76)

print(f"completed missions       : {parked}/{FLEET_SIZE}")
print(f"makespan                 : {engine.sim_time:.3f} s")
print(f"distance                 : {distance:.1f} m")
print(f"realized waiting         : {total_wait:.3f} s")
print(f"wait events              : {wait_events}")
print(f"vertex conflict counts   : {vertex_conflicts}")
print(f"edge conflict counts     : {edge_conflicts}")
print(f"reservation problems     : {len(problems)}")
print(f"unfinished yield states  : {len(engine._yielding)}")
print(f"unfinished wait states   : {len(engine._waiting)}")


# -----------------------------------------------------------------
# Acceptance checks.
# -----------------------------------------------------------------

assert parked == FLEET_SIZE, (
    f"only {parked}/{FLEET_SIZE} missions completed"
)

assert problems == [], problems

assert not engine._yielding, (
    f"unfinished yields: {engine._yielding}"
)

assert not engine._waiting, (
    f"unfinished waiting robots: {engine._waiting}"
)

final_cells = [
    robot.cell
    for robot in robots
]

assert len(set(final_cells)) == FLEET_SIZE, (
    "two robots finished on the same cell"
)


# This is intended as a traffic stress test. A scenario with no actual
# interaction is not useful even if every mission technically succeeds.
assert (
    wait_events > 0
    or vertex_conflicts > 0
), (
    "scenario completed without exercising local traffic arbitration"
)

# Congestion is supposed to survive; head-on is not.
#
# On the directed warehouse an edge conflict cannot be produced by any
# schedule, because no pair of adjacent transit cells is legal in both
# directions. A non-zero count here means a two-way core edge crept
# back in, so fail loudly rather than quietly regressing to the old
# deadlocking topology.
assert edge_conflicts == 0, (
    f"{edge_conflicts} head-on edge conflicts on a one-way core; "
    f"run validate_directional_highways.py"
)


print()
print("checks")
print(f"  all {FLEET_SIZE} completed          : PASS")
print("  unique final occupancy    : PASS")
print("  reservation audit         : PASS")
print("  no unresolved wait        : PASS")
print("  no unresolved yield       : PASS")
print("  traffic arbitration used  : PASS")
print("  zero head-on conflicts    : PASS")

print()
print("status                      : OK")
