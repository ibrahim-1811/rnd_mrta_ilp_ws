"""Objective "jw1": common-workload outcome labels for the multi-pair arm (mp1).

The frozen M1 J charges a branch for its pivot's own mission, so between branches that
assign different orders it mostly measures order length, and the order left pending is
charged to nobody (.scratch/m6-outcome-aware/design.md, "M6-MP"). jw1 charges every
branch of a decision for the same orders:

    A(d)    the evaluated order set, fixed when the probe is planned, before any
            rollout: every candidate's order, and the order the behaviour policy gave
            that candidate's robot at d (probe.json "evaluated_orders")
    J_W(b)  = sum over o in A(d) of (completed_at_o(b) - t_d)
              + lambda * waiting_in_h_s(b)                       lower is better

lambda = 0.50, delta = 2.0 s and the waiting horizon H = 120 s are the frozen M1 values.
A branch runs until H has passed and every window order is done (A(d) is inside the
window), so every term is measured in every completed branch.

Branch status:

    failed      the branch stalled
    censored    an order of A(d) not completed when the branch ended, or J_W not finite;
                with completion="horizon" (sensitivity), also any order of A(d)
                completed after t_d + H
    not_forced  the pair could not be forced (ineligible at replay)
    duplicate   the same joint action as a lower-ranked branch; must score the same,
                or the decision is a conflict
    valid       otherwise

Objective "jw2" (2026-11-06, the proposed primary after jw1 failed its fairness check):
A(d) is the decision's whole candidate window, every order in its decisions.parquet rows,
fixed at the decision and the same in every branch:

    J_W2(b) = sum over o in window(d) of (completed_at_o(b) - t_d)
              + lambda * waiting_in_h_s(b)

The window, its recorded window_orders count and each branch's completed window orders
must agree, or the labels are refused. jw1 stays for the record and for comparison.

Labels over the valid branches of a decision, as M6-A: the unique best by >= delta is
positive, a branch >= delta worse than the best is negative, the rest abstain; mode
"conservative" (primary) withholds the positive when any branch is failed or censored.

The atoms a branch labels: the behaviour branch, the behaviour's pivot pair; a forced
branch, the forced pair and its swap pair. An atom given both signs abstains.
Teacher names are provenance only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from ilp_learning.atoms import ProvenanceTracker
from ilp_learning.sampler import GroundAtomExample
from ilp_learning.supervision.outcome_aware import EPS, MODES, PRIMARY_MODE, state_hashes
from simulation_env.seed_split import check_run_is_induction, workload_seed_of


OBJECTIVE = "jw1"
OBJECTIVES = ("jw1", "jw2")
ARM = "mp1"
COMPLETION_RULES = ("rollout", "horizon")
LABEL_COLUMNS = [
    "run_id", "workload_seed", "robot_count", "quantum_s", "decision_id", "state_sha256",
    "branch", "candidate_rank", "candidate_sources", "candidate_p_select", "forced",
    "atoms", "evaluated_orders", "a_size", "mission_sum_s", "waiting_in_h_s", "j_w_s",
    "margin_s", "status", "label", "reason", "mode", "objective", "completion",
    "config_sha256", "rollouts_sha256",
]


class CommonLabelError(RuntimeError):
    """The mp1 rollouts cannot give trustworthy jw1 labels."""


@dataclass(frozen=True)
class CommonConfig:
    objective: str = OBJECTIVE
    horizon_s: float = 120.0
    lambda_wait: float = 0.50
    delta_s: float = 2.0
    completion: str = "rollout"

    @property
    def sha256(self) -> str:
        return sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]


JW1 = CommonConfig()
JW2 = CommonConfig(objective="jw2")


def window_orders(decision: pd.DataFrame) -> list[str]:
    """window(d): every order of the decision's candidate rows, eligible or not."""
    orders = sorted({str(o) for o in decision["order_id"]})
    recorded = int(decision["window_orders"].iloc[0])
    if len(orders) != recorded:
        raise CommonLabelError(
            f"decision {int(decision['decision_id'].iloc[0])}: {len(orders)} orders in its rows, "
            f"window_orders says {recorded}")
    return orders


