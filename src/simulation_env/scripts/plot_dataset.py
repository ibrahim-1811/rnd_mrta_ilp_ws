#!/usr/bin/env python3

"""Plot figures from recorded Phase 8 datasets.

Every number in every figure is read from a recorded run, so a figure
cannot drift from the simulator the way a hand-drawn one does. Re-run
this after any change that moves the baselines and the documentation
figures update with it.

    python3 src/simulation_env/scripts/plot_dataset.py \
        --run legacy=runs/legacy \
        --run continuous=runs/astar \
        --run batched=runs/batched \
        --out docs/figures

Each --run is LABEL=DIR, where DIR is a directory written by
run_workload.py --dataset. Order is preserved in the figures.
"""

import argparse
from pathlib import Path
import sys


# Run as a script, not a module, so the source tree has to be importable
# before the project imports below.
ROOT = Path(__file__).resolve().parents[3]

for package in ("mrta_core", "simulation_env", "task_management"):
    candidate = ROOT / "src" / package

    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from simulation_env.dataset_export import read_run  # noqa: E402


# One colour per run label, kept consistent across every figure so the
# same configuration is the same colour wherever it appears.
COLOURS = ("#b4432f", "#2f6fb4", "#2f9b6f", "#8a6fb4")


def per_decision(decisions):
    """One record per decision, from the per-pair candidate rows.

    The shape columns (free_robots, window_orders) are properties of the
    decision and repeat across its candidate rows, so the first row of
    each group carries them.
    """

    seen = {}

    for row in decisions:
        seen.setdefault(row["decision_id"], row)

    return [seen[key] for key in sorted(seen)]


def load(runs):
    loaded = []

    for label, path in runs:
        decisions, outcomes, manifest = read_run(path)

        shapes = per_decision(decisions)

        forced = [
            row
            for row in shapes
            if row["free_robots"] == 1 and row["window_orders"] == 1
        ]

        loaded.append(
            {
                "label": label,
                "manifest": manifest,
                "decisions": decisions,
                "outcomes": outcomes,
                "shapes": shapes,
                "forced": len(forced),
                "robots": [row["free_robots"] for row in shapes],
                "orders": [row["window_orders"] for row in shapes],
            }
        )

    return loaded


def figure_decision_shape(runs, out_dir):
    """Why S0 exists: a 1x1 decision cannot tell two strategies apart."""

    import matplotlib.pyplot as plt

    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 4.2))

    labels = [run["label"] for run in runs]
    forced = [run["forced"] for run in runs]
    free = [len(run["shapes"]) - run["forced"] for run in runs]

    left.bar(labels, forced, color="#b4432f", label="forced 1x1")
    left.bar(
        labels,
        free,
        bottom=forced,
        color="#2f6fb4",
        label="choice-bearing",
    )

    for index, run in enumerate(runs):
        total = len(run["shapes"])
        share = 100.0 * run["forced"] / total if total else 0.0

        left.text(
            index,
            total + 1.5,
            f"{run['forced']}/{total}\n{share:.1f}% forced",
            ha="center",
            va="bottom",
            fontsize=9,
        )

    left.set_ylabel("allocation decisions")
    left.set_title("Decisions that could distinguish two strategies")
    left.set_ylim(0, max(len(run["shapes"]) for run in runs) * 1.35)
    left.legend(loc="upper right", fontsize=9)

    span = range(1, max(max(r["robots"]) for r in runs) + 1)
    width = 0.8 / len(runs)

    for index, run in enumerate(runs):
        counts = [run["robots"].count(value) for value in span]
        mean = sum(run["robots"]) / len(run["robots"])

        right.bar(
            [value + (index - (len(runs) - 1) / 2) * width for value in span],
            counts,
            width=width,
            color=COLOURS[index % len(COLOURS)],
            label=f"{run['label']} (mean {mean:.2f})",
        )

    # Log scale: the opening dispatch offers the whole parked fleet at
    # once, so one decision sits two orders of magnitude from the rest
    # and a linear axis flattens everything else to nothing.
    right.set_yscale("log")
    right.set_xticks(list(span))
    right.set_xlabel("free robots offered to the allocator")
    right.set_ylabel("decisions (log scale)")
    right.set_title("Robots per decision")
    right.legend(fontsize=9)

    fig.suptitle(
        "S0: the candidate window and the dispatch quantum "
        "(22 robots, 100 orders, nearest_astar)",
        fontsize=11,
    )
    fig.tight_layout()

    path = out_dir / "decision_shape.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)

    return path


