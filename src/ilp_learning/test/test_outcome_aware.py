"""M6-1 and M6-2: outcome-aware labels from rollout scores (supervision/outcome_aware.py)."""

import ast
import json
from pathlib import Path
import shutil

import pandas as pd
import pytest

from ilp_learning import bias_templates, bk_builder, matcher, predicates, rule_compiler, thresholds
from ilp_learning.atoms import ProvenanceTracker
from ilp_learning.bias_templates import generate_bias_pl
from ilp_learning.bk_builder import BKBuilder, derive_est_finish_s
from ilp_learning.supervision import outcome_aware
from ilp_learning.supervision.outcome_aware import (
    FROZEN,
    OutcomeLabelError,
    j_score,
    label_decision,
    outcome_examples,
    outcome_labels,
    write_labels,
)
from ilp_learning.supervision.single_teacher import build_single_teacher_supervision
from ilp_learning.thresholds import DECISION_DISCRETIZATIONS, fit_thresholds_from_frames
from simulation_env.seed_split import SeedLeakageError

from .conftest import ACCEPTANCE_RUN


def branch(name, order, mission, waiting, *, robot="R1", decision=7, status="complete",
           window=True, done=None, baseline="O1"):
    return {
        "decision_id": decision, "branch": name, "pivot_robot": robot, "order": order,
        "teacher_order": baseline,
        "pivot_mission_s": mission, "waiting_in_h_s": waiting, "window_complete": window,
        "pivot_done_s": mission if done is None else done, "rollout_status": status,
    }


def labels_of(rows, mode="conservative"):
    out = label_decision(pd.DataFrame(rows), mode=mode)
    return dict(zip(out["branch"], out["label"])), dict(zip(out["branch"], out["reason"]))


# ---- the frozen contract ----------------------------------------------------------

def test_frozen_contract():
    assert (FROZEN.horizon_s, FROZEN.lambda_wait, FROZEN.delta_s) == (120.0, 0.5, 2.0)
    assert j_score(60.0, 10.0) == 65.0


def test_unique_winner_by_delta_is_positive_and_worse_by_delta_negative():
    labels, _ = labels_of([
        branch("teacher", "O1", 60.0, 10.0),  # J 65
        branch("alt1", "O2", 60.0, 0.0),      # J 60, best, wins by 5
        branch("alt2", "O3", 62.0, 0.0),      # J 62, 2.0 worse: negative exactly at delta
        branch("alt3", "O4", 61.0, 0.0),      # J 61, within delta: abstain
    ])
    assert labels == {"teacher": "neg", "alt1": "abstain", "alt2": "neg", "alt3": "abstain"}


def test_winner_needs_delta_over_the_second_best():
    labels, reasons = labels_of([
        branch("teacher", "O1", 60.0, 0.0),   # J 60
        branch("alt1", "O2", 63.0, 0.0),      # J 63, 3 worse
    ])
    assert labels == {"teacher": "pos", "alt1": "neg"}
    assert reasons["teacher"] == "best_by_delta"


# ---- ties ----------------------------------------------------------------------------

def test_tie_at_the_top_abstains_on_both():
    labels, reasons = labels_of([
        branch("teacher", "O1", 60.0, 0.0),
        branch("alt1", "O2", 60.0, 0.0),
        branch("alt2", "O3", 70.0, 0.0),
    ])
    assert labels == {"teacher": "abstain", "alt1": "abstain", "alt2": "neg"}
    assert {reasons["teacher"], reasons["alt1"]} == {"best_not_by_delta", "within_delta"}


def test_gap_just_under_delta_abstains():
    labels, _ = labels_of([branch("teacher", "O1", 60.0, 0.0), branch("alt1", "O2", 61.999, 0.0)])
    assert labels == {"teacher": "abstain", "alt1": "abstain"}


def test_float_image_of_an_exact_two_second_gap_still_counts():
    labels, _ = labels_of([
        branch("teacher", "O1", 60.0 + 1 / 3, 0.0),
        branch("alt1", "O2", 62.0 + 1 / 3 - 1e-12, 0.0),
    ])
    assert labels == {"teacher": "pos", "alt1": "neg"}


# ---- failed and unfinished rollouts ---------------------------------------------------

@pytest.mark.parametrize(
    "broken",
    [
        {"status": "stalled"},
        {"window": False},
        {"done": FROZEN.horizon_s + 1.0},
        {"window": "False"},
    ],
)
def test_failed_or_censored_branch_is_excluded_never_labelled(broken):
    rows = [
        branch("teacher", "O1", 60.0, 0.0),
        branch("alt1", "O2", 10.0, 0.0, **broken),   # would win by far if it counted
        branch("alt2", "O3", 70.0, 0.0),
    ]
    labels, reasons = labels_of(rows)
    assert reasons["alt1"] in {"failed", "censored"}
    # Conservative (primary): the unseen branch might have been best, so no positive;
    # the measured negative between completed branches stands.
    assert labels == {"teacher": "abstain", "alt1": "excluded", "alt2": "neg"}
    assert reasons["teacher"] == "best_but_censored_decision"
    # M6-1 rule, kept as the sensitivity mode.
    assert labels_of(rows, mode="m6_1")[0] == {"teacher": "pos", "alt1": "excluded", "alt2": "neg"}