def evaluated_orders(decision: pd.DataFrame, actions: Sequence[tuple[str, str]]) -> list[str]:
    """A(d): each candidate's order, and the behaviour's order of that candidate's robot."""
    behaviour = {
        str(r): str(o) for r, o in zip(decision.loc[decision["selected"], "robot_id"],
                                       decision.loc[decision["selected"], "order_id"])
    }
    orders = {str(o) for _r, o in actions}
    orders |= {behaviour[str(r)] for r, _o in actions if str(r) in behaviour}
    return sorted(orders)


def _atoms(row) -> tuple[tuple[str, str], ...]:
    if row["forced"] == "strategy_pair":
        return ((str(row["forced_robot"]), str(row["forced_order"])),)
    atoms = [(str(row["forced_robot"]), str(row["forced_order"]))]
    swap = row.get("swap")
    if isinstance(swap, str) and swap:
        robot, order = swap.split(":")
        atoms.append((robot, order))
    return tuple(sorted(atoms))


def label_decision(rows: pd.DataFrame, a_orders: Sequence[str], config: CommonConfig = JW1,
                   mode: str = PRIMARY_MODE) -> pd.DataFrame:
    """jw1 labels of one decision's branches, sorted by candidate rank."""
    if mode not in MODES:
        raise ValueError(f"unknown outcome mode {mode!r}; known: {MODES}")
    if config.objective not in OBJECTIVES:
        raise ValueError(f"unknown objective {config.objective!r}")
    if config.completion not in COMPLETION_RULES:
        raise ValueError(f"unknown completion rule {config.completion!r}")
    rows = rows.sort_values("candidate_rank", kind="stable").reset_index(drop=True)
    out = rows[["decision_id", "branch", "candidate_rank", "candidate_sources",
                "candidate_p_select", "forced"]].copy()
    out["atoms"] = [_atoms(row) for _, row in rows.iterrows()]
    out["a_size"] = len(a_orders)

    mission, status = [], []
    for _, row in rows.iterrows():
        done = json.loads(row["window_completion_s"]) if isinstance(row["window_completion_s"], str) \
            else dict(row["window_completion_s"])
        times = [done.get(o) for o in a_orders]
        total = sum(times) if all(t is not None for t in times) else float("nan")
        mission.append(total)
        if row["rollout_status"] != "complete":
            status.append("failed")
        elif row["forced"] not in ("forced", "strategy_pair"):
            status.append("not_forced")
        elif any(t is None for t in times) or pd.isna(row["waiting_in_h_s"]) \
                or not math.isfinite(total):
            status.append("censored")
        elif config.completion == "horizon" and any(t > config.horizon_s + EPS for t in times):
            status.append("censored")
        else:
            status.append("valid")
    out["mission_sum_s"] = mission
    out["waiting_in_h_s"] = rows["waiting_in_h_s"].astype(float)
    out["status"] = status
    out["j_w_s"] = [
        m + config.lambda_wait * w if s == "valid" else float("nan")
        for m, w, s in zip(out["mission_sum_s"], out["waiting_in_h_s"], out["status"])
    ]

    # The same joint action twice is one action; it must have scored the same.
    seen: dict[tuple, float] = {}
    conflict = False
    for index in out.index:
        if out.at[index, "status"] != "valid":
            continue
        key = out.at[index, "atoms"]
        if key in seen:
            conflict |= abs(seen[key] - out.at[index, "j_w_s"]) > EPS
            out.at[index, "status"] = "duplicate"
        else:
            seen[key] = out.at[index, "j_w_s"]

    out["margin_s"] = float("nan")
    out["label"] = "excluded"
    out["reason"] = out["status"]
    valid = out[out["status"] == "valid"]

    def abstain(reason):
        out.loc[valid.index, ["label", "reason"]] = ["abstain", reason]
        return out

    if conflict:
        return abstain("conflict")
    if len(valid) < 2:
        return abstain("no_comparison")

    ranked = valid.sort_values(["j_w_s", "candidate_rank"], kind="stable")
    best = float(ranked["j_w_s"].iloc[0])
    gap = float(ranked["j_w_s"].iloc[1]) - best
    unseen = bool(out["status"].isin(["failed", "censored", "not_forced"]).any())
    out.loc[valid.index, "margin_s"] = out.loc[valid.index, "j_w_s"] - best
    for index in valid.index:
        margin = float(out.at[index, "margin_s"])
        if index == ranked.index[0]:
            if gap < config.delta_s - EPS:
                verdict = ["abstain", "best_not_by_delta"]
            elif unseen and mode == "conservative":
                verdict = ["abstain", "best_but_censored_decision"]
            else:
                verdict = ["pos", "best_by_delta"]
        elif margin >= config.delta_s - EPS:
            verdict = ["neg", "worse_by_delta"]
        else:
            verdict = ["abstain", "within_delta"]
        out.loc[index, ["label", "reason"]] = verdict
    return out


