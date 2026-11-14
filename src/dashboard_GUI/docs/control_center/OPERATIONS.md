# Warehouse Control Center — local operations and handoff

The control plane delivers a local research dashboard for single runs, matrices,
artifacts, paired performance and a supervised ROS/Gazebo launch. The scientific
simulator, runners and fixed infrastructure remain authoritative. Gazebo is a
mirror, not a second planner, allocator or traffic controller.

## One-command operation

From the repository root:

```bash
cd /home/ibrahim/rnd_mrta_ilp_research
python3 src/dashboard_GUI/scripts/launch_control_center.py start
```

This checks the existing environments and free ports, builds the frontend, then
starts the backend on **127.0.0.1:8000** and production frontend preview on
**127.0.0.1:5173**. Wait for `Dashboard ready`, then open
http://127.0.0.1:5173/. Keep this terminal open. No simulation starts automatically.
No dependency is installed or updated by the launcher.

**Ctrl+C stops both services.** Backend shutdown requests cancellation of any
owned active workloads; their scientific artifacts may be partial. Let a job
complete first if you need its completed results. A second Ctrl+C does not
interrupt the bounded cleanup. Closing the browser alone leaves the processes
running. If either service exits, the launcher stops the other to avoid an
unnoticed half-running workspace. Review its retained logs before restarting.

The launcher only signals its own Popen handles and recorded Linux process-group
identities. It does not adopt existing servers, kill a process by port, or trust
stored PID files. If a port is already occupied, use or stop that instance from
its original terminal. Separate old two-terminal startup remains supported.

## Check readiness without starting anything

```bash
python3 src/dashboard_GUI/scripts/launch_control_center.py check
```

Checks existing backend/scientific imports, W100 map/input loading, Node version,
frontend dependency presence, ROS Jazzy setup, this checkout's built workspace
setup and free local ports. It does not build, install, start a server or run a
simulation. A running instance makes the free-port check fail intentionally;
`check` means ready for a fresh start, not a health query.

## Development mode

```bash
python3 src/dashboard_GUI/scripts/launch_control_center.py start --dev
```

Uses the same local backend and Vite development server with live source updates,
without the production build step. Ordinary use should prefer `start`.

The script resolves repository paths from its own location; it also works when
called by absolute path from another directory. Both modes retain strict ports,
loopback binding and the existing Host/Origin/session/WebSocket protections.

## Verification gate

Stop your app servers first. Then:

```bash
cd /home/ibrahim/rnd_mrta_ilp_research
python3 src/dashboard_GUI/scripts/launch_control_center.py verify
```

This explicitly performs:

1. Full relevant backend test suite (fixture processes and tiny scientific cases).
2. Frontend tests.
3. TypeScript validation and production build.
4. Tiny observed-app versus original CLI scientific parity, export equality,
   exact time and real HTTP/WebSocket check using disposable verification output.
5. Real production launcher startup, frontend index/built assets, proxied health,
   session/CSRF/Host/Origin checks, optional existing-job WebSocket proxy, and
   graceful shutdown with both ports released.

No large campaign is run. The standalone parity is two tiny 2-robot/3-order runs.
The backend suite also has small parity/custom-CSV cases. Output stays under
`var/verification/` or pytest temporary directories. The HTTP integration step
reads existing app history if any; it never launches a workload or rewrites
historical science. If no existing job is present, its proxy WebSocket check is
reported as unavailable; the standalone parity still checks real WebSockets.

To omit only the standalone parity smoke:

```bash
python3 src/dashboard_GUI/scripts/launch_control_center.py verify --skip-parity
```

This is a reduced gate: backend tests still contain tiny scientific cases, and
real HTTP startup/shutdown still runs. A successful reduced gate is not evidence
that standalone parity ran. No tests are run automatically on normal startup.

## Dependencies and failures

- Linux and Python 3 with `/proc` process identity are the supported workstation.
- Node.js 22.12+ and npm must be available on PATH (22.23.2 verified).
- Existing backend interpreter: `backend/.venv/bin/python`.
- Existing scientific interpreter: repository `.pixi/envs/default/bin/python`.
- Frontend dependencies: scoped `frontend/node_modules`, using its lockfile.
- ROS setup: `/opt/ros/jazzy/setup.bash`.
- Built workspace setup: repository `install/setup.bash`.

If the workspace setup is absent, build only the packages used by the fixed
Gazebo mirror:

```bash
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install \
  --packages-select robot_interfaces mrta_core task_management gazebo_fleet_pose_transport simulation_env \
  --cmake-args -DPython3_EXECUTABLE=/usr/bin/python3
```

The explicit Python value prevents an old CMake cache from selecting a non-Jazzy
Python installation. The launcher never runs this build automatically.

If frontend dependencies are absent, the launcher reports the command to run:

```bash
cd /home/ibrahim/rnd_mrta_ilp_research/src/dashboard_GUI/frontend
npm ci
```

If Python/scientific imports fail, restore the established project environment
following the repository setup documentation and Stage 1 startup notes. Do not
update root solver/dependency locks just to start the UI. The launcher sets the
project import paths and removes inherited Python/Node injection variables;
scientific child execution retains its existing separately validated environment.

| Failure | Behavior / recovery |
|---|---|
| Missing dependency, ROS/workspace setup or unsupported Node | Clear failure before serving; no auto-install/build |
| Port occupied | Refuse, without signalling the existing listener |
| Build fails | Report build log; no services left running |
| Backend cannot acquire job directory | Refuse startup; inspect existing instance/lock ownership |
| One server fails or readiness times out | Stop owned service(s); inspect logs |
| Interrupt during startup/build | Stop owned subprocess groups; retain logs |
| Verification fails | Nonzero exit with failing step/log; later checks do not run |
| Unexpected power loss or forced kill | Restart uses existing backend interrupted/unknown reconciliation; no simulation-clock resume |

Startup is bounded; servers that cannot become ready fail after the readiness
window. Ctrl+C during a verification gate cancels that gate; partial evidence is
not a passing result. Never use `killall`, broad port-kill commands or delete job
state to conceal a failure.

## Logs, evidence and data

Every launcher start/verify creates its own directory:

```text
src/dashboard_GUI/var/launcher/<UTC timestamp>-<random suffix>/
  frontend-build.log
  backend.log
  frontend.log
```

Verification directories instead contain per-step logs, `result.json` on full
success, and `http-integration/` with real startup/security/shutdown evidence.
The tiny parity log points to its separate scientific equality evidence under
`var/verification/`. Successful output explicitly identifies skipped parity.
Logs and scientific artifacts are retained; the launcher never deletes them.

Operational jobs, observations, CSV copies and scientific app outputs retain the
Stage 5 layout documented in the [user guide](../../README.md). Repository
`runs/` remains a separate read-only history root. Do not confuse process exit,
artifact completeness, scientific validity and observation completeness.

## Handoff

- User workflow, graph commands and current feature inventory: dashboard README.
- API documentation while running: http://127.0.0.1:8000/docs.
- Baseline contracts: API_CONTRACT.md, EVENT_SCHEMA.md, SCIENTIFIC_PARITY.md.
- Implementation evidence and remaining limits: STAGE_1–6 reports.
- Gazebo uses the fixed `warehouse_sim.launch.py` and `simulation_env.fleet_node`;
  startup verification validates its contract but does not open an uncontrolled
  graphical session. Scientific ILP rules/explanations remain separately gated.
- No commit/push or automatic next stage. Large studies are user-run.
- All app servers are left stopped after the control-plane verification gate.
