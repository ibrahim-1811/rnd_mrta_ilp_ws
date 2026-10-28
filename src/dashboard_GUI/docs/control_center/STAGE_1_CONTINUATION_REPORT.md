# Stage 1 continuation report

2026-10-27. Work and review continued in the user's current conversation.
Stage 1 only; no commit, push or scientific phase change.

## Result and initial state

The checkout already contained an untracked backend with catalogue/map/history
adapters, typed CSV/Parquet readers, safe artifact downloads and a metadata
SQLite index. Its `control_center.app` entrypoint had already been extended
with Stage 2 launch/cancel/job functionality, while its README still described
a read-only service. Replacing or rolling back that work would violate the
preservation instruction.

This continuation therefore adds a dedicated `control_center.stage1` application
that reuses the existing readers and index, registers an explicit GET-only route
allowlist, and has no job-manager lifecycle or job/runner imports. The supported
launcher binds `127.0.0.1`, disables proxy headers/WebSockets, and rejects remote
peers, unsafe/duplicate Host headers, mismatched/duplicate Origins, non-read
methods and app-job history scope. Health reports `stage=1`, `read_only=true`.

The Stage 1 index checks manifest versions before native table decoding. Unknown
or missing dataset versions remain browsable with unavailable/unsupported row
metadata, and original downloads remain accessible. A manifest change invalidates
cached table validation even when the table bytes have not changed. Missing
reader dependencies produce an explicit 503 rather than a server error.

Capabilities inventory current registry/map availability, schema versions and
actual CLI argument declarations/hashes via syntax parsing. Neither runner is
imported or executed. The API preserves scientific metric names, values and
version distinctions and keeps decision-time and outcome tables separate.

## Files authored by this continuation

- `backend/control_center/stage1.py`: isolated read-only application, guarded
  index, capability inventory, local security boundary and startup entrypoint.
- `backend/tests/test_stage1.py`: 19 tiny contract/regression tests, including
  parameterized CSV/Parquet reads, original-byte preservation, path/symlink
  protection, pagination drift, schema/dependency failure behavior, source/job
  import isolation and real native-reader lifecycle regression.
- `docs/control_center/STAGE_1_STARTUP.md`: current startup, API, storage,
  security, dependency and test documentation.
- `docs/control_center/STAGE_1_CONTINUATION_REPORT.md`: this measured report.

Generated runtime state is confined to the existing ignored
`src/dashboard_GUI/var/stage1/index.sqlite`. Temporary verification evidence is
in `/tmp/mrta-stage1-continuation-q19r27zk/`. No pre-existing file was edited by
this continuation. Existing backend dependencies/environment were reused; no
installation or scientific lockfile change occurred.

## Verification

The existing reader suite initially passed **24 tests**. Before implementation,
the new Stage 1 contract tests reproduced seven failures in the existing
launcher: writable health/capabilities, job lifecycle state/imports, duplicate
Host handling, non-loopback peer handling and late unsupported-schema handling.

A native Parquet segmentation fault also occurred during the new tests. A
separate tiny regression reproduced exit `-11` across successive application
and request-worker lifetimes. Keeping blocking reads on one process-lifetime
worker passed that regression over twelve successive app lifetimes. This
addresses the observed integration failure without changing scientific/native
packages; it does not establish the native library's internal cause. Health
remains asynchronous and does not wait behind an indexing request.

Fresh independent review found one important issue: the first schema guard ran
after the shared index had already decoded tables. A failing test reproduced
that unsupported history could become inaccessible when the decoder dependency
was missing. The Stage 1 index guard fixed this before decoding, and the new
regression also verifies cache invalidation after a corrected manifest.
No critical or deferred minor findings were reported.

Final complete backend suite, after detecting concurrent external edits:

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src/dashboard_GUI/backend:src/mrta_core:src/simulation_env:src/task_management \
src/dashboard_GUI/backend/.venv/bin/python -m pytest \
  src/dashboard_GUI/backend/tests -q -p no:cacheprovider
