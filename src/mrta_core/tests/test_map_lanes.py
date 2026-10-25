"""Maps that draw their own lanes.

Before this, the one-way lane policy was hard-coded to the 17 x 35 V2
layout: on any other map most of the road network had no legal
direction and robots could not reach the new shelves at all. Now a map
may carry its lanes as arrows typed into its road cells, and is then
routed by its own arrows.

What has to hold:

  * the format can express a real design exactly -- V2, exported as
    arrows and loaded back, is routed on the identical edge set
  * a map without arrows is routed exactly as before
  * terminals behave identically on every map: entered and left from
    any neighbouring road, never hopped between, never driven through
  * a drawing mistake is refused at load time and names its Excel cell
  * the generated example maps pass every structural rule at any size
  * the checker finds each kind of mistake a person will actually make
"""

from pathlib import Path

import pytest

from mrta_core.map_builder import (
    CHARGER,
    DROPOFF,
    EAST,
    FLOOR,
    NORTH,
    PARKING,
    SHELF,
    SOUTH,
    WEST,
    Layout,
    blank_layout,
    example_layout,
    layout_of,
    write_layout_xlsx,
)
from mrta_core.map_check import check_map
from mrta_core.sim_config import load_sim_config, resolve_map_path
from mrta_core.warehouse_map import WarehouseMap, WarehouseZone, ZoneType
from mrta_core.warehouse_map_xlsx import _parse_arrows, load_warehouse_xlsx
from mrta_core.warehouse_routing import directional_blocked_edges
from mrta_core.world import WorldGrid


CONFIGS = Path(__file__).resolve().parents[2] / "simulation_env" / "test" / "fixtures"


@pytest.fixture(scope="module")
def v2():
    config = load_sim_config(CONFIGS / "v2_reference.yaml")
    return load_warehouse_xlsx(config.map_path)


def corridor():
    """The smallest legal drawn map: a one-way loop with two stations.

    5 x 5, walls round the edge, a 3 x 3 interior::

        y=3   < < v
        y=2   ^ S v      S shelf at (2, 2), D drop-off below the loop
        y=1   ^ > >
              D at (2, 0) is on the edge, under the loop's bottom row
    """

    layout = Layout(width=5, height=5)

    loop = {
        (1, 1): EAST,
        (2, 1): EAST,
        (3, 1): NORTH,
        (3, 2): NORTH,
        (3, 3): WEST,
        (2, 3): WEST,
        (1, 3): SOUTH,
        (1, 2): SOUTH,
    }

    for cell, step in loop.items():
        layout.kinds[cell] = FLOOR
        layout.lane(cell, step)

    layout.kinds[(2, 2)] = SHELF
    layout.kinds[(2, 0)] = DROPOFF
    layout.kinds[(0, 2)] = PARKING
    layout.kinds[(4, 2)] = CHARGER

    return layout


def load(layout, tmp_path, name="map.xlsx"):
    return load_warehouse_xlsx(write_layout_xlsx(layout, tmp_path / name))


# ----------------------------------------------------------------------
# The format is complete: V2 survives the round trip exactly.
# ----------------------------------------------------------------------


def test_v2_drawn_as_arrows_is_routed_identically(v2, tmp_path):
    """The strongest test of the format there is.

    V2's lane policy is intricate -- frontage lanes that exist only on
    the rows the home bank spans, westbound rungs inside shelf blocks,
    shelf columns that are northbound but never cross a block. If the
    arrow format could not express any of that, this would fail.
    """

    drawn = load(layout_of(v2), tmp_path)

    assert v2.lane_edges is None
    assert drawn.lane_edges is not None

    assert drawn.grid == v2.grid
    assert directional_blocked_edges(drawn) == directional_blocked_edges(v2)


def test_a_map_without_arrows_keeps_the_built_in_policy(v2):
    """Backward compatibility: nothing drawn, nothing changes."""

    assert v2.lane_edges is None

    # 775 blocked moves is the V2 policy as frozen.
    assert len(directional_blocked_edges(v2)) == 775


