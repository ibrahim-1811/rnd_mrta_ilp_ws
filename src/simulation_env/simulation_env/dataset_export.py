"""Phase 8: reading a run's dataset back out.

Parquet is the storage format because the clock and the SOC ledger are
exact rationals and a run has to stay reproducible. It is not readable
with ``less``, so this module is the inspection counterpart:

    csv       both tables as text, for grepping, diffing and plotting
    prolog    the decision table as ground facts, for an ILP learner

Nothing here computes anything. An export that derived a new quantity
would be a second implementation of a feature that already exists in
``dataset.py``, and the two would drift. Every value written is a value
read from the Parquet file.

What this module deliberately does not do
-----------------------------------------
It does not discretize. ``soc(d12, robot_003, 88.5)`` is emitted, never
``battery_high(d12, robot_003)``. Where the thresholds go is a learner
decision that will be tuned repeatedly, and baking one into the export
would mean re-exporting -- or worse, re-running -- every experiment each
time a threshold moves. See docs/DECISIONS.md, "Decision-Time Features".

It does not write mode or bias declarations. Which predicates a learner
may use, and with what arity and direction, is the hypothesis space:
that is the experiment, not the data, and it belongs to the ILP phase.

It does not emit facts *about* identifiers. ``robot_003`` and ``o0042``
appear as the terms an example is about, because an example has to name
something, but no fact asserts an ordering over them or exposes the
numeric part as a feature. Identifiers are traceability, not predicates.
"""

from csv import writer as csv_writer
from json import loads
from pathlib import Path

from simulation_env.dataset import (
    DECISION_COLUMNS,
    DECISIONS_CSV,
    DECISIONS_FILE,
    MANIFEST_FILE,
    OUTCOME_COLUMNS,
    OUTCOMES_CSV,
    OUTCOMES_FILE,
)


# The decision-time numbers worth handing a learner, as
# predicate -> column. Traceability columns, run context and the labels
# are excluded: the first are not features, the last are the target.
#
# Split by what the number is actually about, so each fact has the
# arity its subject implies. A robot's SOC is a property of the robot at
# t, not of the pair, and emitting it per pair would assert the same
# fact once for every order in the window.
PROLOG_ROBOT_FEATURES = {
    "soc": "robot_soc_percent",
}

PROLOG_PAIR_FEATURES = {
    "cost": "c_static_m",
    "fifo_position": "fifo_position",
    "shelf_count": "order_shelf_count",
    "route_cells": "route_cells",
    "travel_energy": "predicted_travel_energy_percent",
    "service_energy": "predicted_service_energy_percent",
    "charger_distance": "predicted_charger_distance_m",
    "charger_energy": "predicted_charger_energy_percent",
    "required_soc": "required_soc_percent",
    "energy_margin": "energy_margin_percent",
}

# Facts about the decision itself rather than about a candidate pair.
PROLOG_DECISION_FEATURES = {
    "free_robots": "free_robots",
    "window_orders": "window_orders",
    "pending_orders": "pending_orders",
    "robots_busy": "robots_busy",
    "robots_charging": "robots_charging",
}


def read_run(run_dir):
    """Both tables as lists of dicts, plus the manifest.

    Reads whichever format the run was written in. The live ROS node
    runs on the system interpreter, where pyarrow may be absent, so a
    run can legitimately be CSV; the manifest says which.
    """

    run_dir = Path(run_dir)

    manifest_path = run_dir / MANIFEST_FILE
    manifest = loads(manifest_path.read_text()) if manifest_path.is_file() else {}

    if (run_dir / DECISIONS_FILE).is_file():
        import pyarrow.parquet as pq

        decisions = pq.read_table(run_dir / DECISIONS_FILE).to_pylist()
        outcomes = pq.read_table(run_dir / OUTCOMES_FILE).to_pylist()

        return decisions, outcomes, manifest

    return (
        _read_csv(run_dir / DECISIONS_CSV, DECISION_COLUMNS),
        _read_csv(run_dir / OUTCOMES_CSV, OUTCOME_COLUMNS),
        manifest,
    )


def _read_csv(path, columns):
    """A CSV table back as typed rows.

    CSV has one type, so the declared column types are what restores
    ``22`` to an int and ``true`` to a bool. Without that, every
    downstream comparison would be against a string.
    """

    from csv import DictReader

    kinds = dict(columns)

    def cast(key, text):
        kind = kinds.get(key, "string")

        if kind == "string":
            return text

        if text == "":
            return None

        if kind == "bool_":
            return text.lower() in ("true", "1")

        return int(text) if kind == "int32" else float(text)

    with Path(path).open() as handle:
        return [
            {key: cast(key, value) for key, value in row.items()}
            for row in DictReader(handle)
        ]


