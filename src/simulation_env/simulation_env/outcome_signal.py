"""M1: what a realized outcome tells us beyond the teacher's estimate.

Read-only analysis of one recorded run (decisions, outcomes, run.json,
and optionally a probe's features table). Nothing here feeds back into a
run, and nothing realized is ever offered to a strategy: this measures
the gap between State_t -> Decision_t and the Outcome, to decide whether
an outcome-derived label can carry information the teacher's own
estimate does not.

For every executed (selected) pair:

    est_finish_s   the decision-time estimate every timed strategy uses:
                   C_static / speed + shelf service x visits + drop-off
                   service (DecisionTiming.finish_s)
    mission_s      retreat_finished_at - launched_at: the same route as
                   C_static, retreat included, as executed
    residual_s     mission_s - est_finish_s; it decomposes exactly into
                   waiting + extra travel (side steps) + service diff
    margin_s       for the chosen robot, the estimate of its next-best
                   eligible order in that decision minus the estimate of
                   the chosen one; NaN when it had no alternative
    exceeds_margin residual_s > margin_s: the realized mission took
                   longer than the estimate of an alternative. Necessary,
                   not sufficient, for an outcome to flip the choice:
                   the alternative would have waited too, and that is
                   not observed (only a rollout observes it)
    launch_delay_s launched_at - decision time (> 0 only if deferred)
    next_gap_s     the robot's next launch - retreat_finished_at
    soc_rose_before_next
                   a PROXY for charging between this order and the
                   robot's next: its SOC at the next launch is higher
                   than at this completion. It misses a charge followed
                   by enough drain, and it is unknown (censored, not
                   false) after the robot's last order. Charging events
                   are the ground truth; the dataset does not record them
    window_flow_s  mean completed_at - decision time over the orders in
                   the decision's window: what the decision's
                   neighbourhood of work experienced downstream

A non-selected pair has no outcome and none is imputed.
"""

import pandas as pd
from scipy.stats import spearmanr


# Decision-time columns tested for association with the residual. Each
# is in decisions.parquet, built before the decision (DECISIONS.md,
# "Decision-Time Features"); identifiers are never among them.
DECISION_FEATURES = (
    "c_static_m",
    "est_finish_s",
    "order_shelf_count",
    "free_robots",
    "window_orders",
    "pending_orders",
    "robots_busy",
    "robots_charging",
    "robot_soc_percent",
    "energy_margin_percent",
    "margin_s",
)

FEATURES_FILE = "features.parquet"


def _timing(manifest):
    speed = float(manifest["speed_mps"])
    pick = float(manifest["shelf_pick_s"])
    drop = float(manifest["dropoff_s"])
    return speed, pick, drop


def per_order(decisions: pd.DataFrame, outcomes: pd.DataFrame, manifest: dict) -> pd.DataFrame:
    """One row per executed order: estimate, outcome and their gap."""

    speed, pick, drop = _timing(manifest)

    eligible = decisions[decisions["eligible"]].copy()
    eligible["est_finish_s"] = (
        eligible["c_static_m"] / speed + eligible["order_shelf_count"] * pick + drop
    )

    # Per decision: how much choice there was.
    shape = eligible.groupby("decision_id").agg(
        eligible_pairs=("order_id", "size"),
        eligible_robots=("robot_id", "nunique"),
    )
    per_robot = eligible.groupby(["decision_id", "robot_id"]).size().rename("robot_options")

    selected = eligible[eligible["selected"]].copy()
    selected = selected.join(shape, on="decision_id").join(
        per_robot, on=["decision_id", "robot_id"]
    )

    # The chosen robot's next-best alternative in the same decision.
    best_other = {}
    for (decision_id, robot_id), group in eligible.groupby(["decision_id", "robot_id"]):
        chosen = group[group["selected"]]
        if chosen.empty:
            continue
        others = group[~group["selected"]]["est_finish_s"]
        best_other[(decision_id, robot_id)] = others.min() if len(others) else float("nan")

    selected["margin_s"] = [
        best_other[(d, r)] - e
        for d, r, e in zip(selected["decision_id"], selected["robot_id"], selected["est_finish_s"])
    ]

    out = outcomes.merge(
        selected,
        on=["decision_id", "robot_id", "order_id"],
        how="left",
        suffixes=("", "_decision"),
        validate="one_to_one",
    )

    if out["est_finish_s"].isna().any():
        missing = out.loc[out["est_finish_s"].isna(), "order_id"].tolist()[:5]
        raise ValueError(f"outcome rows without a selected decision row: {missing}")

    out["decision_time_s"] = out["sim_time"]
    out["est_travel_s"] = out["c_static_m"] / speed
    out["est_service_s"] = out["order_shelf_count"] * pick + drop
    out["mission_s"] = out["retreat_finished_at"] - out["launched_at"]
    out["residual_s"] = out["mission_s"] - out["est_finish_s"]
    out["extra_travel_s"] = out["realized_travel_time_s"] - out["est_travel_s"]
    out["service_diff_s"] = out["realized_service_s"] - out["est_service_s"]
    out["residual_ratio"] = out["residual_s"] / out["est_finish_s"]
    out["exceeds_margin"] = out["residual_s"] > out["margin_s"]
    out["launch_delay_s"] = out["launched_at"] - out["decision_time_s"]

    due = (manifest.get("battery") or {}).get("charge_due_percent")
    out["charge_due_after"] = (
        out["soc_at_completion_percent"] <= due if due is not None else False
    )

    # The robot's next order: when it launched and whether its SOC rose
    # in between. No next order: unknown, so NaN rather than False.
    out = out.sort_values(["robot_id", "launched_at"])
    following = out.groupby("robot_id")[["launched_at", "soc_at_launch_percent"]].shift(-1)
    out["next_gap_s"] = following["launched_at"] - out["retreat_finished_at"]
    rose = following["soc_at_launch_percent"] > out["soc_at_completion_percent"] + 1e-9
    out["soc_rose_before_next"] = rose.astype("float").where(
        following["soc_at_launch_percent"].notna()
    )

    # Downstream: what the orders of this decision's window experienced.
    completed = outcomes.set_index("order_id")["completed_at"]
    window = decisions.groupby("decision_id").agg(
        t=("sim_time", "first"), orders=("order_id", lambda s: sorted(set(s)))
    )
    window["window_flow_s"] = [
        (completed.reindex(orders).mean() - t) for t, orders in zip(window["t"], window["orders"])
    ]
    out = out.join(window["window_flow_s"], on="decision_id")

    for key in ("strategy", "robot_count", "dispatch_quantum_s", "seed"):
        out[key] = manifest.get(key)

    return out.sort_values("decision_id").reset_index(drop=True)


