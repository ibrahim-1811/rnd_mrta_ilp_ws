"""M6 multi-pair arm (mp1): candidate selection and ForcedPair, from one canonical state."""

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from ilp_learning.bk_builder import derive_est_finish_s
from ilp_learning.m6_candidates import CANDIDATE_SET, pivot_of, select_candidates
from ilp_learning.m6_rollouts import corpus_builder
from ilp_learning.supervision.outcome_aware import OutcomeLabelError, outcome_labels
from simulation_env.rollout import ForcedPair, decision_ticks, probe_pairs, tables, verified
from task_management.allocation_strategy import OrderAssignment

from .conftest import ACCEPTANCE_RUN

needs_run = pytest.mark.skipif(not ACCEPTANCE_RUN.exists(), reason="runs/m2_acceptance absent")


def decision(selected=(("R1", "O1"), ("R2", "O2")), ineligible=(("R3", "O1"),)):
    rows = []
    for k, robot in enumerate(["R1", "R2", "R3"]):
        for j, order in enumerate(["O1", "O2", "O3"]):
            rows.append({
                "decision_id": 9, "robot_id": robot, "order_id": order, "fifo_position": j,
                "c_static_m": 10.0 + 3 * k + j, "est_finish_s": 20.0 + 2 * k + 4 * j,
                "eligible": (robot, order) not in ineligible, "selected": (robot, order) in selected,
            })
    return pd.DataFrame(rows)


PANEL = pd.DataFrame([
    {"decision_id": 9, "teacher": "nearest_astar", "robot_id": "R1", "order_id": "O1"},
    {"decision_id": 9, "teacher": "auction", "robot_id": "R2", "order_id": "O1"},
    {"decision_id": 9, "teacher": "auction", "robot_id": "R1", "order_id": "O3"},
    {"decision_id": 9, "teacher": "milp", "robot_id": "R2", "order_id": "O1"},
    {"decision_id": 9, "teacher": "milp", "robot_id": "R3", "order_id": "O1"},  # ineligible
])


def pairs(actions):
    return [(a.robot_id, a.order_id) for a in actions]


def test_pivot_is_the_behaviour_pair_of_the_oldest_order():
    assert pivot_of(decision()) == ("R1", "O1")


def test_candidates_span_robots_and_orders_with_behaviour_first():
    actions = select_candidates(decision(), PANEL, run_id="r", seed=1)
    assert pairs(actions)[0] == ("R1", "O1") and actions[0].sources == ["behaviour"]
    assert len({a.robot_id for a in actions}) >= 2   # another robot for the pivot order
    assert len({a.order_id for a in actions}) >= 2   # another order for the pivot robot
    assert len(set(pairs(actions))) == len(actions)  # each pair once


def test_panel_proposals_are_provenance_and_merge():
    by_pair = {(a.robot_id, a.order_id): a for a in select_candidates(decision(), PANEL, run_id="r", seed=1)}
    assert by_pair[("R2", "O1")].sources[:2] == ["panel:auction", "panel:milp"]
    assert "panel:auction" in by_pair[("R1", "O3")].sources


def test_ineligible_pairs_are_never_candidates():
    actions = select_candidates(decision(), PANEL, run_id="r", seed=1, max_actions=20)
    assert ("R3", "O1") not in pairs(actions)


def test_bounded_with_a_random_control_kept():
    actions = select_candidates(decision(), PANEL, run_id="r", seed=1, max_actions=3)
    assert len(actions) == 3
    assert actions[-1].sources == ["random"] and 0 < actions[-1].p_select <= 1
    assert all(a.p_select == 1.0 for a in actions[:-1])


def test_selection_is_deterministic_and_seeded():
    a = pairs(select_candidates(decision(), PANEL, run_id="r", seed=1))
    assert a == pairs(select_candidates(decision(), PANEL.sample(frac=1, random_state=4), run_id="r", seed=1))
    draws = {pairs(select_candidates(decision(), PANEL, run_id="r", seed=s))[-1] for s in range(30)}
    assert len(draws) > 1  # the random control does depend on the seed


def test_no_behaviour_pick_gives_no_candidates():
    assert select_candidates(decision(selected=()), PANEL, run_id="r", seed=1) == []


