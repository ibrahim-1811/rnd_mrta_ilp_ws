"""M6-MP continuation policies: the same forced action, continued by different policies."""

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from ilp_learning.bk_builder import derive_est_finish_s
from ilp_learning.m6_candidates import select_candidates
from ilp_learning.m6_rollouts import corpus_builder
from ilp_learning.supervision.outcome_common import (
    JW2,
    cross_policy_labels,
    cross_policy_report,
    window_orders,
)
from simulation_env.rollout import ForcedPair, decision_ticks, probe_pairs, tables, verified
from task_management.allocation_strategy import OrderAssignment

from .conftest import ACCEPTANCE_RUN

needs_run = pytest.mark.skipif(not ACCEPTANCE_RUN.exists(), reason="runs/m2_acceptance absent")


# ---- routing: behaviour before and at the forced decision, continuation after -------

class Policy:
    def __init__(self, name, pairs):
        self.name, self.pairs, self.calls = name, pairs, []

    def assign(self, *args, **kwargs):
        self.calls.append(1)
        return [OrderAssignment(robot_id=r, order_id=o, cost=1.0, route=(), energy=None) for r, o in self.pairs]

    def score(self, *args, **kwargs):
        return [SimpleNamespace(robot_id=r, order_id=o, eligible=True, cost=1.0, route=(), energy=None)
                for r in ("R1", "R2") for o in ("O1", "O2")]


def test_continuation_decides_only_after_the_forced_decision():
    behaviour = Policy("nearest_astar", [("R1", "O1")])
    other = Policy("auction", [("R2", "O2")])
    executor = SimpleNamespace(result=SimpleNamespace(decisions=0), engine=SimpleNamespace(time=0, robots={}))
    forced = ForcedPair(behaviour, executor, 1, "R2", "O1", continuation=other)
    timing = SimpleNamespace(finish_s=lambda c: 1.0)
    fifo = {"O1": 0, "O2": 1}
    picks = []
    for decision in range(3):
        executor.result.decisions = decision + 1
        picks.append({(a.robot_id, a.order_id) for a in forced.assign(
            None, [], [SimpleNamespace(order_id=o) for o in fifo], fifo, timing=timing)})
    assert picks[0] == {("R1", "O1")}            # before: the behaviour policy
    assert picks[1] == {("R2", "O1")}            # forced: behaviour's answer with the pair forced
    assert picks[2] == {("R2", "O2")}            # after: the continuation
    assert len(behaviour.calls) == 2 and len(other.calls) == 1


# ---- on a recorded state -------------------------------------------------------------

def _plan(manifest, corpus):
    frame = corpus.assign(est_finish_s=derive_est_finish_s(corpus, manifest))
    build = corpus_builder(manifest)
    first, made_in = decision_ticks(build)
    for d in sorted(made_in):
        actions = select_candidates(frame[frame["decision_id"] == d], None,
                                    run_id=manifest["run_id"], seed=1, max_actions=2)
        if made_in[d] > 1 and len(actions) == 2:
            return build, first, made_in, {d: [(a.robot_id, a.order_id, a.extra(k)) for k, a in enumerate(actions)]}
    raise AssertionError("no decision with two candidates")


SCORE_COLUMNS = ["decision_id", "branch", "forced", "forced_robot", "forced_order", "swap",
                 "pairs_after", "pivot_mission_s", "waiting_in_h_s", "window_completion_s",
                 "rollout_status"]


@needs_run
def test_each_policy_gets_the_same_state_action_and_window_and_is_reproducible():
    manifest = json.loads((ACCEPTANCE_RUN / "run.json").read_text())
    corpus = pd.read_parquet(ACCEPTANCE_RUN / "decisions.parquet")
    build, first, made_in, plan = _plan(manifest, corpus)
    policies = (None, "nearest_astar", "multi_criteria", "auction")

    rows, checks = probe_pairs(build, plan, made_in, verify=1, baseline=tables(first),
                               continuations=policies, seed=int(manifest["seed"]))
    again, _ = probe_pairs(build, plan, made_in, verify=0, continuations=policies,
                           seed=int(manifest["seed"]))
    assert verified(checks)
    frame = pd.DataFrame(rows)
    frame["policy"] = [p or "behaviour" for p in policies] * len(next(iter(plan.values())))

    # Deterministic: the same branches again give the same scores.
    pd.testing.assert_frame_equal(frame[SCORE_COLUMNS], pd.DataFrame(again)[SCORE_COLUMNS])
    # No continuation and an explicit nearest_astar continuation are the same branch.
    by = {p: g.reset_index(drop=True) for p, g in frame.groupby("policy")}
    pd.testing.assert_frame_equal(by["behaviour"][SCORE_COLUMNS], by["nearest_astar"][SCORE_COLUMNS])
    # Every policy starts from the identical forced action and is measured on the same window.
    for policy in ("multi_criteria", "auction"):
        for column in ("forced", "forced_robot", "forced_order", "swap", "pairs_after", "t"):
            assert list(by[policy][column]) == list(by["nearest_astar"][column]), (policy, column)
        assert [set(json.loads(json.dumps(w))) for w in by[policy]["window_completion_s"]] == \
               [set(w) for w in by["nearest_astar"]["window_completion_s"]]
    assert set(frame["continuation"]) == {"nearest_astar", "multi_criteria", "auction"}