def load_mp1(run_dir: Path | str, rollout_dirs: Sequence[Path | str], config: CommonConfig = JW1):
    """(manifest, decisions, [(probe, rollouts, rollouts_sha)]) after every check."""
    path = Path(run_dir)
    manifest = json.loads((path / "run.json").read_text(encoding="utf-8"))
    check_run_is_induction(manifest)
    parts = []
    for source in map(Path, rollout_dirs):
        probe = json.loads((source / "probe.json").read_text(encoding="utf-8"))
        if probe.get("arm") != ARM:
            raise CommonLabelError(f"{source}: arm {probe.get('arm')!r} is not {ARM}")
        if probe.get("run_id") != manifest["run_id"]:
            raise CommonLabelError(f"{source}: probe.json names another run")
        if probe.get("clone_verified") is not True or probe.get("replay_identical") is not True:
            raise CommonLabelError(f"{source}: replay or clone check did not pass")
        if not math.isclose(float(probe["args"]["horizon"]), config.horizon_s):
            raise CommonLabelError(f"{source}: rollouts ran with another horizon")
        if "evaluated_orders" not in probe:
            raise CommonLabelError(f"{source}: no evaluated order set; re-run the probe")
        rollouts = pd.read_csv(source / "rollouts.csv", dtype={
            "forced_robot": str, "forced_order": str, "swap": str, "branch": str})
        canonical = rollouts.sort_values(["decision_id", "candidate_rank"], kind="stable")
        digest = sha256(canonical.drop(columns=["copy_s", "wall_s", "base_copy_s"], errors="ignore")
                        .to_csv(index=False, float_format="%.12g").encode()).hexdigest()[:16]
        parts.append((probe, rollouts, digest))
    return manifest, pd.read_parquet(path / "decisions.parquet"), parts