# ----------------------------------------------------------------------
# Reading arrows.
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (">", {(0, 1)}),
        ("→", {(0, 1)}),
        ("<", {(0, -1)}),
        ("^", {(-1, 0)}),
        ("v", {(1, 0)}),
        ("V", {(1, 0)}),
        ("↓", {(1, 0)}),
        (">^", {(0, 1), (-1, 0)}),
        ("> ^", {(0, 1), (-1, 0)}),
        (">,^", {(0, 1), (-1, 0)}),
        ("→↑", {(0, 1), (-1, 0)}),
    ],
)
def test_arrows_are_read_as_screen_directions(text, expected):
    assert _parse_arrows(text) == frozenset(expected)


@pytest.mark.parametrize("text", ["SHELF", "x", ">x", "", None, 3, "->"])
def test_anything_else_is_not_an_arrow(text):
    assert _parse_arrows(text) is None


def test_up_on_the_sheet_is_north_in_the_grid(tmp_path):
    """The sheet's Y labels run upward, so the up arrow is +y."""

    warehouse = load(corridor(), tmp_path)

    assert ((1, 1), (2, 1)) in warehouse.lane_edges  # > is east
    assert ((3, 1), (3, 2)) in warehouse.lane_edges  # ^ is north
    assert ((1, 3), (1, 2)) in warehouse.lane_edges  # v is south

    assert len(warehouse.lane_edges) == 8


def test_one_letter_codes_load_as_stations(tmp_path):
    warehouse = load(corridor(), tmp_path)

    kinds = {zone.zone_type for zone in warehouse.zones}

    assert kinds == {
        ZoneType.SHELF,
        ZoneType.DROPOFF,
        ZoneType.STAGING,
        ZoneType.CHARGING,
    }


# ----------------------------------------------------------------------
# Drawing mistakes are refused, and name the Excel cell.
# ----------------------------------------------------------------------


def test_an_arrow_into_a_wall_is_refused(tmp_path):
    layout = corridor()
    layout.lane((1, 1), SOUTH)  # (1, 0) is the wall under the loop

    with pytest.raises(ValueError, match=r"arrow at [A-Z]+\d+ points into the wall"):
        load(layout, tmp_path)


def test_an_arrow_into_a_station_is_refused(tmp_path):
    layout = corridor()
    layout.lane((2, 1), NORTH)  # the shelf at (2, 2)

    with pytest.raises(ValueError, match="points into station shelf_001"):
        load(layout, tmp_path)


def test_unknown_cell_text_is_refused_with_both_vocabularies(tmp_path):
    from openpyxl import load_workbook

    path = write_layout_xlsx(corridor(), tmp_path / "map.xlsx")

    workbook = load_workbook(path)
    workbook["MAP"]["C3"] = "SHELFF"
    workbook.save(path)

    with pytest.raises(ValueError, match="station label .* or lane arrows"):
        load_warehouse_xlsx(path)


def test_a_lane_touching_a_station_is_refused_by_the_map_itself():
    """The safety net under the loader: no construction path routes it."""

    grid = WorldGrid(
        origin_x=0.0,
        origin_y=0.0,
        resolution=1.0,
        width=3,
        height=1,
        obstacles=frozenset(),
    )

    shelf = WarehouseZone("shelf_001", ZoneType.SHELF, frozenset({(2, 0)}))

    with pytest.raises(ValueError, match="is station 'shelf_001'"):
        WarehouseMap(grid=grid, zones=(shelf,), lane_edges=frozenset({((1, 0), (2, 0))}))


def test_a_lane_that_skips_a_cell_is_refused():
    grid = WorldGrid(
        origin_x=0.0,
        origin_y=0.0,
        resolution=1.0,
        width=3,
        height=1,
        obstacles=frozenset(),
    )

    with pytest.raises(ValueError, match="neighbouring"):
        WarehouseMap(grid=grid, lane_edges=frozenset({((0, 0), (2, 0))}))


