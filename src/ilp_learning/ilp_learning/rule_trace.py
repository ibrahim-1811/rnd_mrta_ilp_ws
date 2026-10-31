"""Per-pick telemetry of a rule-driven run: the optional sidecar rule_trace.parquet.

Written beside a run's tables for ILP runs only, so decisions.parquet and run.json
stay exactly what every other run writes (run_id hashes the manifest). Every
assignment says whether a rule or fallback made it (docs/DECISIONS.md, "M3 Execution
Semantics Amendment (2026-10-31)", item 3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd


TRACE_FILE = "rule_trace.parquet"
TRACE_COLUMNS = {
    "run_id": "string",
    "decision_id": "Int32",
    "round": "Int32",
    "robot_id": "string",
    "order_id": "string",
    "matched_by_rule": "boolean",
    "fallback": "boolean",
    "clause_rank": "Int32",  # null for a fallback pick
    "tie_level": "string",
    "matching": "string",
    "cardinality": "Int32",          # pairs this decision assigned
    "maximum_cardinality": "Int32",  # maximum matching over its eligible pairs
}


def write_rule_trace(run_dir: Path | str, run_id: str, records: Sequence[Mapping]) -> Path:
    """Write one row per pick; a run with no pick still writes the empty table."""
    frame = pd.DataFrame(
        [{"run_id": run_id, **record} for record in records], columns=list(TRACE_COLUMNS)
    ).astype(TRACE_COLUMNS)
    path = Path(run_dir) / TRACE_FILE
    frame.to_parquet(path, index=False)
    return path
