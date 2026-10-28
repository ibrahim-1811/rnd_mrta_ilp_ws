# Stage 2 local startup and review

Stage 2 is an operational backend, not a React GUI. It can preview and start
existing CLI workloads, track them, stream logs/status and cancel its owned
process group. Scientific code is unchanged. Spatial telemetry, replay and
experiment analysis are deferred.

Current verification and review fixes are recorded in
`STAGE_2_CONTINUATION_REPORT.md`; `STAGE_2_REPORT.md` is the earlier handoff.

## Start

From `/home/ibrahim/rnd_mrta_ilp_ws`, use the Stage 2 operational commands in
`../../backend/README.md`, ending in `python -m control_center.app`. If the old
backend already owns port 8000, stop it yourself with Ctrl+C before starting
this entrypoint. The separately added `control_center.stage1` service stays
read-only; it does not offer these actions. Startup binds 127.0.0.1:8000 with
proxy headers disabled. No LAN exposure or ROS setup.

The backend requires the app virtualenv, source checkout and existing scientific
pixi interpreter. `websockets==15.0.1` is now in the app dependency manifest.
Do not update root lockfiles or install web dependencies into pixi.

## Review using the API documentation

Open http://127.0.0.1:8000/docs (standard FastAPI docs use external assets).
OpenAPI JSON at `/openapi.json` and the API itself run locally.

1. Execute GET `/api/v1/session`. Copy its `data.csrf_token`.
2. Use Swagger's **Authorize** button and paste that token for
   `X-Control-Center-Token`. Browser WebSocket session cookie is also set by
   step 1; never send the token in a URL.
3. POST `/api/v1/jobs/preview` with the small single-run request below.
4. Review `data.argv`, effective inputs, output directory and warnings. Preview
   does not create a run or execute a process. It expires after 10 minutes.
5. Copy `data.preview_id` into POST `/api/v1/runs` as shown below. This is the
   deliberate action that starts a simulation.
6. GET `/api/v1/jobs/{job_id}` using the returned `data.id`. Inspect status,
   original argv, actual gated launch argv, wall elapsed time, exit code and
   artifact_status. GET its `/logs` and `/snapshot` routes. Snapshot is process
   status only; it explicitly reports spatial telemetry unavailable.
7. On completion, browse `/api/v1/runs?scope=app`, then use the returned app run
   IDs with metrics/tables/download routes and the same `scope=app` query.
   Default scope remains historical repository runs/.

Preview request:

```json
{"kind":"run","scenario_id":"W100","strategy":"nearest_astar","robots":2,"orders":3,"seed":7,"progress_interval_s":0}
```

Launch body (substitute the actual preview ID):

```json
{"preview_id":"0123456789abcdef0123456789abcdef"}
```

To request cancellation, POST `/api/v1/jobs/{job_id}/cancel` with the same token
and JSON `{}`. A three-order run may finish before cancellation reaches it.
Use a separate disposable run for intentional manual cancellation checks; do
not overwrite or cancel historical experiments. Cancellation means terminating
an owned CLI group, not pausing a scientific clock. Poll until terminal state.

## Python example

This example previews only. It never launches until the explicit final line is
uncommented. Run using the backend virtualenv so HTTPX is available.

```python
import httpx
with httpx.Client(base_url="http://127.0.0.1:8000") as client:
    token = client.get("/api/v1/session").json()["data"]["csrf_token"]
    headers = {"X-Control-Center-Token": token}
    response = client.post("/api/v1/jobs/preview", headers=headers,
        json={"kind":"run", "robots":2, "orders":3, "seed":7})
    response.raise_for_status()
    preview = response.json()["data"]
    print(preview["argv"], preview["effective"], preview["warnings"])
    # Deliberate run action after review:
    # job = client.post("/api/v1/runs", headers=headers,
    #     json={"preview_id":preview["preview_id"]}).json()["data"]
```

## Experiments

POST `/jobs/preview` accepts `kind: experiment`, name, strategies, robots list,
seeds list, orders, jobs (1–4, default 1), workloads (seeded/reference),
workload_orders, export and mode (new/resume/force). Launch that preview through
POST `/experiments`, not `/runs`. The preview expands the actual runner grid;
reference mode uses the first seed for deterministic policies and every seed
for random. Example: 2 policies × 1 robot count × 1 seed × 1 order:

```json
{"kind":"experiment","name":"tiny-review","strategies":["nearest_astar","random"],"robots":[1],"seeds":[7],"orders":1,"workload_orders":1,"jobs":1,"workloads":"seeded","mode":"new"}
```

No experiment quantum/config/window/speed/custom-CSV controls are accepted:
those flags are absent from the original matrix runner. Single runs accept
scenario, strategy, robots (0=config default), orders (0=all), allocation seed,
window, quantum_s, speed_mps, progress_interval_s, quiet and export. Unsupported
fields and wrong JSON types are rejected; custom CSV upload is still deferred.

Resume/force operate only on previously app-owned experiments. Resume checks
spec, input/source hashes, actual workload bytes and output fingerprint; a
changed completed artifact or workload requires a new preview and may refuse
resume. Every existing run that the original runner would skip is checked for
complete artifacts and matching original manifest settings. Corrupt or mismatched
skipped runs return 409 before anything launches; use a new name or deliberately
preview force. Preview creates no validation database and repairs no artifacts.
Recorded output-generation identity is checked when available; older ownership
records remain subject to the source/spec/artifact checks. The original runner
keeps its own safeguards. Before retry/force, the
previous directory is copied into the job's `previous_attempt/`; retry archive
is bounded to 256 MiB (larger attempts require a new name). Force is explicit
and delegates the original timestamped rename; it never deletes historical data.
No simulation clock resumes. Source/input changes during an actual run remain
a limitation: keep the checkout/config unchanged while a job is running.