# ----------------------------------------------------------------------
# Terminals behave the same on every map.
# ----------------------------------------------------------------------


def test_stations_are_entered_and_left_without_drawn_arrows(tmp_path):
    """No arrow points at the shelf, yet robots can serve it."""

    warehouse = load(corridor(), tmp_path)
    blocked = directional_blocked_edges(warehouse)

    shelf = (2, 2)

    for road in ((2, 1), (1, 2), (3, 2), (2, 3)):
        assert (road, shelf) not in blocked
        assert (shelf, road) not in blocked


def test_stations_are_never_hopped_between():
    """Two neighbouring stations are joined through the road, never directly.

    Even a drawn lane between them could not make it legal: the
    terminal rule runs before the map's own lanes are consulted.
    """

    from mrta_core.warehouse_routing import _drawn_edge_allowed

    shelf, charger, road = (2, 2), (3, 2), (2, 1)
    terminals = frozenset({shelf, charger})
    lanes = frozenset({(shelf, charger), (charger, shelf)})

    assert not _drawn_edge_allowed(shelf, charger, terminals, lanes)
    assert not _drawn_edge_allowed(charger, shelf, terminals, lanes)

    # The road beside them is still a way in and out.
    assert _drawn_edge_allowed(road, shelf, terminals, frozenset())
    assert _drawn_edge_allowed(shelf, road, terminals, frozenset())


def test_undrawn_road_moves_are_blocked(tmp_path):
    """On a drawn map the arrows are the whole policy."""

    warehouse = load(corridor(), tmp_path)
    blocked = directional_blocked_edges(warehouse)

    # The loop runs east along the bottom, never west.
    assert ((2, 1), (1, 1)) in blocked


# ----------------------------------------------------------------------
# The checker.
# ----------------------------------------------------------------------


def test_the_corridor_passes(tmp_path):
    report = check_map(load(corridor(), tmp_path))

    assert report.ok, [p.message for p in report.problems]
    assert report.drawn
    assert report.max_robots() == 1


def test_v2_passes_the_checker(v2):
    report = check_map(v2)

    assert report.ok, [p.message for p in report.problems]
    assert not report.drawn
    assert report.max_robots() == 22


def test_a_dead_end_is_found(tmp_path):
    layout = corridor()
    layout.arrows[(3, 3)] = set()

    problems = check_map(load(layout, tmp_path)).problems

    assert any(p.kind == "dead end" and p.cells == ((3, 3),) for p in problems)


def test_a_two_way_road_is_found(tmp_path):
    layout = corridor()
    layout.lane((2, 1), WEST)  # against the loop's own EAST

    problems = check_map(load(layout, tmp_path)).problems

    assert any(
        p.kind == "two-way road" and set(p.cells) == {(1, 1), (2, 1)} for p in problems
    )


def test_a_cut_off_area_is_found(tmp_path):
    """A road you can drive into but never leave."""

    layout = corridor()

    # Break the loop: the top-right corner no longer turns west.
    layout.arrows[(3, 3)] = {SOUTH}

    problems = check_map(load(layout, tmp_path)).problems

    assert any(p.kind in ("disconnected roads", "two-way road") for p in problems)


def test_a_station_off_the_road_is_found(tmp_path):
    layout = blank_layout(7, 7)

    # One loop in the corner, and a shelf far from it.
    for cell, step in {
        (1, 1): EAST,
        (2, 1): NORTH,
        (2, 2): WEST,
        (1, 2): SOUTH,
    }.items():
        layout.lane(cell, step)

    for cell in list(layout.kinds):
        if cell not in {(1, 1), (2, 1), (2, 2), (1, 2)}:
            layout.kinds.pop(cell)

    layout.kinds[(4, 4)] = SHELF
    layout.kinds[(3, 1)] = DROPOFF
    layout.kinds[(1, 3)] = PARKING
    layout.kinds[(0, 1)] = CHARGER

    problems = check_map(load(layout, tmp_path)).problems

    assert any(p.kind == "station not on a road" for p in problems)


