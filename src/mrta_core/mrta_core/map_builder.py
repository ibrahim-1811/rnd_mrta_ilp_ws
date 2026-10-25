"""Build, export and write warehouse maps in the Excel format.

The Excel workbook is the source of truth for a map (see
``warehouse_map_xlsx``). This module is the other direction: it makes
workbooks, so nobody has to start from an empty spreadsheet.

    blank_layout(width, height)   an empty hall: walls round the edge,
                                  floor inside, nothing drawn yet
    example_layout(robots)        a complete, valid warehouse sized for
                                  a given fleet, lanes and all
    layout_of(warehouse)          any loaded map as a Layout, with the
                                  lanes it is actually routed by drawn
                                  in as arrows
    write_layout_xlsx(layout, p)  a Layout as a workbook the loader
                                  reads back exactly

``layout_of`` is what turns a map routed by the built-in V2 policy into
one that carries its own arrows, and it is how the drawn-lane format is
proven complete: V2 exported and loaded back is routed on the identical
edge set.

Directions inside a Layout are grid steps (dx, dy) with +y pointing up
the sheet, which is how the coordinate labels run on every map in this
repository.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from pathlib import Path

from mrta_core.grid import Cell
from mrta_core.warehouse_map import WarehouseMap, ZoneType
from mrta_core.warehouse_map_xlsx import ORANGE_RGB, RED_RGB


EAST = (1, 0)
WEST = (-1, 0)
NORTH = (0, 1)
SOUTH = (0, -1)


# What a cell is. Floor is drivable road; everything else except a wall
# is a station a robot drives into and back out of.
WALL = "wall"
FLOOR = "floor"
SHELF = "shelf"
DROPOFF = "dropoff"
CHARGER = "charger"
HOLDING = "holding"
PARKING = "parking"

# The one-letter code written into the cell, and the label it means.
CODE = {
    SHELF: "S",
    DROPOFF: "D",
    CHARGER: "C",
    HOLDING: "H",
    PARKING: "P",
}

# Cosmetic fill colours, matching the V2 workbook where V2 has one. The
# loader reads only red (wall) and orange (border); every other colour is
# for the person editing the sheet.
FILL = {
    WALL: RED_RGB,
    SHELF: "FFF1C232",
    DROPOFF: "FF00FF00",
    CHARGER: "FF00FFFF",
    HOLDING: "FF8E7CC3",
    PARKING: "FFFFF2CC",
}

# Arrow glyph for each grid step. The sheet runs +y upwards, so north is
# the arrow pointing up the screen.
GLYPH = {
    EAST: "→",
    WEST: "←",
    NORTH: "↑",
    SOUTH: "↓",
}

# Fixed order for multi-arrow cells, so a written workbook is
# reproducible byte for byte.
GLYPH_ORDER = (EAST, WEST, NORTH, SOUTH)

_KIND_OF_ZONE = {
    ZoneType.SHELF: SHELF,
    ZoneType.DROPOFF: DROPOFF,
    ZoneType.CHARGING: CHARGER,
    ZoneType.CHARGE_QUEUE: HOLDING,
    ZoneType.STAGING: PARKING,
    ZoneType.COLLECTION: PARKING,
}


@dataclass
class Layout:
    """A warehouse as plain cells, ready to be written to Excel.

    ``kinds`` maps every cell to what it is; a cell missing from it is a
    wall. ``arrows`` maps floor cells to the grid steps a robot may
    leave them by.
    """

    width: int
    height: int
    kinds: dict[Cell, str] = field(default_factory=dict)
    arrows: dict[Cell, set[tuple[int, int]]] = field(default_factory=dict)

    def kind(self, cell: Cell) -> str:
        return self.kinds.get(cell, WALL)

    def count(self, kind: str) -> int:
        return sum(1 for value in self.kinds.values() if value == kind)

    def lane(self, cell: Cell, *steps) -> None:
        self.arrows.setdefault(cell, set()).update(steps)


# ----------------------------------------------------------------------
# Layouts
# ----------------------------------------------------------------------


def blank_layout(width: int, height: int) -> Layout:
    """An empty hall: a wall all the way round, floor inside.

    Nothing is drawn. It is the sheet to start a hand-drawn map from.
    """

    if width < 5 or height < 5:
        raise ValueError("a warehouse needs at least 5 x 5 cells")

    layout = Layout(width=width, height=height)

    for y in range(1, height - 1):
        for x in range(1, width - 1):
            layout.kinds[(x, y)] = FLOOR

    return layout


def example_layout(robots: int, units: int | None = None) -> Layout:
    """A complete, valid warehouse sized for a fleet of ``robots``.

    The plan, with y running up the page::

        top edge      chargers and holding cells
        ring road     one-way, anticlockwise, just inside the edge
        middle        racking units  [up-aisle | shelves | down-aisle]
                      cut by one or more cross-bands of two rows
        bottom edge   drop-offs
        side edges    parking, enough for the whole fleet

    Why it is always valid, without trial and error: the ring road is a
    single one-way loop, every aisle and every cross-band row is
    one-way from the ring back to the ring, and every station touches
    the ring or an aisle. So every road cell can reach the ring and be
    reached from it -- the network is strongly connected -- and no road
    is ever driven both ways. Nor does it contain a 2 x 2 rotary, the
    tightest loop four robots can deadlock in (see the band rows).

    It is a starting point, not a recommendation: open it in Excel and
    move things.
    """

    if robots < 1:
        raise ValueError("robots must be at least 1")

    if units is None:
        units = max(4, round(robots / 8))

    width = 3 * units + 4

    per_side = ceil(robots / 2)
    height = max(per_side + 4, 20)

    layout = Layout(width=width, height=height)

    left, right = 1, width - 2
    bottom, top = 1, height - 2

    # ---- the ring road -------------------------------------------
    for x in range(left, right + 1):
        for y in (bottom, top):
            layout.kinds[(x, y)] = FLOOR

    for y in range(bottom, top + 1):
        for x in (left, right):
            layout.kinds[(x, y)] = FLOOR

    for x in range(left, right):
        layout.lane((x, bottom), EAST)

    for y in range(bottom, top):
        layout.lane((right, y), NORTH)

    for x in range(left + 1, right + 1):
        layout.lane((x, top), WEST)

    for y in range(bottom + 1, top + 1):
        layout.lane((left, y), SOUTH)

    # ---- cross-bands ---------------------------------------------
    inner = top - bottom - 1
    bands = max(1, inner // 18)

    # Each band is two rows: west-bound below, east-bound above. The
    # order is not cosmetic. Every pair of neighbouring aisles here runs
    # (down, up) from left to right, and with the east row BELOW, those
    # two aisles and two band rows form a 2 x 2 loop -- a rotary -- in
    # which four robots can each wait for the next forever. With the
    # east row above, every such square has an exit and no loop forms.
    band_rows = []

    for index in range(bands):
        west = bottom + 1 + round((index + 1) * inner / (bands + 1)) - 1
        band_rows.append((west + 1, west))

    crossing = {row for pair in band_rows for row in pair}

    # ---- racking units -------------------------------------------
    for unit in range(units):
        up = 2 + 3 * unit
        rack = up + 1
        down = up + 2

        for y in range(bottom + 1, top):
            layout.kinds[(up, y)] = FLOOR
            layout.lane((up, y), NORTH)

            layout.kinds[(down, y)] = FLOOR
            layout.lane((down, y), SOUTH)

            layout.kinds[(rack, y)] = FLOOR if y in crossing else SHELF

        # The ring feeds each up-aisle from the bottom and each
        # down-aisle from the top.
        layout.lane((up, bottom), NORTH)
        layout.lane((down, top), SOUTH)

    for east, west in band_rows:
        for x in range(left + 1, right):
            layout.lane((x, east), EAST)
            layout.lane((x, west), WEST)

        # Ring on-ramps into each band row.
        layout.lane((left, east), EAST)
        layout.lane((right, west), WEST)

    # ---- stations on the edge ------------------------------------
    for x in range(left + 1, right):
        layout.kinds[(x, 0)] = DROPOFF

    for index, x in enumerate(range(left + 1, right)):
        layout.kinds[(x, height - 1)] = HOLDING if index % 3 == 2 else CHARGER

    start = max(bottom, (height - per_side) // 2)

    for y in range(start, min(start + per_side, top + 1)):
        layout.kinds[(0, y)] = PARKING
        layout.kinds[(width - 1, y)] = PARKING

    return layout


def layout_of(warehouse: WarehouseMap) -> Layout:
    """A loaded map as a Layout, with its effective lanes drawn in.

    The arrows are read from the live routing policy -- whatever the
    map is actually routed by, built-in or drawn -- so writing the
    result to Excel produces a self-describing copy of the same map.
    """

    from mrta_core.warehouse_routing import (
        directional_blocked_edges,
        non_transit_cells,
    )

    grid = warehouse.grid

    layout = Layout(width=grid.width, height=grid.height)

    for y in range(grid.height):
        for x in range(grid.width):
            if (x, y) not in grid.obstacles:
                layout.kinds[(x, y)] = FLOOR

    for zone in warehouse.zones:
        kind = _KIND_OF_ZONE.get(zone.zone_type)

        if kind is None:
            raise ValueError(
                f"{zone.zone_id} is a {zone.zone_type.value} zone, which "
                "the Excel layout format does not carry"
            )

        for cell in zone.cells:
            layout.kinds[cell] = kind

    terminals = non_transit_cells(warehouse)
    blocked = directional_blocked_edges(warehouse)

    for cell, kind in layout.kinds.items():
        if kind != FLOOR or cell in terminals:
            continue

        for step in GLYPH_ORDER:
            target = (cell[0] + step[0], cell[1] + step[1])

            if layout.kind(target) != FLOOR or target in terminals:
                continue

            if (cell, target) not in blocked:
                layout.lane(cell, step)

    return layout


# ----------------------------------------------------------------------
# Writing
# ----------------------------------------------------------------------


def _excel(layout: Layout, cell: Cell) -> tuple[int, int]:
    """Grid cell -> (spreadsheet row, column), y labels running upward."""

    x, y = cell

    return (layout.height - y + 1, x + 2)


def write_layout_xlsx(layout: Layout, path, *, legend: bool = True) -> Path:
    """Write a Layout as a workbook ``load_warehouse_xlsx`` reads back.

    The map is the first, active sheet, framed in orange with X labels
    1..width along the top and bottom and Y labels height..1 down both
    sides, the same frame the V2 workbook uses. A second sheet explains
    the symbols to whoever edits it.
    """

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    def solid(rgb):
        return PatternFill(fill_type="solid", fgColor=rgb)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "MAP"

    orange = solid(ORANGE_RGB)
    centre = Alignment(horizontal="center", vertical="center")

    last_row = layout.height + 2
    last_col = layout.width + 2

    # ---- the orange coordinate frame -----------------------------
    for col in range(1, last_col + 1):
        for row in (1, last_row):
            sheet.cell(row=row, column=col).fill = orange

    for row in range(1, last_row + 1):
        for col in (1, last_col):
            sheet.cell(row=row, column=col).fill = orange

    for x in range(layout.width):
        for row in (1, last_row):
            label = sheet.cell(row=row, column=x + 2, value=x + 1)
            label.alignment = centre
            label.font = Font(bold=True)

    for y in range(layout.height):
        for col in (1, last_col):
            label = sheet.cell(row=layout.height - y + 1, column=col, value=y + 1)
            label.alignment = centre
            label.font = Font(bold=True)

    # ---- the warehouse -------------------------------------------
    for y in range(layout.height):
        for x in range(layout.width):
            row, col = _excel(layout, (x, y))
            target = sheet.cell(row=row, column=col)
            kind = layout.kind((x, y))

            target.alignment = centre

            if kind in FILL:
                target.fill = solid(FILL[kind])

            if kind in CODE:
                target.value = CODE[kind]
                target.font = Font(bold=True)

            elif kind == FLOOR and layout.arrows.get((x, y)):
                target.value = "".join(
                    GLYPH[step]
                    for step in GLYPH_ORDER
                    if step in layout.arrows[(x, y)]
                )

    for col in range(1, last_col + 1):
        sheet.column_dimensions[_column_letter(col)].width = 4.5

    sheet.freeze_panes = "B2"

    if legend:
        _write_legend(workbook)

    workbook.active = 0

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    workbook.save(path)

    return path


def _column_letter(col: int) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(col)


def _write_legend(workbook) -> None:
    from openpyxl.styles import Font, PatternFill

    sheet = workbook.create_sheet("HOW TO EDIT")

    rows = [
        ("HOW TO EDIT THIS MAP", None),
        ("", None),
        ("Only the MAP sheet is read. This sheet is ignored.", None),
        ("", None),
        ("ORANGE", ORANGE_RGB),
        ("  The coordinate frame. Do not change it except to resize.", None),
        ("RED fill", RED_RGB),
        ("  A wall. Robots cannot drive here.", None),
        ("WHITE cell", None),
        ("  Road. Type arrows for the directions a robot may LEAVE it by.", None),
        ("", None),
        ("STATIONS - type the letter in the cell (colour is optional)", None),
        ("  S   shelf, where items are picked", FILL[SHELF]),
        ("  D   drop-off, where an order is delivered", FILL[DROPOFF]),
        ("  C   charger", FILL[CHARGER]),
        ("  H   holding cell, where a robot waits for a charger", FILL[HOLDING]),
        ("  P   parking, one per robot - this is your fleet size limit", FILL[PARKING]),
        ("", None),
        ("ARROWS - type in road cells", None),
        ("  >  or →   right        <  or ←   left", None),
        ("  ^  or ↑   up           v  or ↓   down", None),
        ("  A junction gets two, for example  >^", None),
        ("", None),
        ("RULES", None),
        ("  1. Every road is one-way. Never let two cells point at each other.", None),
        ("  2. Every road cell needs at least one arrow.", None),
        ("  3. Never draw an arrow into a station or a wall.", None),
        ("     Robots drive into a station from any road next to it by themselves.", None),
        ("  4. Every station must touch at least one road.", None),
        ("  5. From any road you must be able to reach every other road.", None),
        ("", None),
        ("CHECK YOUR MAP after every change:", None),
        ("  python3 src/simulation_env/scripts/map_tool.py check --map <this file>", None),
    ]

    for index, (text, colour) in enumerate(rows, start=1):
        cell = sheet.cell(row=index, column=1, value=text)

        if index == 1 or text.isupper() or text.startswith(("STATIONS", "ARROWS", "RULES", "CHECK")):
            cell.font = Font(bold=True)

        if colour is not None:
            swatch = sheet.cell(row=index, column=2)
            swatch.fill = PatternFill(fill_type="solid", fgColor=colour)

    sheet.column_dimensions["A"].width = 78
    sheet.column_dimensions["B"].width = 6