# ---- combining the policies' labels -------------------------------------------------

def _probed(tmp_path, scores):
    """A real decision of the acceptance run, with synthetic per-policy branch scores.

    scores: {policy: [j for each of three candidates]}; each window order gets the
    branch's score divided evenly, so jw2 is exactly that score.
    """
    run = tmp_path / "run"
    run.mkdir(parents=True)
    corpus = pd.read_parquet(ACCEPTANCE_RUN / "decisions.parquet")
    corpus.to_parquet(run / "decisions.parquet")
    manifest = json.loads((ACCEPTANCE_RUN / "run.json").read_text())
    (run / "run.json").write_text(json.dumps(manifest))
    for d, g in corpus[corpus["eligible"]].groupby("decision_id"):
        robots = g["robot_id"].value_counts()
        if len(robots) >= 3 and int(d) > 2:
            break
    window = window_orders(corpus[corpus["decision_id"] == d])
    eligible = g.drop_duplicates("robot_id").head(3)
    rows = []
    for policy, js in scores.items():
        for rank, ((_, pair), j) in enumerate(zip(eligible.iterrows(), js)):
            done = {o: (j / len(window) if j == j else None) for o in window}
            rows.append({
                "decision_id": int(d), "continuation": policy, "branch": f"c{rank}",
                "candidate_rank": rank, "candidate_sources": "x", "candidate_p_select": 1.0,
                "forced": "strategy_pair" if rank == 0 else "forced",
                "forced_robot": pair["robot_id"], "forced_order": pair["order_id"], "swap": "",
                "waiting_in_h_s": 0.0, "rollout_status": "complete",
                "window_completion_s": json.dumps({o: t for o, t in done.items() if t is not None}),
            })
    out = tmp_path / "rollouts"
    out.mkdir()
    pd.DataFrame(rows).to_csv(out / "rollouts.csv", index=False)
    (out / "probe.json").write_text(json.dumps({
        "arm": "mp1", "run_id": manifest["run_id"], "clone_verified": True, "replay_identical": True,
        "args": {"horizon": "120.0"}, "evaluated_orders": {}, "continuations": sorted(scores),
    }))
    return run, out


def labels_of(tmp_path, scores, mode="conservative"):
    run, out = _probed(tmp_path, scores)
    frame = cross_policy_labels(run, [out], mode=mode, config=JW2)
    return dict(zip(frame["branch"], frame["label"])), frame


@needs_run
def test_positive_only_when_best_under_every_policy(tmp_path):
    got, frame = labels_of(tmp_path, {"nearest_astar": [100, 200, 300], "auction": [100, 250, 260]})
    assert got == {"c0": "pos", "c1": "neg", "c2": "neg"}
    assert set(frame["reason"]) == {"best_under_every_policy", "worse_under_every_policy"}


@needs_run
def test_policies_that_disagree_abstain(tmp_path):
    got, frame = labels_of(tmp_path, {"nearest_astar": [100, 200, 300], "auction": [300, 100, 200]})
    assert got == {"c0": "abstain", "c1": "abstain", "c2": "neg"}
    assert set(frame.loc[frame["branch"].isin(["c0", "c1"]), "reason"]) == {"policy_conflict"}


@needs_run
def test_censored_under_any_policy_excludes_and_blocks_the_positive(tmp_path):
    scores = {"nearest_astar": [100, 200, 300], "auction": [100, 200, float("nan")]}
    got, frame = labels_of(tmp_path, scores)
    assert got == {"c0": "abstain", "c1": "neg", "c2": "excluded"}
    assert "censored:auction" in frame.set_index("branch").at["c2", "reason"]
    assert labels_of(tmp_path / "s", scores, mode="m6_1")[0]["c0"] == "pos"


@needs_run
def test_report_quantifies_policy_dependence(tmp_path):
    _, frame = labels_of(tmp_path, {"nearest_astar": [100, 200, 300], "auction": [300, 100, 200]})
    report = cross_policy_report(frame)
    assert report["policies"] == ["auction", "nearest_astar"]
    assert report["same_best_under_all_policies"] == 0
    assert report["regret_median_s"]["best_nearest_astar_under_auction"] == 200.0
    assert report["kendall_tau_median"]["auction~nearest_astar"] < 0
