# Stage 1 implementation report

2026-10-27. Stage 1 only, ready for review. No commit or push.

## Scope and result

Implemented the read-only FastAPI backend under `src/dashboard_GUI/backend/`.
The existing production registry, configuration/XLSX loaders, home-bank capacity,
and directed lane/terminal functions supply catalogue and geometry data. No
allocator instance, executor, runner, ROS process or simulation is started by
any endpoint. Scientific modules and historical artifacts were not edited.

The SQLite operational index is `src/dashboard_GUI/var/index.sqlite` (ignored).
History is discovered recursively within repository `runs/`. File access uses
no-follow descriptor-relative opens, a finite allowlist and stable-read checks.
Downloads spool verified original bytes before serving. JSON is bounded, and
CSV/Parquet tables stream in physical order with bounded pages and filter-bound,
source-bound cursors. Original metric names, versions and values remain intact.

Startup, all exact routes, query parameters, reader limits and operational
instructions are in `../../backend/README.md`. OpenAPI is served at
`/openapi.json`; interactive documentation is at `/docs` and `/redoc`.

## Exact application routes

All are GET, under `/api/v1`:

```text
/health
/capabilities
/strategies
/scenarios
/scenarios/{scenario_id}/map
/runs
/runs/{run_id}
/runs/{run_id}/metrics
/runs/{run_id}/decisions
/runs/{run_id}/outcomes
/runs/{run_id}/artifacts
/runs/{run_id}/artifacts/{artifact_id}
```

There are no mutation, launch, stop, experiment-control, or WebSocket routes.

## New files

```text
src/dashboard_GUI/.gitignore
src/dashboard_GUI/backend/pyproject.toml
src/dashboard_GUI/backend/README.md
src/dashboard_GUI/backend/control_center/__init__.py
src/dashboard_GUI/backend/control_center/app.py
src/dashboard_GUI/backend/control_center/api/routes.py
src/dashboard_GUI/backend/control_center/adapters/catalogue.py
src/dashboard_GUI/backend/control_center/adapters/tables.py
src/dashboard_GUI/backend/control_center/schemas/responses.py
src/dashboard_GUI/backend/control_center/services/pagination.py
src/dashboard_GUI/backend/control_center/storage/files.py
src/dashboard_GUI/backend/control_center/storage/index.py
src/dashboard_GUI/backend/tests/test_backend.py
src/dashboard_GUI/docs/control_center/STAGE_1_REPORT.md
```

Generated, ignored: backend `.venv/` and application `var/index.sqlite`.
No existing tracked file was edited by this implementation. The Stage 0
SCIENTIFIC_PARITY changes and deleted old plan path predated this task and
remain untouched. CURRENT_PHASE.md is unchanged: no scientific phase advanced.

## Dependencies

Application manifest: FastAPI 0.119.1, Uvicorn 0.38.0. Test extras: HTTPX 0.28.1,
pytest >=8,<9. Build dependency: setuptools >=68. Backend Python requires >=3.11;
verification used the existing Python 3.12 through an application-owned virtual
environment with system-site-packages. Existing scientific packages including
pyarrow, openpyxl and PyYAML are inherited; no root dependency or lockfile edit.

Observed newly installed transitive versions: Starlette 0.48.0, Pydantic 2.14.1,
pydantic-core 2.50.1, AnyIO 4.15.1, HTTPcore 1.0.9, h11 0.16.0, Click 8.5.0,
certifi 2026.10.23, idna 3.20, annotated-types 0.8.0,
typing-extensions 4.16.0 and typing-inspection 0.4.4. These are observations,
not a fully locked standalone scientific environment.

## Verification and measured repository responses

Focused suite:

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src/dashboard_GUI/backend:src/mrta_core:src/simulation_env:src/task_management \
src/dashboard_GUI/backend/.venv/bin/python -m pytest \
  src/dashboard_GUI/backend/tests -q -p no:cacheprovider
```

**24 passed in 1.60 s.** One upstream Starlette/AnyIO deprecation warning about
`anyio.abc.BlockingPortal`; no test failures. Initial red tests exercised absent
implementation; subsequent regression tests failed before fixes for schema
validation, malformed values, numeric overflow, oversized CSV fields, missing
legacy metric version and inconsistent outcome counts.

Tests cover startup/OpenAPI, registry discovery, production geometry equality,
legacy/current metrics, missing/malformed manifests and artifacts, failure
log evidence, table schema and type validation, source ID separation,
CSV/Parquet pagination and filters, stale/invalid cursors, traversal/symlinks,
repeat indexing with unchanged source hashes, concurrent file mutation,
unknown IDs, Host/Origin rejection, and refusal of POST /runs.

A separate reviewer identified four important defects in the initial version:
unknown/truncated schemas, invalid metric types, JSON exponent overflow, and
uncaught CSV errors. Each was reproduced with a regression test, fixed, and
verified by the full focused suite. No review finding remains deferred.

Actual socket startup used `python -m control_center.app`. Uvicorn logged
`http://127.0.0.1:8000`; GET health returned HTTP 200, then the owned process
was terminated and logged clean application shutdown. The first smoke harness
attempt mistakenly resolved the virtualenv executable symlink to the base
interpreter and failed its FastAPI import; preserving the executable path
corrected the harness. The documented startup command succeeded.

