"""Phase 8: the decision and outcome dataset.

Two tables and a manifest, one directory per run::

    decisions.parquet   one row per scored (robot, order) candidate pair
    outcomes.parquet    one row per order
    run.json            provenance, without which the tables are not evidence

The point of the split is the causal structure the research rests on::

    State_t -> Decision_t -> Outcome

A decision row may contain only what was knowable at the instant of the
decision. Realized waiting, actual completion time, actual energy,
charging events and future congestion live in the outcome table and
nowhere else. The boundary is enforced structurally -- a decision row is
built before any mission is planned and never touched again -- and the
truncation test in ``test/test_dataset.py`` is what proves it.

Deliberately raw. Every feature is a number in its natural unit, never a
discretized predicate: ``battery_high`` style thresholds are
learner-side choices that will be tuned repeatedly, and freezing them
into the dataset would mean re-running every experiment whenever one
moves. See docs/DECISIONS.md, "Decision-Time Features".

The clock and the SOC ledger are exact rationals and Parquet has no
rational type, so where exactness matters a value is stored twice: a
float64 column for analysis and an exact ``"numerator/denominator"``
string beside it, which is what makes a run reproducible rather than
merely plottable.
"""

from dataclasses import dataclass, field
from fractions import Fraction
from hashlib import sha256
from json import dumps
from pathlib import Path
from subprocess import run as run_process


# A dataset whose schema changed is not comparable with an older one.
# Mixing the two silently would invalidate any rule learned from them,
# so the version is recorded in every manifest.
SCHEMA_VERSION = 1

DECISIONS_FILE = "decisions.parquet"
OUTCOMES_FILE = "outcomes.parquet"
MANIFEST_FILE = "run.json"

# Fallback names, used when pyarrow is unavailable. The live ROS node
# runs on the system interpreter, not the pixi environment, so Parquet
# cannot be assumed there -- and a finished run must not be thrown away
# because an optional dependency is missing.
DECISIONS_CSV = "decisions.csv"
OUTCOMES_CSV = "outcomes.csv"


def exact(value) -> str:
    """An exact rational as ``"numerator/denominator"``.

    Round-trips through ``Fraction(text)`` without loss, which float64
    does not.
    """

    if value is None:
        return ""

    return str(Fraction(value))


def _float(value) -> float | None:
    return None if value is None else float(value)


def sha256_of(path) -> str:
    """Content hash of an input file, so a run names its real inputs."""

    path = Path(path)

    if not path.is_file():
        return ""

    digest = sha256()

    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)

    return digest.hexdigest()