def test_nan_score_is_unfinished():
    labels, reasons = labels_of([
        branch("teacher", "O1", 60.0, 0.0),
        branch("alt1", "O2", float("nan"), 0.0),
    ])
    assert (labels["alt1"], reasons["alt1"]) == ("excluded", "censored")
    assert labels["teacher"] == "abstain"  # one valid branch: nothing to compare


def test_all_branches_failed_gives_no_example():
    out = label_decision(pd.DataFrame([
        branch("teacher", "O1", 60.0, 0.0, status="stalled"),
        branch("alt1", "O2", 50.0, 0.0, status="stalled"),
    ]))
    assert set(out["label"]) == {"excluded"}


# ---- conflicting labels ---------------------------------------------------------------

def test_same_pair_with_two_scores_abstains_the_whole_decision():
    labels, reasons = labels_of([
        branch("teacher", "O1", 60.0, 0.0),
        branch("alt1", "O2", 70.0, 0.0),
        branch("alt2", "O1", 80.0, 0.0),      # O1 again, different outcome
    ])
    assert set(labels.values()) == {"abstain"}
    assert set(reasons.values()) == {"conflict"}


def test_two_pivot_robots_in_one_decision_is_a_conflict():
    labels, reasons = labels_of([
        branch("teacher", "O1", 60.0, 0.0, robot="R1"),
        branch("alt1", "O2", 70.0, 0.0, robot="R2"),
    ])
    assert set(labels.values()) == {"abstain"} and set(reasons.values()) == {"conflict"}


def test_identical_repeat_of_a_pair_counts_once():
    labels, _ = labels_of([
        branch("teacher", "O1", 60.0, 0.0),
        branch("alt1", "O1", 60.0, 0.0),
        branch("alt2", "O2", 70.0, 0.0),
    ])
    assert labels == {"teacher": "pos", "alt1": "pos", "alt2": "neg"}


def test_examples_refuse_a_pair_labelled_both_ways():
    labels = pd.DataFrame([
        {"run_id": "r", "decision_id": 1, "branch": "a", "robot_id": "R1", "order_id": "O1",
         "j_s": 1.0, "status": "valid", "label": "pos", "reason": ""},
        {"run_id": "r", "decision_id": 1, "branch": "b", "robot_id": "R1", "order_id": "O1",
         "j_s": 1.0, "status": "valid", "label": "neg", "reason": ""},
    ])
    decisions = pd.DataFrame([{"decision_id": 1, "sim_time": 0.0}])
    with pytest.raises(OutcomeLabelError, match="both ways"):
        outcome_examples(labels, decisions, ProvenanceTracker())


# ---- a probed run on disk -------------------------------------------------------------

needs_run = pytest.mark.skipif(not ACCEPTANCE_RUN.exists(), reason="runs/m2_acceptance absent")


def probed_run(tmp_path: Path, *, seed=104, verified=True, horizon=120.0, drop=None) -> Path:
    """The acceptance run's decisions, with rollouts over three of its real pairs."""
    run = tmp_path / "run"
    run.mkdir()
    shutil.copy(ACCEPTANCE_RUN / "decisions.parquet", run / "decisions.parquet")
    manifest = json.loads((ACCEPTANCE_RUN / "run.json").read_text())
    manifest["seed"] = seed
    manifest["orders_path"] = f"x/orders_s{seed}.csv"
    (run / "run.json").write_text(json.dumps(manifest))
    (run / "probe.json").write_text(json.dumps({
        "run_id": manifest["run_id"], "clone_verified": verified, "args": {"horizon": str(horizon)},
    }))

    decisions = pd.read_parquet(run / "decisions.parquet")
    eligible = decisions[decisions["eligible"]]
    rows = []
    for decision_id, group in list(eligible.groupby("decision_id"))[5:8]:
        robot = group["robot_id"].value_counts().index[0]
        orders = sorted(group.loc[group["robot_id"] == robot, "order_id"])[:3]
        for k, order in enumerate(orders):
            rows.append(branch("teacher" if k == 0 else f"alt{k}", order, 50.0 + 3 * k, 0.0,
                               robot=robot, decision=int(decision_id)))
    frame = pd.DataFrame(rows)
    if drop:
        frame = frame.drop(columns=[drop])
    frame.to_csv(run / "rollouts.csv", index=False)
    return run


