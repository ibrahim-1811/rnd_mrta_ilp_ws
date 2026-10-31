"""The rule artifact: a learned program and everything needed to say what it is.

One JSON file, read by ILPAllocationStrategy and identified in a run's strategy
identity by file hash (docs/DECISIONS.md, "Strategy Identity"). It carries raw and
compiled clauses, ranks, per-clause training metrics, thresholds and bias hashes,
source runs with their workload hashes, git state, Popper version, solver settings,
and a hash of its own content. Loading re-checks that hash and re-parses every
clause, so an edited or out-of-vocabulary file is refused, not executed.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from ilp_learning.rule_compiler import RuleProgram, parse_clause


SCHEMA_VERSION = "mrta-rule-artifact/1"
_REQUIRED = (
    "schema_version", "teacher", "arm", "raw_clauses", "clauses", "pruned_clauses",
    "k_max", "thresholds_sha256", "bias_sha256", "source_runs", "git", "popper",
    "solver_settings", "sampling",
)


class RuleArtifactError(ValueError):
    """A rule artifact that cannot be trusted to be what it says it is."""


def content_sha256(artifact: Mapping[str, Any]) -> str:
    """Hash of everything but the hash itself, in canonical key order."""
    body = {key: value for key, value in artifact.items() if key != "content_sha256"}
    return sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def write_rule_artifact(path: Path | str, artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Write the artifact with its content hash; refuses an incomplete one."""
    missing = [key for key in _REQUIRED if key not in artifact]
    if missing:
        raise RuleArtifactError(f"rule artifact lacks {missing}")
    sealed = {**artifact, "content_sha256": content_sha256(artifact)}
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(sealed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return sealed


def load_rule_artifact(path: Path | str) -> tuple[RuleProgram, dict[str, Any]]:
    """The executable program of an artifact, and the artifact, after checking both."""
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [key for key in (*_REQUIRED, "content_sha256") if key not in artifact]
    if missing:
        raise RuleArtifactError(f"rule artifact {path} lacks {missing}")
    if artifact["schema_version"] != SCHEMA_VERSION:
        raise RuleArtifactError(f"rule artifact {path}: schema {artifact['schema_version']!r}")
    if content_sha256(artifact) != artifact["content_sha256"]:
        raise RuleArtifactError(f"rule artifact {path}: content hash does not match")

    ranked = sorted(artifact["clauses"], key=lambda entry: entry["rank"])
    if [entry["rank"] for entry in ranked] != list(range(len(ranked))):
        raise RuleArtifactError(f"rule artifact {path}: clause ranks are not 0..m-1")

    clauses = []
    for entry in ranked:
        clause = parse_clause(entry["clause"])
        if clause.text != entry["clause"]:
            raise RuleArtifactError(f"rule artifact {path}: clause is not canonical")
        clauses.append(clause)
    return RuleProgram(tuple(clauses)), artifact
