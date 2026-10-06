"""Grid <-> world coordinate transform.

One definition, because three layers must place the same cell at the
same metric point and can never be allowed to disagree:

  * ``simulation_env`` renders the Gazebo world and drives live robot
    poses,
  * ``rmf_grid_traffic`` places RMF navigation-graph vertices,
  * anything reading a pose back has to recover the cell.

The convention is a direct identity on the cell index:

    world_x = origin_x + grid_x * resolution
    world_y = origin_y + grid_y * resolution

so with the default 1 m cells and a zero origin, grid cell ``(5, 15)``
is world point ``(5.0, 15.0)``. A pose read out of Gazebo *is* the
cell, with no offset to undo.

The cell's tile therefore spans ``x - 0.5 .. x + 0.5``: cell centres
land on integers and tile edges on half-integers. That is the
deliberate trade for having the two coordinate systems be the same
numbers.

This module sits in ``mrta_core`` for the same reason
``warehouse_routing`` does: it is shared truth, not the property of any
one consumer.
"""

from mrta_core.grid import Cell


def grid_to_world(
    cell: Cell,
    resolution: float = 1.0,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> tuple[float, float]:
    """Grid cell -> world XY of that cell's centre."""

    x, y = cell

    return (
        origin_x + x * resolution,
        origin_y + y * resolution,
    )


def world_to_grid(
    point: tuple[float, float],
    resolution: float = 1.0,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> Cell:
    """World XY -> the grid cell whose centre is nearest.

    Exact inverse of :func:`grid_to_world` for any point at a cell
    centre, which is where robots and station tiles are placed.
    """

    world_x, world_y = point

    return (
        int(round((world_x - origin_x) / resolution)),
        int(round((world_y - origin_y) / resolution)),
    )