def figure_predicted_vs_realized(runs, out_dir):
    """The predicted/realized join, and where divergence legitimately is."""

    import matplotlib.pyplot as plt

    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 4.2))

    run = runs[-1]

    predicted = [row["predicted_distance_m"] for row in run["outcomes"]]
    realized = [row["realized_distance_m"] for row in run["outcomes"]]

    left.scatter(predicted, realized, s=26, color="#2f6fb4", alpha=0.75)

    limit = max(predicted + realized) * 1.08
    left.plot([0, limit], [0, limit], "--", color="#b4432f", linewidth=1.2)

    left.set_xlim(0, limit)
    left.set_ylim(0, limit)
    left.set_xlabel("predicted distance at decision time, C_static (m)")
    left.set_ylabel("realized distance (m)")
    left.set_title("Distance: exactly on the diagonal")

    left.text(
        0.04,
        0.94,
        "the router's prediction and the\nexecutor's realization agree\ncell for cell",
        transform=left.transAxes,
        fontsize=9,
        va="top",
    )

    for index, other in enumerate(runs):
        waiting = sorted(row["realized_waiting_s"] for row in other["outcomes"])

        right.plot(
            waiting,
            range(len(waiting)),
            label=f"{other['label']} ({sum(waiting):.1f} s over 100 orders)",
            color=COLOURS[index % len(COLOURS)],
            linewidth=1.8,
        )

    right.set_xlabel("realized waiting attributed to the order (s)")
    right.set_ylabel("orders (cumulative)")
    right.set_title("Waiting: an outcome, never a decision-time feature")
    right.legend(fontsize=9, loc="lower right")

    # These sums are deliberately below the fleet totals in the run
    # report. Waiting during a charger trip or an end-of-run parking
    # trip falls between orders and is attributed to no order, which is
    # the point of per-order attribution.
    right.text(
        0.04,
        0.94,
        "per-order attribution: waiting between\norders belongs to no order, so these\nsums sit below the fleet totals",
        transform=right.transAxes,
        fontsize=8,
        va="top",
    )

    fig.suptitle(
        "Phase 8: predicted at t vs realized after execution", fontsize=11
    )
    fig.tight_layout()

    path = out_dir / "predicted_vs_realized.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)

    return path


def figure_tradeoff(runs, out_dir):
    """What the window and the quantum cost and buy, side by side."""

    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(11, 3.8))

    metrics = (
        ("makespan (s)", "makespan_s", "lower is better"),
        ("total distance (m)", "distance_m", "lower is better"),
        ("total waiting (s)", "waiting_time_s", "the cost of batching"),
    )

    # Totals are not in the dataset -- they are a property of the run,
    # not of a decision -- so they are read from each run's own report.
    totals = [run["totals"] for run in runs]

    for axis, (title, key, note) in zip(axes, metrics):
        values = [total[key] for total in totals]

        bars = axis.bar(
            [run["label"] for run in runs],
            values,
            color=[COLOURS[i % len(COLOURS)] for i in range(len(runs))],
        )

        for bar, value in zip(bars, values):
            axis.text(
                bar.get_x() + bar.get_width() / 2,
                value,
                f"{value:.1f}",
                ha="center",
                va="bottom",
                fontsize=9,
            )

        axis.set_title(f"{title}\n{note}", fontsize=10)
        axis.set_ylim(0, max(values) * 1.18)

    fig.suptitle(
        "Same fixed infrastructure, same orders, same strategy "
        "-- only the decision shape changes",
        fontsize=11,
    )
    fig.tight_layout()

    path = out_dir / "baseline_tradeoff.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)

    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--run",
        action="append",
        default=[],
        metavar="LABEL=DIR",
        help="a recorded dataset directory; repeatable, order preserved",
    )
    parser.add_argument("--out", default="docs/figures")
    parser.add_argument(
        "--totals",
        action="append",
        default=[],
        metavar="LABEL=MAKESPAN,DISTANCE,WAITING",
        help="run totals, which are a property of the run and not of the "
        "dataset; taken from the run's own RESULT block",
    )

    args = parser.parse_args()

    if not args.run:
        parser.error("give at least one --run LABEL=DIR")

    runs = []

    for entry in args.run:
        if "=" not in entry:
            parser.error(f"--run expects LABEL=DIR, got {entry!r}")

        label, path = entry.split("=", 1)
        runs.append((label, path))

    loaded = load(runs)

    supplied = {}

    for entry in args.totals:
        label, numbers = entry.split("=", 1)
        makespan, distance, waiting = (float(x) for x in numbers.split(","))

        supplied[label] = {
            "makespan_s": makespan,
            "distance_m": distance,
            "waiting_time_s": waiting,
        }

    for run in loaded:
        run["totals"] = supplied.get(run["label"])

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    written = [
        figure_decision_shape(loaded, out_dir),
        figure_predicted_vs_realized(loaded, out_dir),
    ]

    if all(run["totals"] for run in loaded):
        written.append(figure_tradeoff(loaded, out_dir))
    else:
        print("skipping the trade-off figure: --totals not given for every run")

    for path in written:
        print(f"wrote {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