def decision_shape(decisions: pd.DataFrame) -> dict:
    """How much choice the decisions offered, from the decision table."""

    first = decisions.groupby("decision_id").first()
    eligible = decisions[decisions["eligible"]]
    pairs = eligible.groupby("decision_id").size().reindex(first.index, fill_value=0)
    options = eligible.groupby(["decision_id", "robot_id"]).size()

    return {
        "decisions": int(len(first)),
        "choice_bearing_share": float(first["choice_bearing"].mean()),
        "single_robot_share": float((first["free_robots"] == 1).mean()),
        "free_robots_mean": float(first["free_robots"].mean()),
        "window_orders_mean": float(first["window_orders"].mean()),
        "eligible_pairs_mean": float(pairs.mean()),
        "eligible_pairs_median": float(pairs.median()),
        "eligible_orders_per_robot_mean": float(options.mean()) if len(options) else 0.0,
        "ineligible_share": float(1 - decisions["eligible"].mean()),
    }


def _q(series, q):
    return float(series.quantile(q)) if len(series) else float("nan")


def associations(rows: pd.DataFrame, columns, target="residual_s") -> dict:
    """Spearman rho of the target with each decision-time column."""

    result = {}

    for column in columns:
        if column not in rows:
            continue
        pair = rows[[column, target]].dropna()
        if len(pair) < 10 or pair[column].nunique() < 2 or pair[target].nunique() < 2:
            result[column] = float("nan")
            continue
        result[column] = float(spearmanr(pair[column], pair[target]).statistic)

    return result


def summarize(rows: pd.DataFrame, decisions: pd.DataFrame, manifest: dict, metrics=None) -> dict:
    """One run's signal summary: choice, residual, availability."""

    residual = rows["residual_s"]
    mission = rows["mission_s"]
    with_alternative = rows[rows["margin_s"].notna() & (rows["margin_s"] >= 0)]

    summary = {
        "strategy": manifest.get("strategy"),
        "robots": manifest.get("robot_count"),
        "quantum_s": manifest.get("dispatch_quantum_s"),
        "seed": manifest.get("seed"),
        "orders": int(len(rows)),
        **decision_shape(decisions),
        "est_finish_mean_s": float(rows["est_finish_s"].mean()),
        "residual_mean_s": float(residual.mean()),
        "residual_p50_s": _q(residual, 0.5),
        "residual_p90_s": _q(residual, 0.9),
        "residual_p99_s": _q(residual, 0.99),
        "residual_max_s": float(residual.max()),
        "residual_share_of_mission": float(residual.sum() / mission.sum()),
        "orders_residual_gt_0": float((residual > 1e-9).mean()),
        "orders_residual_gt_5s": float((residual > 5).mean()),
        "orders_residual_gt_10pct": float((rows["residual_ratio"] > 0.1).mean()),
        "residual_from_waiting": float(rows["realized_waiting_s"].sum() / residual.sum())
        if residual.sum()
        else float("nan"),
        "residual_from_extra_travel": float(rows["extra_travel_s"].sum() / residual.sum())
        if residual.sum()
        else float("nan"),
        "margin_median_s": _q(with_alternative["margin_s"], 0.5),
        "exceeds_margin_share": float(with_alternative["exceeds_margin"].mean())
        if len(with_alternative)
        else float("nan"),
        "deferred_launch_share": float((rows["launch_delay_s"] > 1e-9).mean()),
        "charge_due_after_share": float(rows["charge_due_after"].mean()),
        # Over orders with a next one only (mean skips the censored NaN).
        "soc_rose_before_next_share": float(rows["soc_rose_before_next"].mean()),
        "next_gap_mean_s": float(rows["next_gap_s"].mean()),
        "window_flow_mean_s": float(rows["window_flow_s"].mean()),
    }

    for column, rho in associations(rows, DECISION_FEATURES).items():
        summary[f"rho_residual_{column}"] = rho

    for key in (
        "order_makespan_s",
        "waiting_share",
        "charge_sessions",
        "charger_wait_time_s",
        "out_of_fleet_time_s",
        "yield_events",
    ):
        if metrics and key in metrics:
            summary[key] = metrics[key]

    return summary


