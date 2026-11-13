"""The held-out transfer map H100 and the --map / --purpose transfer plumbing.

DECISIONS.md, "Warehouse Scalability": one held-out map of a different
layout and size, never trained on, used only to test whether rules
learned on W100 transfer. The full acceptance gate
(check_map_topology.py, about 3 minutes) is run by hand; these tests
keep the map, its generator and the runner options from drifting.
"""

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import openpyxl
import pytest

from mrta_core.warehouse_map import ZoneType
from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
from mrta_core.warehouse_routing import directional_blocked_edges, non_transit_cells
from simulation_env.seed_split import (
    FINAL_RESERVED,
    TRANSFER_RESERVED,
    SeedLeakageError,
    check_purpose,
    role_of,
)


PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[1]
SCRIPTS = PACKAGE / "scripts"
MAPS = PACKAGE / "maps"
H100 = MAPS / "H100.xlsx"
W100 = MAPS / "W100.xlsx"


def script(name):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))

    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def cells(path):
    book = openpyxl.load_workbook(path)
    return {
        name: [[cell.value for cell in row] for row in book[name].iter_rows()]
        for name in book.sheetnames
    }


def zones(warehouse, kind):
    return [cell for zone in warehouse.zones_of_type(kind) for cell in zone.cells]


# -- the map -----------------------------------------------------------


def test_the_generator_rebuilds_the_committed_map(tmp_path):
    make = script("make_heldout_map")
    out = tmp_path / "H100.xlsx"

    make.write_layout_xlsx(make.heldout_layout(), out)

    assert cells(out) == cells(H100)


def test_h100_has_the_planned_stations_and_a_one_way_connected_core():
    warehouse = load_warehouse_xlsx(H100)
    grid = warehouse.grid

    assert (grid.width, grid.height) == (76, 28)
    assert {kind: len(zones(warehouse, kind)) for kind in (
        ZoneType.SHELF, ZoneType.DROPOFF, ZoneType.CHARGING, ZoneType.CHARGE_QUEUE, ZoneType.STAGING,
    )} == {
        ZoneType.SHELF: 512,
        ZoneType.DROPOFF: 36,
        ZoneType.CHARGING: 24,
        ZoneType.CHARGE_QUEUE: 12,
        ZoneType.STAGING: 100,
    }

    terminals = non_transit_cells(warehouse)
    blocked = directional_blocked_edges(warehouse, terminals)
    transit = {
        (x, y)
        for y in range(grid.height)
        for x in range(grid.width)
        if (x, y) not in grid.obstacles and (x, y) not in terminals
    }

    forward = {cell: [] for cell in transit}
    reverse = {cell: [] for cell in transit}

    for x, y in transit:
        for step in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if step in transit and ((x, y), step) not in blocked:
                forward[(x, y)].append(step)
                reverse[step].append((x, y))

    two_way = [(a, b) for a in transit for b in forward[a] if a < b and a in forward[b]]
    assert two_way == []

    def reach(start, adjacency):
        seen, stack = {start}, [start]
        while stack:
            for nxt in adjacency[stack.pop()]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    start = min(transit)
    assert reach(start, forward) == transit
    assert reach(start, reverse) == transit
    assert len(transit) == 1412


def test_h100_is_a_different_layout_from_w100():
    held, main = load_warehouse_xlsx(H100), load_warehouse_xlsx(W100)

    assert (held.grid.width, held.grid.height) != (main.grid.width, main.grid.height)

    # Drop-offs on the side walls (W100: the bottom wall).
    assert {x for x, _ in zones(held, ZoneType.DROPOFF)} == {0, held.grid.width - 1}
    assert {y for _, y in zones(main, ZoneType.DROPOFF)} == {0}

    # Chargers in one block at the top-right (W100: spread over the top wall).
    held_x = [x for x, _ in zones(held, ZoneType.CHARGING)]
    main_x = [x for x, _ in zones(main, ZoneType.CHARGING)]
    assert min(held_x) > held.grid.width // 2
    assert min(main_x) < main.grid.width // 4 and max(main_x) > 3 * main.grid.width // 4


