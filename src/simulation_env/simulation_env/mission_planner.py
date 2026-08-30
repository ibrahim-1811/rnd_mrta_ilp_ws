"""Static mission planning, re-exported from the shared core.

The implementation moved to :mod:`mrta_core.warehouse_routing` so that
``task_management`` (which predicts route cost when allocating) and
``simulation_env`` (which executes the route) call exactly the same
A* and the same free-cell rule. Importing it from either package must
never yield a second, subtly different answer.

This module stays as the executor-side name so existing call sites
read unchanged. There is no logic here.
"""

from mrta_core.warehouse_routing import (
    SERVICE_TYPES,
    MissionPlan,
    MissionSegment,
    RouteCache,
    best_path_to_zone,
    is_service_zone,
    navigation_grid,
    plan_from_cell,
    plan_return_to_zone,
    plan_zone_mission,
    service_cells,
    station_cells,
)

__all__ = [
    "SERVICE_TYPES",
    "MissionPlan",
    "MissionSegment",
    "RouteCache",
    "best_path_to_zone",
    "is_service_zone",
    "navigation_grid",
    "plan_from_cell",
    "plan_return_to_zone",
    "plan_zone_mission",
    "service_cells",
    "station_cells",
]
