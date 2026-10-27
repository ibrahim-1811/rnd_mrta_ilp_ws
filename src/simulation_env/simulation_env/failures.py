"""Why a run failed: classified, and recorded beside its outputs.

A run that does not finish leaves no metrics.json and is never compared.
What it does leave is failure.json, so an experiment can say *why* a run
is missing. The kinds are not interchangeable:

    traffic_deadlock       the engine proved a wait cycle or a livelock
    workload_stall         work outstanding, no events, recovery freed
                           nothing
    invalid_assignment     the strategy broke the allocation contract
    eligibility_violation  the strategy returned a battery-ineligible pair
    solver_failure         an optimising strategy's solver failed
    charging_saturation    no charger, holding cell or Stage Zone for a
                           robot that must leave the aisle
    execution_error        anything else: a defect to investigate, never
                           a scientific result

Only the first two are what EXPERIMENT_PROTOCOL.md calls a STALLED run.
An ineligible or invalid assignment is the strategy's defect and must
not be read as the warehouse jamming. See docs/DECISIONS.md, "Run
Failure Classification".
"""

from json import dumps, loads
from pathlib import Path

from simulation_env.charging import ChargingSaturationError
from simulation_env.dataset import exact
from simulation_env.fleet_engine import DeadlockError
from simulation_env.workload_executor import (
    IneligibleAssignmentError,
    InvalidAssignmentError,
    WorkloadStalledError,
)
from task_management.allocation_strategy import AllocationSolverError


FAILURE_FILE = "failure.json"
FAILURE_SCHEMA_VERSION = 1

TRAFFIC_DEADLOCK = "traffic_deadlock"
WORKLOAD_STALL = "workload_stall"
INVALID_ASSIGNMENT = "invalid_assignment"
ELIGIBILITY_VIOLATION = "eligibility_violation"
SOLVER_FAILURE = "solver_failure"
CHARGING_SATURATION = "charging_saturation"
EXECUTION_ERROR = "execution_error"

# Every class is a RuntimeError, so the order matters only if one ever
# subclasses another; the first match wins.
_KINDS = (
    (DeadlockError, TRAFFIC_DEADLOCK),
    (WorkloadStalledError, WORKLOAD_STALL),
    (InvalidAssignmentError, INVALID_ASSIGNMENT),
    (IneligibleAssignmentError, ELIGIBILITY_VIOLATION),
    (AllocationSolverError, SOLVER_FAILURE),
    (ChargingSaturationError, CHARGING_SATURATION),
)

# The kinds the protocol calls STALLED (EXPERIMENT_PROTOCOL.md, "Stalled
# Runs"): the run stopped, not the strategy misbehaved.
STALL_KINDS = frozenset({TRAFFIC_DEADLOCK, WORKLOAD_STALL})


def classify(error: BaseException) -> str:
    """The failure kind of an exception that ended a run."""

    for error_type, kind in _KINDS:
        if isinstance(error, error_type):
            return kind

    return EXECUTION_ERROR


def failure_record(error: BaseException, executor=None, manifest=None) -> dict:
    """What failure.json holds: the kind, the error, and the run's state.

    The order counts say how far the run got. They are counts, not
    outcomes: an order that did not finish has no outcome row, and none
    is invented for it.
    """

    kind = classify(error)

    record = {
        "failure_schema_version": FAILURE_SCHEMA_VERSION,
        "status": "failed",
        "kind": kind,
        "stalled": kind in STALL_KINDS,
        "exception": f"{type(error).__module__}.{type(error).__qualname__}",
        "message": str(error),
    }

    if manifest is not None:
        for key in ("run_id", "strategy", "strategy_sha256", "seed", "robot_count"):
            record[key] = manifest.get(key)

    if executor is not None:
        now = executor.engine.time

        record["sim_time"] = float(now)
        record["sim_time_exact"] = exact(now)
        record["orders"] = {
            "total": len(executor.queue),
            "completed": len(executor.result.completions),
            "pending": len(executor.queue.pending()),
            "in_flight": len(executor.active),
            "deferred": len(executor.deferred),
        }
        record["decisions"] = executor.result.decisions

    return record


def write_failure(run_dir, record: dict) -> Path:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    path = run_dir / FAILURE_FILE
    path.write_text(dumps(record, indent=2, default=str))

    return path


def read_failure(run_dir) -> dict | None:
    """The run's failure.json, or None if it has none."""

    path = Path(run_dir) / FAILURE_FILE

    if not path.is_file():
        return None

    return loads(path.read_text())
