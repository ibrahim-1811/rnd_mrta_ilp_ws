"""Load a warehouse map directly from an Excel XLSX layout.

The Excel workbook is the human-editable source of truth.

Coordinate frame
----------------
The workbook carries an explicit orange coordinate border.

    Orange cells:
        coordinate axes and origin annotation

    Axis row:
        warehouse X labels, 1 .. width

    Axis column:
        warehouse Y labels, 1 .. height
        displayed top-down as height .. 1

The warehouse interior is the rectangle enclosed by that border.
Its size comes from the coordinate labels, never from the extent
of the drawn content.

    grid_x = warehouse_x - 1
    grid_y = warehouse_y - 1

So warehouse (1, 1) is WorldGrid (0, 0), which Gazebo renders at
cell centre (0.5, 0.5).

Cell rules
----------
Orange cells:
    coordinate labels, never part of the warehouse

Red cells:
    blocked / non-traversable

Blank white cells:
    normal traversable floor

Merged semantic regions:
    SHELVES
    CHARGING
    DROP ZONE
    COLLECTION ZONE

Every cell of a merged semantic region becomes its own
single-cell WarehouseZone, so each shelf, charging bay, drop-off
point and collection point is individually addressable.

IDs are numbered flat across the whole warehouse, in spreadsheet
region order, then by ascending (x, y) within a region:

    shelf_001 .. shelf_072
    charging_001 .. charging_016
    charge_queue_001 .. charge_queue_00N

All semantic zone cells remain traversable.
"""

from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook

from mrta_core.grid import Cell
from mrta_core.warehouse_map import (
    WarehouseMap,
    WarehouseZone,
    ZoneType,
)
from mrta_core.world import WorldGrid


RED_RGB = "FFFF0000"

ORANGE_RGB = "FFFF9900"


ZONE_ALIASES = {
    "SHELF": ZoneType.SHELF,
    "SHELVES": ZoneType.SHELF,

    "CHARGING": ZoneType.CHARGING,
    "CHARGING ZONE": ZoneType.CHARGING,

    # Charging holding bank. Not a generic queue: these cells exist
    # only to hold robots that are waiting for, or were preempted
    # from, a charging slot.
    "CHARGE QUEUE": ZoneType.CHARGE_QUEUE,
    "CHARGING QUEUE": ZoneType.CHARGE_QUEUE,
    "CHARGE HOLDING": ZoneType.CHARGE_QUEUE,
    "CHARGING HOLDING": ZoneType.CHARGE_QUEUE,
    "CHARGE QUEUE ZONE": ZoneType.CHARGE_QUEUE,
    "CHARGING QUEUE ZONE": ZoneType.CHARGE_QUEUE,
    "CHARGE HOLDING ZONE": ZoneType.CHARGE_QUEUE,
    "CHARGING HOLDING ZONE": ZoneType.CHARGE_QUEUE,

    "DROP ZONE": ZoneType.DROPOFF,
    "DROP OFF": ZoneType.DROPOFF,
    "DROPOFF": ZoneType.DROPOFF,

    "COLLECTION": ZoneType.COLLECTION,
    "COLLECTION ZONE": ZoneType.COLLECTION,

    "PICKUP": ZoneType.PICKUP,
    "PICKUP ZONE": ZoneType.PICKUP,

    "STAGE": ZoneType.STAGING,
    "STAGE ZONE": ZoneType.STAGING,
    "STAGING": ZoneType.STAGING,
    "STAGING ZONE": ZoneType.STAGING,

    "WAITING": ZoneType.WAITING,
    "WAITING ZONE": ZoneType.WAITING,
}


ZONE_PREFIX = {
    ZoneType.SHELF: "shelf",
    ZoneType.CHARGING: "charging",
    ZoneType.CHARGE_QUEUE: "charge_queue",
    ZoneType.DROPOFF: "dropoff",
    ZoneType.COLLECTION: "collection",
    ZoneType.PICKUP: "pickup",
    ZoneType.STAGING: "staging",
    ZoneType.WAITING: "waiting",
}


def _normalise(value) -> str:
    if value is None:
        return ""

    return " ".join(
        str(value)
        .strip()
        .replace("_", " ")
        .replace("-", " ")
        .upper()
        .split()
    )


