"""Presentation-only plots of validated source artifacts; no simulation changes."""

import html
import json
import math
from pathlib import Path
from statistics import mean
from fastapi import HTTPException

METRICS = [
    ("order_makespan_s", "Order makespan", "simulated seconds", "lower"),
    (
        "order_throughput_per_h",
        "Order throughput",
        "orders per simulated hour",
        "higher",
    ),
    ("productive_distance_m", "Productive travel distance", "metres", "lower"),
    ("productive_waiting_s", "Productive traffic waiting", "robot-seconds", "lower"),
    (
        "makespan_s",
        "Fleet settled time (includes recovery)",
        "simulated seconds",
        "lower",
    ),
    ("total_distance_m", "Total fleet travel distance", "metres", "lower"),
    (
        "energy_consumed_percent",
        "Total fleet energy",
        "sum of SOC percentage points",
        "lower",
    ),
    ("min_soc_percent", "Lowest SOC reached", "SOC percent", "higher"),
    ("charge_sessions", "Charge sessions", "count", "context"),
    ("allocator_cpu_s", "Allocator CPU time", "CPU seconds; host dependent", "lower"),
]
COLORS = [
    "#0072B2",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#E69F00",
    "#56B4E9",
    "#7C4D00",
    "#555555",
]


def finite(v):
    return type(v) in (int, float) and math.isfinite(v)


