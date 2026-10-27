# Control Center staged delivery plan

> Stage 0 design only — audited 2026-10-27. No application or telemetry implementation exists from this stage. The user's subsequent layout instruction overrides the supplied plan: all Control Center development belongs under `src/dashboard_GUI/`; these documents belong in its `docs/control_center/` subdirectory. Scientific sources and historical artifacts remain unchanged. Later stages require separate authorization.

## Scope and chosen architecture

This is a research operations interface for configuring existing headless runners, observing genuine state, and inspecting reproducible artifacts. Follow the supplied headless-first FastAPI + React design, with the user's updated structured folder location. Stage 0 does not change the scientific development phase or approve pending science work; CURRENT_PHASE.md remains untouched.

```text
src/dashboard_GUI/
  MRTA_CODEX_STAGE_0_PROMPT.txt              existing, preserved
  MRTA_CONTROL_CENTER_CODEX_PLAN (1).md     existing, preserved
  docs/control_center/                     Stage 0 documents
    BASELINE_INVENTORY.md
    API_CONTRACT.md
    EVENT_SCHEMA.md
    SCIENTIFIC_PARITY.md
    STAGED_PLAN.md
  backend/                                 proposed, later stages only
    control_center/{api,adapters,services,schemas,storage}/
    tests/
    pyproject.toml
  frontend/                                proposed
    src/
    package.json
  tests/                                   proposed integration fixtures
  scripts/                                 proposed local launch helpers
  var/                                     proposed ignored runtime state
    index.sqlite
    inputs/
    jobs/
    runs/
```

Do not create empty implementation scaffolding in Stage 0. App runtime state/new outputs stay here; existing repository runs/ remains a separate read-only history root. OPERATIONS.md belongs in this docs directory at Stage 6. Do not use the old apps/control_center path in the supplied plan. Do not create a second ROS package or scientific implementation.

Alternatives considered: original CLI + logs/artifacts has the lowest integration risk and is the Stage 2 default; a standalone callback adapter can expose spatial state but duplicates runner setup and requires parity; a core/runner hook needs separate review and is not assumed necessary or authorized. ROS is deferred. The only proposed dependency choices are those already requested by the plan; version pinning/installation is later work.

## Review sequence and acceptance

| Stage | Authorized scope when separately requested | Acceptance / stop |
|---|---|---|
| 0 (this task) | Source audit and five documents only | exact CLI/signal/schema evidence, risks, lightweight checks and preserved tree; STOP for review |
| 1 | Read-only backend in backend/; synthetic fixture tests | localhost catalogue/map/history, typed bounded artifact reads, path safety, legacy/incomplete data; no launch endpoints |
| 2 | Job manager, command preview, original CLI launches, cancellation, logs | one active top-level job, bounded child concurrency, fresh run outputs, process-group ownership, restart reconciliation; no motion claim |
| 3 | Optional observer adapter/relay and sampled recording | tiny parity proof; exact clock/no new events; bounded copying/backpressure; otherwise documented logs-only fallback |
| 4 | React frontend in frontend/ | real API data, map/fleet when available, run controls, history/metrics/logs, accessible responsive states; no fabricated KPI/ILP output |
| 5 | Matrix UX, integrity-aware comparison, decision/outcome inspection | only real CLI fields, original paired math, actual sample counts, separate q conditions; immutable single-run CSV input option |
| 6 | Integration QA and scripts/ one-command local start | scoped build/type/API/mock process tests and tiny parity, operations/handoff, preserved science; user performs large runs |
| 7 (deferred) | ROS/Gazebo adapter only after separate approval | mirror authority; never a second allocator/planner |

Every stage begins by re-reading current instructions and git status/diffs, preserving existing work, and verifying capability drift. Stop after its report; no autonomous next stage. No commits/pushes unless separately requested. No broad formatters, branch changes, stash/reset/clean/revert. Implementation tests must remain small; no large simulations by the agent.

## Risks and decisions carried forward

1. **CLI asymmetry:** matrix quantum/config/window/speed/CSV overrides unavailable. Disable those matrix controls. Never edit warehouse.yaml to satisfy a GUI form. A future runner extension is a separate approval, not hidden Stage 5 work.
2. **Live telemetry access:** callback exists but original main has no injection API; standalone adapter requires source-equivalent setup and parity. Original experiment children remain logs/artifacts only. Dormant on_trajectory cannot supply paths.
3. **Event semantics:** on_event precedes handling; only external executor on_tick is suitable for coherent copied snapshots. Repeated same-time batches remain valid. Polling from another thread can tear state.
4. **Resume/provenance limitations:** finished() is presence-only; work files may be rewritten; config/source drift is not rejected at resume; dirty contents absent from settings signature. Preserve logs and disclose uncertainty without changing scientific rules.
5. **History compatibility:** 16/17 metric files are v1; no current experiment manifests in local history. Do not promise valid paired analysis or spatial replay for these runs.
6. **Final-only datasets:** cancellation/stall can lose in-memory rows. Preserve operational evidence and label partial; no fake completed rows or regenerated metrics.
7. **Sampling/scaling:** a fast headless run can outrun browser frames; trace recording is sampled, bounded, and may drop frames. Backend pagination must avoid eager read_run on large tables.
8. **Runtime dependency isolation:** no FastAPI/Vite installation is authorized now. Preserve pixi solver versions; different HiGHS builds can resolve ties differently. UI startup must not change the science environment.
9. **Data integrity on launch:** config/order files are ordinary filesystem inputs, not inherently immutable. Preview must fingerprint and recheck them; mid-run source/input edits remain a limitation to surface. Validated CSV copies use server-owned immutable paths, never overwrite reference orders.csv.
10. **Documentation drift:** README/architecture/roadmap contain historical defaults/status. Ground capabilities in code and clearly separate pending review from registered availability. G6, learning and explanations remain unavailable.

## Stage 0 review deliverables

Read BASELINE_INVENTORY.md for examined sources and runner tables; API_CONTRACT.md for proposed routes/layout; EVENT_SCHEMA.md for signal mapping/fallback; SCIENTIFIC_PARITY.md for integrity rules and checks. All interfaces are proposed and no server exists. No scientific phase document is updated because no scientific behavior or phase changed.

After review, a separate request may authorize Stage 1 only. Later user-run checks progress from a tiny CLI/app parity pair, to small W100 observed run, then controlled cancellation and larger fleets, then a small paired matrix. The q=3 matrix requires a separately approved runner capability first. Full 100-robot/30-seed campaign is user-run and remains outside the agent's Stage 0 acceptance.
