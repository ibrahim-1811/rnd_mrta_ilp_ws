#!/usr/bin/env python3

"""Make, check and inspect warehouse maps.

    # start an empty map to draw by hand
    map_tool.py new      --width 30 --height 40 --out maps/my_map.xlsx

    # or start from a complete, valid map sized for a fleet
    map_tool.py example  --robots 100 --out maps/my_map.xlsx

    # after every edit: what is wrong, and in which Excel cell
    map_tool.py check    --map maps/my_map.xlsx

    # print the map and its lanes in the terminal
    map_tool.py show     --map maps/my_map.xlsx

    # draw a map's effective lanes into a copy of it as arrows
    map_tool.py export   --map test/fixtures/CSV_To_Gazebo_v2.xlsx --out v2_drawn.xlsx

The step-by-step guide is docs/MAKE_A_MAP.md.
"""

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]

for package in ("mrta_core", "simulation_env", "task_management"):
    candidate = ROOT / "src" / package

    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from mrta_core.map_builder import (  # noqa: E402
    GLYPH_ORDER,
    blank_layout,
    example_layout,
    layout_of,
    write_layout_xlsx,
)
from mrta_core.map_check import Problem, check_map  # noqa: E402
from mrta_core.warehouse_map import ZoneType  # noqa: E402
from mrta_core.warehouse_map_xlsx import (  # noqa: E402
    load_warehouse_xlsx,
    read_xlsx_coordinate_frame,
)
from mrta_core.warehouse_routing import (  # noqa: E402
    directional_blocked_edges,
    non_transit_cells,
)


# How many problems of one kind to print before summarising the rest.
SHOW_PER_KIND = 5


def excel_names(path):
    """grid cell -> Excel cell name, from the map's own labels."""

    from openpyxl.utils import get_column_letter

    frame = read_xlsx_coordinate_frame(path)

    names = {}

    for row in frame.excel_row_to_y:
        for col in frame.excel_col_to_x:
            names[frame.to_grid(row, col)] = f"{get_column_letter(col)}{row}"

    return names


def where(cells, names, limit=8):
    shown = [f"{names.get(cell, '?')} {cell}" for cell in cells[:limit]]

    more = len(cells) - limit

    return ", ".join(shown) + (f", ... and {more} more" if more > 0 else "")


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------


def cmd_new(args) -> int:
    layout = blank_layout(args.width, args.height)
    path = write_layout_xlsx(layout, args.out)

    print(f"wrote {path}")
    print(f"  {args.width} x {args.height} cells: a red wall round the edge, white floor inside")
    print("  Nothing is drawn yet. Open it in Excel and follow docs/MAKE_A_MAP.md.")

    return 0


def cmd_example(args) -> int:
    layout = example_layout(args.robots, args.units)
    path = write_layout_xlsx(layout, args.out)

    print(f"wrote {path}")
    print(f"  size       : {layout.width} x {layout.height} cells")
    print(f"  parking    : {layout.count('parking')}   (the most robots it can run)")
    print(f"  shelves    : {layout.count('shelf')}")
    print(f"  drop-offs  : {layout.count('dropoff')}")
    print(f"  chargers   : {layout.count('charger')}")
    print(f"  holding    : {layout.count('holding')}")
    print()
    print("  Check it:  map_tool.py check --map " + str(path))

    return 0


def cmd_check(args) -> int:
    print("=" * 72)
    print("MAP CHECK")
    print("=" * 72)
    print(f"  map        : {args.map}")

    # Drawing errors -- an arrow into a wall, an unknown label -- are
    # found while loading and already name their Excel cell.
    try:
        warehouse = load_warehouse_xlsx(args.map)

    except ValueError as error:
        print()
        print("  The map could not be read:")
        print(f"    {error}")
        print()
        print("status                   : FAIL")
        return 1

    names = excel_names(args.map)
    report = check_map(warehouse)

    print(f"  size       : {report.width} x {report.height} cells")
    print(f"  lanes      : {'drawn in the map' if report.drawn else 'built-in V2 policy (no arrows in the map)'}")
    print(f"  roads      : {report.roads} cells")

    for kind, count in report.stations.items():
        print(f"  {kind:<11}: {count}")

    print(f"  max robots : {report.max_robots()}   (one parking cell per robot)")

    if args.robots and args.robots > report.max_robots():
        report.problems.append(
            Problem(
                "not enough parking",
                (),
                f"you asked for {args.robots} robots but the map has only "
                f"{report.max_robots()} parking (P) cells. Add P cells.",
            )
        )

    print()

    if report.clean:
        print("  No problems found.")
        print()
        print("  Next, the full acceptance gate (slower: it plans a route")
        print("  between every pair of stations):")
        print("    python3 src/simulation_env/scripts/check_map_topology.py --map " + str(args.map))
        print()
        print("status                   : OK")
        return 0

    if not report.drawn and report.errors:
        print("  NOTE: this map has no arrows yet, so it is being read with the")
        print("  built-in V2 lane rules, which only fit the V2 layout. On your own")
        print("  map, draw arrows on every road cell (see the HOW TO EDIT sheet).")
        print()

    number = 0

    for heading, problems in (
        ("ERRORS - robots cannot use the map until these are fixed", report.errors),
        ("WARNINGS - the map runs, but these get riskier with more robots", report.warnings),
    ):
        if not problems:
            continue

        print(f"  {heading}")
        print()

        # The same mistake repeated across a whole area is one lesson,
        # not three hundred. Show a few of each kind, then count the rest.
        shown = {}

        for problem in problems:
            shown[problem.kind] = shown.get(problem.kind, 0) + 1

            if shown[problem.kind] > SHOW_PER_KIND:
                continue

            number += 1

            print(f"  {number}. {problem.kind.upper()}")

            if problem.cells:
                print(f"     at {where(list(problem.cells), names)}")

            print(f"     {problem.message}")
            print()

        for kind, count in shown.items():
            if count > SHOW_PER_KIND:
                print(f"     ... and {count - SHOW_PER_KIND} more {kind} problem(s) like these")
                print()

    if report.ok:
        print(f"  {len(report.warnings)} warning(s), no errors. The map is usable;")
        print("  for a large fleet, fix the warnings too.")
        print()
        print("status                   : OK (with warnings)")
        return 0

    print("  Fix them in Excel, save, and run this again.")
    print()
    print("status                   : FAIL")

    return 1


