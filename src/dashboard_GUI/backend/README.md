# MRTA Control Center backend — current delivery

Local FastAPI service with catalogue/history readers, guarded single-run and experiment jobs, observation, inputs and report support. Current startup/operations: [dashboard guide](../README.md) and [cross-workspace handover](../../../docs/PROJECT_HANDOVER.md).

The detailed reader/index sections below originated in Stage 1. They describe the read-only subsystem; later job/input routes also exist. Consult live OpenAPI and the current dashboard guide for the complete API.

## Start locally

From `/home/ibrahim/rnd_mrta_ilp_ws`:

```bash
.pixi/envs/default/bin/python -m venv --system-site-packages src/dashboard_GUI/backend/.venv
src/dashboard_GUI/backend/.venv/bin/python -m pip install -e 'src/dashboard_GUI/backend[test]'
export PYTHONPATH="$PWD/src/dashboard_GUI/backend:$PWD/src/mrta_core:$PWD/src/simulation_env:$PWD/src/task_management"
export PYTHONDONTWRITEBYTECODE=1
src/dashboard_GUI/backend/.venv/bin/python -m control_center.app
```

The interpreter inherits existing scientific libraries read-only. Installation
is confined to the ignored backend virtual environment; do not install into
`.pixi` or regenerate root lockfiles. The existing environment supplies pyarrow,
openpyxl, PyYAML, and dependencies needed to import the registry and loaders.
No ROS setup or simulator launch is needed. The repository source checkout is
required; this is not a standalone wheel containing the scientific packages.

The supported entry point binds **127.0.0.1:8000**, with proxy headers disabled.
Stop with Ctrl+C. Do not expose it through a public proxy or bind another host.
There is no authentication for other local processes/users.

- Health: <http://127.0.0.1:8000/api/v1/health>
- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>
- Swagger UI: <http://127.0.0.1:8000/docs> (FastAPI's standard UI loads CDN assets;
  OpenAPI JSON is fully local).
- ReDoc: <http://127.0.0.1:8000/redoc>

No CORS allowlist is installed. Host must be `127.0.0.1`, `localhost`, or `[::1]`,
optionally with a port. Requests with Origin must exactly match their scheme
and Host. Hostnames such as `localhost.evil.example` are rejected.

## Implemented routes

The Stage 1 reader routes below are GET; this is a subset of the current API:

| Route | Result |
|---|---|
| `/api/v1/health` | Backend version/readiness |
| `/api/v1/capabilities` | Implemented/readable/planned features and import errors |
| `/api/v1/strategies` | Production registry IDs/classes; no strategy instances |
| `/api/v1/scenarios` | W100 settings, input hashes, capacity |
| `/api/v1/scenarios/{scenario_id}/map` | Production geometry, zones, terminals, directed edges |
| `/api/v1/runs` | Directory-scoped history with integrity and provenance |
| `/api/v1/runs/{run_id}` | Original manifest and separate operational assessment |
| `/api/v1/runs/{run_id}/metrics` | Original JSON values; legacy/current/missing/invalid/unsupported |
| `/api/v1/runs/{run_id}/decisions` | Typed decision rows only |
| `/api/v1/runs/{run_id}/outcomes` | Typed realized outcome rows only |
| `/api/v1/runs/{run_id}/artifacts` | Explicit allowlist with existence, size, hash and validation |
| `/api/v1/runs/{run_id}/artifacts/{artifact_id}` | Original bytes as attachment |

Responses use `api_schema_version`, `data`, `availability`, `warnings`, `source`,
and `next_cursor`. Missing values remain null or absent; no metrics are computed.
Unknown object IDs return 404, malformed query values 422, stale cursors 409,
and catalogue dependency/load failures 503.

History supports `strategy`, `integrity=complete|incomplete`, `limit` and
`cursor`. Both table routes support `decision_id`, `order_id`, `limit`, `cursor`;
decisions also supports `robot_id` and `selected`. Limits default to 100, maximum
1000. History orders by opaque app ID; tables retain physical row order.
Cursors bind to the source signature and filters; restart on 409. The same
scientific run ID in two directories produces two different app IDs.

## Integrity and indexing

SQLite lives at `src/dashboard_GUI/var/index.sqlite`. It holds disposable
operational metadata, manifest metadata, validation results, file signatures,
hashes, counts, diagnostic warnings, and indexing timestamps. Metrics endpoints
always reopen original JSON; SQLite is never the metrics authority. Deleting
this **application-owned** database while stopped causes it to rebuild on the
next history request. Never delete or repair source artifacts to fix the index.

