# Stage 2 continuation: verified backend run controls

2026-10-27. Stage 2 only, completed in this conversation. The visual dashboard
and Stage 3 observation adapter have not been implemented by this continuation.

## Delivered behavior

Reviewed the existing Stage 2 implementation and fixed concrete defects while
retaining its original CLI execution architecture, durable job/log/event state,
process-group ownership, fail-closed launch gate, cancellation and restart
handling. The current startup/API guide is `STAGE_2_STARTUP.md`.

The operational service supports validated previews, original single/experiment
runner launches, one active owned top-level job, bounded child concurrency,
status/log history and reconnect, cancellation with escalation, and interrupted
job recovery. Stage 1's separate read-only entrypoint remains intact.

One independent whole-stage review found three important defects. Each was
reproduced with a failing test before its fix:

1. Failed force attempts could replace old experiment provenance before the
   runner replaced the output. Ownership now follows the observed directory
   generation after the owned group settles and the expected experiment
   manifest exists. Old provenance survives failed spawn or cancellation before
   replacement. Atomic owner records and one-time finalization prevent older
   jobs from attributing later outputs to themselves. Resume checks generation
   identity when the ownership record supplies it.
2. Resume accepted corrupt artifacts that the unchanged runner would skip based
   on manifest/metrics presence. Preview now reads those directories, validates
   completeness and requested manifest settings, and refuses unsafe resume
   with 409. This creates no validation database and never repairs old evidence.
3. Completion checked artifact counts/grid without establishing correspondence
   to the requested scientific inputs. It now rechecks the source/input
   fingerprint and compares original manifest fields against the original
   manifest builder, order loader and window resolver. Strategy, fleet, seed,
   workload, counts, battery/service settings and config/map/input hashes must
   match. Equivalent path spellings are compared through file hashes.

The parent review also reproduced and fixed duplicate Host/Origin acceptance,
spoofed localhost Host from a non-loopback peer, and non-ASCII CSRF input causing
a server error. HTTP and WebSocket connections now share strict authority/peer/
Origin validation; malformed tokens return forbidden. Responses disable caching.

## Files changed

Eight existing files were changed narrowly:

```text
backend/control_center/adapters/commands.py       resume guards and generation fingerprint
backend/control_center/api/jobs.py                token and WebSocket security
backend/control_center/app.py                     shared local connection checks
backend/control_center/services/jobs.py           ownership finalization and result correspondence
backend/control_center/storage/index.py           expose existing validation without SQLite writes
backend/tests/test_backend.py                     explicit loopback test-client peer
backend/tests/test_jobs_api.py                    explicit loopback test-client peer
docs/control_center/STAGE_2_STARTUP.md             current behavior and usage
```

New files:

```text
backend/control_center/adapters/validation.py
backend/tests/test_stage2_hardening.py
docs/control_center/STAGE_2_CONTINUATION_REPORT.md
```

No scientific source, runner, map, configuration, root dependency/lockfile or
scientific phase document changed. No dependency installation was needed.

## Measured verification

Baseline backend suite: **78 passed in 14.59 s**. The continuation adds **16**
focused regression cases, using synthetic tables and stand-in process failures.
Final complete backend suite: **94 passed in 16.02 s**. One upstream
Starlette/AnyIO deprecation warning remains; no failures. Scoped Ruff passed for
all backend source/tests and dashboard scripts; tracked whitespace checks passed.

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src/dashboard_GUI/backend:src/mrta_core:src/simulation_env:src/task_management \
src/dashboard_GUI/backend/.venv/bin/python -m pytest \
  src/dashboard_GUI/backend/tests -q -p no:cacheprovider

.pixi/envs/default/bin/ruff check src/dashboard_GUI/backend/control_center \
  src/dashboard_GUI/backend/tests src/dashboard_GUI/scripts --output-format concise
```

After all production fixes, the explicit tiny smoke check passed:

- Original CLI and app-launched single run, each **2 robots / 3 logical orders**:
  exact parsed decisions, outcomes and manifests equal; **5 decision rows** and
  **3 outcomes**; all deterministic metric keys equal. Only the four declared
  host timing fields are excluded from equality, with original values retained.
- Original matrix runner: **2 strategies × 1 robot × 1 seed × 1 order** completed.
  No-op resume skipped both complete runs after the new validation guards.
- Actual loopback socket: health **200**, session acquired, authenticated real
  WebSocket upgrade and first durable status event at sequence **1**. The owned
  server stopped; no pre-existing user service was terminated.

Final smoke evidence:
`src/dashboard_GUI/var/verification/stage2_3fc4cd745d/result.json`.

A separate tiny force check also passed: the original runner retained its old
directory, `previous_attempt` preserved the old evidence, new ownership matched
the replacement directory's inode, reading older job history did not rewrite
that owner, and subsequent no-op resume succeeded. Evidence:
`var/verification/stage2_244fa4d365/force-result.json` under the dashboard folder.
Repeated smoke attempts use fresh disposable application-owned directories.
No long simulation, campaign, ROS/Gazebo or full scientific test suite ran.

There was one fresh independent review, followed by one test-driven fix pass.
No critical or deferred minor findings were reported. No second independent
review of the fixes is claimed; regression tests and integration checks verify them.

## Preservation

Before edits, **444 pre-existing files** were hashed and dashboard originals
backed up outside the repository at `/tmp/mrta-stage2-review-mtejpxj7/original/`.
Final audit: **436 byte-identical**, eight intentional changes listed above,
no baseline file missing. All **321 protected scientific/history/contract files**,
including all **168 historical run artifacts**, remained byte-identical.

HEAD stayed `badbc59671acd06fdcaf513060175ab74aeccc3d` on
`traffic/rmf-integration`; Git index bytes and staged state stayed unchanged.
Existing dirty/untracked files, old plan deletion and `SCIENTIFIC_PARITY.md`
modification were retained. No commit, push, branch switch, stash/reset/clean/
revert or user-process termination occurred. Temporary detailed audit/test logs
are under `/tmp/mrta-stage2-review-mtejpxj7/`; new runtime evidence stays in the
existing ignored dashboard `var/` directory.

## Scope decisions and limits

- Reused and repaired the existing Stage 2 implementation in place. Cost:
  backend changes remain uncommitted and existing services need a deliberate
  restart to load them; no unrelated checkout work was reset.
- Kept the original scientific algorithms and runners authoritative. Cost:
  operational structural/input validation does not certify scientific findings
  or fill historical exit-code gaps; paused-clock resume is not supported.
- Kept the documented trusted-local-account boundary. Cost: deliberately escaped
  sessions, hostile same-account filesystem races, power loss and disk failure
  are not comprehensively supervised. Ordinary ownership, cancellation,
  restart, HTTP and WebSocket behaviors are tested.
- Retained current streaming/indexing behavior and performed only tiny acceptance
  runs. Cost: validation can be slow for large histories/matrices. Source checks
  occur at checkpoints and do not make a mutable checkout immutable mid-run.
- Stopped at Stage 2. Cost: no visual GUI yet; Stage 3 checks genuine live-state
  observation, then Stage 4 supplies the React dashboard. Maps, run controls,
  logs, history and metrics are available to that frontend through the backend;
  robot movement must not be fabricated if live observation is unavailable.
