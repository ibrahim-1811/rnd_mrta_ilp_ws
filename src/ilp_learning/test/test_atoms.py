import re
from ilp_learning.atoms import (
    ProvenanceTracker,
    encode_decision_id,
    encode_order_id,
    encode_robot_id,
)


def test_atom_syntax():
    pattern = re.compile(r"^[a-z][a-z0-9_]*$")
    d = encode_decision_id("run_001", 5)
    r = encode_robot_id("run_001", 5, "R01")
    o = encode_order_id("run_001", 5, "O0042")

    assert pattern.match(d) is not None
    assert pattern.match(r) is not None
    assert pattern.match(o) is not None
    assert d.startswith("d_")
    assert r.startswith("r_")
    assert o.startswith("o_")


def test_scoping_and_collision_resistance():
    d1 = encode_decision_id("run_001", 5)
    d2 = encode_decision_id("run_001", 6)
    d3 = encode_decision_id("run_002", 5)

    assert d1 != d2
    assert d1 != d3

    r1 = encode_robot_id("run_001", 5, "R01")
    r2 = encode_robot_id("run_001", 5, "R02")
    r3 = encode_robot_id("run_001", 6, "R01")

    assert r1 != r2
    assert r1 != r3


def test_provenance_round_trip(tmp_path):
    tracker = ProvenanceTracker()
    d = tracker.get_or_create_decision("run_test", 10, tick=33.33)
    r = tracker.get_or_create_robot("run_test", 10, "R05")
    o = tracker.get_or_create_order("run_test", 10, "O0100")

    tracker.save_parquet(tmp_path)
    loaded = ProvenanceTracker.load_parquet(tmp_path)

    assert d in loaded.decisions
    assert loaded.decisions[d].decision_id == 10
    assert loaded.decisions[d].run_id == "run_test"

    assert r in loaded.robots
    assert loaded.robots[r].robot_id == "R05"

    assert o in loaded.orders
    assert loaded.orders[o].order_id == "O0100"