def cmd_show(args) -> int:
    warehouse = load_warehouse_xlsx(args.map)
    grid = warehouse.grid

    terminals = non_transit_cells(warehouse)
    blocked = directional_blocked_edges(warehouse)

    letter = {
        ZoneType.SHELF: "S",
        ZoneType.DROPOFF: "D",
        ZoneType.CHARGING: "C",
        ZoneType.CHARGE_QUEUE: "H",
        ZoneType.STAGING: "P",
        ZoneType.COLLECTION: "P",
    }

    # One character per cell, so a junction shows its row direction --
    # which is what makes the traffic flow readable at a glance.
    arrow = {(1, 0): ">", (-1, 0): "<", (0, 1): "^", (0, -1): "v"}

    print("     " + "".join(str(x % 10) for x in range(grid.width)))

    # Top of the sheet first, as it appears in Excel.
    for y in reversed(range(grid.height)):
        row = []

        for x in range(grid.width):
            cell = (x, y)

            if cell in grid.obstacles:
                row.append("#")
                continue

            zone = warehouse.zone_at(cell)

            if zone is not None and cell in terminals:
                row.append(letter.get(zone.zone_type, "?"))
                continue

            exits = [
                step
                for step in GLYPH_ORDER
                if (cell, (x + step[0], y + step[1])) not in blocked
                and (x + step[0], y + step[1]) not in terminals
                and (x + step[0], y + step[1]) not in grid.obstacles
                and 0 <= x + step[0] < grid.width
                and 0 <= y + step[1] < grid.height
            ]

            horizontal = [s for s in exits if s[1] == 0]
            vertical = [s for s in exits if s[0] == 0]

            if len(horizontal) == 1:
                row.append(arrow[horizontal[0]])
            elif len(vertical) == 1:
                row.append(arrow[vertical[0]])
            elif exits:
                row.append("+")
            else:
                row.append(".")

        print(f"  {y + 1:>3}" + " " + "".join(row))

    print()
    print("  # wall   S shelf   D drop-off   C charger   H holding   P parking")
    print("  > < ^ v  one-way road ('.' = no way out)")

    return 0


def cmd_export(args) -> int:
    warehouse = load_warehouse_xlsx(args.map)
    path = write_layout_xlsx(layout_of(warehouse), args.out)

    print(f"wrote {path}")
    print("  The same map, with the lanes it is routed by drawn in as arrows.")
    print("  Station ids are renumbered top-to-bottom, left-to-right, so order")
    print("  files written for the original map do not apply to this copy.")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    commands = parser.add_subparsers(dest="command", required=True)

    new = commands.add_parser("new", help="an empty map to draw by hand")
    new.add_argument("--width", type=int, required=True)
    new.add_argument("--height", type=int, required=True)
    new.add_argument("--out", required=True)
    new.set_defaults(run=cmd_new)

    example = commands.add_parser("example", help="a complete, valid map for a fleet")
    example.add_argument("--robots", type=int, required=True)
    example.add_argument(
        "--units",
        type=int,
        default=None,
        help="number of racking units (default: about one per 8 robots)",
    )
    example.add_argument("--out", required=True)
    example.set_defaults(run=cmd_example)

    check = commands.add_parser("check", help="find mistakes, by Excel cell")
    check.add_argument("--map", required=True)
    check.add_argument(
        "--robots",
        type=int,
        default=0,
        help="also check the map can hold this many robots",
    )
    check.set_defaults(run=cmd_check)

    show = commands.add_parser("show", help="print the map and its lanes")
    show.add_argument("--map", required=True)
    show.set_defaults(run=cmd_show)

    export = commands.add_parser("export", help="draw a map's lanes into a copy")
    export.add_argument("--map", required=True)
    export.add_argument("--out", required=True)
    export.set_defaults(run=cmd_export)

    args = parser.parse_args()

    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
