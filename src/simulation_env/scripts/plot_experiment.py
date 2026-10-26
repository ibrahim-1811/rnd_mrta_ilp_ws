#!/usr/bin/env python3

"""Plot an experiment written by run_experiment.py: every core metric.

    python3 src/simulation_env/scripts/plot_experiment.py --name fleet_sweep

reads runs/<name>/summary.csv and each run's outcomes.parquet, and
writes runs/<name>/figures/:

    mrta.png               order makespan, throughput, completion time,
                           fleet settled time, distance (fleet and per
                           phase), waiting, allocator CPU
    traffic.png            waiting per order, waiting share, yields
    battery.png            energy (fleet and per phase), minimum SOC,
                           charging, time out of the fleet, waiting for
                           a charger, saturation
    completion_curves.png  orders completed over time, per fleet size
    order_times.png        per-order duration and waiting, per strategy

and runs/<name>/summary.md, the same numbers as a table. Every number
is read from the recorded run, never typed in. A stochastic strategy
is drawn as its mean over seeds with the min-max range as a bar.
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]

# Categorical slots in fixed order, validated for colour-vision
# deficiency (dataviz reference palette, light mode). Colour follows the
# strategy, never its rank; every strategy also has its own marker.
STYLE = {
    "nearest_astar": ("#2a78d6", "o", "nearest A*"),
    "nearest_manhattan": ("#eb6834", "s", "nearest Manhattan"),
    "random": ("#1baf7a", "^", "random"),
}
EXTRA = [("#eda100", "D"), ("#e87ba4", "v"), ("#008300", "P")]

SURFACE = "#fcfcfb"
INK = "#1f1f1e"
MUTED = "#6b6a63"
GRID = "#e4e3de"

# Headline makespan and throughput are on order work (final drop-off);
# makespan_s is the fleet settled time, end-of-work recovery included.
# Full-fleet distance and energy sit beside their phase components.
# See docs/DECISIONS.md, "End of Workload". A version-1 summary has no
# order_* or phase fields, and those panels stay empty.
MRTA = [
    ("order_makespan_s", "Order makespan (final drop-off)", "simulated s"),
    ("order_throughput_per_h", "Throughput", "orders / hour of order makespan"),
    ("mean_completion_time_s", "Mean order completion time", "simulated s"),
    ("makespan_s", "Fleet settled time", "simulated s"),
    ("total_distance_m", "Distance (fleet)", "m"),
    ("productive_distance_m", "Distance: order work", "m"),
    ("support_distance_m", "Distance: support", "m"),
    ("recovery_distance_m", "Distance: recovery", "m"),
    ("total_waiting_s", "Total waiting (traffic)", "robot-s"),
    ("allocator_cpu_s", "Allocator runtime", "CPU s"),
]

TRAFFIC = [
    ("waiting_per_order_s", "Waiting per order", "s"),
    ("waiting_share", "Waiting share of mission time", "fraction"),
    ("yield_events", "Yield events", "count"),
]

BATTERY = [
    ("energy_consumed_percent", "Energy consumed (fleet)", "% SOC"),
    ("productive_energy_percent", "Energy: order work", "% SOC"),
    ("support_energy_percent", "Energy: support", "% SOC"),
    ("recovery_energy_percent", "Energy: recovery", "% SOC"),
    ("energy_per_order_percent", "Energy per order", "% SOC"),
    ("min_soc_percent", "Lowest SOC reached", "% SOC"),
    ("charge_sessions", "Charge sessions", "count"),
    ("charging_time_s", "Time on chargers", "robot-s"),
    ("out_of_fleet_time_s", "Out of the fleet to charge", "robot-s"),
    ("charger_wait_time_s", "Waiting for a charger", "robot-s"),
    ("saturation_events", "Saturation events", "count"),
]


def style_of(strategy, order):
    if strategy in STYLE:
        return STYLE[strategy]

    color, marker = EXTRA[order.index(strategy) % len(EXTRA)]
    return color, marker, strategy


def load(summary_path):
    with open(summary_path) as handle:
        rows = list(csv.DictReader(handle))

    for row in rows:
        for key, value in row.items():
            try:
                row[key] = float(value)
            except (TypeError, ValueError):
                pass

    return rows


def by_strategy(rows, metric):
    """strategy -> sorted [(robots, mean, low, high)] over seeds."""

    values = defaultdict(lambda: defaultdict(list))

    for row in rows:
        value = row.get(metric)

        if isinstance(value, float):
            values[row["strategy"]][int(row["robots"])].append(value)

    return {
        strategy: [
            (robots, mean(v), min(v), max(v)) for robots, v in sorted(points.items())
        ]
        for strategy, points in values.items()
    }


def setup(ax, title, unit):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, color=INK, fontsize=11, loc="left")
    ax.set_ylabel(unit, color=MUTED, fontsize=9)
    ax.set_xlabel("robots", color=MUTED, fontsize=9)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.tick_params(colors=MUTED, labelsize=9)

    for side in ("top", "right"):
        ax.spines[side].set_visible(False)

    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)


def metric_grid(rows, metrics, title, path, cols):
    strategies = sorted({row["strategy"] for row in rows})
    fleet = sorted({int(row["robots"]) for row in rows})
    lines = len(metrics)
    rows_n = (lines + cols - 1) // cols

    fig, axes = plt.subplots(rows_n, cols, figsize=(4.2 * cols, 3.4 * rows_n), squeeze=False)
    fig.patch.set_facecolor(SURFACE)

    for ax, (metric, name, unit) in zip(axes.flat, metrics):
        setup(ax, name, unit)
        series = by_strategy(rows, metric)

        for strategy in strategies:
            points = series.get(strategy)

            if not points:
                continue

            color, marker, label = style_of(strategy, strategies)
            x = [p[0] for p in points]
            y = [p[1] for p in points]

            ax.plot(x, y, color=color, marker=marker, markersize=7, linewidth=2, label=label)

            spread = [(p[0], p[2], p[3]) for p in points if p[3] > p[2]]

            if spread:
                ax.vlines(
                    [s[0] for s in spread],
                    [s[1] for s in spread],
                    [s[2] for s in spread],
                    color=color,
                    linewidth=3,
                    alpha=0.35,
                )

        ax.set_xticks(fleet)

    for ax in list(axes.flat)[lines:]:
        ax.set_visible(False)

    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", frameon=False, fontsize=10, labelcolor=INK, ncols=len(labels))
    fig.suptitle(title, x=0.01, ha="left", color=INK, fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=140, facecolor=SURFACE)
    plt.close(fig)

    return path


def outcomes_of(row):
    run_dir = ROOT / row["run_dir"]
    return pq.read_table(run_dir / "outcomes.parquet").to_pydict()


def first_seed(rows):
    """One run per (strategy, robots): the lowest seed, for per-order plots."""

    chosen = {}

    for row in sorted(rows, key=lambda r: r["seed"]):
        chosen.setdefault((row["strategy"], int(row["robots"])), row)

    return chosen


def completion_curves(rows, path):
    chosen = first_seed(rows)
    strategies = sorted({key[0] for key in chosen})
    fleet = sorted({key[1] for key in chosen})

    fig, axes = plt.subplots(1, len(fleet), figsize=(4.4 * len(fleet), 3.6), squeeze=False)
    fig.patch.set_facecolor(SURFACE)

    for ax, robots in zip(axes.flat, fleet):
        setup(ax, f"{robots} robots", "orders completed")
        ax.set_xlabel("simulated time (s)", color=MUTED, fontsize=9)

        for strategy in strategies:
            row = chosen.get((strategy, robots))

            if row is None:
                continue

            done = sorted(t for t in outcomes_of(row)["completed_at"] if t is not None)
            color, _, label = style_of(strategy, strategies)
            ax.step(done, range(1, len(done) + 1), where="post", color=color, linewidth=2, label=label)

    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", frameon=False, fontsize=10, labelcolor=INK, ncols=len(labels))
    fig.suptitle("Orders completed over time (random: seed shown is the lowest)", x=0.01, ha="left", color=INK, fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(path, dpi=140, facecolor=SURFACE)
    plt.close(fig)

    return path


def order_times(rows, path):
    chosen = first_seed(rows)
    strategies = sorted({key[0] for key in chosen})
    fleet = sorted({key[1] for key in chosen})

    fig, axes = plt.subplots(2, len(fleet), figsize=(4.4 * len(fleet), 6.6), squeeze=False)
    fig.patch.set_facecolor(SURFACE)

    for column, robots in enumerate(fleet):
        for line, (field, name) in enumerate(
            (("realized_duration_s", "Order duration, start to drop-off"), ("realized_waiting_s", "Waiting per order"))
        ):
            ax = axes[line][column]
            setup(ax, f"{name}, {robots} robots", "s")
            ax.set_xlabel("")

            data, colors, labels = [], [], []

            for strategy in strategies:
                row = chosen.get((strategy, robots))

                if row is None:
                    continue

                values = [v for v in outcomes_of(row)[field] if v is not None]
                color, _, label = style_of(strategy, strategies)
                data.append(values)
                colors.append(color)
                labels.append(label)

            parts = ax.boxplot(data, patch_artist=True, showfliers=False, widths=0.55)

            for patch, color in zip(parts["boxes"], colors):
                patch.set_facecolor(color)
                patch.set_alpha(0.8)
                patch.set_edgecolor(SURFACE)

            for median in parts["medians"]:
                median.set_color(INK)

            ax.set_xticks(range(1, len(labels) + 1), labels, fontsize=8)

    fig.suptitle("Per-order times (boxes: middle half; whiskers: 1.5 IQR)", x=0.01, ha="left", color=INK, fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=140, facecolor=SURFACE)
    plt.close(fig)

    return path


def summary_table(rows, path):
    metrics = [m for m, _, _ in MRTA + TRAFFIC + BATTERY]
    strategies = sorted({row["strategy"] for row in rows})
    fleet = sorted({int(row["robots"]) for row in rows})

    lines = ["| metric | robots | " + " | ".join(strategies) + " |"]
    lines.append("|---|---|" + "---|" * len(strategies))

    for metric in metrics:
        series = by_strategy(rows, metric)

        for robots in fleet:
            cells = []

            for strategy in strategies:
                point = next((p for p in series.get(strategy, []) if p[0] == robots), None)
                cells.append("" if point is None else f"{point[1]:.2f}")

            lines.append(f"| {metric} | {robots} | " + " | ".join(cells) + " |")

    path.write_text("\n".join(lines) + "\n")

    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", default="experiment")
    parser.add_argument("--out", default="runs")
    args = parser.parse_args()

    base = ROOT / args.out / args.name
    rows = load(base / "summary.csv")
    figures = base / "figures"
    figures.mkdir(exist_ok=True)

    written = [
        metric_grid(rows, MRTA, "MRTA performance by fleet size", figures / "mrta.png", 3),
        metric_grid(rows, TRAFFIC, "Traffic", figures / "traffic.png", 3),
        metric_grid(rows, BATTERY, "Battery and charging", figures / "battery.png", 4),
        completion_curves(rows, figures / "completion_curves.png"),
        order_times(rows, figures / "order_times.png"),
        summary_table(rows, base / "summary.md"),
    ]

    for path in written:
        print(f"  wrote {path.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
