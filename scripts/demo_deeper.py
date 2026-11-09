"""The "deeper look" section of the demo report: one graph per question, from run tables.

Reads only what every run already records: outcomes.parquet (each order's launch,
completion, travel, traffic waiting, service, robot, battery), decisions.parquet and
features.parquet (each decision's options, the picks, the routes' congestion) and, for a
learned policy, rule_trace.parquet (which rule made each pick). Used by demo_compare.py.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

KEYS = ["decision_id", "robot_id", "order_id"]


def load(out: Path, policies, robots: int, seed: int) -> dict[str, dict[str, pd.DataFrame]]:
    tables = {}
    for policy in policies:
        folder = out / policy / f"r{robots}_s{seed}"
        entry = {"outcomes": pd.read_parquet(folder / "outcomes.parquet"),
                 "decisions": pd.read_parquet(folder / "decisions.parquet")}
        if (folder / "features.parquet").exists():
            entry["features"] = pd.read_parquet(folder / "features.parquet")
        if (folder / "rule_trace.parquet").exists():
            entry["trace"] = pd.read_parquet(folder / "rule_trace.parquet")
        tables[policy] = entry
    return tables


def sections(tables, group_of, colors, png) -> list[tuple[str, str, str, str]]:
    """[(title, question, how to read it, base64 png)]"""
    order = sorted(tables, key=lambda p: tables[p]["outcomes"]["completed_at"].max())
    color = {p: colors[group_of(p)] for p in order}
    out = []

    # 1. Progress over time.
    fig, ax = plt.subplots(figsize=(10, 5))
    for p in order:
        done = np.sort(tables[p]["outcomes"]["completed_at"].to_numpy())
        ax.step(done, np.arange(1, len(done) + 1), where="post", color=color[p],
                lw=2.2 if p.startswith("ilp_") else 1.2, alpha=0.9 if p.startswith("ilp_") else 0.6, label=p)
    ax.set_xlabel("simulated time (s)")
    ax.set_ylabel("orders delivered")
    ax.legend(fontsize=7, ncol=2)
    out.append(("Progress over time", "Where does a policy gain or lose time: at the start, in the middle, or in the last orders?",
                "Each line counts delivered orders. A line further left delivered sooner. Lines that run together "
                "and split only at the top differ in the last few orders (the tail), not in their everyday choices.",
                png(fig)))

    # 2. Where an order's time goes.
    parts = pd.DataFrame({
        p: {"queue (waiting to be assigned)": t["outcomes"]["launched_at"].mean(),
            "driving": t["outcomes"]["realized_travel_time_s"].mean(),
            "traffic waiting": t["outcomes"]["realized_waiting_s"].mean(),
            "picking and drop-off": t["outcomes"]["realized_service_s"].mean()}
        for p, t in tables.items()}).T.loc[order]
    fig, ax = plt.subplots(figsize=(10, 0.32 * len(parts) + 1.6))
    parts.plot.barh(stacked=True, ax=ax, color=["#bbbbbb", "#4c72b0", "#c44e52", "#55a868"])
    ax.invert_yaxis()
    ax.set_xlabel("mean seconds per order")
    ax.legend(fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=4)
    out.append(("Where an order's time goes", "Is a policy slow because orders wait in the queue, because robots drive far, or because they wait in traffic?",
                "Each bar splits the average order into time in the queue before a robot is assigned, driving, waiting "
                "in traffic and service. Traffic waiting is the congestion a policy causes; queue time is how fast it gets orders started.",
                png(fig)))

    # 3. Spread of completion times.
    fig, ax = plt.subplots(figsize=(10, 0.32 * len(order) + 1.6))
    ax.boxplot([tables[p]["outcomes"]["completed_at"] for p in order], vert=False, tick_labels=order,
               patch_artist=True, boxprops={"facecolor": "#e8e8f4"})
    ax.invert_yaxis()
    ax.set_xlabel("order completion time (s)")
    out.append(("How long orders take, order by order", "Do some orders wait much longer than others under a policy?",
                "Box = middle half of the orders, line = median, whiskers and dots = the slowest orders. "
                "A long right whisker means some orders are served very late.", png(fig)))

    # 4. Distance against traffic.
    fig, ax = plt.subplots(figsize=(8, 6))
    for p in order:
        o = tables[p]["outcomes"]
        x, y = o["realized_distance_m"].mean(), o["realized_waiting_s"].mean()
        ax.scatter(x, y, color=color[p], s=60)
        ax.annotate(p, (x, y), fontsize=7, xytext=(4, 3), textcoords="offset points")
    ax.set_xlabel("mean driving distance per order (m)")
    ax.set_ylabel("mean traffic waiting per order (s)")
    out.append(("Short trips or less traffic?", "Does a policy save distance at the cost of more traffic, or the other way round?",
                "Each dot is one policy. Bottom left is best: short trips and little waiting. Nearest-first policies "
                "sit to the left; a policy higher up sends robots into each other's way.", png(fig)))

    # 5. Work shared between robots.
    fig, axes = plt.subplots(1, 2, figsize=(12, 0.32 * len(order) + 1.6), sharey=True)
    per_robot = {p: t["outcomes"].groupby("robot_id") for p, t in tables.items()}
    axes[0].boxplot([per_robot[p].size() for p in order], vert=False, tick_labels=order)
    axes[0].set_xlabel("orders per robot")
    axes[1].boxplot([per_robot[p]["realized_distance_m"].sum() for p in order], vert=False, tick_labels=order)
    axes[1].set_xlabel("distance driven per robot (m)")
    axes[0].invert_yaxis()
    out.append(("Is the work shared fairly between robots?", "Does one policy make some robots do much more work than others?",
                "Each box shows how orders (left) and distance (right) are spread over the 100 robots. A narrow box "
                "means work is shared evenly; a wide one means some robots work much harder.", png(fig)))

    # 6. Congestion of the chosen routes.
    rows = []
    for p in order:
        t = tables[p]
        if "features" not in t:
            continue
        d = t["decisions"]
        d = d[d["eligible"]].merge(t["features"][[*KEYS, "overlap_ratio", "st_conflict_robots", "occupied_now"]],
                                   on=KEYS, how="left")
        chosen, offered = d[d["selected"]], d
        rows.append({"policy": p,
                     "route overlap": chosen["overlap_ratio"].mean() / max(offered["overlap_ratio"].mean(), 1e-9),
                     "robots crossing the route": chosen["st_conflict_robots"].mean() / max(offered["st_conflict_robots"].mean(), 1e-9),
                     "occupied cells on the route": chosen["occupied_now"].mean() / max(offered["occupied_now"].mean(), 1e-9)})
    if rows:
        c = pd.DataFrame(rows).set_index("policy")
        fig, ax = plt.subplots(figsize=(10, 0.36 * len(c) + 1.6))
        c.plot.barh(ax=ax, color=["#4c72b0", "#c44e52", "#dd8452"])
        ax.axvline(1.0, color="black", lw=1, ls="--")
        ax.invert_yaxis()
        ax.set_xlabel("chosen routes ÷ all offered routes (below 1 = the policy picks less congested routes)")
        ax.legend(fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=3)
        out.append(("Does a policy avoid congested routes?", "When it chooses, does a policy prefer routes with less traffic than the average option?",
                    "Measured at decision time, before anything happens: the congestion of the routes a policy picked, "
                    "divided by the congestion of every route it could have picked. Below the dashed line, it avoids traffic.",
                    png(fig)))

    # 7. Which rule decided.
    usage = {}
    for p in order:
        trace = tables[p].get("trace")
        if trace is None:
            continue
        label = trace["clause_rank"].map(lambda r: f"rule {int(r) + 1}")
        label[trace["fallback"]] = "fallback"
        usage[p] = label.value_counts(normalize=True)
    if usage:
        u = pd.DataFrame(usage).T.fillna(0.0)
        u = u[sorted(u.columns, key=lambda c: (c == "fallback", c))]
        fig, ax = plt.subplots(figsize=(10, 0.4 * len(u) + 1.6))
        u.plot.barh(stacked=True, ax=ax, colormap="tab20")
        ax.invert_yaxis()
        ax.set_xlabel("share of picks")
        ax.legend(fontsize=8, ncol=6, loc="upper center", bbox_to_anchor=(0.5, -0.15))
        out.append(("Which rule made each pick?", "For a learned policy, which of its rules actually decides, and how often does the fallback take over?",
                    "Each bar splits a learned policy's picks by the rule that made them. A large fallback share means "
                    "its rules rarely apply, so the fallback (oldest order, nearest robot) is really deciding.", png(fig)))

    # 8. When a copy departs from its teacher.
    rows = []
    for p in order:
        if not p.startswith("ilp_"):
            continue
        teacher = p.replace("ilp_corrected_", "").replace("ilp_", "")
        if teacher not in tables:
            continue
        a = tables[p]["decisions"]
        b = tables[teacher]["decisions"]
        picks = lambda d: d[d["selected"]].groupby("decision_id").apply(  # noqa: E731
            lambda g: frozenset(zip(g["robot_id"], g["order_id"])))
        pa, pb = picks(a), picks(b)
        common = [i for i in pa.index if i in pb.index]
        same = [pa[i] == pb[i] for i in common]
        first = next((i for i, s in zip(common, same) if not s), None)
        rows.append({"policy": p, "teacher": teacher,
                     "identical decisions in a row from the start": common.index(first) if first is not None else len(common),
                     "first different decision": "none" if first is None else int(first),
                     "makespan difference (s)": round(tables[p]["outcomes"]["completed_at"].max()
                                                      - tables[teacher]["outcomes"]["completed_at"].max(), 1)})
    if rows:
        out.append(("When does a copy start to differ from its teacher?",
                    "Does the learned copy make the teacher's decisions, and from which decision on does the run take another path?",
                    "Both runs start from the same state. As long as every pick is the same, the runs are identical; after the "
                    "first different pick the warehouse is in a different state, so later decisions are no longer comparable one to one.",
                    pd.DataFrame(rows).to_html(index=False, border=0)))
    return out