@needs_run
def test_labels_from_a_real_run(tmp_path):
    labels = outcome_labels(probed_run(tmp_path))
    assert set(labels["label"]) <= {"pos", "neg", "abstain", "excluded"}
    assert (labels["label"] == "pos").sum() >= 1


@needs_run
@pytest.mark.parametrize("seed", [101, 111, 116, 5, 201, 42])
def test_seed_leakage_refused(tmp_path, seed):
    with pytest.raises(SeedLeakageError):
        outcome_labels(probed_run(tmp_path, seed=seed))


@needs_run
def test_unverified_clone_refused(tmp_path):
    with pytest.raises(OutcomeLabelError, match="clone"):
        outcome_labels(probed_run(tmp_path, verified=False))


@needs_run
def test_other_horizon_refused(tmp_path):
    with pytest.raises(OutcomeLabelError, match="H="):
        outcome_labels(probed_run(tmp_path, horizon=300.0))


@needs_run
def test_old_rollouts_without_censoring_columns_refused(tmp_path):
    with pytest.raises(OutcomeLabelError, match="lacks"):
        outcome_labels(probed_run(tmp_path, drop="pivot_done_s"))


@needs_run
def test_pair_not_in_decisions_refused(tmp_path):
    run = probed_run(tmp_path)
    rollouts = pd.read_csv(run / "rollouts.csv")
    rollouts.loc[0, "order"] = "O_NOT_A_CANDIDATE"
    rollouts.to_csv(run / "rollouts.csv", index=False)
    with pytest.raises(OutcomeLabelError, match="not eligible"):
        outcome_labels(run)


# ---- determinism ----------------------------------------------------------------------

@needs_run
def test_labels_are_reproducible_byte_for_byte(tmp_path):
    run = probed_run(tmp_path)
    first = write_labels(outcome_labels(run), tmp_path / "a.csv")
    rollouts = pd.read_csv(run / "rollouts.csv")
    rollouts.sample(frac=1.0, random_state=3).to_csv(run / "rollouts.csv", index=False)
    second = write_labels(outcome_labels(run), tmp_path / "b.csv")
    assert first == second
    assert (tmp_path / "a.csv").read_bytes() == (tmp_path / "b.csv").read_bytes()


# ---- bk.pl and bias.pl are those of M4/M5 ---------------------------------------------

@needs_run
def test_bk_pl_is_byte_identical_with_teacher_or_outcome_examples(tmp_path):
    run = probed_run(tmp_path)
    manifest = json.loads((run / "run.json").read_text())
    decisions = pd.read_parquet(run / "decisions.parquet")
    frame = decisions.assign(est_finish_s=derive_est_finish_s(decisions, manifest))
    model = fit_thresholds_from_frames(
        [frame[frame["eligible"] & frame["choice_bearing"]]], seeds_used=[104],
        spec=DECISION_DISCRETIZATIONS,
    )

    def build(examples_from):
        tracker = ProvenanceTracker()
        builder = BKBuilder(thresholds=model, tracker=tracker)
        builder.build_from_frames(manifest["run_id"], frame, run_manifest=manifest)
        examples_from(tracker)
        path = tmp_path / f"bk_{examples_from.__name__}.pl"
        builder.write_bk_pl(path)
        return path.read_bytes()

    def teacher(tracker):
        return build_single_teacher_supervision(
            manifest["run_id"], frame, tracker, run_manifest=manifest
        )

    def outcome(tracker):
        examples = outcome_examples(outcome_labels(run), frame, tracker)
        assert examples
        return examples

    def none(tracker):
        return []

    assert build(teacher) == build(outcome) == build(none)


def test_bias_pl_takes_no_label_input():
    # bias.pl is a function of the vocabulary switches alone, so M4, M5 and M6
    # with the same switches get the same bytes.
    import inspect
    parameters = set(inspect.signature(generate_bias_pl).parameters)
    assert not {p for p in parameters if "label" in p or "outcome" in p or "teacher" in p}


# ---- the firewall: outcomes reach the learner as labels only --------------------------

def _imports(module) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    names = {alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names}
    return names | {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}


@pytest.mark.parametrize("module", [bk_builder, predicates, rule_compiler, matcher, thresholds, bias_templates])
def test_runtime_and_kb_modules_never_see_outcomes(module):
    source = Path(module.__file__).read_text()
    assert not any("outcome_aware" in name or "rollout" in name for name in _imports(module))
    assert "rollouts.csv" not in source and "outcome_labels" not in source


def test_outcome_supervision_builds_no_kb():
    imported = _imports(outcome_aware)
    assert not imported & {
        "ilp_learning.bk_builder", "ilp_learning.bias_templates", "ilp_learning.thresholds",
        "ilp_learning.predicates", "ilp_learning.rule_compiler", "ilp_learning.matcher",
    }


