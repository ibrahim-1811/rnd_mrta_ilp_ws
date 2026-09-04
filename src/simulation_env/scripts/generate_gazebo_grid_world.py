#!/usr/bin/env python3

"""Generate a lightweight Gazebo warehouse world from the XLSX map.

Gazebo is visualization only.

White floor:
    traversable warehouse area

Red tiles:
    blocked / no-go cells

Coloured tiles:
    semantic stations, one model per station ID, named after
    the ID so a pose can be read straight off the entity tree

Black cylinder:
    lightweight grid robot

Station tiles are visual only. They carry no collision and never
affect A*; the occupancy layer is still just the red cells.
"""

import argparse
from pathlib import Path

from mrta_core.sim_config import load_sim_config
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from simulation_env.fleet import build_fleet
from simulation_env.gazebo_view import grid_to_gazebo


DEFAULT_CONFIG = "src/simulation_env/config/warehouse_sim.yaml"

WORLDS_DIR = Path("src/simulation_env/worlds")

_parser = argparse.ArgumentParser(description=__doc__)

_parser.add_argument(
    "--config",
    default=DEFAULT_CONFIG,
    type=Path,
    help="simulation config; map, fleet size, cell size and world "
         "name all come from it",
)

_parser.add_argument(
    "--output",
    default=None,
    type=Path,
    help="output .world path "
         "(default: worlds/<gazebo.world_name>.world)",
)

_args = _parser.parse_args()

CONFIG_PATH = _args.config

# Map, fleet size, resolution and world name all come from the central
# config, so the world and the executor can never disagree about any of
# them. Regenerate this world whenever any of those change.
CONFIG = load_sim_config(CONFIG_PATH)

MAP_PATH = CONFIG.map_path

WORLD_NAME = CONFIG.gazebo_world_name

OUTPUT_PATH = (
    _args.output
    if _args.output is not None
    else WORLDS_DIR / f"{WORLD_NAME}.world"
)

CELL_SIZE = CONFIG.resolution_m

FLOOR_Z = 0.0

BLOCK_HEIGHT = 0.05

ROBOT_RADIUS = 0.30
ROBOT_HEIGHT = 0.15

STATION_HEIGHT = 0.02
STATION_INSET = 0.8

# Roughly the XLSX fill colours, so the world reads like the sheet.
STATION_COLORS = {
    "shelf": "0.10 0.70 0.10",
    "charging": "0.00 0.70 0.80",
    "dropoff": "0.90 0.70 0.10",
    "collection": "0.85 0.80 0.55",
    # Stage / wait / park zone, matching its XLSX fill.
    "pickup": "0.60 0.30 0.80",
    "staging": "0.85 0.80 0.55",
    "waiting": "0.30 0.30 0.30",
}


def grid_to_world(x: int, y: int) -> tuple[float, float]:

    # Cell centres. Shared with the live mission runner so the
    # generated world and the animated robot cannot disagree.
    return grid_to_gazebo(
        (x, y),
        CELL_SIZE,
    )


warehouse = load_warehouse_xlsx(
    MAP_PATH,
    resolution=CELL_SIZE,
)


width_m = (
    warehouse.grid.width
    * CELL_SIZE
)

height_m = (
    warehouse.grid.height
    * CELL_SIZE
)


# The fleet is built from the same config the executor uses, so the
# world and the running robots always agree on where each one starts.
fleet = build_fleet(CONFIG, warehouse)


lines = []

lines.append(
    '<?xml version="1.0"?>'
)

lines.append(
    '<sdf version="1.10">'
)

lines.append(
    f'  <world name="{WORLD_NAME}">'
)


# --------------------------------------------------
# Gazebo systems
# --------------------------------------------------

lines.append(
    '''
    <plugin
      filename="gz-sim-physics-system"
      name="gz::sim::systems::Physics">
    </plugin>

    <plugin
      filename="gz-sim-user-commands-system"
      name="gz::sim::systems::UserCommands">
    </plugin>

    <plugin
      filename="gz-sim-scene-broadcaster-system"
      name="gz::sim::systems::SceneBroadcaster">
    </plugin>
'''
)


# --------------------------------------------------
# Scene
# --------------------------------------------------

lines.append(
    '''
    <scene>
      <ambient>0.8 0.8 0.8 1</ambient>
      <background>0.6 0.6 0.6 1</background>
      <shadows>false</shadows>
    </scene>
'''
)


# --------------------------------------------------
# Light
# --------------------------------------------------

lines.append(
    '''
    <light type="directional" name="sun">
      <pose>0 0 20 0 0 0</pose>
      <direction>0 0 -1</direction>
      <diffuse>1 1 1 1</diffuse>
      <specular>0.2 0.2 0.2 1</specular>
      <cast_shadows>false</cast_shadows>
    </light>
'''
)


