# Stage 6 integration and operations implementation plan

**Goal:** One-command loopback startup, automated integration/QA gate and local
operations handoff, retaining all Stage 1–5 functionality and scientific parity.
**Spec:** STAGED_PLAN.md Stage 6 and user's explicit request to proceed.
**Architecture:** Python standard-library foreground supervisor under scripts/;
existing backend interpreter and Vite preview/dev processes, no new dependencies.
Default start builds the frontend. No automatic install or simulation on startup.

Constraints: preserve existing dirty/untracked files; no commits, science edits,
large simulations, plot UI expansion, ILP, ROS/Gazebo or public exposure. Prior
user request to leave servers stopped persists: all verification processes end.

- [x] Task 1: failing focused launcher tests, then check/start commands. Validate
  Node/dependencies/ports, launch fixed argv arrays with fixed cwd/environment,
  log each invocation under var/, wait for backend and proxied frontend readiness,
  stop only owned child processes on Ctrl+C, signal, failed startup or child exit.
  Test busy ports, missing dependencies, failed child, startup timeout, cleanup,
  real short-lived fixture services. Avoid stored PID-only signalling/adoption.
- [x] Task 2: verify command for backend tests, frontend tests/type/build and
  existing tiny observed-vs-original parity + HTTP/WebSocket smoke. Separate
  check (readiness only) from verify (explicit tests/tiny runs). No installs,
  large studies or serving left behind. Persist bounded diagnostics/evidence.
- [x] Task 3: actual localhost production-build startup through the supervisor,
  API/session/Origin/proxy/asset checks, Ctrl+C shutdown, full acceptance gate,
  independent review, baseline hash audit, OPERATIONS.md, README and stage report.

Interfaces: check/start share discovered absolute runtime paths and port policy;
verify shares scientific PYTHONPATH but uses separate disposable smoke outputs.
Original backend close cancels its own active workloads; document shutdown.

Review focus: unrelated occupied servers, partial startup and child death,
shutdown of native Vite children, injected environment, frontend proxy security,
clear distinction of check/start from explicit parity verification.

## Completion ledger

Task 1: complete — launcher fixtures RED→GREEN, including busy ports,
startup timeout, missing dependencies, partial startup, child exit, descendant
cleanup and repeat interrupt protection.
Task 2: complete — verify-contract fixture RED→GREEN; backend/frontend/build,
existing tiny parity and real production HTTP/proxy/WS/shutdown integrated.
Task 3: complete — final full gate PASS, independent review fix verified,
operations/README/report and preservation audit complete; servers stopped.
Ruling: use safe 400 or 403 Host rejection depending on which local layer rejects
first; record actual result rather than weaken Vite's protections.
Ruling: rename launcher to avoid backend package shadowing; no scientific source
or original smoke changes required.
Ruling: prior no-commit/direct-checkout/server-stop user preferences override
skill defaults; no worktree or commits, services stopped at handoff.