## Job and event interfaces

All paths below start `/api/v1`:

| Method | Route | Purpose |
|---|---|---|
| GET | /session | Token + HttpOnly WebSocket cookie, no-store |
| POST | /jobs/preview | Validated exact command, effective settings, fingerprint/grid |
| POST | /runs or /experiments | Launch one-use preview; HTTP 202 |
| GET | /jobs?offset=0&limit=100 | Durable operational history |
| GET | /jobs/{id} | Status, identity, exit/artifact evidence |
| GET | /jobs/{id}/snapshot | Same status with last event sequence; no motion |
| GET | /jobs/{id}/logs?cursor=0&limit=65536 | Original merged stdout/stderr, byte cursor |
| POST | /jobs/{id}/cancel | Cancel only provably owned process group |
| WS | /ws/jobs/{id}?after_sequence=0 | Retained ordered status/log events |

Use a relative same-origin browser WebSocket URL and the session cookie. Origin
and exactly one loopback Host are checked, and the actual peer must be loopback.
Duplicate Host/Origin headers are rejected for HTTP and WebSockets. Mutation
headers are required even when Origin
is absent. The 500 most recent events per job are retained. On an older cursor,
the socket sends `resync_required` with snapshot/high-water sequence; recover
logs through the byte-cursor endpoint. Terminal streams close normally after
retained events are delivered. Invalid/future cursors require resync. No simulated
time is fabricated for log/status events: exact/display sim time are null.

Logs use UTF-8 replacement on byte-page boundaries; keep raw process.log for
exact bytes. Reads are at most 64 KiB. The monitor requests cancellation when
process.log exceeds 64 MiB; this is a polled threshold, not a disk quota, so a
burst can overshoot. Slow clients do not block subprocesses or simulation.
Experiments write child logs through their original runner; operational parent
log quota does not cap those child artifact files.

## Storage and recovery

All writable application state is under `src/dashboard_GUI/var/` (ignored):

```text
jobs.sqlite                         durable job status and bounded events
jobs.lock                           one backend owner per data directory
jobs/<id>/process.log                merged original process output
jobs/<id>/process_identity.json      fail-closed gate readiness/identity
jobs/<id>/exec_authorized            durable-ownership execution permit
jobs/<id>/previous_attempt/          preserved experiment before resume/force
experiment_owners/<name>.json        app experiment ownership and input hash
runs/singles/<job_id>/               original single-run artifacts
runs/experiments/<name>/             original matrix output tree
app-index/index.sqlite              disposable app-run reader index
validation/<job_id>/index.sqlite     final structural validation cache
```

One top-level job at a time. Backend shutdown requests cancellation and waits
for owned workers. TERM escalates after 2 seconds to KILL for live group members;
PID/start ticks/boot/session identity is checked before signals. A small stdlib
exec gate runs in the new session, publishes identity, and waits up to 10 seconds
for a permit issued only after SQLite owns its process identity. The gate then
execs the original CLI argv unchanged. A backend crash before ownership is
recorded cannot start an untracked scientific run. An unresolved launch blocks
a new job for at most the 15-second recovery window while the gate fails closed.

After a crash, jobs with uncertain exit become interrupted, never completed by
inference. Recoverable live groups block new jobs and can be cancelled after a
new session token is obtained. Otherwise the original exit code stays unknown.
A PID whose start identity changed is not signalled. Logs/artifacts survive.
Experiment ownership follows the observed output generation after the owned
process group settles. A force attempt leaves old provenance intact until the
original runner has replaced the directory and written the expected experiment
manifest. Failed spawn or cancellation before replacement retains the old owner.
Ownership records are replaced atomically; finalized older jobs cannot attribute
a later replacement directory to their own source fingerprint.
The local account and its processes are trusted; this is not a multi-user or
public authenticated service. Forced power loss, disk failure and a process
escaping its session are not promised to be recoverable process supervision.

`completed` requires exit 0 AND structurally valid artifacts whose original
manifest settings match the requested strategy/fleet/seed, workload and
config/map/input hashes. The source/input fingerprint is checked again during
validation. The app reuses the original manifest builder, order loader and window
resolver; it does not create another scientific model. Equivalent relative and
absolute path spellings are checked by their file hashes. Failure,
partial cancellation and unknown process evidence remain separate from artifact
integrity. Original decisions/outcomes and metrics are never recomputed. The
scientific engine remains the only source of simulated behavior.

## Checks

Small backend suite and scoped lint:

```bash
PYTHONDONTWRITEBYTECODE=1 src/dashboard_GUI/backend/.venv/bin/python -m pytest src/dashboard_GUI/backend/tests -q -p no:cacheprovider
.pixi/envs/default/bin/ruff check src/dashboard_GUI/backend/control_center src/dashboard_GUI/backend/tests src/dashboard_GUI/scripts
```

The explicit optional smoke script `scripts/check_stage2_smoke.py` executes only
two 3-order/2-robot single runs and a 2-run 1-order/1-robot experiment, then no-op
resume and a temporary loopback socket check. It writes evidence under
var/verification/. It is not part of the default tests. Never substitute a large
fleet or 30-seed campaign into the agent's focused gate.