def write_csv(run_dir, out_dir=None) -> list:
    """Both tables beside the Parquet files, as ``*.csv``.

    A run already written as CSV is left alone: rewriting it in place
    would read and truncate the same file.
    """

    run_dir = Path(run_dir)

    if out_dir is None and not (run_dir / DECISIONS_FILE).is_file():
        return [run_dir / DECISIONS_CSV, run_dir / OUTCOMES_CSV]

    decisions, outcomes, _ = read_run(run_dir)

    out_dir = Path(out_dir) if out_dir else run_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    written = []

    for rows, name in ((decisions, DECISIONS_CSV), (outcomes, OUTCOMES_CSV)):
        path = out_dir / name

        with path.open("w", newline="") as handle:
            out = csv_writer(handle)

            if not rows:
                # An empty table still has to say what it is empty of,
                # or a downstream reader cannot tell a run with no
                # decisions from a failed export.
                out.writerow(_columns(rows, name))
            else:
                out.writerow(rows[0].keys())
                out.writerows(row.values() for row in rows)

        written.append(path)

    return written


def _columns(rows, name) -> list:
    columns = DECISION_COLUMNS if name.startswith("decisions") else OUTCOME_COLUMNS

    return [key for key, _kind in columns]


def _atom(value) -> str:
    """A Prolog atom. Identifiers are lowercased, not renumbered."""

    text = str(value).strip().lower()

    return text if text else "unknown"


def _number(value) -> str:
    """A Prolog number, keeping an integer an integer.

    Counts, FIFO positions and cell counts are ``int32`` in the schema.
    Widening them to ``22.0`` would invite a learner to compare them as
    floats, which is wrong for a count and reads as though a measurement
    were involved.
    """

    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, int):
        return str(value)

    return repr(float(value))


def write_prolog(run_dir, out_dir=None) -> list:
    """The decision table as ground facts: ``bk.pl`` and ``exs.pl``.

    One ``pos``/``neg`` example per *eligible* candidate pair. Pairs the
    infrastructure refused -- unreachable route, battery-ineligible --
    are not negative examples: a learner asked to explain them would be
    learning to reproduce the feasibility mask, which is fixed
    infrastructure and not the research variable. They stay in the
    Parquet table, which is the record of what was offered.
    """

    decisions, _, _ = read_run(run_dir)

    out_dir = Path(out_dir) if out_dir else Path(run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    bk = out_dir / "bk.pl"
    exs = out_dir / "exs.pl"

    seen_decisions = set()
    seen_robots = set()

    with bk.open("w") as facts, exs.open("w") as examples:
        # f-strings throughout, not %-formatting: "%" is Prolog's
        # comment character, so a %-formatted comment line is a format
        # string with an accidental conversion in it.
        facts.write("% Background knowledge: one run, ground facts only.\n")
        facts.write(f"% Generated from {DECISIONS_FILE} -- do not edit.\n\n")

        examples.write("% Positive: the pair the strategy chose.\n")
        examples.write("% Negative: an eligible pair it passed over.\n\n")

        for row in decisions:
            decision = f"d{row['decision_id']}"
            robot = _atom(row["robot_id"])
            order = _atom(row["order_id"])

            if decision not in seen_decisions:
                seen_decisions.add(decision)

                facts.write(f"decision({decision}).\n")
                facts.write(
                    f"decision_time({decision}, {_number(row['sim_time'])}).\n"
                )

                for predicate, column in PROLOG_DECISION_FEATURES.items():
                    value = row.get(column)

                    if value is not None:
                        facts.write(
                            f"{predicate}({decision}, {_number(value)}).\n"
                        )

                facts.write("\n")

            facts.write(f"candidate({decision}, {robot}, {order}).\n")

            if (decision, robot) not in seen_robots:
                seen_robots.add((decision, robot))

                for predicate, column in PROLOG_ROBOT_FEATURES.items():
                    value = row.get(column)

                    if value is not None:
                        facts.write(
                            f"{predicate}({decision}, {robot}, "
                            f"{_number(value)}).\n"
                        )

            for predicate, column in PROLOG_PAIR_FEATURES.items():
                value = row.get(column)

                if value is None:
                    continue

                facts.write(
                    f"{predicate}({decision}, {robot}, {order}, "
                    f"{_number(value)}).\n"
                )

            if not row["eligible"]:
                continue

            label = "pos" if row["selected"] else "neg"

            examples.write(f"{label}(assign({decision}, {robot}, {order})).\n")

    return [bk, exs]