def _zone_type(value):
    return ZONE_ALIASES.get(
        _normalise(value)
    )


def _fill_rgb(cell) -> str | None:
    fill = cell.fill

    if fill is None or fill.fill_type != "solid":
        return None

    rgb = fill.fgColor.rgb

    if rgb is None:
        return None

    return str(rgb).upper()


def _is_red(cell) -> bool:
    rgb = _fill_rgb(cell)

    return (
        rgb is not None
        and rgb.endswith(RED_RGB[2:])
    )


def _is_orange(cell) -> bool:
    rgb = _fill_rgb(cell)

    return (
        rgb is not None
        and rgb.endswith(ORANGE_RGB[2:])
    )


def _coordinate_label(value) -> int | None:
    """Read a warehouse coordinate label written in a border cell."""

    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        number = float(value)

    else:
        text = str(value).strip()

        if not text:
            return None

        try:
            number = float(text)
        except ValueError:
            return None

    if number != int(number):
        return None

    return int(number)


# ------------------------------------------------------
# Coordinate frame
# ------------------------------------------------------


@dataclass(frozen=True)
class XlsxCoordinateFrame:
    """Authoritative mapping from spreadsheet cells to grid cells.

    ``excel_col_to_x`` and ``excel_row_to_y`` hold the one-based
    warehouse coordinates read from the orange border, so every
    conversion is traceable back to a label a human typed.
    """

    width: int
    height: int
    excel_col_to_x: dict[int, int]
    excel_row_to_y: dict[int, int]
    axis_rows: tuple[int, ...]
    axis_cols: tuple[int, ...]

    def contains(self, row: int, col: int) -> bool:
        """Is this spreadsheet cell inside the warehouse interior?"""
        return (
            row in self.excel_row_to_y
            and col in self.excel_col_to_x
        )

    def warehouse_coord(
        self,
        row: int,
        col: int,
    ) -> tuple[int, int]:
        """Spreadsheet cell -> one-based warehouse (X, Y)."""
        return (
            self.excel_col_to_x[col],
            self.excel_row_to_y[row],
        )

    def to_grid(
        self,
        row: int,
        col: int,
    ) -> Cell:
        """Spreadsheet cell -> zero-based WorldGrid cell."""

        warehouse_x, warehouse_y = self.warehouse_coord(
            row,
            col,
        )

        return (
            warehouse_x - 1,
            warehouse_y - 1,
        )


def _contiguous(values: list[int]) -> bool:
    return values == list(
        range(
            values[0],
            values[0] + len(values),
        )
    )


def _read_axis_labels(
    sheet,
    *,
    interior: list[int],
    axis_lines: list[int],
    along_rows: bool,
    axis_name: str,
) -> dict[int, int]:
    """Read coordinate labels for one axis of the border.

    ``interior`` holds the warehouse rows or columns being labelled.
    ``axis_lines`` holds the fully orange border lines to read from.
    Every border line that carries a label must agree.
    """

    labels: dict[int, int] = {}

    for index in interior:

        found: dict[int, int] = {}

        for line in axis_lines:

            if along_rows:
                cell = sheet.cell(row=line, column=index)
            else:
                cell = sheet.cell(row=index, column=line)

            label = _coordinate_label(cell.value)

            if label is not None:
                found[line] = label

        if not found:
            raise ValueError(
                f"missing {axis_name} coordinate label "
                f"for spreadsheet "
                f"{'column' if along_rows else 'row'} {index}"
            )

        distinct = set(found.values())

        if len(distinct) != 1:
            raise ValueError(
                f"conflicting {axis_name} coordinate labels "
                f"for spreadsheet "
                f"{'column' if along_rows else 'row'} {index}: "
                f"{sorted(distinct)}"
            )

        labels[index] = distinct.pop()

    expected = list(
        range(
            1,
            len(interior) + 1,
        )
    )

    if sorted(labels.values()) != expected:
        raise ValueError(
            f"{axis_name} coordinate labels must be "
            f"{expected[0]} .. {expected[-1]}, got "
            f"{sorted(labels.values())}"
        )

    ordered = [
        labels[index]
        for index in interior
    ]

    ascending = ordered == sorted(ordered)
    descending = ordered == sorted(ordered, reverse=True)

    if not (ascending or descending):
        raise ValueError(
            f"{axis_name} coordinate labels must run "
            f"monotonically along the border, got {ordered}"
        )

    return labels


