# Stage 4: use the warehouse dashboard

## Open it now

Open http://127.0.0.1:5173/ on this computer. The backend must also be running
on http://127.0.0.1:8000/. Both services are local only.

To restart later, use two terminals. Leave each terminal running.

Terminal 1 — backend:

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws
export PYTHONPATH="$PWD/src/dashboard_GUI/backend:$PWD/src/mrta_core:$PWD/src/simulation_env:$PWD/src/task_management"
PYTHONDONTWRITEBYTECODE=1 src/dashboard_GUI/backend/.venv/bin/python -m control_center.app
```

Terminal 2 — dashboard:

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws/src/dashboard_GUI/frontend
npm run dev
```

Node 22.12+ is required (verified with 22.23.2). Dependencies are already installed.
For a fresh checkout, run `npm ci` in frontend first. Stop either service with
Ctrl+C in its terminal. If its port is already occupied, use the existing service
or stop your own earlier instance; do not terminate unrelated processes.

## First use

1. Operations opens the real W100 map. Select an existing job to see its logs,
   original metrics and any recorded robot state.
2. Click **Play recording** to display captured robot cells. Pause, seek, select
   a robot, change the display rate, or return to **Latest**. These controls only
   change what the browser displays; they never change simulated time or speed.
3. To start a small run, keep **2 robots / 3 orders / seed 7**, choose **Observed**,
   then **Preview run**. Review the effective settings and click **Launch run**.
   Opening the dashboard or previewing never starts a simulation.
4. An active selected job exposes **Cancel run**. Cancellation is a request;
   the backend retains ownership until its process has stopped. A tiny headless
   run may finish before you can click this button.
5. **Run history** browses app runs or repository history. Select a row to see
   original metrics, manifest, integrity notes and artifact downloads. **Open
   observation** only opens an actual recording associated with that run.
6. **System** shows backend capabilities, registered strategies and source hashes.

Fast headless simulations can finish in under a few real seconds. Use playback
to inspect their recorded movement. Playback loads at most 500 samples / roughly
8 MiB of source pages per segment (plus at most one bounded page at the boundary).
It does not invent positions between samples. Display rate is samples per real
second, not a claim about real-time simulation speed. Missing samples, incomplete
recordings and stale observations are labelled separately from scientific success.
A complete trace can still have dropped observation samples.

## Backend explained for a robotics engineer

Think of the dashboard as your control-station screen. FastAPI is the station's
service interface. It validates a request and passes it to the job manager.
The job manager supervises the simulation process, keeps its logs and reports
whether it is running, finished, failed or cancelled. Your simulator remains
responsible for all robot behavior and scientific results.

```text
Dashboard → FastAPI → job manager → existing simulation components
    ↑           ↑                         ↓
    └── state/logs/recorded samples ← original files + read-only observer
```

- **HTTP GET** reads information, similar in purpose to a query service.
- **HTTP POST** performs an action: preview, start or cancel. The dashboard
  obtains a local session token automatically; you need not copy one manually.
- **WebSocket** streams sequenced updates, conceptually like subscribing to a
  topic. The dashboard also reads snapshots/log pages to recover missed updates.
- **SQLite** is an operations ledger and searchable metadata catalogue. It is
  not the source of scientific metrics and does not allocate tasks.
- **Observed mode** copies actual robot/order state after existing executor
  ticks. Its NDJSON recording is conceptually like a small sampled bag file.
  This is an analogy: no ROS integration was added.
- **Original CLI mode** runs the existing CLI with logs/artifacts, without a
  fleet recording. Observation is unavailable rather than simulated by the UI.

A job ID identifies a supervised execution. An app run ID identifies its indexed
artifact directory. The manifest's scientific run ID is a third, input-derived
identifier; two equivalent executions can share that scientific identifier.

New operational data and run artifacts live under ignored
`src/dashboard_GUI/var/`. Repository `runs/` history remains read-only. Metrics
are read from original files; a legacy fleet makespan is never relabelled as
order makespan. A dash means unknown, not zero. Registered strategy does not
mean scientifically reviewed or approved.

The backend's interactive API reference remains at http://127.0.0.1:8000/docs.
Stage 3's startup document contains individual request examples. Stage 5 matrix
controls, comparisons and detailed decisions/outcomes inspection are deferred,
as are ROS/Gazebo integration and remote browser access.

## Frontend verification

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws/src/dashboard_GUI/frontend
npm test
npm run build
```

`npm run preview` serves the built frontend on the same loopback port when the
dev server is stopped. It still requires the backend on port 8000. Vite proxies
relative `/api` requests and WebSockets without rewriting Host or Origin. Keep
both services local; this is a research workstation application.