History requests rescan approved directory names and reuse validations for
unchanged device/inode/size/mtime/ctime signatures. Changed artifacts are hashed
and validated again. Initial validation streams entire tables in bounded batches;
it can be slow on a large history. Pagination is bounded in memory but may scan
preceding rows for each page/filter; there is no secondary scientific dataset.
Each rebuild is serialized and committed transactionally. Vanished files and
partially written artifacts become unavailable/invalid and are retried on later
requests. There is no watcher and no public mutation/reindex route.

`integrity=complete` means readable expected artifacts, supported schemas, matching
manifest/table counts and run IDs, and agreement of completed order counts. It
is structural validation, not re-execution or proof of every scientific value.
`scientific_status=completed` additionally requires recorded input/source
provenance and no observed failure evidence. `process_status=unknown` remains
unknown even for complete artifacts: historical runs do not record verified exit
codes. Manifest failed/cancelled/stalled status and explicit STALLED/traceback
log evidence are reported. Log detection examines first/last 64 KiB only and
does not infer failure from ordinary prose. A missing log is not success.

Dataset v1 uses the production column declarations, including Parquet types;
unknown/incomplete schemas are refused by typed table readers. Raw downloads
remain available for inspection. Legacy metrics with no version field are v1,
as documented in EXPERIMENT_PROTOCOL.md. They are never relabelled as v2 or
converted to order-work makespan. The API validates core metric types/counts;
it does not recompute metrics or certify historical experimental comparability.
Decision rows are never joined with outcomes or given counterfactual labels.

Allowed downloads are only `run.json`, `metrics.json`, `decisions.parquet`,
`outcomes.parquet`, `decisions.csv`, `outcomes.csv`, `run.log`, `report.txt`,
`bk.pl`, and `exs.pl` within discovered run directories. Manifest path fields
are never download authorization. All source opens use Linux descriptor-relative
`O_NOFOLLOW`, including every ancestor, and require regular files. File changes
during a read produce 409. Downloads are verified into an app-owned temporary
spool before headers/body are served; large downloads consume temporary disk.
JSON reads are limited to 4 MiB; CSV fields use Python's 128 KiB parser bound.
No support for arbitrary files, plots, symlinked history, or remote storage.

## Verification

```bash
export PYTHONPATH="$PWD/src/dashboard_GUI/backend:$PWD/src/mrta_core:$PWD/src/simulation_env:$PWD/src/task_management"
PYTHONDONTWRITEBYTECODE=1 src/dashboard_GUI/backend/.venv/bin/python -m pytest src/dashboard_GUI/backend/tests -q -p no:cacheprovider
```

Tests use tiny synthetic runs and read the existing W100 geometry. They do not
run workloads. See `../docs/control_center/STAGE_1_REPORT.md` for measured results,
real response examples, dependency versions, preservation checks and limitations.

Stage 2 jobs, experiment management/comparisons, telemetry/replay, React, ROS,
Gazebo, ILP and explanations are unavailable. No scientific phase is advanced.

## Stage 2 operational service

Stage 2 adds explicit preview/launch/cancel actions to `control_center.app`.
The Stage 1 read-only statements above describe its earlier scope; the separate
`control_center.stage1` entrypoint remains read-only. Use
[`STAGE_2_STARTUP.md`](../docs/control_center/STAGE_2_STARTUP.md) for current job
controls, authentication, runtime paths, examples and limitations. Restart the
backend to load new code. Do not stop another service automatically.

Install the updated application extras in the existing backend virtualenv (adds
pinned `websockets==15.0.1` only; no root/scientific environment changes), then
start the operational entrypoint with the existing source PYTHONPATH:

```bash
src/dashboard_GUI/backend/.venv/bin/python -m pip install -e 'src/dashboard_GUI/backend[test]'
export PYTHONPATH="$PWD/src/dashboard_GUI/backend:$PWD/src/mrta_core:$PWD/src/simulation_env:$PWD/src/task_management"
export PYTHONDONTWRITEBYTECODE=1
src/dashboard_GUI/backend/.venv/bin/python -m control_center.app
```

`GET /api/v1/session` supplies the token for the `X-Control-Center-Token` header
required by POST actions, and an HttpOnly same-site WebSocket session cookie.
GET readers stay read-only. Health truthfully returns `read_only: false` for this
operational service. `GET /api/v1/runs?scope=app` browses newly generated artifacts;
plain `/runs` still browses the original read-only repository history.