```

**78 passed in 15.17 s**, including **19 Stage 1 tests**. One upstream
Starlette/AnyIO deprecation warning remains. Existing broader-backend tests use
tiny synthetic child processes; no actual scientific runner or simulation was
launched. Focused lint of the two new Python files passed. `git diff --check`
passed for tracked changes; new-file whitespace was checked separately.

A real socket smoke check started the documented module from `/tmp` with an
available local port, then stopped only that owned server. Uvicorn logged
complete application shutdown. Measured responses:

- Health `read_only=true`, `stage=1`; OpenAPI has **12 GET application routes**.
- **Eight strategies** from the current registry.
- W100 **40 × 54**, **100 homes**, **4,244 directed edges** from production loaders.
- **20 historical directories**, **16 legacy metrics**, **one current metrics**,
  **three missing metrics**; **two structurally complete**, 18 unverified/incomplete.
- Original metrics plus typed decision/outcome pages from `gazebo_10_50` read
  successfully. POST `/api/v1/runs` returned **405**.

These are observations of existing artifacts, not newly executed experiments.

## Preservation and external edits

Initial HEAD `badbc59671acd06fdcaf513060175ab74aeccc3d`, branch
`traffic/rmf-integration`. SHA-256 baseline covered **437 pre-existing files**,
including **168 historical artifacts**. At final audit:

- **429 files remained byte-identical**, no baseline file disappeared.
- All **321 protected scientific/history/contract files**, including the 168
  historical artifacts, remained byte-identical.
- HEAD, branch and Git index bytes remained unchanged; staged diff stayed empty.
- Eight existing backend files changed concurrently outside this continuation:
  `backend/README.md`, `control_center/adapters/commands.py`, `api/jobs.py`,
  `schemas/jobs.py`, `services/jobs.py`, `services/processes.py`,
  `tests/test_commands.py`, `tests/test_jobs.py` (the latter paths are relative
  to `backend/`, with Python module paths relative to `backend/control_center/`).
  Those changes were retained, not overwritten or reverted. A new external
  `STAGE_2_STARTUP.md` also appeared and was left alone.

The first strict preservation assertion failed on those eight concurrent
changes, as it should; the follow-up distinguished them from scientific/history
modification and validated the protected files and Git state. This report does
not claim that the whole shared checkout remained unchanged.

Pre-existing tracked status remains the deleted old suffixed plan filename and
the modified `SCIENTIFIC_PARITY.md`. Pre-existing `.codex/`, `26.2.2`, backend,
scripts and later-stage documents remain present. No stash/reset/clean/revert,
branch switch, staging, commit, push or environment mutation was performed.

## Scope decisions and remaining limits

1. **Retain broader Stage 2 work unchanged by this continuation and supply a
   separate Stage 1 entrypoint.** It preserves concurrent work and enforces the
   requested scope. Cost: users must choose `control_center.stage1`; the older
   launcher still exposes its existing broader functionality.
2. **Validate history structurally, without certifying scientific results.**
   Source/metric semantics and scientific packages are unchanged. Cost: process
   success and scientific validity remain unknown where original evidence is
   absent; this task does not approve other scientific work.
3. **Keep streaming index/read behavior and serialize readers.** No large-history
   performance campaign was authorized. Cost: initial full validation and deep
   pages may be slow; simultaneous reader requests queue. Limits and non-atomic
   multi-file snapshots are documented in the startup guide.
4. **Measure preservation locally and retain external edits.** Reviewer scope
   excluded proving the parent session's initial filesystem state; the parent
   performed the SHA-256 audit above. Cost: eight external files cannot be
   described as unchanged, and they were not frozen or rolled back.

The visual frontend, simulation/job controls, experiment comparisons, live
telemetry/replay, ROS/Gazebo and ILP/explanations remain outside this Stage 1
application. No later stage was implemented by this continuation.
