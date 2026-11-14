# Stage 4: local React warehouse dashboard

The user verified Stage 3 and explicitly authorized the frontend. Implement the
approved React/TypeScript/Vite design directly in this checkout; no additional
approval round, commits, worktree or scientific edits. Preserve existing work.

## Design and scope

An operational light workspace with a dark navigation rail, warm neutral canvas,
olive accents and compact, readable typography. The main Operations view has run
setup, a large real W100 map and an observed fleet inspector. Lower panels provide
job state, sampled-recording playback, logs and original metrics. History and
System views are functional. Layout adapts to laptop and narrow screens.

Use actual registry/scenario/map/job/history data only. Before a run/recording is
selected, show a real map and explain that robot state is unavailable; never place
invented robots or show fabricated KPIs. Labels distinguish live, sampled playback,
final, stale, partial and unavailable observation. No trajectory interpolation.

Single-run inputs map only to the existing RunSpec. Require preview then deliberate
launch; changing inputs invalidates the preview. User-selected small defaults are
2 robots/3 orders/seed 7 and observed mode when supported. No auto-launch on mount.
Cancellation acts on the selected owned job. Matrix UX/comparisons and scientific
decision/outcome inspection remain Stage 5; ROS/Gazebo remain deferred.

All code/dependencies live in frontend/. No root lockfile/environment changes.
Vite binds 127.0.0.1:5173 and proxies relative /api requests/WebSockets to the
existing local backend. Preserve Host and Origin through the proxy so the backend
still validates the actual browser origin; no wildcard CORS or origin rewrite.

## Implementation/acceptance

- [x] Create scoped package/config, typed API/state helpers, and failing tests for
  preview invalidation, exact scientific value display, map coordinate conversion,
  null/legacy metrics, bounded trace pagination and sample gaps.
- [x] Implement the visible React workspace and real catalogue/map connection;
  start the local frontend and expose it for review during development.
- [x] Implement preview/launch/cancel, jobs, authenticated WebSocket plus polling
  fallback, correct source age/loss states, paginated recorded frames and sampled
  playback. Changing jobs cannot leak old responses into the selected view.
- [x] Implement app/history scopes, metrics/manifest/artifact downloads and honest
  unavailable/error/loading states, accessible forms, keyboard controls and
  responsive layouts.
- [x] Run focused UI/API fixture tests, TypeScript/build and browser checks against
  real backend data. Verify same-origin proxy/session/WS behavior and one tiny
  deliberate observed launch. No long simulation or automatic historical repair.
- [x] One independent frontend review, focused regression fixes, startup/report and hash
  preservation audit; leave the local GUI available and stop at Stage 4.