Real health response:

```json
{
  "api_schema_version": 1,
  "data": {"status": "ok", "version": "0.1.0", "read_only": true},
  "availability": "available",
  "warnings": [],
  "source": {},
  "next_cursor": null
}
```

Real repository measurements:

- Registry: auction, earliest_finish, hungarian, milp, multi_criteria,
  nearest_astar, nearest_manhattan, random. Discovered, not hardcoded.
- W100: 40 × 54 cells, origin `[0.0, 0.0]`, resolution 1 m, 100 homes,
  4,244 directed edges including terminal entry/retreat. Coordinates come
  directly from WorldGrid; no display half-cell offset is invented.
- History: 20 directories, 16 legacy metrics, one current metrics, three
  missing metrics. Two pass full structural completion checks; 18 remain
  incomplete/unverified. Older manifests omit expected order count, so the
  index does not infer it. Their tables still match the production columns
  and are readable. A missing count is not a failed simulation diagnosis.
- First full hash/table-validation scan: 25.55 s. Subsequent unchanged history
  request: 0.085 s. These are operational timings, not scientific metrics.
- Real decision and outcome pages were read from both `gazebo_10_50` and
  `fleet_sweep/nearest_astar/r30_s1` without rewriting their artifacts.

Actual history item (subset of GET /runs data):

```json
{
  "id": "11217176fe7167cca4e47e9d55e2ae46",
  "directory": "gazebo_10_50",
  "scientific_status": "completed",
  "integrity": "complete",
  "process_status": "unknown",
  "provenance": "present",
  "metrics_schema": "legacy",
  "dataset_schema_version": 1
}
```

Completion here is structural artifact evidence, **not verified process exit
success**. The actual response includes warnings about legacy metric semantics
and absent process exit evidence. No metric value has been fabricated.

## Decisions and limitations

- Existing checkout retained, as requested; no worktree/branch switch, staging,
  commit or push. Tests remained lightweight, overriding generic skill advice
  to run a whole scientific test suite or create an isolated worktree.
- Completion assessment is deliberately conservative when old manifests lack
  expected order counts. Cost: some historically finished runs remain labelled
  incomplete/unverified; their raw artifacts and typed rows stay available.
- Application index validation is structural, not scientific re-execution.
  Source presence/hash provenance is not proof that dirty source bytes were
  archived or that historical runs form a valid paired experiment.
- First indexing scans all table rows; memory is bounded but time scales with
  history size. Filtered deep pages rescan preceding rows. Concurrent multi-file
  snapshots are not atomic; per-file mutation is detected and later requests
  reconcile changes. Malformed artifacts are retained with diagnostics.
- Failure evidence uses explicit manifest status or first/last 64 KiB log
  markers; no exit-code ledger exists in historical runs. Unknown stays unknown.
- Reader limits: 4 MiB JSON, Python CSV field bound, 512-row Parquet batches,
  API pages up to 1000. Downloads may use disk for spooling. Linux no-follow
  filesystem semantics are required. Local accounts/processes are trusted;
  there is no remote authentication or public deployment support.
- Swagger/ReDoc use FastAPI's default remote UI assets; OpenAPI JSON and the
  backend itself need no external service at runtime.
- Unavailable: job launch/cancellation, experiment management/comparison,
  telemetry/replay, React UI, ROS/Gazebo integration, ILP and explanations.

## Preservation and final Git status

SHA-256 preservation check: **411 pre-existing files unchanged**, including
**168 historical run files**. No baseline path disappeared during this task.
Root scientific source/config/lockfiles remain byte-identical. Initial HEAD
and branch: `badbc59671acd06fdcaf513060175ab74aeccc3d`, `traffic/rmf-integration`.
Staged diff remains empty.

```text
 D "src/dashboard_GUI/MRTA_CONTROL_CENTER_CODEX_PLAN (1).md"
 M src/dashboard_GUI/docs/control_center/SCIENTIFIC_PARITY.md
?? .codex/
?? src/dashboard_GUI/.gitignore
?? src/dashboard_GUI/backend/
?? src/dashboard_GUI/docs/control_center/STAGE_1_REPORT.md
```

The first three entries are pre-existing. Stage 1 stops here for user review.