# --------------------------------------------------
# White warehouse floor
# --------------------------------------------------

lines.append(
    f'''
    <model name="warehouse_floor">
      <static>true</static>

      <pose>
        {width_m / 2.0}
        {height_m / 2.0}
        {FLOOR_Z}
        0 0 0
      </pose>

      <link name="floor_link">

        <visual name="floor_visual">
          <geometry>
            <box>
              <size>
                {width_m}
                {height_m}
                0.02
              </size>
            </box>
          </geometry>

          <material>
            <ambient>0.95 0.95 0.95 1</ambient>
            <diffuse>0.95 0.95 0.95 1</diffuse>
          </material>
        </visual>

      </link>
    </model>
'''
)


# --------------------------------------------------
# Red blocked cells
# --------------------------------------------------

for index, cell in enumerate(
    sorted(
        warehouse.grid.obstacles
    )
):

    x, y = cell

    wx, wy = grid_to_world(
        x,
        y,
    )

    lines.append(
        f'''
    <model name="blocked_{index:04d}">
      <static>true</static>

      <pose>
        {wx}
        {wy}
        {BLOCK_HEIGHT / 2.0 + 0.02}
        0 0 0
      </pose>

      <link name="link">

        <visual name="visual">
          <geometry>
            <box>
              <size>
                {CELL_SIZE}
                {CELL_SIZE}
                {BLOCK_HEIGHT}
              </size>
            </box>
          </geometry>

          <material>
            <ambient>0.8 0.05 0.05 1</ambient>
            <diffuse>0.8 0.05 0.05 1</diffuse>
          </material>
        </visual>

      </link>
    </model>
'''
    )


# --------------------------------------------------
# Semantic stations
#
# One model per station ID so the Gazebo entity tree can be
# used to confirm that e.g. shelf_018 really sits at (7.5, 28.5).
# --------------------------------------------------

station_count = 0

for zone in warehouse.zones:

    color = STATION_COLORS.get(
        zone.zone_type.value,
        "0.5 0.5 0.5",
    )

    for cell in sorted(zone.cells):

        wx, wy = grid_to_world(
            *cell
        )

        station_count += 1

        lines.append(
            f'''
    <model name="{zone.zone_id}">
      <static>true</static>

      <pose>
        {wx}
        {wy}
        {STATION_HEIGHT / 2.0 + 0.01}
        0 0 0
      </pose>

      <link name="link">

        <visual name="visual">
          <geometry>
            <box>
              <size>
                {CELL_SIZE * STATION_INSET}
                {CELL_SIZE * STATION_INSET}
                {STATION_HEIGHT}
              </size>
            </box>
          </geometry>

          <material>
            <ambient>{color} 1</ambient>
            <diffuse>{color} 1</diffuse>
          </material>
        </visual>

      </link>
    </model>
'''
        )


# --------------------------------------------------
# Lightweight black-dot robots, one model per robot
# --------------------------------------------------

for robot in fleet:

    wx, wy = grid_to_world(*robot.cell)

    lines.append(
        f'''
    <model name="{robot.robot_id}">

      <static>true</static>

      <pose>
        {wx}
        {wy}
        {ROBOT_HEIGHT / 2.0 + 0.08}
        0 0 0
      </pose>

      <link name="robot_link">

        <visual name="robot_visual">

          <geometry>
            <cylinder>
              <radius>{ROBOT_RADIUS}</radius>
              <length>{ROBOT_HEIGHT}</length>
            </cylinder>
          </geometry>

          <material>
            <ambient>0.02 0.02 0.02 1</ambient>
            <diffuse>0.02 0.02 0.02 1</diffuse>
          </material>

        </visual>

      </link>

    </model>
'''
    )


lines.append(
    '  </world>'
)

lines.append(
    '</sdf>'
)


OUTPUT_PATH.write_text(
    "\n".join(lines)
)


print()
print("=" * 80)
print("GAZEBO WORLD GENERATED")
print("=" * 80)

print(
    f"Map          : {MAP_PATH}"
)

print(
    f"Output       : {OUTPUT_PATH}"
)

print(
    f"Grid         : "
    f"{warehouse.grid.width} x "
    f"{warehouse.grid.height}"
)

print(
    f"World size   : "
    f"{width_m:.1f} m x "
    f"{height_m:.1f} m"
)

print(
    f"Blocked cells: "
    f"{len(warehouse.grid.obstacles)}"
)

print(
    f"Stations     : "
    f"{station_count}"
)

print(
    f"Robots       : "
    f"{len(fleet)}"
)

for robot in fleet:
    wx, wy = grid_to_world(*robot.cell)
    print(
        f"  {robot.robot_id} cell={robot.cell} "
        f"gazebo=({wx:.1f}, {wy:.1f})"
    )

print()
