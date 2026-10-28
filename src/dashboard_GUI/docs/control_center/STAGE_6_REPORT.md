# Stage 6 integration and operations report

Completed the authorized one-command local startup, integration QA and handoff.
No scientific behavior, backend API, frontend feature, runner or root dependency
lock changes were made. All services are stopped for the user to start locally.

## Delivered commands

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws
python3 src/dashboard_GUI/scripts/launch_control_center.py check
python3 src/dashboard_GUI/scripts/launch_control_center.py start
python3 src/dashboard_GUI/scripts/launch_control_center.py verify
```

`check` inspects existing dependencies/map/config and free ports without serving,
building or simulating. `start` builds and supervises the local backend and Vite
production preview in one terminal; `--dev` selects development mode. Ctrl+C or
SIGTERM performs bounded owned-process cleanup; unexpected child exit stops the
other service. Startup never installs dependencies or launches a simulation.
Occupied ports are refused without signalling/adopting the existing listeners.

`verify` runs the full backend suite, frontend tests, TypeScript/build, existing
tiny Stage 3 observed/CLI parity smoke, and a real production-start/proxy/security/
shutdown check. `--skip-parity` is explicitly reported as a reduced gate, and
backend tests still include tiny scientific fixtures. Logs/evidence are retained
under ignored `var/launcher/`; standalone parity retains `var/verification/` data.

## Final measured acceptance

Successful full gate:
`var/launcher/20261028T105651Z-8fd7683b/result.json`

- Backend: **148 passed**, one existing Starlette/AnyIO deprecation warning,
  33.48 seconds. Includes nine new focused launcher fixtures.
- Frontend: **21 passed**, six test files.
- TypeScript and production build passed (119 ms Vite build).
- Tiny original-CLI/observed equality passed: decision/outcome/manifest,
  deterministic metrics, original CSV/Prolog exports, exact time, HTTP snapshot
  and real WebSocket transport. No large simulation.
- Real production launcher: frontend index and generated JS asset returned 200;
  proxied backend health 200; bad Origin 403; bad Host 403 (Vite rejects before
  backend); action without CSRF 403; same-origin session 200/HttpOnly cookie.
  Existing-job WebSocket through the frontend proxy passed. Graceful launcher
  exit was 0 and both ports were released. Evidence:
  `var/launcher/20261028T105651Z-8fd7683b/http-integration/result.json`.
- Read-only `check` also passed from `/tmp` via the script's absolute path,
  confirming independence from caller working directory.
- Independent reviewer identified repeat Ctrl+C/SIGTERM interrupting cleanup.
  The regression failed before signal masking, then passed; cleanup restores
  handlers after bounded termination/escalation/reaping. Other reviewed paths
  had no actionable findings.

## Corrections found during acceptance

The first launcher filename (`control_center.py`) shadowed the backend package
when the existing smoke script ran from scripts/. Renaming the new launcher to
`launch_control_center.py` fixed the import collision; the final gate executes
that smoke successfully. No existing package or smoke source was changed.

The initial HTTP fixture expected backend Host rejection 400. Vite correctly
rejects the invalid host earlier with 403. The fixture now accepts either safe
rejection and records the actual status; no security behavior was weakened.
The final successful gate runs the corrected fixture end to end.

## Preservation and handoff

Audit: **496 baseline files**, **168 existing historical artifacts**. No missing
baseline file, no change outside dashboard_GUI, HEAD and Git index unchanged.
Only the existing dashboard README changed; launcher/helper/tests/Stage 6 docs
are additions. Runtime logs, builds and audit records remain ignored. Earlier
dirty/untracked work was retained. No commits, pushes, installations, root lock
edits or automatic next stage. Hash evidence: `var/stage6-audit/preservation.json`.

Read `OPERATIONS.md` for startup/shutdown, dependencies, failure behavior, logs,
verification semantics and handoff. Dashboard README retains the complete feature
inventory, workflows, graph commands and scientific-learning limits.

## Rulings and remaining limits

- Reuse the existing recorded Linux process identity helper and exact child
  handles. No saved-PID stop command or port-based kill was added. This limits
  the launcher to the established Linux workstation; cross-platform packaging
  is separate work.
- Use foreground operation rather than a detached daemon so one terminal owns
  the services. Closing that terminal/interrupting the launcher shuts down the
  backend and requests cancellation of its active owned workloads. Closing just
  a browser tab does not stop them.
- Leave application servers stopped, honoring the user's earlier instruction;
  production verification temporarily starts/stops them itself.
- Existing graphs remain generated with the scientific plotting CLI. Embedding
  plots, adding matrix runner flags, ILP/learned rules/explanations and Stage 7
  ROS/Gazebo are outside this integration stage.
- Dirty-source contents and original resume/provenance limitations documented in
  earlier reports remain. The launcher does not change scientific guarantees.
