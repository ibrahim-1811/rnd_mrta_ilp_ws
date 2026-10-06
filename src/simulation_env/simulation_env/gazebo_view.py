"""Grid <-> Gazebo view transform.

Gazebo is visualization only. The actual transform lives in
``mrta_core.world_view`` because the RMF navigation graph has to place
vertices at exactly the same points; this module is the thin
Gazebo-facing name for it.

    gazebo_x = origin_x + grid_x * cell_size
    gazebo_y = origin_y + grid_y * cell_size

Grid cell (0, 0) therefore renders at (0, 0), and a pose read back out
of Gazebo is the cell index itself.
"""

from mrta_core.grid import Cell
from mrta_core.world_view import grid_to_world, world_to_grid


CELL_SIZE = 1.0

__all__ = ["CELL_SIZE", "grid_to_gazebo", "gazebo_to_grid"]


def grid_to_gazebo(
    cell: Cell,
    cell_size: float = CELL_SIZE,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> tuple[float, float]:
    """Grid cell -> Gazebo cell-centre XY."""

    return grid_to_world(
        cell,
        cell_size,
        origin_x,
        origin_y,
    )


def gazebo_to_grid(
    point: tuple[float, float],
    cell_size: float = CELL_SIZE,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> Cell:
    """Gazebo XY -> grid cell."""

    return world_to_grid(
        point,
        cell_size,
        origin_x,
        origin_y,
    )