def detect_coordinate_frame(sheet) -> XlsxCoordinateFrame:
    """Discover the warehouse coordinate frame from the orange border."""

    orange = {
        (cell.row, cell.column)
        for row in sheet.iter_rows()
        for cell in row
        if _is_orange(cell)
    }

    if not orange:
        raise ValueError(
            "no orange coordinate border found; "
            "the warehouse sheet must carry explicit "
            "X and Y coordinate headers"
        )

    min_row = min(row for row, _ in orange)
    max_row = max(row for row, _ in orange)

    min_col = min(col for _, col in orange)
    max_col = max(col for _, col in orange)

    # A border line is orange all the way across the frame.
    # Everything else inside the frame is warehouse.

    axis_rows = [
        row
        for row in range(min_row, max_row + 1)
        if all(
            (row, col) in orange
            for col in range(min_col, max_col + 1)
        )
    ]

    axis_cols = [
        col
        for col in range(min_col, max_col + 1)
        if all(
            (row, col) in orange
            for row in range(min_row, max_row + 1)
        )
    ]

    if not axis_rows or not axis_cols:
        raise ValueError(
            "coordinate border is incomplete; "
            "expected a fully orange header row "
            "and a fully orange header column"
        )

    interior_rows = [
        row
        for row in range(min_row, max_row + 1)
        if row not in set(axis_rows)
    ]

    interior_cols = [
        col
        for col in range(min_col, max_col + 1)
        if col not in set(axis_cols)
    ]

    if not interior_rows or not interior_cols:
        raise ValueError(
            "coordinate border encloses no warehouse cells"
        )

    if not _contiguous(interior_rows) or not _contiguous(interior_cols):
        raise ValueError(
            "warehouse interior must be a single rectangle; "
            "the coordinate border splits it"
        )

    excel_col_to_x = _read_axis_labels(
        sheet,
        interior=interior_cols,
        axis_lines=axis_rows,
        along_rows=True,
        axis_name="X",
    )

    excel_row_to_y = _read_axis_labels(
        sheet,
        interior=interior_rows,
        axis_lines=axis_cols,
        along_rows=False,
        axis_name="Y",
    )

    return XlsxCoordinateFrame(
        width=len(interior_cols),
        height=len(interior_rows),
        excel_col_to_x=excel_col_to_x,
        excel_row_to_y=excel_row_to_y,
        axis_rows=tuple(axis_rows),
        axis_cols=tuple(axis_cols),
    )


def read_xlsx_coordinate_frame(
    path: str | Path,
    *,
    sheet_name: str | None = None,
) -> XlsxCoordinateFrame:
    """Load only the coordinate frame, for inspection and validation."""

    workbook = load_workbook(
        Path(path),
        data_only=True,
    )

    if sheet_name is None:
        sheet = workbook.active
    else:
        sheet = workbook[sheet_name]

    return detect_coordinate_frame(sheet)


# ------------------------------------------------------
# Map loading
# ------------------------------------------------------


