"""The one-pass station audit finds what planning every leg would find."""

import importlib.util
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_map_topology.py"
spec = importlib.util.spec_from_file_location("check_map_topology", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

# A one-way ring of four road cells, with a station hanging off two of them.
A, B, C, D = (1, 1), (2, 1), (2, 2), (1, 2)
SHELF, DROP = (1, 0), (3, 2)
ROADS = {A, B, C, D}
RING = {A: [B], B: [C], C: [D], D: [A]}
STATIONS = {"shelf_1": SHELF, "dropoff_1": DROP}


def _closed(*open_edges):
    """Every edge touching a station is blocked except the ones named."""
    cells = list(ROADS) + [SHELF, DROP]
    edges = {(a, b) for a in cells for b in gate._neighbors(a) if SHELF in (a, b) or DROP in (a, b)}
    return edges - set(open_edges)


SERVICE = ((SHELF, A), (A, SHELF), (DROP, C), (C, DROP))


def _lanes(*extra):
    return {(a, b) for a, nexts in RING.items() for b in nexts} | set(SERVICE) | set(extra)


def audit(blocked, adj=RING, connected=True, lanes=None):
    return gate.audit_station_legs(STATIONS, ROADS, adj, blocked, lanes or _lanes(), connected)


def test_a_clean_map_has_no_findings():
    assert audit(_closed(*SERVICE)) == ([], [], [])


def test_a_station_with_no_exit_cannot_start_a_leg():
    unreachable, _, _ = audit(_closed((A, SHELF), (DROP, C), (C, DROP)))
    assert unreachable == [("shelf_1", "dropoff_1")]


def test_a_station_with_no_entrance_cannot_end_a_leg():
    unreachable, _, _ = audit(_closed((SHELF, A), (A, SHELF), (DROP, C)))
    assert unreachable == [("shelf_1", "dropoff_1")]


def test_a_broken_road_network_is_searched_rather_than_assumed():
    broken = {A: [B], B: [], C: [D], D: [A]}
    unreachable, _, _ = audit(_closed(*SERVICE), adj=broken, connected=False)
    assert unreachable == [("shelf_1", "dropoff_1")]


def test_an_edge_the_nav_graph_lacks_is_reported():
    lanes = _lanes() - {(A, B)}
    assert [row[2] for row in audit(_closed(*SERVICE), lanes=lanes)[2]] == [(A, B)]


def test_adjacent_stations_with_an_open_edge_are_a_terminal_hop():
    stations = {"shelf_1": SHELF, "shelf_2": (0, 0)}
    blocked = (_closed(*SERVICE) | {((0, 0), SHELF)}) - {(SHELF, (0, 0))}
    _, hop, _ = gate.audit_station_legs(stations, ROADS, RING, blocked, _lanes(), True)
    assert hop == [("shelf_1", "shelf_2", (SHELF, (0, 0)))]


def test_the_reference_map_passes_and_reports_progress():
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(SCRIPT.parents[1] / "config" / "warehouse.yaml")],
        capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stdout
    assert "auditing 523452 station legs" in done.stdout
    assert "status                   : OK" in done.stdout
