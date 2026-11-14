# Stage 1: local read-only backend

This is the startup guide for the Stage 1 continuation in this chat. The checkout
already contained an untracked Stage 1 backend plus Stage 2 job-control work when
this request began. This continuation does not edit those files, including
`backend/README.md` and `control_center/app.py`. Concurrent external edits to
existing Stage 2 files have been retained. The `control_center.app` launcher
starts the broader application and does not enforce Stage 1 scope.

**Use `control_center.stage1` for this stage.** It registers only history and
catalogue readers. It never imports the job router/manager, starts a runner,
creates job state, or exposes launch, preview, cancellation, experiment or
WebSocket endpoints. The visual dashboard is a later stage.

## Start

The existing application virtual environment already has the backend libraries.
No installation or root environment/lockfile update was needed for this task.
From any terminal:

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws
export PYTHONPATH="$PWD/src/dashboard_GUI/backend:$PWD/src/mrta_core:$PWD/src/simulation_env:$PWD/src/task_management"
export PYTHONDONTWRITEBYTECODE=1
src/dashboard_GUI/backend/.venv/bin/python -m control_center.stage1
```

The supported launcher binds `127.0.0.1:8000`, disables proxy headers and
WebSockets, and accepts no host override. If this port is occupied, leave the
existing process alone and use `python -m control_center.stage1 --port 8001`
with the same interpreter and environment. Stop your own server with Ctrl+C.

- Health: <http://127.0.0.1:8000/api/v1/health>
- OpenAPI specification: <http://127.0.0.1:8000/openapi.json>
- Interactive API documentation: <http://127.0.0.1:8000/docs>

Health reports `stage: 1` and `read_only: true`. OpenAPI contains only GET
application routes. FastAPI's optional Swagger/ReDoc pages load their standard
UI assets from CDNs; the API and OpenAPI JSON use only local files and services.

Dependency declarations remain in `backend/pyproject.toml`. The application
environment inherits the existing scientific environment's source-reader
dependencies (PyArrow, openpyxl, PyYAML and registry imports). It requires the
repository checkout and is not a standalone scientific distribution. If the
application environment is missing on a fresh checkout, follow the isolated
virtual-environment installation steps in `backend/README.md`, then use the
**Stage 1 launcher above**. Never install backend packages into `.pixi`.

## API

All routes below are GET, prefixed `/api/v1`:

| Route | Behavior |
|---|---|
| `/health` | Service readiness, version, stage and read-only flag |
| `/capabilities` | Reader dependencies, schema versions, unavailable features, source-declared CLI flags and hashes |
| `/strategies` | Current production registry IDs/classes with separate review-status caveat |
| `/scenarios` | Allowlisted W100 config, map/order/config hashes and loader capacity |
| `/scenarios/{scenario_id}/map` | Production grid dimensions, origin/resolution, obstacles, zones, non-transit terminals and directed edges |
| `/runs` | Directory-scoped, paginated history with operational integrity metadata |
| `/runs/{run_id}` | Original manifest plus separate operational assessment |
| `/runs/{run_id}/metrics` | Original metrics, preserving legacy/current versions and missing values |
| `/runs/{run_id}/decisions` | Typed decision-time rows, physical order, supported dataset version only |
| `/runs/{run_id}/outcomes` | Typed outcome rows, physical order, supported dataset version only |
| `/runs/{run_id}/artifacts` | Allowed original artifacts, availability, sizes and hashes |
| `/runs/{run_id}/artifacts/{artifact_id}` | Verified original bytes as an attachment |

Responses have `api_schema_version=1`, `data`, `availability`, `warnings`,
`source` and `next_cursor`. Metrics are not recomputed or converted. A legacy
fleet-settled makespan is never relabelled as order makespan. Missing values are
not zero. Decisions and outcomes remain separate; no counterfactual outcome or
ILP explanation is inferred.

`/runs` accepts `strategy`, `integrity=complete|incomplete`, `limit` and `cursor`.
Both table routes accept `decision_id`, `order_id`, `limit` and `cursor`;
decisions additionally accepts `robot_id` and `selected`. Limits default to 100,
maximum 1000. Cursors bind to filters and source signatures; changed files cause
409, requiring a fresh first page. Only `scope=history` or omitted scope is
accepted. App-owned jobs/runs are outside this Stage 1 service.

Unknown IDs/artifacts return 404, invalid query values 422, file/cursor drift
409, and unavailable source-reader dependencies 503. Malformed, legacy or
incomplete history remains listed with diagnostics. An unsupported dataset
manifest version prevents typed decoding; original downloads remain available.
Capabilities inspect CLI declarations with Python's syntax parser, without
importing or executing either runner. This is source inventory, not launch
support or proof of successful CLI execution.

## Storage and trust boundary

The approved history root is repository `runs/`. The disposable operational
index lives separately at `src/dashboard_GUI/var/stage1/index.sqlite`, covered by
the existing dashboard `.gitignore`. This separate directory avoids disturbing
any pre-existing job-manager state or index. No scientific phase is advanced.

The shared adapters and SQLite index are reused from the existing backend.
SQLite stores metadata, manifests, validation results, source signatures/hashes
and counts; scientific metrics are always read from their original artifacts.
GET requests may refresh this application-owned cache and spool a download.
They never write to history, repair an artifact, generate data, or run science.
When stopped, deleting only this application's `var/stage1/index.sqlite` makes
the next history read rebuild it.

Source access uses Linux descriptor-relative no-follow opens through all path
components, requires regular files, ignores manifest path fields as authority,
and checks the open descriptor and directory entry for changes. Downloads are
spooled and verified before serving. The finite download allowlist is:
`run.json`, `metrics.json`, `decisions.parquet`, `outcomes.parquet`,
`decisions.csv`, `outcomes.csv`, `run.log`, `report.txt`, `bk.pl`, `exs.pl`.
There is no arbitrary path, upload or filesystem-browsing endpoint.

Requests require a loopback peer, exactly one loopback Host (`127.0.0.1`,
`localhost`, or `[::1]`, with an optional valid port), and an exactly matching
Origin when present. Duplicate Host/Origin headers are rejected. CORS is not
enabled. Non-read methods are rejected before handlers run. These controls
protect a local read-only service; other local users/processes are trusted.
Responses disable caching. Do not expose this application through a proxy.

## Limits and checks

JSON reads are limited to 4 MiB, CSV fields to the parser's 128 KiB default,
Parquet batches to 512 rows, and API pages to 1000 rows. Initial indexing hashes
and validates full tables, so time scales with history size. Deep filtered
pages rescan earlier rows. Downloads can consume temporary disk. Memory is
bounded for streaming rows, but no global history-size quota is claimed.

Blocking reads use one process-lifetime worker to avoid a native Parquet crash
reproduced across successive application/request-worker lifetimes. The native
library's internal cause was not established. Reader requests serialize; health stays on the event loop and can
respond during indexing. No scientific dependency versions were changed.

Completeness is structural evidence, not verified process exit or scientific
re-execution. Existing failure/cancellation markers are surfaced; absent exit
evidence stays unknown. Individual files are checked for drift; a whole run's
multiple artifacts are not an atomic filesystem snapshot. Source provenance
presence does not prove that historical dirty source bytes were archived.

Run the focused Stage 1 checks with the same environment as startup:

```bash
src/dashboard_GUI/backend/.venv/bin/python -m pytest \
  src/dashboard_GUI/backend/tests/test_backend.py \
  src/dashboard_GUI/backend/tests/test_stage1.py -q -p no:cacheprovider
```

The existing `test_backend.py` targets the preserved broader launcher and
exercises the shared readers; `test_stage1.py` exercises the Stage 1 entrypoint
and its stricter boundary. Fixtures are tiny and no scientific workloads run.