def load_warehouse_xlsx(
    path: str | Path,
    *,
    sheet_name: str | None = None,
    resolution: float = 1.0,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> WarehouseMap:

    path = Path(path)

    workbook = load_workbook(
        path,
        data_only=True,
    )

    if sheet_name is None:
        sheet = workbook.active
    else:
        sheet = workbook[sheet_name]

    # --------------------------------------------------
    # The coordinate headers define the grid.
    # --------------------------------------------------

    frame = detect_coordinate_frame(sheet)

    # --------------------------------------------------
    # Find semantic merged ranges.
    #
    # A range fully outside the warehouse is a legend or a
    # note and is ignored. A range straddling the border is
    # a map error.
    # --------------------------------------------------

    semantic_ranges = []

    merged_top_left = set()

    for merged in sorted(
        sheet.merged_cells.ranges,
        key=lambda r: (
            r.min_row,
            r.min_col,
            r.max_row,
            r.max_col,
        ),
    ):

        top_left = sheet.cell(
            row=merged.min_row,
            column=merged.min_col,
        )

        zone_type = _zone_type(
            top_left.value
        )

        if zone_type is None:
            continue

        corners = [
            (merged.min_row, merged.min_col),
            (merged.max_row, merged.max_col),
        ]

        inside = [
            frame.contains(row, col)
            for row, col in corners
        ]

        if not any(inside):
            continue

        if not all(inside):
            raise ValueError(
                f"semantic region {str(merged)!r} "
                f"crosses the coordinate border"
            )

        semantic_ranges.append(
            (
                merged,
                zone_type,
            )
        )

        merged_top_left.add(
            (
                merged.min_row,
                merged.min_col,
            )
        )

    # --------------------------------------------------
    # Also support non-merged semantic cells,
    # and find red blocked cells.
    #
    # Only warehouse interior cells are considered, so
    # orange coordinate labels can never become zones
    # or obstacles.
    # --------------------------------------------------

    semantic_cells = []

    red_cells_excel = []

    for row in sorted(frame.excel_row_to_y):
        for col in sorted(frame.excel_col_to_x):

            cell = sheet.cell(row=row, column=col)

            if _is_orange(cell):
                continue

            if _is_red(cell):
                red_cells_excel.append(
                    (
                        row,
                        col,
                    )
                )
                continue

            if (row, col) in merged_top_left:
                continue

            zone_type = _zone_type(
                cell.value
            )

            if zone_type is not None:

                semantic_cells.append(
                    (
                        row,
                        col,
                        zone_type,
                    )
                )

            elif _normalise(cell.value):
                # Refuse to silently drop labelled map content. A
                # mistyped zone name would otherwise become plain
                # floor and quietly delete stations.
                raise ValueError(
                    f"unrecognised zone label "
                    f"{cell.value!r} at {cell.coordinate}; "
                    f"known labels: "
                    f"{', '.join(sorted(ZONE_ALIASES))}"
                )

    # --------------------------------------------------
    # Occupancy layer.
    # --------------------------------------------------

    obstacles = {
        frame.to_grid(
            row,
            col,
        )
        for row, col
        in red_cells_excel
    }

    grid = WorldGrid(
        origin_x=origin_x,
        origin_y=origin_y,
        resolution=resolution,
        width=frame.width,
        height=frame.height,
        obstacles=frozenset(
            obstacles
        ),
    )

    # --------------------------------------------------
    # Semantic layer.
    # --------------------------------------------------

    counters = {}

    zones = []

    for merged, zone_type in semantic_ranges:

        prefix = ZONE_PREFIX[
            zone_type
        ]

        cells = set()

        for row in range(
            merged.min_row,
            merged.max_row + 1,
        ):

            for col in range(
                merged.min_col,
                merged.max_col + 1,
            ):

                cell = frame.to_grid(
                    row,
                    col,
                )

                if cell in obstacles:
                    raise ValueError(
                        f"semantic region {str(merged)!r} "
                        f"overlaps a blocked red cell "
                        f"at grid cell {cell}"
                    )

                cells.add(
                    cell
                )

        # The merge marks one physical area, but every cell in it
        # is a separate station the robot can be sent to.
        for cell in sorted(cells):

            counters[zone_type] = (
                counters.get(
                    zone_type,
                    0,
                )
                + 1
            )

            zone_id = (
                f"{prefix}_"
                f"{counters[zone_type]:03d}"
            )

            zones.append(
                WarehouseZone(
                    zone_id=zone_id,
                    zone_type=zone_type,
                    cells=frozenset(
                        {cell}
                    ),
                )
            )

    for row, col, zone_type in sorted(
        semantic_cells,
        key=lambda item: (
            item[0],
            item[1],
        ),
    ):

        counters[zone_type] = (
            counters.get(
                zone_type,
                0,
            )
            + 1
        )

        prefix = ZONE_PREFIX[
            zone_type
        ]

        zone_id = (
            f"{prefix}_"
            f"{counters[zone_type]:03d}"
        )

        cell = frame.to_grid(
            row,
            col,
        )

        if cell in obstacles:
            raise ValueError(
                f"{zone_id} overlaps "
                f"a blocked red cell"
            )

        zones.append(
            WarehouseZone(
                zone_id=zone_id,
                zone_type=zone_type,
                cells=frozenset(
                    {cell}
                ),
            )
        )

    return WarehouseMap(
        grid=grid,
        zones=tuple(
            zones
        ),
    )
