"""Demo: every teacher and every learned ILP policy on one unseen workload, compared.

No training happens here. The rule sets already exist; each policy (8 teachers, the
single-teacher, multi-teacher and outcome-corrected rule sets) runs once on the same
workload, fleet and seed, headless and in parallel, through run_experiment.py. Then:

  1. ranking            makespan of every policy
  2. copy vs teacher    each teacher beside its single-teacher and outcome-corrected copy
  3. crossover results  makespan of each ILP policy minus each teacher (seconds)
  4. crossover choices  how often each rule set picks what each teacher picked, in that
                        teacher's own recorded situations (offline, same scorer as fidelity)

and one self-contained HTML page (report.html) that can be opened or sent anywhere.
Runs that already exist are reused, so a second call only redraws.

    pixi run python scripts/demo_compare.py --robots 100 --orders 300 --seed 116
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
for package in ("mrta_core", "simulation_env", "task_management", "ilp_learning"):
    sys.path.insert(0, str(REPO / "src" / package))

from ilp_learning.bk_builder import derive_est_finish_s  # noqa: E402
from ilp_learning.fidelity import evaluate_frame, rates, with_features  # noqa: E402
from ilp_learning.train_rules import InductionRun  # noqa: E402
from ilp_learning.rule_artifact import load_rule_artifact  # noqa: E402
from ilp_learning.teachers import matching_of_artifact  # noqa: E402

TEACHERS = ["random", "nearest_manhattan", "nearest_astar", "earliest_finish", "multi_criteria",
            "auction", "hungarian", "milp"]
CAMPAIGN = Path.home() / "rnd_mrta_campaign/runs/teachers"
GROUP_COLORS = {"teacher": "#4c72b0", "single-teacher ILP": "#55a868",
                "multi-teacher ILP": "#c44e52", "outcome-corrected ILP": "#8172b3", "oracle": "#dd8452"}


def rule_sets(single_root: Path, corrected_root: Path, groups: set[str]) -> dict[str, Path]:
    """label -> rule_artifact.json, for the groups asked for."""
    found = {}
    for t in TEACHERS:
        if "single" in groups and (single_root / t / "rule_artifact.json").exists():
            found[t] = single_root / t / "rule_artifact.json"
        if "corrected" in groups and (corrected_root / t / "rule_artifact.json").exists():
            found[f"corrected_{t}"] = corrected_root / t / "rule_artifact.json"
    if "multi" in groups and (single_root / "pooled" / "rule_artifact.json").exists():
        found["pooled"] = single_root / "pooled" / "rule_artifact.json"
    if "corrected" in groups and (corrected_root / "pooled" / "rule_artifact.json").exists():
        found["corrected_pooled"] = corrected_root / "pooled" / "rule_artifact.json"
    return found


def group_of(policy: str) -> str:
    if policy.startswith("oracle_"):
        return "oracle"
    if policy in TEACHERS:
        return "teacher"
    if policy.startswith("ilp_corrected_"):
        return "outcome-corrected ILP"
    if policy == "ilp_pooled":
        return "multi-teacher ILP"
    return "single-teacher ILP"


def png(fig) -> str:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buffer.getvalue()).decode()


def heatmap(matrix: pd.DataFrame, title: str, fmt: str, cmap: str, center: float | None, label: str):
    fig, ax = plt.subplots(figsize=(1.1 * len(matrix.columns) + 3, 0.45 * len(matrix) + 1.5))
    values = matrix.to_numpy(dtype=float)
    if center is not None:
        span = max(abs(values.min() - center), abs(values.max() - center)) or 1.0
        image = ax.imshow(values, cmap=cmap, vmin=center - span, vmax=center + span, aspect="auto")
    else:
        image = ax.imshow(values, cmap=cmap, vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(matrix.columns)), matrix.columns, rotation=40, ha="right")
    ax.set_yticks(range(len(matrix.index)), matrix.index)
    for i in range(values.shape[0]):
        for j in range(values.shape[1]):
            ax.text(j, i, format(values[i, j], fmt), ha="center", va="center", fontsize=8)
    ax.set_title(title)
    fig.colorbar(image, ax=ax, label=label)
    return fig


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--robots", type=int, default=100)
    parser.add_argument("--orders", type=int, default=300)
    parser.add_argument("--seed", type=int, default=116, help="a validation seed: the rules never saw it")
    parser.add_argument("--groups", default="single,multi,corrected")
    parser.add_argument("--only", default="", help="comma list of rule sets to keep, e.g. nearest_astar,corrected_nearest_astar")
    parser.add_argument("--single-rules", type=Path, default=CAMPAIGN / "rules/o300/A/full")
    parser.add_argument("--corrected-rules", type=Path, default=CAMPAIGN / "dev/corrected/o300")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--oracle", default="", help="teachers to run the oracle for, e.g. nearest_astar,multi_criteria "
                        "(about 15-20 min each at 100 robots; reused once made)")
    args = parser.parse_args()

    name = f"demo/r{args.robots}_o{args.orders}_s{args.seed}"
    out = REPO / "runs" / name
    rules = rule_sets(args.single_rules, args.corrected_rules, set(args.groups.split(",")))
    if args.only:
        keep = set(args.only.split(","))
        rules = {label: path for label, path in rules.items() if label in keep}
        name += "_" + "_".join(sorted(keep))
        out = REPO / "runs" / name
    print(f"demo: {len(TEACHERS)} teachers + {len(rules)} rule sets, {args.robots} robots, "
          f"{args.orders} orders, workload seed {args.seed} -> {out}", flush=True)

    # 1. Run every policy once (reused when already there).
    command = [sys.executable, str(REPO / "src/simulation_env/scripts/run_experiment.py"),
               "--name", name, "--strategies", *TEACHERS,
               "--rules", *[f"{label}={path}" for label, path in rules.items()],
               "--robots", str(args.robots), "--seeds", str(args.seed),
               "--orders", str(args.orders), "--workload-orders", str(args.orders),
               "--quantum", "3", "--window", "fleet_size", "--purpose", "backup",
               "--features", "--jobs", str(args.jobs), "--resume"]
    done = subprocess.run(command, cwd=REPO)
    if done.returncode != 0:
        print("run_experiment failed; see its output above", file=sys.stderr)
        return done.returncode

    summary = pd.read_csv(out / "summary.csv")
    summary["fallback_rate"] = summary.get("fallback_rate", pd.Series(0.0, index=summary.index)).fillna(0.0)

    # 1b. Oracle: the teacher's run with every decision replaced by its rollout-best pair.
    oracles = [o for o in args.oracle.split(",") if o]
    jobs = []
    for t in oracles:
        folder = out / f"oracle_{t}" / f"r{args.robots}_s{args.seed}"
        if not (folder / "oracle.json").exists():
            print(f"oracle for {t}: running (progress in {folder.parent / 'oracle.log'})", flush=True)
            folder.parent.mkdir(parents=True, exist_ok=True)
            log = open(folder.parent / "oracle.log", "w")
            jobs.append(subprocess.Popen(
                [sys.executable, "-m", "ilp_learning.m6_oracle", str(out / t / f"r{args.robots}_s{args.seed}"),
                 "--out", str(folder)], cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
                env={**__import__("os").environ, "PYTHONPATH": ":".join(
                    str(REPO / "src" / p) for p in ("mrta_core", "simulation_env", "task_management", "ilp_learning"))}))
    for job in jobs:
        job.wait()
    extra = []
    for t in oracles:
        folder = out / f"oracle_{t}" / f"r{args.robots}_s{args.seed}"
        report = json.loads((folder / "oracle.json").read_text())
        o = report["oracle"]
        outcomes = pd.read_parquet(folder / "outcomes.parquet")
        extra.append({"strategy": f"oracle_{t}", "order_makespan_s": o["order_makespan_s"],
                      "mean_completion_time_s": float(outcomes["completed_at"].mean()),
                      "waiting_per_order_s": o["waiting_time_s"] / o["orders_completed"],
                      "distance_per_order_m": o["distance_m"] / o["orders_completed"],
                      "fallback_rate": 0.0, "overrides": report["overrides"]})
    if extra:
        summary = pd.concat([summary, pd.DataFrame(extra)], ignore_index=True)
    summary["group"] = summary["strategy"].map(group_of)
    table = summary[["strategy", "group", "order_makespan_s", "mean_completion_time_s", "waiting_per_order_s",
                     "distance_per_order_m", "fallback_rate"]].sort_values("order_makespan_s")
    table.to_csv(out / "demo_table.csv", index=False)
    makespan = summary.set_index("strategy")["order_makespan_s"]

    # 2. Charts.
    fig, ax = plt.subplots(figsize=(10, 0.32 * len(table) + 1.5))
    ax.barh(table["strategy"], table["order_makespan_s"], color=table["group"].map(GROUP_COLORS))
    ax.invert_yaxis()
    ax.set_xlim(left=table["order_makespan_s"].min() * 0.9)
    ax.set_xlabel("makespan (s), lower is better")
    ax.legend([plt.Rectangle((0, 0), 1, 1, color=c) for c in GROUP_COLORS.values()], GROUP_COLORS.keys(),
              loc="lower right")
    ax.set_title("All policies on the same workload")
    ranking = png(fig)

    pairs = []
    for t in TEACHERS:
        # A teacher without a learned copy in this demo gets no bar (NaN), not an error.
        row = {"teacher": t, "teacher_s": makespan.get(t, float("nan"))}
        row["single_s"] = makespan.get(f"ilp_{t}", float("nan"))
        row["corrected_s"] = makespan.get(f"ilp_corrected_{t}", float("nan"))
        row["oracle_s"] = makespan.get(f"oracle_{t}", float("nan"))
        pairs.append(row)
    pairs = pd.DataFrame(pairs).set_index("teacher")
    fig, ax = plt.subplots(figsize=(11, 4.5))
    width = 0.2
    x = range(len(pairs))
    for k, (column, label, color) in enumerate([
            ("teacher_s", "teacher", GROUP_COLORS["teacher"]),
            ("single_s", "single-teacher ILP copy", GROUP_COLORS["single-teacher ILP"]),
            ("corrected_s", "outcome-corrected ILP", GROUP_COLORS["outcome-corrected ILP"]),
            ("oracle_s", "oracle (best possible)", GROUP_COLORS["oracle"])]):
        ax.bar([i + (k - 1.5) * width for i in x], pairs[column], width, label=label, color=color)
    ax.set_xticks(list(x), pairs.index, rotation=30, ha="right")
    ax.set_ylim(bottom=pairs.min().min() * 0.9)
    ax.set_ylabel("makespan (s)")
    ax.set_title("Each teacher beside its learned copies")
    ax.legend()
    copies = png(fig)

    ilp = [s for s in makespan.index if s.startswith(("ilp_", "oracle_"))]
    results = pd.DataFrame({t: [makespan[s] - makespan[t] for s in ilp] for t in TEACHERS}, index=ilp)
    results.to_csv(out / "crossover_results.csv")
    cross_results = png(heatmap(results, "Crossover results: ILP policy minus teacher (s); negative = ILP faster",
                                ".0f", "RdYlGn_r", 0.0, "seconds"))

    # 3. Crossover choices: each rule set in each teacher's recorded situations.
    agreement = {}
    for label, path in rules.items():
        program, _ = load_rule_artifact(path)
        matching = matching_of_artifact(path)
        agreement[f"ilp_{label}"] = {}
        for t in TEACHERS:
            folder = out / t / f"r{args.robots}_s{args.seed}"
            run = InductionRun(json.loads((folder / "run.json").read_text()),
                               pd.read_parquet(folder / "decisions.parquet"),
                               pd.read_parquet(folder / "features.parquet"))
            frame = with_features(run).assign(est_finish_s=derive_est_finish_s(run.decisions, run.manifest))
            scores = rates(evaluate_frame(frame, program, matching)["scopes"]["choice_bearing"])
            agreement[f"ilp_{label}"][t] = scores["frozen_key"]
        print(f"  choices compared: ilp_{label}", flush=True)
    agree = pd.DataFrame(agreement).T[TEACHERS]
    agree.to_csv(out / "crossover_choices.csv")
    cross_choices = png(heatmap(agree, "Crossover choices: share of the teacher's picks the rule set makes too",
                                ".0%", "Blues", None, "agreement"))

    # 3b. The deeper look: one graph per question, from the runs' own tables.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from demo_deeper import load, sections  # noqa: E402
    deeper = sections(load(out, list(summary["strategy"]), args.robots, args.seed), group_of, GROUP_COLORS, png)
    deeper_html = "".join(
        f"<h3>{title}</h3><p><b>Question:</b> {question}</p><p>{how}</p>"
        + (body if body.lstrip().startswith("<table") else f'<img src="data:image/png;base64,{body}">')
        for title, question, how, body in deeper)

    # 3c. Workload length: when does the battery start to matter?
    ref = summary.set_index("strategy").loc["nearest_astar"] if "nearest_astar" in set(summary["strategy"]) else summary.iloc[0]
    per_order = float(ref["energy_consumed_percent"]) / float(ref["orders_completed"])
    usable = 100.0 - 10.0  # start full, charge at 10%
    before_charge = usable / per_order
    lengths = []
    for n in (300, 500, 1000, 2000, 5000):
        each = n / args.robots
        lengths.append(f"<tr><td>{n}</td><td>{each:.0f}</td><td>{each * per_order:.0f}%</td>"
                       f"<td>{'no' if each < before_charge else f'about {each * per_order / usable:.1f}'}</td></tr>")
    oracle_note = ""
    if oracles:
        oracle_note = ("<h2>The oracle</h2><p>The oracle is the teacher with a look-ahead: at every decision it copies "
                       "the warehouse, tries about four candidate pairs, simulates each for 120 s with the teacher "
                       "continuing, and takes the one that finished the orders soonest. It is not a deployable "
                       "policy (it simulates at every decision and explains nothing); it shows how much better "
                       "choices could make the run. A learned policy is judged by how much of the gap between "
                       "its teacher and the oracle it closes.</p>")

    # 4. The page.
    rows = "".join(
        f"<tr><td>{r.strategy}</td><td>{r.group}</td><td>{r.order_makespan_s:.1f}</td>"
        f"<td>{r.mean_completion_time_s:.1f}</td><td>{r.waiting_per_order_s:.1f}</td>"
        f"<td>{r.distance_per_order_m:.1f}</td><td>{r.fallback_rate:.0%}</td></tr>"
        for r in table.itertuples())
    html = f"""<!doctype html><meta charset="utf-8"><title>ILP vs teachers demo</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;line-height:1.5}}