def common_labels(run_dir: Path | str, rollout_dirs: Sequence[Path | str], *,
                  mode: str = PRIMARY_MODE, config: CommonConfig = JW1,
                  continuation: str | None = None) -> pd.DataFrame:
    """Every mp1 branch of one run, with its jw1 label and provenance.

    continuation keeps only the branches that policy continued, so rollouts made under
    several continuation policies give one policy's labels; every branch still starts
    from the same recorded state. None needs single-policy rollouts.
    """
    manifest, decisions, parts = load_mp1(run_dir, rollout_dirs, config)
    hashes = state_hashes(decisions)
    frames, probed = [], set()
    for probe, rollouts, digest in parts:
        if "continuation" not in rollouts.columns:
            rollouts = rollouts.assign(continuation=_policies_of(rollouts, probe)[0])
        policies = sorted(rollouts["continuation"].astype(str).unique())
        if continuation is None and len(policies) > 1:
            raise CommonLabelError(
                f"rollouts continued by {policies}: name one continuation or use cross_policy_labels")
        if continuation is not None:
            if continuation not in policies:
                raise CommonLabelError(f"no branch continued by {continuation!r}; rollouts have {policies}")
            rollouts = rollouts[rollouts["continuation"].astype(str) == continuation]
        for decision_id, rows in sorted(rollouts.groupby("decision_id"), key=lambda i: int(i[0])):
            decision_id = int(decision_id)
            if decision_id in probed:
                raise CommonLabelError(f"decision {decision_id} probed in two rollout directories")
            probed.add(decision_id)
            if config.objective == "jw2":
                a_orders = window_orders(decisions[decisions["decision_id"] == decision_id])
                for done in rows["window_completion_s"]:
                    extra = set(json.loads(done)) - set(a_orders)
                    if extra:
                        raise CommonLabelError(
                            f"decision {decision_id}: branch completed orders outside its window {sorted(extra)[:3]}")
            else:
                a_orders = probe["evaluated_orders"][str(decision_id)]
            labelled = label_decision(rows, a_orders, config, mode)
            labelled["evaluated_orders"] = " ".join(a_orders)
            labelled["rollouts_sha256"] = digest
            frames.append(labelled)
    if not frames:
        return pd.DataFrame(columns=LABEL_COLUMNS)
    labels = pd.concat(frames, ignore_index=True).assign(
        run_id=manifest["run_id"],
        workload_seed=workload_seed_of(manifest["orders_path"]),
        robot_count=int(manifest["robot_count"]),
        quantum_s=float(manifest["dispatch_quantum_s"]),
        mode=mode, objective=config.objective, completion=config.completion,
        config_sha256=config.sha256,
    )
    labels["state_sha256"] = labels["decision_id"].map(hashes)

    # An atom given both signs at one decision abstains.
    signed: dict[tuple, set] = {}
    for row in labels[labels["label"].isin(["pos", "neg"])].itertuples():
        for atom in row.atoms:
            signed.setdefault((row.decision_id, atom), set()).add(row.label)
    eligible = decisions[decisions["eligible"]]
    allowed = set(zip(eligible["decision_id"].astype(int), eligible["robot_id"].astype(str),
                      eligible["order_id"].astype(str)))
    for (decision_id, (robot, order)), signs in signed.items():
        if (decision_id, robot, order) not in allowed:
            raise CommonLabelError(f"labelled atom not eligible: {(decision_id, robot, order)}")
    labels["atoms"] = [" ".join(f"{r}:{o}" for r, o in atoms) for atoms in labels["atoms"]]
    labels.attrs["conflicting_atoms"] = sorted(k for k, s in signed.items() if len(s) > 1)
    return labels[LABEL_COLUMNS].reset_index(drop=True)


def common_examples(labels: pd.DataFrame, decisions: pd.DataFrame,
                    tracker: ProvenanceTracker) -> list[GroundAtomExample]:
    """pos and neg atoms as ground examples; an atom given both signs gives none."""
    times = decisions.groupby("decision_id")["sim_time"].first()
    signs: dict[tuple, set] = {}
    for row in labels[labels["label"].isin(["pos", "neg"])].itertuples():
        for atom in row.atoms.split():
            robot, order = atom.split(":")
            signs.setdefault((row.run_id, int(row.decision_id), robot, order), set()).add(row.label)
    examples = []
    for (run_id, decision_id, robot, order), sign in sorted(signs.items()):
        if len(sign) > 1:
            continue
        positive = sign == {"pos"}
        examples.append(GroundAtomExample(
            decision_atom=tracker.get_or_create_decision(run_id, decision_id, times[decision_id]),
            robot_atom=tracker.get_or_create_robot(run_id, decision_id, robot),
            order_atom=tracker.get_or_create_order(run_id, decision_id, order),
            is_positive=positive, weight=1,
            origin="jw1_better" if positive else "jw1_worse",
        ))
    return examples


def write_common_labels(labels: pd.DataFrame, path: Path | str) -> str:
    text = labels[LABEL_COLUMNS].to_csv(index=False, float_format="%.9f", lineterminator="\n")
    Path(path).write_text(text, encoding="utf-8")
    return sha256(text.encode()).hexdigest()