def test_mp1_rollouts_have_no_label_rule(tmp_path):
    (tmp_path / "run.json").write_text((ACCEPTANCE_RUN / "run.json").read_text()) if ACCEPTANCE_RUN.exists() else None
    (tmp_path / "probe.json").write_text(json.dumps({"arm": CANDIDATE_SET, "clone_verified": True}))
    if not (tmp_path / "run.json").exists():
        pytest.skip("runs/m2_acceptance absent")
    with pytest.raises(OutcomeLabelError, match="no frozen label rule"):
        outcome_labels(tmp_path)


# ---- ForcedPair semantics, on a stand-in strategy -----------------------------------

class Inner:
    def __init__(self, chosen, eligible):
        self.chosen, self.eligible = chosen, eligible

    def assign(self, *args, **kwargs):
        return [OrderAssignment(robot_id=r, order_id=o, cost=1.0, route=(), energy=None) for r, o in self.chosen]

    def score(self, *args, **kwargs):
        return [SimpleNamespace(robot_id=r, order_id=o, eligible=True, cost=1.0, route=(), energy=None)
                for r, o in self.eligible]


def force(chosen, eligible, robot, order):
    executor = SimpleNamespace(result=SimpleNamespace(decisions=1), engine=SimpleNamespace(time=5, robots={}))
    forced = ForcedPair(Inner(chosen, eligible), executor, 0, robot, order)
    timing = SimpleNamespace(finish_s=lambda c: 1.0)
    fifo = {"O1": 0, "O2": 1, "O3": 2}
    out = forced.assign(None, [], [SimpleNamespace(order_id=o) for o in fifo], fifo, timing=timing)
    return {(a.robot_id, a.order_id) for a in out}, forced.decided


ALL = [(r, o) for r in ("R1", "R2", "R3") for o in ("O1", "O2", "O3")]


def test_forced_pair_swaps_the_displaced_partners():
    got, decided = force([("R1", "O1"), ("R2", "O2")], ALL, "R2", "O1")
    assert got == {("R2", "O1"), ("R1", "O2")}
    assert (decided["forced"], decided["swap"], decided["pairs_after"]) == ("forced", "R1:O2", 2)


def test_forced_pair_drops_when_the_swap_is_ineligible():
    eligible = [p for p in ALL if p != ("R1", "O2")]
    got, decided = force([("R1", "O1"), ("R2", "O2")], eligible, "R2", "O1")
    assert got == {("R2", "O1")} and decided["swap"] == "" and decided["pairs_after"] == 1


def test_forcing_a_free_robot_onto_an_unassigned_order_adds_it():
    got, _ = force([("R1", "O1")], ALL, "R3", "O3")
    assert got == {("R1", "O1"), ("R3", "O3")}


def test_strategy_pair_and_ineligible_pair_change_nothing():
    chosen = [("R1", "O1"), ("R2", "O2")]
    assert force(chosen, ALL, "R1", "O1")[1]["forced"] == "strategy_pair"
    got, decided = force(chosen, [p for p in ALL if p != ("R3", "O3")], "R3", "O3")
    assert got == set(chosen) and decided["forced"] == "ineligible"


# ---- several robots from one recorded state ------------------------------------------

@needs_run
def test_several_robot_choices_rolled_out_from_one_canonical_state():
    manifest = json.loads((ACCEPTANCE_RUN / "run.json").read_text())
    corpus = pd.read_parquet(ACCEPTANCE_RUN / "decisions.parquet")
    frame = corpus.assign(est_finish_s=derive_est_finish_s(corpus, manifest))
    build = corpus_builder(manifest)
    first, made_in = decision_ticks(build)

    decision_id = None
    for d in sorted(made_in):
        actions = select_candidates(frame[frame["decision_id"] == d], None, run_id=manifest["run_id"], seed=1)
        if made_in[d] > 1 and len({a.robot_id for a in actions}) >= 2:
            decision_id = d
            break
    assert decision_id is not None
    plan = {decision_id: [(a.robot_id, a.order_id, a.extra(k)) for k, a in enumerate(actions)]}

    rows, checks = probe_pairs(build, plan, made_in, horizon_s=120.0, verify=1, baseline=tables(first))
    assert verified(checks)
    assert len(rows) == len(actions)
    assert len({row["forced_robot"] for row in rows}) >= 2
    assert rows[0]["forced"] == "strategy_pair"          # the behaviour branch is the run itself
    assert all(row["forced"] in {"forced", "strategy_pair"} for row in rows)
    # The same orders are measured in every branch: a common workload to compare on.
    completions = [set(row["window_completion_s"]) for row in rows if row["rollout_status"] == "complete"]
    assert completions and all(c == completions[0] for c in completions)
