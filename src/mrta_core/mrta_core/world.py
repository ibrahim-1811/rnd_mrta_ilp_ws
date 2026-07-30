"""World <-> grid coordinate mapping.

The simulator and the ILP feature-extraction layer must agree on exactly one grid.
``WorldGrid`` is that single source of truth: it converts continuous world coordinates
(metres, as stored in the shelf database) to integer grid cells and back, and builds
the :class:`~mrta_core.grid.GridMap` that A* runs on.
"""

from dataclasses import dataclass
from math import ceil

from mrta_core.grid import Cell, GridMap


@dataclass(frozen=True)
class WorldGrid:
    origin_x: float
    origin_y: float
    resolution: float
    width: int
    height: int
    obstacles: frozenset[Cell] = frozenset()

    @classmethod
    def from_bounds(
        cls,
        x_min: float,
        y_min: float,
        x_max: float,
        y_max: float,
        resolution: float = 1.0,
        obstacles: object = (),
    ) -> "WorldGrid":
        width = int(ceil((x_max - x_min) / resolution)) + 1
        height = int(ceil((y_max - y_min) / resolution)) + 1
        return cls(
            origin_x=x_min,
            origin_y=y_min,
            resolution=resolution,
            width=width,
            height=height,
            obstacles=frozenset(obstacles),
        )

    @classmethod
    def square(
        cls,
        world_size: float,
        resolution: float = 1.0,
        obstacles: object = (),
    ) -> "WorldGrid":
        """Grid centred on x=0, with y starting at 0 (drop-off convention)."""
        half = world_size / 2.0
        return cls.from_bounds(-half, 0.0, half, world_size, resolution, obstacles)

    def to_cell(self, x: float, y: float) -> Cell:
        cx = round((x - self.origin_x) / self.resolution)
        cy = round((y - self.origin_y) / self.resolution)
        cx = min(max(cx, 0), self.width - 1)
        cy = min(max(cy, 0), self.height - 1)
        return (cx, cy)

    def to_world(self, cell: Cell) -> tuple[float, float]:
        cx, cy = cell
        return (
            self.origin_x + cx * self.resolution,
            self.origin_y + cy * self.resolution,
        )

    def grid_map(self, congestion: dict[Cell, float] | None = None) -> GridMap:
        return GridMap(
            width=self.width,
            height=self.height,
            obstacles=self.obstacles,
            congestion=congestion or {},
        )

    def with_obstacles(self, obstacles: object) -> "WorldGrid":
        return WorldGrid(
            origin_x=self.origin_x,
            origin_y=self.origin_y,
            resolution=self.resolution,
            width=self.width,
            height=self.height,
            obstacles=frozenset(obstacles),
        )


def load_ascii_obstacles(path: str) -> tuple[int, int, frozenset[Cell]]:
    """Load an ASCII obstacle map. ``#`` = obstacle, anything else = free.

    The first text line is the TOP of the map (highest y), so the file reads the
    way the warehouse looks from above. Returns ``(width, height, obstacles)``.
    """
    with open(path) as f:
        lines = [line.rstrip("\n") for line in f if line.strip("\n") != ""]

    height = len(lines)
    width = max((len(line) for line in lines), default=0)

    obstacles: set[Cell] = set()
    for row_index, line in enumerate(lines):
        y = height - 1 - row_index
        for x, char in enumerate(line):
            if char == "#":
                obstacles.add((x, y))

    return width, height, frozenset(obstacles)


def world_grid_from_ascii(
    path: str,
    origin_x: float,
    origin_y: float,
    resolution: float = 1.0,
) -> WorldGrid:
    width, height, obstacles = load_ascii_obstacles(path)
    return WorldGrid(
        origin_x=origin_x,
        origin_y=origin_y,
        resolution=resolution,
        width=width,
        height=height,
        obstacles=obstacles,
    )