def summarize_common(labels: pd.DataFrame) -> dict[str, Any]:
    counts, status = labels["label"].value_counts(), labels["status"].value_counts()
    return {
        "branches": int(len(labels)), "pos": int(counts.get("pos", 0)),
        "neg": int(counts.get("neg", 0)), "abstain": int(counts.get("abstain", 0)),
        "excluded": int(counts.get("excluded", 0)),
        **{f"status_{k}": int(v) for k, v in status.sort_index().items()},
        "probed_decisions": int(labels["decision_id"].nunique()),
        "decisions_with_pos": int(labels.groupby("decision_id")["label"].apply(lambda s: (s == "pos").any()).sum()),
        "a_size_mean": float(labels.groupby("decision_id")["a_size"].first().mean()) if len(labels) else 0.0,
        "reasons": {k: int(v) for k, v in labels["reason"].value_counts().sort_index().items()},
    }


# ---- Cross-policy labels: the same candidates continued by several policies ----------
#
# A candidate's jw score depends on who decides after it. With rollouts continued by
# several policies (rollout.ForcedPair continuation), each policy's branches are scored
# on the same fixed window, labelled per policy as above, and combined:
#
#     pos       positive under every policy
#     neg       negative under every policy
#     abstain   any disagreement ("policy_conflict" when one policy says pos and another
#               neg), or the per-policy labels are not all decisive
#     excluded  failed, censored or not forced under any policy, or a duplicate joint action
#
# Mode "conservative" withholds the positive at a decision where a candidate was excluded
# for anything but duplication. Teacher names stay provenance; the policy names are which
# continuation produced a score, never a label.

def _policies_of(rollouts: pd.DataFrame, probe: dict) -> list[str]:
    if "continuation" not in rollouts.columns:
        return [probe.get("continuations", ["behaviour"])[0]]
    return sorted(rollouts["continuation"].astype(str).unique())


def cross_policy_labels(run_dir: Path | str, rollout_dirs: Sequence[Path | str], *,
                        mode: str = PRIMARY_MODE, config: CommonConfig = JW2) -> pd.DataFrame:
    """One row per candidate and decision, with each policy's score and the combined label."""
    if mode not in MODES:
        raise ValueError(f"unknown outcome mode {mode!r}; known: {MODES}")
    manifest, decisions, parts = load_mp1(run_dir, rollout_dirs, config)
    hashes = state_hashes(decisions)
    out_rows = []
    for probe, rollouts, digest in parts:
        if "continuation" not in rollouts.columns:
            rollouts = rollouts.assign(continuation=_policies_of(rollouts, probe)[0])
        policies = sorted(rollouts["continuation"].astype(str).unique())
        for decision_id, rows in sorted(rollouts.groupby("decision_id"), key=lambda i: int(i[0])):
            decision_id = int(decision_id)
            if config.objective == "jw2":
                a_orders = window_orders(decisions[decisions["decision_id"] == decision_id])
            else:
                a_orders = probe["evaluated_orders"][str(decision_id)]
            per_policy = {
                p: label_decision(rows[rows["continuation"] == p], a_orders, config, "m6_1")
                .set_index("candidate_rank")
                for p in policies
            }
            ranks = sorted(set().union(*(set(f.index) for f in per_policy.values())))
            combined = []
            for rank in ranks:
                entry = {"run_id": manifest["run_id"], "decision_id": decision_id,
                         "candidate_rank": int(rank), "rollouts_sha256": digest,
                         "state_sha256": hashes[decision_id], "a_size": len(a_orders)}
                statuses, labels = {}, {}
                for p, frame in per_policy.items():
                    if rank not in frame.index:
                        statuses[p], labels[p] = "missing", "excluded"
                        continue
                    row = frame.loc[rank]
                    entry.setdefault("branch", row["branch"])
                    entry.setdefault("candidate_sources", row["candidate_sources"])
                    entry.setdefault("atoms", row["atoms"])
                    statuses[p], labels[p] = row["status"], row["label"]
                    entry[f"j_{p}"] = row["j_w_s"]
                    entry[f"regret_{p}"] = row["margin_s"]
                    entry[f"label_{p}"] = row["label"]
                    entry[f"status_{p}"] = row["status"]
                bad = sorted(p for p, s in statuses.items() if s not in ("valid", "duplicate"))
                if all(s == "duplicate" for s in statuses.values()):
                    entry["label"], entry["reason"] = "excluded", "duplicate"
                elif bad:
                    entry["label"] = "excluded"
                    entry["reason"] = "+".join(f"{statuses[p]}:{p}" for p in bad)
                elif any(s == "duplicate" for s in statuses.values()):
                    entry["label"], entry["reason"] = "abstain", "duplicate_under_some_policy"
                elif all(v == "pos" for v in labels.values()):
                    entry["label"], entry["reason"] = "pos", "best_under_every_policy"
                elif all(v == "neg" for v in labels.values()):
                    entry["label"], entry["reason"] = "neg", "worse_under_every_policy"
                elif {"pos", "neg"} <= set(labels.values()):
                    entry["label"], entry["reason"] = "abstain", "policy_conflict"
                else:
                    entry["label"], entry["reason"] = "abstain", "not_decisive_under_every_policy"
                combined.append(entry)
            unseen = any(e["label"] == "excluded" and e["reason"] != "duplicate" for e in combined)
            if unseen and mode == "conservative":
                for e in combined:
                    if e["label"] == "pos":
                        e["label"], e["reason"] = "abstain", "best_but_censored_decision"
            out_rows.extend(combined)
    labels = pd.DataFrame(out_rows)
    if labels.empty:
        return labels
    labels = labels.assign(
        workload_seed=workload_seed_of(manifest["orders_path"]),
        robot_count=int(manifest["robot_count"]),
        quantum_s=float(manifest["dispatch_quantum_s"]),
        mode=mode, objective=config.objective, config_sha256=config.sha256,
        policies="+".join(sorted({c[2:] for c in labels.columns if c.startswith("j_")})),
    )
    labels["atoms"] = [" ".join(f"{r}:{o}" for r, o in atoms) for atoms in labels["atoms"]]
    return labels.sort_values(["decision_id", "candidate_rank"]).reset_index(drop=True)