# ---- M6-2: modes, teacher independence, provenance ------------------------------------

def test_unknown_mode_refused():
    with pytest.raises(ValueError, match="mode"):
        label_decision(pd.DataFrame([branch("teacher", "O1", 60.0, 0.0)]), mode="vote")


def test_conservative_and_m6_1_agree_when_every_branch_completed():
    rows = [branch("teacher", "O1", 60.0, 0.0), branch("alt1", "O2", 65.0, 0.0),
            branch("alt2", "O3", 61.0, 0.0)]
    assert labels_of(rows) == labels_of(rows, mode="m6_1")


@pytest.mark.parametrize("mode", ["conservative", "m6_1"])
def test_which_branch_was_the_teacher_never_changes_a_label(mode):
    scores = {"O1": 60.0, "O2": 66.0, "O3": 61.0, "O4": 75.0}
    as_given = [branch(n, o, scores[o], 0.0, baseline="O1")
                for n, o in [("teacher", "O1"), ("alt1", "O2"), ("alt2", "O3"), ("alt3", "O4")]]
    # The same tested pairs and scores, with another pair named as the teacher's.
    renamed = [branch(n, o, scores[o], 0.0, baseline="O4")
               for n, o in [("alt3", "O1"), ("alt1", "O2"), ("alt2", "O3"), ("teacher", "O4")]]

    def by_pair(rows):
        out = label_decision(pd.DataFrame(rows), mode=mode)
        return dict(zip(out["order_id"], zip(out["label"], out["reason"], out["margin_s"])))

    assert by_pair(as_given) == by_pair(renamed)


def test_outcome_supervision_reads_no_teacher_pick_or_vote():
    imported = _imports(outcome_aware)
    assert not {m for m in imported if "multi_teacher" in m or "teacher_panel" in m or "single_teacher" in m}
    source = Path(outcome_aware.__file__).read_text()
    assert "teacher_labels" not in source and '"selected"' in source  # only in NOT_STATE
    assert source.count('"selected"') == 1


@needs_run
def test_teacher_pick_in_decisions_does_not_change_labels(tmp_path):
    run = probed_run(tmp_path)
    before = outcome_labels(run)
    decisions = pd.read_parquet(run / "decisions.parquet")
    decisions["selected"] = ~decisions["selected"]
    decisions.to_parquet(run / "decisions.parquet")
    pd.testing.assert_frame_equal(before, outcome_labels(run))


@needs_run
def test_provenance_of_every_label(tmp_path):
    labels = outcome_labels(probed_run(tmp_path))
    manifest = json.loads((ACCEPTANCE_RUN / "run.json").read_text())
    assert list(labels.columns) == outcome_aware.LABELS_COLUMNS
    assert labels.notna()[["run_id", "workload_seed", "robot_count", "quantum_s", "decision_id",
                           "state_sha256", "robot_id", "order_id", "baseline_order", "status",
                           "label", "reason", "mode", "config_sha256", "rollouts_sha256"]].all().all()
    assert set(labels["workload_seed"]) == {104}
    assert set(labels["robot_count"]) == {manifest["robot_count"]}
    assert set(labels["mode"]) == {"conservative"}
    assert labels.groupby("decision_id")["state_sha256"].nunique().eq(1).all()


@needs_run
def test_rollouts_in_their_own_directory(tmp_path):
    run = probed_run(tmp_path)
    elsewhere = tmp_path / "rollouts"
    elsewhere.mkdir()
    for name in ("probe.json", "rollouts.csv"):
        shutil.move(run / name, elsewhere / name)
    labels = outcome_labels(run, elsewhere)
    assert (labels["label"] == "pos").sum() >= 1


@needs_run
def test_replay_that_differs_is_refused(tmp_path):
    run = probed_run(tmp_path)
    probe = json.loads((run / "probe.json").read_text())
    (run / "probe.json").write_text(json.dumps({**probe, "replay_identical": False}))
    with pytest.raises(OutcomeLabelError, match="replay"):
        outcome_labels(run)


def test_summary_keeps_censored_failed_ambiguous_and_usable_apart():
    rows = [branch("teacher", "O1", 60.0, 0.0), branch("alt1", "O2", 70.0, 0.0),
            branch("alt2", "O3", 61.0, 0.0), branch("alt3", "O4", 50.0, 0.0, window=False),
            branch("alt4", "O5", 50.0, 0.0, status="stalled")]
    out = label_decision(pd.DataFrame(rows)).assign(run_id="r")
    summary = outcome_aware.summarize(out)
    assert (summary["usable"], summary["ambiguous"], summary["censored"], summary["failed"]) == (1, 2, 1, 1)
    assert summary["pos"] == 0  # conservative: censored decision