def rollout_summary(rollouts: pd.DataFrame, tolerance_s: float = 1e-6) -> dict:
    """Does the realized best differ from the teacher and from the estimate?

    Per branched decision: the teacher branch and each alternative,
    compared on window_flow_s (lower is better) among rollouts whose
    window completed. "Outcome-best" is the branch with the lowest flow;
    a tie with the teacher goes to the teacher. "Estimate-best" is the
    branch with the lowest decision-time estimate.
    """

    usable = rollouts[rollouts["window_complete"].astype(bool)]
    decisions = []

    for decision_id, group in usable.groupby("decision_id"):
        teacher = group[group["branch"] == "teacher"]

        if teacher.empty or len(group) < 2:
            continue

        flow = group.set_index("branch")["window_flow_s"]
        teacher_flow = flow["teacher"]
        best = flow.idxmin()

        if flow[best] >= teacher_flow - tolerance_s:
            best = "teacher"

        estimate_best = group.set_index("branch")["est_s"].idxmin()

        decisions.append(
            {
                "flip": best != "teacher",
                "gain_s": teacher_flow - flow[best],
                "spread_s": flow.max() - flow.min(),
                "outcome_vs_estimate": best != estimate_best,
                "teacher_rank": int((flow < teacher_flow - tolerance_s).sum()) + 1,
                "branches": len(group),
                "copy_s": group["copy_s"].mean(),
                "wall_s": group["wall_s"].mean(),
            }
        )

    if not decisions:
        return {"rollout_decisions": 0}

    frame = pd.DataFrame(decisions)

    # Deterministic, but a sub-second difference is the butterfly, not a
    # better decision: the share of flips that survive a margin.
    margins = {
        f"rollout_flip_share_gt_{m}s": float((frame["gain_s"] > m).mean()) for m in (1, 5)
    }

    return {
        **margins,
        "rollout_decisions": int(len(frame)),
        "rollout_branches_mean": float(frame["branches"].mean()),
        "rollout_flip_share": float(frame["flip"].mean()),
        "rollout_gain_mean_s": float(frame["gain_s"].mean()),
        "rollout_gain_when_flip_mean_s": float(frame.loc[frame["flip"], "gain_s"].mean())
        if frame["flip"].any()
        else 0.0,
        "rollout_spread_mean_s": float(frame["spread_s"].mean()),
        "rollout_outcome_vs_estimate_share": float(frame["outcome_vs_estimate"].mean()),
        "rollout_teacher_rank_mean": float(frame["teacher_rank"].mean()),
        "rollout_copy_mean_s": float(frame["copy_s"].mean()),
        "rollout_branch_wall_mean_s": float(frame["wall_s"].mean()),
    }


def partial_spearman(frame: pd.DataFrame, x: str, y: str, z: str) -> float:
    """Spearman rho of x and y with z partialled out.

    Each column is ranked; the ranks of x and of y are each regressed on
    the ranks of z, and the residuals correlated. A feature that relates
    to the residual only through the route estimate z scores near 0.
    """

    data = frame[[x, y, z]].dropna().rank()

    if len(data) < 10 or data[z].nunique() < 2:
        return float("nan")

    def residual(column):
        slope = data[column].cov(data[z]) / data[z].var()
        return data[column] - data[column].mean() - slope * (data[z] - data[z].mean())

    rx, ry = residual(x), residual(y)

    if rx.std() == 0 or ry.std() == 0:
        return float("nan")

    return float(rx.corr(ry))


def feature_associations(rows: pd.DataFrame, features: pd.DataFrame) -> dict:
    """Spearman rho of the residual with each probe feature (selected pairs).

    Exploratory only, on the run being analysed. ``rho_partial_*`` is the
    association left after partialling out the decision-time estimate
    (est_finish_s); it is an adjusted association, not evidence of added
    predictive value. That claim needs held-out validation seeds.
    """

    keys = ["decision_id", "robot_id", "order_id"]
    columns = [c for c in features.columns if c not in (*keys, "run_id")]
    overlap = [c for c in features.columns if c not in keys and c in rows.columns]
    base = rows.drop(columns=overlap) if overlap else rows
    joined = base.merge(features, on=keys, how="left", validate="one_to_one")

    result = {}
    for name, rho in associations(joined, columns).items():
        result[f"rho_residual_{name}"] = rho
    for name in columns:
        result[f"rho_partial_{name}"] = partial_spearman(joined, name, "residual_s", "est_finish_s")

    return result