def render(reader, eid, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    detail = reader.detail(eid)
    if not detail["complete"]:
        raise HTTPException(
            409, "Report refused: inspect missing/invalid/outside-grid runs first"
        )
    baseline = (
        "nearest_astar"
        if any(r["strategy"] == "nearest_astar" for r in detail["runs"])
        else detail["runs"][0]["strategy"]
    )
    comparison = reader.compare(eid, baseline, False)
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    strategies = sorted({r["strategy"] for r in detail["rows"]})
    fleets = sorted({int(r["robots"]) for r in detail["rows"]})
    known = [
        "auction",
        "earliest_finish",
        "hungarian",
        "milp",
        "multi_criteria",
        "nearest_astar",
        "nearest_manhattan",
        "random",
    ]
    color = {
        s: COLORS[known.index(s) if s in known else i % len(COLORS)]
        for i, s in enumerate(strategies)
    }
    rows = detail["rows"]
    sections = []
    images = []

    def esc(value):
        return html.escape(str(value))

    facts = dict(
        name=detail["name"],
        expected_runs=len(detail["runs"]),
        present_runs=len(rows),
        robots=fleets,
        strategies=strategies,
        seeds=sorted({int(r["seed"]) for r in rows}),
        actual_orders=sorted({int(r["orders"]) for r in rows}),
        requested_spec=detail["spec"],
    )
    metric_samples = {
        str(f): {
            m: {
                s: dict(
                    actual=sum(
                        r["strategy"] == s and finite(r.get(m))
                        for r in rows
                        if int(r["robots"]) == f
                    ),
                    expected=sum(
                        r["strategy"] == s for r in rows if int(r["robots"]) == f
                    ),
                )
                for s in strategies
            }
            for m, _, _, _ in METRICS
        }
        for f in fleets
    }
    (out / "data.json").write_text(
        json.dumps(
            dict(
                facts=facts,
                rows=rows,
                comparison=comparison,
                metric_samples=metric_samples,
            ),
            indent=2,
        )
    )

    def axis(ax, selected, metric, title, unit):
        counts = []
        all_values = []
        for i, s in enumerate(strategies):
            values = [
                (int(r["seed"]), r[metric])
                for r in selected
                if r["strategy"] == s and finite(r.get(metric))
            ]
            if not values:
                ax.text(
                    0.03,
                    i,
                    "Not recorded",
                    transform=ax.get_yaxis_transform(),
                    va="center",
                    fontsize=9,
                    color="#66715e",
                )
                continue
            values = sorted(values)
            v = [x for _, x in values]
            all_values += v
            counts.append(len(v))
            ax.hlines(i, min(v), max(v), color=color[s], alpha=0.45, lw=3)
            offsets = (
                [0]
                if len(v) == 1
                else [(k / (len(v) - 1) - 0.5) * 0.24 for k in range(len(v))]
            )
            ax.scatter(
                v, [i + d for d in offsets], color=color[s], s=18, alpha=0.6, zorder=3
            )
            ax.scatter(
                [mean(v)],
                [i],
                color=color[s],
                marker="D",
                s=48,
                edgecolors="white",
                linewidth=0.7,
                zorder=4,
            )
        labels = [
            s.replace("_", " ")
            + f" (n={sum(r['strategy'] == s and finite(r.get(metric)) for r in selected)}/{sum(r['strategy'] == s for r in selected)})"
            for s in strategies
        ]
        ax.set_yticks(range(len(strategies)), labels)
        ax.invert_yaxis()
        ax.set_title(title, loc="left", fontsize=12, pad=12)
        ax.set_xlabel(unit, fontsize=10)
        ax.grid(axis="x", alpha=0.2)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(labelsize=10)
        if all_values and all(v == 0 for v in all_values):
            ax.set_xlim(-0.1, 1)
            ax.set_xticks([0])
            ax.text(
                0.22,
                0.5,
                "All recorded values are zero\nNo event observed in this workload",
                transform=ax.transAxes,
                fontsize=11,
                color="#66715e",
                va="center",
            )
        elif not all_values:
            ax.text(
                0.5,
                0.5,
                "Not recorded / unavailable",
                transform=ax.transAxes,
                ha="center",
            )
            ax.set_xticks([])
        else:
            ax.margins(x=0.12)
        return counts

    for fleet in fleets:
        folder = out / f"r{fleet}"
        folder.mkdir()
        selected = [r for r in rows if int(r["robots"]) == fleet]
        fig, axes = plt.subplots(2, 2, figsize=(13, 10), layout="constrained")
        for ax, (m, title, unit, _) in zip(axes.flat, METRICS[:4]):
            axis(ax, selected, m, title, unit)
        fig.suptitle(
            f"{detail['name']} · {fleet} robots · {len(facts['seeds'])} seeds\nPoints = seed results; diamond = mean; line = min–max, not confidence interval",
            fontsize=13,
        )
        fig.savefig(folder / "overview.png", dpi=150)
        plt.close(fig)
        images.append(f"r{fleet}/overview.png")
        cards = []
        for m, title, unit, direction in METRICS:
            fig, ax = plt.subplots(
                figsize=(10, max(3.5, 0.5 * len(strategies) + 1.8)),
                layout="constrained",
            )
            axis(ax, selected, m, title, unit)
            fig.suptitle(
                f"{fleet} robots · {direction} is better"
                if direction != "context"
                else f"{fleet} robots · workload context",
                fontsize=10,
                color="#66715e",
            )
            path = f"r{fleet}/{m}.png"
            fig.savefig(out / path, dpi=150)
            plt.close(fig)
            images.append(path)
            cards.append(
                f'<details><summary>{esc(title)} — {esc(unit)}</summary><img src="{path}" alt="{esc(title)}"></details>'
            )
        availability = (
            "<details><summary>Metric sample counts (actual/expected)</summary>"
            + "".join(
                "<p>"
                + esc(m)
                + ": "
                + ", ".join(
                    esc(s) + " n=" + str(v["actual"]) + "/" + str(v["expected"])
                    for s, v in metric_samples[str(fleet)][m].items()
                )
                + "</p>"
                for m, _, _, _ in METRICS
            )
            + "</details>"
        )
        extra = outcome_panels(reader, detail, fleet, folder, plt, color, strategies)
        images.extend(f"r{fleet}/" + name for name in extra)
        cards.extend(
            "<details><summary>"
            + esc(name)
            + '</summary><img src="r'
            + str(fleet)
            + "/"
            + name
            + '" alt="'
            + esc(name)
            + '"></details>'
            for name in extra
        )
        sections.append(
            f'<h2>{fleet} robots</h2><img src="r{fleet}/overview.png" alt="Four primary metrics by strategy"><p>Read each strategy on its own row. Small points are actual seeds; diamonds are means; lines are observed min–max, not CI. Overlapping values mean matching results.</p>'
            + availability
            + "".join(cards)
        )
    paired = []
    for r in comparison["records"]:
        ci = (
            "Unavailable: n < 2"
            if r["ci_low"] is None
            else f"[{r['ci_low']:.3f}, {r['ci_high']:.3f}]"
        )
        paired.append(
            "<tr>"
            + "".join(
                f"<td>{esc(v)}</td>"
                for v in [
                    r["strategy"],
                    r["robots"],
                    r["metric"],
                    f"{r['n']}/{r['expected_n']}",
                    f"{r['mean_difference']:.3f}",
                    ci,
                ]
            )
            + "</tr>"
        )
    content = f"""<!doctype html><meta charset="utf-8"><title>{esc(detail["name"])} — evidence report</title>
<style>body{{font:16px system-ui;color:#27392d;background:#f6f8f2;max-width:1250px;margin:35px auto;padding:0 22px}}h1{{font-size:30px}}p{{line-height:1.7}}img{{width:100%;height:auto;background:white;border:1px solid #dee5d4;border-radius:8px}}details{{background:white;border:1px solid #dee5d4;padding:16px;margin:12px 0}}summary{{cursor:pointer}}table{{border-collapse:collapse;font-size:13px;width:100%}}td,th{{padding:10px;border-bottom:1px solid #dfe5d7;text-align:left}}.note{{background:#fff2d6;padding:18px;border-radius:8px}}.table{{overflow:auto}}</style>
<h1>{esc(detail["name"])}</h1><p>{len(rows)}/{len(detail["runs"])} validated runs · fleets {esc(fleets)} · seeds {esc(facts["seeds"])} · <b>actual orders/run {esc(facts["actual_orders"])}</b>.</p>
<p class="note">Requested orders {esc(detail["spec"].get("orders", "unknown"))}; generated workload orders {esc(detail["spec"].get("workload_orders", "unknown"))}. A requested limit cannot create more orders than the file contains. This report never changes original figures or scientific artifacts. {len(facts["seeds"])} seeds is a limited sample; this is not a ranking or definitive study.</p>
{"".join(sections)}<h2>Paired differences vs {esc(baseline)}</h2><p>Strategy minus baseline. Original simulation_env.paired math; intervals here are paired 95% t intervals. Negative order makespan is earlier; positive throughput is higher. Actual n can differ by metric.</p><div class="table"><table><thead><tr><th>Strategy</th><th>Fleet</th><th>Metric</th><th>Actual/expected n</th><th>Mean difference</th><th>95% CI</th></tr></thead><tbody>{"".join(paired)}</tbody></table></div><p>Complete source values and comparison records: <a href="data.json">data.json</a>. Metric names/units retain original semantics; total energy sums SOC percentage points across robots.</p>"""
    (out / "index.html").write_text(content)
    return dict(
        expected_runs=len(detail["runs"]),
        present_runs=len(rows),
        images=images,
        facts=facts,
        index=str(out / "index.html"),
    )


def outcome_panels(reader, detail, fleet, folder, plt, colors, strategies):
    """Actual per-order observations, descriptive only; no statistical pooling claim."""
    from control_center.adapters.tables import rows
    from control_center.storage.files import open_source, read_json

    traces = {}
    for run in detail["runs"]:
        if run["robots"] != fleet:
            continue
        with open_source(reader.root, str(Path(run["directory"]) / "run.json")) as (
            h,
            _,
        ):
            manifest = read_json(h)
        name = "outcomes." + manifest["format"]
        with open_source(reader.root, str(Path(run["directory"]) / name)) as (h, _):
            traces[(run["strategy"], run["seed"])] = [
                (
                    r.get("completed_at"),
                    r.get("realized_duration_s"),
                    r.get("realized_waiting_s"),
                )
                for r in rows(h, name)
            ]
    written = []
    for seed in sorted({seed for _, seed in traces}):
        fig, ax = plt.subplots(figsize=(11, 6))
        any_data = False
        for i, s in enumerate(strategies):
            times = sorted(t[0] for t in traces.get((s, seed), []) if finite(t[0]))
            if not times:
                continue
            any_data = True
            ax.step(
                [0] + times,
                list(range(len(times) + 1)),
                where="post",
                color=colors[s],
                label=s.replace("_", " "),
                lw=1.8,
                linestyle=["-", "--", "-.", ":"][i % 4],
            )
        if not any_data:
            plt.close(fig)
            continue
        ax.set(
            title=f"Orders completed · {fleet} robots · seed {seed}",
            xlabel="simulated seconds",
            ylabel="completed logical orders",
        )
        ax.grid(alpha=0.2)
        ax.legend(
            loc="upper center",
            bbox_to_anchor=(0.5, -0.15),
            ncol=min(4, len(strategies)),
            frameon=False,
            fontsize=9,
        )
        fig.subplots_adjust(left=0.1, right=0.97, top=0.9, bottom=0.3)
        name = f"completion_seed_{seed}.png"
        fig.savefig(folder / name, dpi=150)
        plt.close(fig)
        written.append(name)
    for column, metric in [(1, "order_duration"), (2, "order_waiting")]:
        data = [
            [
                t[column]
                for (s, _), trace in traces.items()
                if s == strategy
                for t in trace
                if finite(t[column])
            ]
            for strategy in strategies
        ]
        if not all(data):
            continue
        fig, ax = plt.subplots(
            figsize=(11, max(4, 0.5 * len(strategies) + 2)), layout="constrained"
        )
        if all(v == 0 for values in data for v in values):
            ax.axis("off")
            ax.text(
                0.5,
                0.5,
                "All recorded order waiting is zero. No traffic waiting occurred.",
                ha="center",
                va="center",
            )
        else:
            ax.boxplot(
                data,
                vert=False,
                showfliers=True,
                flierprops=dict(markersize=2, alpha=0.4),
            )
            ax.set_yticks(
                range(1, len(strategies) + 1),
                [
                    s.replace("_", " ") + f" ({len(v)} orders)"
                    for s, v in zip(strategies, data)
                ],
            )
            ax.set_xlabel("simulated seconds")
            ax.grid(axis="x", alpha=0.2)
        ax.set_title(
            f"{metric.replace('_', ' ').title()} · {fleet} robots\nDescriptive individual orders across recorded seeds; not independent seed confidence intervals",
            fontsize=11,
        )
        name = metric + ".png"
        fig.savefig(folder / name, dpi=150)
        plt.close(fig)
        written.append(name)
    return written