def git_state(repo=None) -> dict:
    """HEAD and whether the tree was dirty when the run was made.

    A result measured on an uncommitted tree is still useful, but it must
    say so: ``dirty`` is the difference between a reproducible run and an
    anecdote.
    """

    def git(*args):
        try:
            done = run_process(
                ["git", *args],
                cwd=str(repo) if repo else None,
                capture_output=True,
                text=True,
                check=False,
            )

        except OSError:
            return ""

        return done.stdout.strip() if done.returncode == 0 else ""

    return {
        "commit": git("rev-parse", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
    }


# ----------------------------------------------------------------------
# Column schemas. Declared explicitly, so a column's type is a decision
# rather than whatever the first row happened to contain.
# ----------------------------------------------------------------------


# Column order and type, declared in plain Python rather than in
# pyarrow terms. The CSV fallback needs the column order and must not
# require pyarrow to get it, and one declaration cannot drift from
# itself the way two would.
def _schema(columns):
    import pyarrow as pa

    types = {
        "string": pa.string(),
        "int32": pa.int32(),
        "float64": pa.float64(),
        "bool_": pa.bool_(),
    }

    return pa.schema([(name, types[kind]) for name, kind in columns])


DECISION_COLUMNS = (
    # -- identity and run context -----------------------------
    ("run_id", "string"),
    ("decision_id", "int32"),
    ("sim_time", "float64"),
    ("sim_time_exact", "string"),
    ("strategy", "string"),
    ("window", "string"),
    ("window_width", "int32"),
    ("dispatch_quantum_s", "float64"),
    # -- state at t: the shape of the decision ----------------
    ("free_robots", "int32"),
    ("window_orders", "int32"),
    ("choice_bearing", "bool_"),
    ("pending_orders", "int32"),
    ("robots_busy", "int32"),
    ("robots_charging", "int32"),
    # -- traceability only; never a learned feature -----------
    ("robot_id", "string"),
    ("order_id", "string"),
    # -- state at t: this robot and this order ----------------
    ("robot_x", "int32"),
    ("robot_y", "int32"),
    ("robot_soc_percent", "float64"),
    ("robot_soc_exact", "string"),
    ("fifo_position", "int32"),
    ("order_shelf_count", "int32"),
    ("order_dropoff_id", "string"),
    # -- prediction at t: the planned route -------------------
    # Distance, not time: C_static in metres is what the
    # allocator actually ranks on. Travel time is derivable
    # downstream from the speed recorded in the manifest, and
    # recording it here would mean reading a speed the executor
    # does not in fact use.
    ("c_static_m", "float64"),
    ("route_reachable", "bool_"),
    ("route_cells", "int32"),
    # -- prediction at t: energy (EnergyRequirement) ----------
    ("predicted_travel_energy_percent", "float64"),
    ("predicted_service_energy_percent", "float64"),
    ("predicted_charger_distance_m", "float64"),
    ("predicted_charger_energy_percent", "float64"),
    ("reserve_percent", "float64"),
    ("required_soc_percent", "float64"),
    ("required_soc_exact", "string"),
    ("energy_margin_percent", "float64"),
    ("energy_margin_exact", "string"),
    ("nearest_charger_id", "string"),
    # -- labels -----------------------------------------------
    ("battery_eligible", "bool_"),
    ("eligible", "bool_"),
    ("selected", "bool_"),
)


def _decision_schema():
    """The pyarrow schema. Column order comes from DECISION_COLUMNS."""

    return _schema(DECISION_COLUMNS)


OUTCOME_COLUMNS = (
    ("run_id", "string"),
    ("order_id", "string"),
    # The join back to the decision that assigned this order.
    ("decision_id", "int32"),
    ("robot_id", "string"),
    ("launched_at", "float64"),
    ("launched_at_exact", "string"),
    ("completed_at", "float64"),
    ("completed_at_exact", "string"),
    # Realized, by differencing the robot's own counters across
    # the order. Never available at decision time.
    ("realized_distance_m", "float64"),
    ("realized_travel_time_s", "float64"),
    ("realized_waiting_s", "float64"),
    ("realized_service_s", "float64"),
    ("realized_duration_s", "float64"),
    # When the robot finished reversing out of the drop-off and
    # became free again. Later than completed_at by the retreat.
    ("retreat_finished_at", "float64"),
    ("energy_consumed_percent", "float64"),
    ("energy_consumed_exact", "string"),
    ("soc_at_launch_percent", "float64"),
    ("soc_at_completion_percent", "float64"),
    ("charge_sessions_before", "int32"),
    ("yield_events_during", "int32"),
    # What the decision predicted, carried over so predicted and
    # realized can be compared without a second join. A
    # decision-time value, not an outcome.
    #
    # No predicted wait column: the planner has no pre-planned
    # traffic wait any more (Dispatch.planned_wait_s is always
    # 0.0), and a structurally-zero column in a research dataset
    # only invites someone to read meaning into it.
    ("predicted_distance_m", "float64"),
    ("success", "bool_"),
)


def _outcome_schema():
    """The pyarrow schema. Column order comes from OUTCOME_COLUMNS."""

    return _schema(OUTCOME_COLUMNS)


@dataclass
class RunRecorder:
    """Accumulates one run's rows, then writes the dataset.

    Rows are finalized as they are handed over and never revisited, so
    buffering them until the end of the run cannot introduce leakage:
    what is written is what was true when the row was built.
    """

    run_id: str = ""
    decisions: list = field(default_factory=list)
    outcomes: list = field(default_factory=list)

    # order_id -> the counter snapshot taken when its mission launched.
    _launched: dict = field(default_factory=dict)

    def decision(self, row: dict) -> None:
        self.decisions.append(row)

    def launched(self, order_id: str, snapshot: dict) -> None:
        self._launched[order_id] = snapshot

    def snapshot_of(self, order_id: str) -> dict | None:
        return self._launched.get(order_id)

    def outcome(self, row: dict) -> None:
        self.outcomes.append(row)

    # -- writing --------------------------------------------

    def write(self, run_dir, manifest: dict) -> Path:
        """Write both tables and the manifest. Returns the directory.

        Parquet when pyarrow is available, CSV when it is not. Both are
        lossless: the exact columns are already strings, so the rational
        clock and the SOC ledger survive either way. The manifest records
        which format was used, so a reader never has to guess.
        """

        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._write_parquet(run_dir)
            written = "parquet"

        except ImportError:
            self._write_csv(run_dir)
            written = "csv"

        payload = dict(manifest)
        payload["schema_version"] = SCHEMA_VERSION
        payload["format"] = written
        payload["decision_rows"] = len(self.decisions)
        payload["outcome_rows"] = len(self.outcomes)

        (run_dir / MANIFEST_FILE).write_text(dumps(payload, indent=2, default=str))

        return run_dir

    def _tables(self):
        return (
            (self.decisions, DECISION_COLUMNS, DECISIONS_FILE, DECISIONS_CSV),
            (self.outcomes, OUTCOME_COLUMNS, OUTCOMES_FILE, OUTCOMES_CSV),
        )

    def _write_parquet(self, run_dir: Path) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        for rows, columns, name, _ in self._tables():
            keys = [key for key, _kind in columns]

            table = pa.Table.from_pylist(
                [{key: row.get(key) for key in keys} for row in rows],
                schema=_schema(columns),
            )

            pq.write_table(table, run_dir / name)

    def _write_csv(self, run_dir: Path) -> None:
        from csv import writer as csv_writer

        for rows, columns, _, name in self._tables():
            keys = [key for key, _kind in columns]

            with (run_dir / name).open("w", newline="") as handle:
                out = csv_writer(handle)

                # The header is written even for an empty table, or a
                # reader cannot tell a run with no rows from a failed
                # write.
                out.writerow(keys)
                out.writerows([row.get(key) for key in keys] for row in rows)


def build_manifest(config, *, strategy: str, window, window_width, repo=None) -> dict:
    """Everything needed to say what a dataset is a measurement of."""

    battery = config.battery

    manifest = {
        "git": git_state(repo),
        "config_path": str(config.config_path),
        "config_sha256": sha256_of(config.config_path),
        "map_path": str(config.map_path),
        "map_sha256": sha256_of(config.map_path),
        "orders_path": str(config.orders_path),
        "orders_sha256": sha256_of(config.orders_path),
        "robot_count": config.robot_count,
        "speed_mps": config.speed_mps,
        "resolution_m": config.resolution_m,
        "shelf_pick_s": config.shelf_pick_s,
        "dropoff_s": config.dropoff_s,
        "strategy": strategy,
        "seed": config.seed,
        "allocation_window": str(window),
        "window_width": window_width,
        "dispatch_quantum_s": config.dispatch_quantum_s,
        "battery": {
            "enabled": getattr(battery, "enabled", True),
            "initial_soc_percent": _float(battery.initial_soc_percent),
            "travel_percent_per_20m": _float(battery.travel_percent_per_20m),
            "service_percent_per_60s": _float(battery.service_percent_per_60s),
            "waiting_idle_percent_per_300s": _float(
                battery.waiting_idle_percent_per_300s
            ),
            "charging_percent_per_second": _float(
                battery.charging_percent_per_second
            ),
            "reserve_percent": _float(battery.reserve_percent),
            "charge_due_percent": _float(battery.charge_due_percent),
        },
    }

    # The run_id is a hash of the manifest itself, so two runs collide
    # exactly when every input that could change a result is identical.
    manifest["run_id"] = sha256(
        dumps(manifest, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]

    return manifest
