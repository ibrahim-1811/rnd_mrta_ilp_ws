from mrta_core.grid import astar_path
from mrta_core.world import WorldGrid, load_ascii_obstacles, world_grid_from_ascii


def test_from_bounds_dimensions():
    wg = WorldGrid.from_bounds(-15.0, 0.0, 15.0, 30.0, resolution=1.0)
    assert wg.width == 31
    assert wg.height == 31


def test_round_trip_cell_world():
    wg = WorldGrid.from_bounds(-15.0, 0.0, 15.0, 30.0, resolution=1.0)
    # shelf at (-13.5, 3.0) -> nearest cell -> back to world on the grid
    cell = wg.to_cell(-13.5, 3.0)
    assert cell == (2, 3)
    assert wg.to_world(cell) == (-13.0, 3.0)


def test_to_cell_clamps_out_of_bounds():
    wg = WorldGrid.square(10.0, resolution=1.0)
    assert wg.to_cell(-999.0, -999.0) == (0, 0)
    assert wg.to_cell(999.0, 999.0) == (wg.width - 1, wg.height - 1)


def test_grid_map_carries_obstacles():
    wg = WorldGrid.from_bounds(0.0, 0.0, 5.0, 5.0, 1.0, obstacles={(2, 2)})
    gm = wg.grid_map()
    assert (2, 2) in gm.obstacles
    assert not gm.is_free((2, 2))


def test_astar_routes_around_obstacle_wall():
    # vertical wall at x=2 for y in 0..3, gap at y=4 lets the path through
    wall = {(2, y) for y in range(0, 4)}
    wg = WorldGrid.from_bounds(0.0, 0.0, 5.0, 5.0, 1.0, obstacles=wall)
    result = astar_path(wg.grid_map(), wg.to_cell(0.0, 0.0), wg.to_cell(4.0, 0.0))
    assert result.found
    # straight-line manhattan would be 4; routing around the wall costs more
    assert result.distance > 4
    assert all(cell not in wall for cell in result.path)


def test_ascii_obstacle_loader(tmp_path):
    map_file = tmp_path / "wall.txt"
    map_file.write_text("...\n.#.\n...\n")
    width, height, obstacles = load_ascii_obstacles(str(map_file))
    assert (width, height) == (3, 3)
    # the '#' is on the middle row -> y = 1 (top line is highest y)
    assert obstacles == frozenset({(1, 1)})
    wg = world_grid_from_ascii(str(map_file), origin_x=0.0, origin_y=0.0)
    assert (1, 1) in wg.obstacles