def test_orders_are_generated_from_the_named_map():
    generate = script("generate_orders")

    held = generate.stations_of(generate.DEFAULT_CONFIG, "H100.xlsx")
    main = generate.stations_of(generate.DEFAULT_CONFIG)

    assert (len(held[0]), len(held[1])) == (512, 36)
    assert (len(main[0]), len(main[1])) == (552, 36)


# -- seeds -------------------------------------------------------------


def test_transfer_seeds_are_sealed_and_their_own_role():
    assert list(TRANSFER_RESERVED) == list(range(301, 331))
    assert role_of(301) == "transfer"

    with pytest.raises(SeedLeakageError, match="held-out map evaluation"):
        check_purpose([301], None)

    check_purpose(TRANSFER_RESERVED, "transfer")

    with pytest.raises(SeedLeakageError, match="not transfer"):
        check_purpose([FINAL_RESERVED[0]], "transfer")

    with pytest.raises(SeedLeakageError, match="not final"):
        check_purpose([301], "final")


# -- the runners -------------------------------------------------------


def run(name, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        capture_output=True, text=True, cwd=ROOT, timeout=600,
    )


def experiment(tmp_path, name, *extra):
    return run(
        "run_experiment.py",
        "--out", str(tmp_path), "--name", name,
        "--strategies", "nearest_astar",
        "--robots", "4", "--orders", "3", "--jobs", "2",
        *extra,
    )


def test_run_workload_refuses_a_map_without_its_own_orders():
    refused = run("run_workload.py", "--map", "H100.xlsx", "--robots", "4", "--orders", "3", "--quiet")

    assert refused.returncode == 2
    assert "--map needs --orders-file" in refused.stderr


@pytest.mark.parametrize(
    "extra, message",
    [
        (("--map", "H100.xlsx", "--workloads", "reference"), "--map needs --workloads seeded"),
        (("--purpose", "transfer", "--seeds", "301"), "--purpose transfer is the held-out map"),
        (("--map", "NO_SUCH_MAP.xlsx"), "not found"),
        (("--seeds", "301"), "reserved for the held-out map evaluation"),
    ],
)
def test_the_runner_refuses_a_misused_map_or_transfer_seed(tmp_path, extra, message):
    refused = experiment(tmp_path, "refused", *extra)

    assert refused.returncode == 2
    assert message in refused.stderr
    assert not (tmp_path / "refused").exists()


def test_an_experiment_on_the_heldout_map_runs_there_and_cannot_pair_with_w100(tmp_path):
    held = experiment(tmp_path, "held", "--map", "H100.xlsx", "--seeds", "1")
    assert held.returncode == 0, held.stdout + held.stderr

    main = experiment(tmp_path, "main", "--seeds", "1")
    assert main.returncode == 0, main.stdout + main.stderr

    h100_sha = hashlib.sha256(H100.read_bytes()).hexdigest()

    spec = json.loads((tmp_path / "held" / "experiment.json").read_text())["spec"]
    assert spec["map"] == {"file": "H100.xlsx", "sha256": h100_sha}
    assert json.loads((tmp_path / "main" / "experiment.json").read_text())["spec"]["map"] is None

    manifest = json.loads((tmp_path / "held" / "nearest_astar" / "r4_s1" / "run.json").read_text())
    assert manifest["map_sha256"] == h100_sha
    assert Path(manifest["map_path"]).name == "H100.xlsx"

    # The seed's workload is the H100 workload of that seed, not W100's.
    generate = script("generate_orders")
    expected = generate.build(2000, 1, *generate.stations_of(generate.DEFAULT_CONFIG, "H100.xlsx"))
    assert (tmp_path / "held" / "workloads" / "orders_s1.csv").read_text() == expected

    def signature(name):
        text = (tmp_path / name / "summary.csv").read_text().splitlines()
        header = text[0].split(",")
        return text[1].split(",")[header.index("settings_sha256")]

    assert signature("held") != signature("main")