def test_a_blank_map_is_reported_rather_than_crashing(tmp_path):
    """What a person sees the first time they run check on a new sheet."""

    report = check_map(load(blank_layout(8, 8), tmp_path))

    # The built-in V2 policy is used when nothing is drawn, so the
    # blank interior has no legal moves, and there are no stations.
    assert not report.ok
    assert any(p.kind == "missing station" for p in report.problems)


# ----------------------------------------------------------------------
# Generated maps.
# ----------------------------------------------------------------------


@pytest.mark.parametrize("robots", [1, 10, 22, 50, 100])
def test_example_maps_pass_every_structural_rule(robots, tmp_path):
    layout = example_layout(robots)

    report = check_map(load(layout, tmp_path))

    # Clean, not merely usable: no rotaries either. A rotary is the
    # four-robot deadlock trap, and these maps exist to be scaled up.
    assert report.clean, [(p.kind, p.cells[:3]) for p in report.problems]
    assert report.max_robots() >= robots


def test_example_maps_are_reproducible(tmp_path):
    first = write_layout_xlsx(example_layout(30), tmp_path / "a.xlsx")
    second = write_layout_xlsx(example_layout(30), tmp_path / "b.xlsx")

    a = load_warehouse_xlsx(first)
    b = load_warehouse_xlsx(second)

    assert a.lane_edges == b.lane_edges
    assert a.zones == b.zones


# ----------------------------------------------------------------------
# Config.
# ----------------------------------------------------------------------


def test_the_map_resolves_before_the_order_file_exists(tmp_path):
    """Generating orders needs the map, and must not need the orders."""

    config_dir = tmp_path / "config"
    maps_dir = tmp_path / "maps"
    config_dir.mkdir()
    maps_dir.mkdir()

    write_layout_xlsx(corridor(), maps_dir / "mine.xlsx")

    config = config_dir / "mine.yaml"
    config.write_text(
        "warehouse:\n  map: mine.xlsx\norders:\n  file: not_written_yet.csv\n"
    )

    assert resolve_map_path(config) == maps_dir / "mine.xlsx"


def test_a_rotary_is_found_and_is_a_warning(tmp_path):
    """Four roads chasing each other round a square.

    The exact shape of the V2 deadlock under the random strategy: four
    robots on (13,3) (14,3) (14,4) (13,4), each waiting for the next.
    """

    layout = blank_layout(6, 6)

    for cell in list(layout.kinds):
        layout.kinds.pop(cell)

    loop = {(1, 1): EAST, (2, 1): NORTH, (2, 2): WEST, (1, 2): SOUTH}

    for cell, step in loop.items():
        layout.kinds[cell] = FLOOR
        layout.lane(cell, step)

    layout.kinds[(3, 1)] = SHELF
    layout.kinds[(1, 0)] = DROPOFF
    layout.kinds[(0, 1)] = PARKING
    layout.kinds[(1, 3)] = CHARGER

    report = check_map(load(layout, tmp_path))

    rotaries = [p for p in report.problems if p.kind == "rotary"]

    assert len(rotaries) == 1
    assert set(rotaries[0].cells) == set(loop)
    assert rotaries[0].severity == "warning"

    # Usable, but not clean.
    assert report.ok
    assert not report.clean


def test_v2_has_the_rotary_it_deadlocked_in(v2):
    report = check_map(v2)

    squares = {frozenset(p.cells) for p in report.warnings if p.kind == "rotary"}

    assert frozenset({(13, 3), (14, 3), (14, 4), (13, 4)}) in squares
    assert len(squares) == 33