img{{max-width:100%}}table{{border-collapse:collapse;font-size:14px}}td,th{{border-bottom:1px solid #ddd;padding:4px 8px;text-align:left}}</style>
<h1>Learned ILP policies vs the strategies they learned from</h1>
<p>{args.robots} robots, {args.orders} orders, workload seed {args.seed}. The rules were learned on training
seeds 104-110, so this workload is new to them. One run per policy: good for showing behaviour, not a
statistical test (the paired evaluation over 5 seeds and 4 fleet sizes is the evidence).</p>
<h2>How it works</h2>
<p>A <b>teacher</b> is a conventional allocation strategy. It ran the warehouse and every decision was recorded.
<b>Single-teacher ILP</b>: Popper learned short logic rules that reproduce one teacher's choices.
<b>Multi-teacher ILP</b>: one rule set from all eight teachers' decisions.
<b>Outcome-corrected ILP</b>: the teacher's choices, corrected where simulating the alternatives showed a
clearly better choice. When no rule covers a pair, a fixed fallback picks the oldest order and the nearest robot.</p>
<h2>1. Ranking</h2><img src="data:image/png;base64,{ranking}">
<h2>2. Each teacher beside its learned copies</h2><img src="data:image/png;base64,{copies}">
<h2>3. Crossover results</h2><p>Every learned policy against every teacher on this workload: makespan of the
policy minus the teacher's, in seconds. Green means the learned policy finished sooner.</p>
<img src="data:image/png;base64,{cross_results}">
<h2>4. Crossover choices</h2><p>Each rule set placed in each teacher's own recorded situations: the share of the
teacher's picks it would make too. A strong diagonal means each rule set learned its own teacher; a bright
column for auction means a rule set behaves like auction.</p>
<img src="data:image/png;base64,{cross_choices}">
{oracle_note}
<h2>5. A deeper look</h2>{deeper_html}
<h2>6. Why 300 orders shows no battery effect</h2>
<p>The battery model is part of every run, but it only matters once robots reach the 10% charging level. In this run an
order used about <b>{per_order:.1f}%</b> battery on average, so a robot can do about <b>{before_charge:.0f} orders</b>
from full before its first charge. With {args.robots} robots the fleet needs roughly
<b>{before_charge * args.robots:.0f} orders</b> before charging starts.</p>
<table><tr><th>Orders</th><th>Orders per robot</th><th>Battery used per robot</th><th>Charges per robot</th></tr>
{"".join(lengths)}</table>
<p>So 300 and even 500 orders never reach charging at {args.robots} robots; battery-aware choices cannot matter there.
Charging starts above about {before_charge * args.robots:.0f} orders and becomes a regular part of the work at 5000
(one measured 5000-order run with nearest_astar at 100 robots had 300 charging sessions). Longer workloads also add
steady congestion instead of a start-up rush and a tail, which is where the oracle's gain is largest.</p>
<h2>All numbers</h2><table><tr><th>Policy</th><th>Group</th><th>Makespan (s)</th><th>Mean completion (s)</th>
<th>Waiting per order (s)</th><th>Distance per order (m)</th><th>Fallback picks</th></tr>{rows}</table>
"""
    (out / "report.html").write_text(html, encoding="utf-8")
    print(table.to_string(index=False))
    print(f"\nreport : {out / 'report.html'}\ntables : demo_table.csv, crossover_results.csv, crossover_choices.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
