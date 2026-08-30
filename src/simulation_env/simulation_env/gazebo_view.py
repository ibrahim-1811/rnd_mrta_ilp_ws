"""Grid <-> Gazebo view transform.

Gazebo is visualization only. This module holds the single
definition of that mapping so the world generator and the live
mission runner can never drift apart:

    gazebo_x = (grid_x + 0.5) * cell_size
    gazebo_y = (grid_y + 0.5) * cell_size

Grid cell (0, 0) therefore renders at (0.5, 0.5).
"""

from mrta_core.grid import Cell


CELL_SIZE = 1.0


def grid_to_gazebo(
    cell: Cell,
    cell_size: float = CELL_SIZE,
) -> tuple[float, float]:
    """Grid cell -> Gazebo cell-centre XY."""

    x, y = cell

    return (
        (x + 0.5) * cell_size,
        (y + 0.5) * cell_size,
    )
