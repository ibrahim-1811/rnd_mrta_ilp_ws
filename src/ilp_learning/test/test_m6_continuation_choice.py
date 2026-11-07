"""M6 outcome labels from one continuation policy (decisions of 2026-11-07).

nearest_astar continuation is the initial outcome-aware baseline; "behaviour" (each run's
own strategy continues) is per-teacher outcome supervision, and pooled over teachers.
Cross-policy agreement stays available as the sensitivity experiment.
"""

import json

import pandas as pd
import pytest

from ilp_learning.supervision.outcome_common import JW2, CommonLabelError, common_labels
from ilp_learning.train_rules import TrainingError, train

from .conftest import ACCEPTANCE_RUN
from .test_m6_continuation import _probed

needs_run = pytest.mark.skipif(not ACCEPTANCE_RUN.exists(), reason="runs/m2_acceptance absent")

# The two policies disagree: nearest_astar prefers c0, auction prefers c1.
DISAGREE = {"nearest_astar": [100, 200, 300], "auction": [300, 100, 200]}


def by_branch(frame):
    return dict(zip(frame["branch"], frame["label"]))


@needs_run
def test_one_policys_labels_are_taken_without_requiring_agreement(tmp_path):
    run, out = _probed(tmp_path, DISAGREE)
    near = common_labels(run, [out], config=JW2, continuation="nearest_astar")
    auction = common_labels(run, [out], config=JW2, continuation="auction")

    assert by_branch(near) == {"c0": "pos", "c1": "neg", "c2": "neg"}
    assert by_branch(auction) == {"c0": "neg", "c1": "pos", "c2": "neg"}
    # Same state, same rollout file: only the continuation differs.
    assert set(near["state_sha256"]) == set(auction["state_sha256"])
    assert set(near["rollouts_sha256"]) == set(auction["rollouts_sha256"])


@needs_run
def test_several_policies_need_a_named_continuation(tmp_path):
    run, out = _probed(tmp_path, DISAGREE)
    with pytest.raises(CommonLabelError, match="name one continuation"):
        common_labels(run, [out], config=JW2)
    with pytest.raises(CommonLabelError, match="no branch continued by 'milp'"):
        common_labels(run, [out], config=JW2, continuation="milp")


@needs_run
def test_continuation_needs_outcome_rollouts(tmp_path):
    with pytest.raises(TrainingError, match="needs --outcome-rollouts"):
        train([ACCEPTANCE_RUN], tmp_path, continuation="nearest_astar")


def _train(tmp_path, run, out, **options):
    manifest = json.loads((run / "run.json").read_text())
    decision = int(pd.read_csv(out / "rollouts.csv")["decision_id"].iloc[0])
    return train(
        [run], tmp_path / "rules", outcome_rollouts={manifest["run_id"]: out},
        only_decisions={manifest["run_id"]: {decision}}, outcome_objective="jw2",
        outcome_mode="conservative", timeout_s=20, **options,
    )


@needs_run
def test_nearest_astar_baseline_trains_from_multi_policy_rollouts(tmp_path):
    run, out = _probed(tmp_path, DISAGREE)
    artifact = _train(tmp_path, run, out, continuation="nearest_astar")

    supervision = artifact["supervision"]
    assert artifact["teacher"] == "outcome_aware"
    assert supervision["objective"] == "jw2"
    assert supervision["continuation"] == "nearest_astar"
    assert supervision["continued_by"] == ["nearest_astar"]
    assert supervision["labels"]["pos"] == 1


@needs_run
def test_behaviour_is_the_runs_own_strategy_and_pools(tmp_path):
    # The acceptance run was driven by nearest_astar, so "behaviour" is nearest_astar.
    run, out = _probed(tmp_path, DISAGREE)
    own = _train(tmp_path / "own", run, out, continuation="behaviour")
    assert own["supervision"]["continued_by"] == ["nearest_astar"]

    pooled = _train(tmp_path / "pool", run, out, continuation="behaviour", pooled=True)
    assert pooled["teacher"] == "outcome_aware"
    assert pooled["supervision"]["continuation"] == "behaviour"
    assert pooled["pool"]["nearest_astar"]["positives"] == 1
    assert pooled["supervision"]["labels_sha256"] == own["supervision"]["labels_sha256"]