def cross_policy_report(labels: pd.DataFrame) -> dict[str, Any]:
    """How much the continuation policy changes the evaluation of the same candidates."""
    from scipy.stats import kendalltau

    policies = sorted(c[2:] for c in labels.columns if c.startswith("j_"))
    report: dict[str, Any] = {"policies": policies, "decisions": int(labels["decision_id"].nunique())}
    counts = labels["label"].value_counts()
    report.update({k: int(counts.get(k, 0)) for k in ("pos", "neg", "abstain", "excluded")})
    report["reasons"] = {k: int(v) for k, v in labels["reason"].value_counts().sort_index().items()}
    same_best, taus, regrets = 0, {}, {}
    usable = 0
    for _, g in labels.groupby("decision_id"):
        valid = g.dropna(subset=[f"j_{p}" for p in policies])
        valid = valid[~valid["reason"].isin(["duplicate"])]
        if len(valid) < 2:
            continue
        usable += 1
        bests = {p: valid.loc[valid[f"j_{p}"].idxmin(), "candidate_rank"] for p in policies}
        same_best += len(set(bests.values())) == 1
        for i, a in enumerate(policies):
            for b in policies[i + 1:]:
                taus.setdefault(f"{a}~{b}", []).append(kendalltau(valid[f"j_{a}"], valid[f"j_{b}"])[0])
            for b in policies:
                if a != b:
                    # What a's choice costs when b decides afterwards.
                    regret = valid.loc[valid["candidate_rank"] == bests[a], f"j_{b}"].iloc[0] - valid[f"j_{b}"].min()
                    regrets.setdefault(f"best_{a}_under_{b}", []).append(float(regret))
    report["decisions_compared"] = usable
    report["same_best_under_all_policies"] = int(same_best)
    report["kendall_tau_median"] = {k: round(float(pd.Series(v).median()), 2) for k, v in taus.items()}
    report["regret_median_s"] = {k: round(float(pd.Series(v).median()), 1) for k, v in regrets.items()}
    report["regret_max_s"] = {k: round(float(pd.Series(v).max()), 1) for k, v in regrets.items()}
    for p in policies:
        report[f"per_policy_labels_{p}"] = {k: int(v) for k, v in labels[f"label_{p}"].value_counts().items()}
    return report
