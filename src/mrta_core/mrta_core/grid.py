from dataclasses import dataclass, field
from heapq import heappop, heappush
from math import inf
from typing import TypeAlias

from mrta_core.models import RobotState, TaskState

Cell: TypeAlias = tuple[int, int]
DirectedEdge: TypeAlias = tuple[Cell, Cell]


@dataclass(frozen=True)
class GridMap:
    width: int
    height: int
    obstacles: frozenset[Cell] = field(default_factory=frozenset)
    congestion: dict[Cell, float] = field(default_factory=dict)

    # Optional directed edges that may not be traversed.
    #
    # Keeping this generic means GridMap knows nothing about warehouse
    # highways. Warehouse routing supplies the actual policy.
    blocked_edges: frozenset[DirectedEdge] = field(
        default_factory=frozenset
    )

    def in_bounds(self, cell: Cell) -> bool:
        x, y = cell
        return 0 <= x < self.width and 0 <= y < self.height

    def is_free(self, cell: Cell) -> bool:
        return self.in_bounds(cell) and cell not in self.obstacles

    def neighbors(self, cell: Cell) -> list[Cell]:
        x, y = cell

        candidates = [
            (x + 1, y),
            (x - 1, y),
            (x, y + 1),
            (x, y - 1),
        ]

        return [
            candidate
            for candidate in candidates
            if self.is_free(candidate)
            and (cell, candidate) not in self.blocked_edges
        ]

    def congestion_at(self, cell: Cell) -> float:
        return self.congestion.get(cell, 0.0)


@dataclass(frozen=True)
class PathResult:
    found: bool
    path: list[Cell]
    distance: float
    congestion_cost: float
    total_cost: float


@dataclass(frozen=True)
class PathFeatures:
    reachable: bool
    path_distance: float
    travel_time: float
    congestion_cost: float
    energy_cost: float


def manhattan_distance(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def astar_path(
    grid: GridMap,
    start: Cell,
    goal: Cell,
    congestion_weight: float = 1.0,
) -> PathResult:
    if not grid.is_free(start) or not grid.is_free(goal):
        return PathResult(
            found=False,
            path=[],
            distance=inf,
            congestion_cost=inf,
            total_cost=inf,
        )

    frontier: list[tuple[float, int, Cell]] = []
    counter = 0

    heappush(frontier, (0.0, counter, start))

    came_from: dict[Cell, Cell | None] = {start: None}
    cost_so_far: dict[Cell, float] = {start: 0.0}

    while frontier:
        _, _, current = heappop(frontier)

        if current == goal:
            break

        for next_cell in grid.neighbors(current):
            step_cost = 1.0 + congestion_weight * grid.congestion_at(next_cell)
            new_cost = cost_so_far[current] + step_cost

            if next_cell not in cost_so_far or new_cost < cost_so_far[next_cell]:
                cost_so_far[next_cell] = new_cost
                priority = new_cost + manhattan_distance(next_cell, goal)

                counter += 1
                heappush(frontier, (priority, counter, next_cell))
                came_from[next_cell] = current

    if goal not in came_from:
        return PathResult(
            found=False,
            path=[],
            distance=inf,
            congestion_cost=inf,
            total_cost=inf,
        )

    path = reconstruct_path(came_from, start, goal)
    distance = float(max(len(path) - 1, 0))
    congestion_cost = sum(grid.congestion_at(cell) for cell in path[1:])
    total_cost = distance + congestion_weight * congestion_cost

    return PathResult(
        found=True,
        path=path,
        distance=distance,
        congestion_cost=congestion_cost,
        total_cost=total_cost,
    )


def reconstruct_path(
    came_from: dict[Cell, Cell | None],
    start: Cell,
    goal: Cell,
) -> list[Cell]:
    current = goal
    path = [current]

    while current != start:
        previous = came_from[current]

        if previous is None:
            break

        current = previous
        path.append(current)

    path.reverse()
    return path


def extract_path_features(
    grid: GridMap,
    robot: RobotState,
    task: TaskState,
    robot_speed: float = 1.0,
    energy_per_cell: float = 0.01,
) -> PathFeatures:
    start = (robot.x, robot.y)
    goal = (task.pickup_x, task.pickup_y)

    result = astar_path(grid, start, goal)

    if not result.found:
        return PathFeatures(
            reachable=False,
            path_distance=inf,
            travel_time=inf,
            congestion_cost=inf,
            energy_cost=inf,
        )

    travel_time = result.distance / robot_speed
    energy_cost = result.distance * energy_per_cell

    return PathFeatures(
        reachable=True,
        path_distance=result.distance,
        travel_time=travel_time,
        congestion_cost=result.congestion_cost,
        energy_cost=energy_cost,
    )