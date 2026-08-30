from fractions import Fraction

from mrta_core.warehouse_map import (
    WarehouseMap,
    WarehouseZone,
    ZoneType,
)
from mrta_core.world import WorldGrid
from simulation_env.fleet_engine import (
    EventType,
    FleetSimulationEngine,
)
from simulation_env.grid_robot import GridRobot, GridRobotStatus
from simulation_env.mapf import (
    MOVE,
    RETREAT,
    SERVICE,
    WAIT,
    PlanAction,
    TimedReservations,
    Trajectory,
)


EDGE_TIME = Fraction(2, 3)


def warehouse(width=16, shelf_cell=None):
    zones = ()

    if shelf_cell is not None:
        zones = (
            WarehouseZone(
                zone_id="shelf",
                zone_type=ZoneType.SHELF,
                cells=frozenset((shelf_cell,)),
            ),
        )

    return WarehouseMap(
        grid=WorldGrid(
            origin_x=0.0,
            origin_y=0.0,
            resolution=1.0,
            width=width,
            height=2,
        ),
        zones=zones,
    )


def engine_for(test_warehouse, robots):
    engine = FleetSimulationEngine(
        warehouse=test_warehouse,
        resolution=1.0,
        service_seconds={
            ZoneType.SHELF: 2.0,
            ZoneType.DROPOFF: 2.0,
        },
    )

    for robot in robots:
        engine.add_robot(robot)

    return engine


def test_six_cell_buffer_refills_at_two_without_stopping():
    actions = []
    now = Fraction(0)

    for x in range(1, 7):
        actions.append(PlanAction(MOVE, (x, 0), now, now + EDGE_TIME))
        now += EDGE_TIME

    actions.append(PlanAction(WAIT, (6, 0), now, now + EDGE_TIME))
    now += EDGE_TIME
    actions.append(
        PlanAction(SERVICE, (6, 0), now, now + 2, zone="shelf")
    )
    now += 2

    for x in range(7, 11):
        kind = RETREAT if x == 10 else MOVE
        actions.append(PlanAction(kind, (x, 0), now, now + EDGE_TIME))
        now += EDGE_TIME

    trajectory = Trajectory("robot_001", (0, 0), actions)
    original_actions = tuple(trajectory.actions)
    robot = GridRobot("robot_001", (0, 0), speed_mps=1.5)
    engine = engine_for(warehouse(shelf_cell=(6, 0)), [robot])
    ticks = []
    trace = []
    engine.on_tick = lambda time, batch: ticks.append(
        (
            Fraction(time).limit_denominator(),
            robot.status,
            tuple(event.kind for event in batch),
        )
    )
    engine.on_event = lambda event: trace.append((event.time, event.kind))

    engine.assign_trajectory(robot.robot_id, trajectory)
    execution = engine._trajectories[robot.robot_id]

    assert execution.trajectory is trajectory
    assert execution.buffered_cells == 6
    assert execution.refill_log[0].cells_before == 0
    assert execution.refill_log[0].cells_after == 6

    engine.run()

    refill = execution.refill_log[1]
    assert refill.time == 4 * EDGE_TIME
    assert refill.cells_before == 2
    assert refill.cells_after == 6
    assert refill.actions_added == 6  # four moves plus WAIT and SERVICE

    refill_tick = next(tick for tick in ticks if tick[0] == refill.time)
    assert refill_tick[1] is GridRobotStatus.MOVING
    assert trace[4][0] - trace[3][0] == EDGE_TIME
    assert [time for time, kind in trace[:6] if kind is EventType.MOVE_COMPLETE] == [
        EDGE_TIME * index for index in range(1, 7)
    ]
    assert all(kind.value != "BUFFER_REFILL" for _, kind in trace)

    assert trajectory.actions is actions
    assert tuple(trajectory.actions) == original_actions
    assert robot.distance_m == 10.0
    assert robot.travel_time_s == float(10 * EDGE_TIME)
    assert robot.waiting_time_s == float(EDGE_TIME)
    assert robot.service_time_s == 2.0


def test_departed_cell_is_reusable_at_the_exact_arrival_time():
    cell_a = (1, 0)
    cell_b = (2, 0)
    cell_c = (0, 0)

    robot_001 = GridRobot("robot_001", cell_a, speed_mps=1.5)
    robot_002 = GridRobot("robot_002", cell_c, speed_mps=1.5)
    first = Trajectory(
        "robot_001",
        cell_a,
        [PlanAction(MOVE, cell_b, Fraction(0), EDGE_TIME)],
    )
    second = Trajectory(
        "robot_002",
        cell_c,
        [
            PlanAction(WAIT, cell_c, Fraction(0), EDGE_TIME),
            PlanAction(MOVE, cell_a, EDGE_TIME, 2 * EDGE_TIME),
        ],
    )

    engine = engine_for(warehouse(width=4), [robot_001, robot_002])
    engine.assign_trajectory("robot_001", first)
    engine.assign_trajectory("robot_002", second)

    engine.step_once()

    released = [
        interval
        for interval in engine.reservations.cell_log
        if interval.cell == cell_a and interval.robot_id == "robot_001"
    ]
    assert len(released) == 1
    assert (released[0].start, released[0].end) == (
        Fraction(0),
        EDGE_TIME,
    )
    assert engine.reservations.owner(cell_a) == "robot_002"

    engine.run()
    engine.reservations.close(engine.time)

    intervals = sorted(
        (
            interval.start,
            interval.end,
            interval.robot_id,
        )
        for interval in engine.reservations.cell_log
        if interval.cell == cell_a
    )
    assert intervals == [
        (Fraction(0), EDGE_TIME, "robot_001"),
        (EDGE_TIME, None, "robot_002"),
    ]
    assert robot_002.cell == cell_a
    assert robot_001.replan_count == robot_002.replan_count == 0
    assert engine.reservations.audit() == []

    # The complete planning reservation remains intact.  Its historical A
    # interval ends at arrival, its current B hold remains, and touching edge
    # intervals are legal under the half-open occupancy model.
    timed = TimedReservations()
    timed.commit(first, hold_forever=True)
    assert timed.cell_free(
        cell_a, EDGE_TIME, 2 * EDGE_TIME, "robot_002"
    )
    assert not timed.cell_free(
        cell_b, EDGE_TIME, 2 * EDGE_TIME, "robot_002"
    )
    assert timed.edge_free(
        cell_b, cell_a, EDGE_TIME, 2 * EDGE_TIME, "robot_002"
    )
