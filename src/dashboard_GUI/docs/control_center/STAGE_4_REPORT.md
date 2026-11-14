# Stage 4 implementation report

Implemented the local React/TypeScript/Vite frontend in `frontend/`. Operations
connects to the existing Stage 3 backend: real W100 geometry, registry-driven
strategy selection, preview then deliberate launch, cancellation of the selected
active job, sequenced WebSocket observations with snapshot/log recovery, fleet
inspection, sampled recording playback, logs and original metrics. History
supports app/repository scopes, original manifest/metrics, provenance notes,
artifact downloads and opening an associated genuine recording. System exposes
catalogue hashes, capabilities and strategy registration information.

## Verification

- TypeScript and production build passed.
- 16 focused frontend tests passed: scientific/legacy/null display, coordinate
  conversion, input validation, preview invalidation, deliberate single launch,
  CSRF/session API handling, unsupported schema, sample gaps/generation checks,
  recording bounds, late responses after selection changes, playback restart,
  and continued log recovery beyond 400 KB.
- Existing backend suite: 130 passed in 30.55 seconds; one existing Starlette/
  AnyIO deprecation warning. The first invocation encountered an unrelated ROS
  pytest plugin import failure (`lark`); disabling automatic plugin loading and
  setting the documented project import paths resolved test collection.
- Real browser checks: existing verified job, captured playback, pause/latest,
  deliberate 2-robot/3-order observed preview/launch, completion, original metrics,
  history details and associated observation navigation. New browser test job
  `72bf65af9031466890148ba76ca0a97f` completed 3/3; original scientific metrics:
  order makespan 202 s, fleet settled time 656/3 s, distance 528 m, waiting 0 s.
  Its observation had 14 dropped samples, displayed as partial separately from
  completed scientific execution.
- Browser console had no errors/warnings during the checked flow. Layout checks
  at 1440 and 390 CSS pixels showed no horizontal document overflow. Temporary
  viewport overrides were reset.
- Proxy session: actual dashboard origin accepted with session cookie (200);
  unrelated HTTPS origin rejected (403). Host/Origin and WebSocket origin are
  preserved, with no wildcard CORS.
- Independent review found cancellation visibility, stale/partial labels,
  terminal log continuation, partial-page cursor and replay-at-end issues. All
  were fixed; follow-up reviewer confirmed no unresolved reported findings.

## Preservation and boundaries

Stage 4's preservation audit confirmed all 457 baseline files byte-identical,
including 168 existing repository history artifacts. HEAD remained
`badbc59671acd06fdcaf513060175ab74aeccc3d`, branch `traffic/rmf-integration`, and
Git index hash unchanged. Existing dirty/untracked work was retained. Baseline
hash evidence was produced during implementation; its temporary baseline file
was no longer available after the later session continuation. No simulator,
runner, backend source, scientific configuration or existing history edits were
made by this stage. No commits, pushes, long simulation or Stage 5 work.

All additions are under `src/dashboard_GUI/`: frontend source/lockfile and Stage 4
plan/startup/report. Installed dependencies/build output are ignored. Operational
server logs and PID metadata are under ignored `var/`. Servers were restarted
after the continuation using detached local processes, with identities in
`var/stage4-servers.json`; startup instructions also support ordinary terminals.

## Limits

Playback is captured samples at a browser display rate, without interpolation or
continuous trajectory claims. Recording is loaded in bounded segments. Live
headless runs may finish before the browser can show intermediate samples;
recorded playback supports inspection. Cancellation is implemented but its
button was not exercised against a deliberately prolonged real run. History
process evidence can be unknown even when scientific artifacts are complete.
Missing/legacy values remain unknown. No matrix UX, comparison charts, detailed
decisions/outcomes tables, remote access or ROS/Gazebo integration was added.

See `STAGE_4_STARTUP.md` for startup, use and the backend explanation.
