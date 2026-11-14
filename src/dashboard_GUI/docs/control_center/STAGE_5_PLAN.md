# Stage 5 research dashboard implementation plan

**Goal:** Complete the next authorized dashboard stage: matrix operations,
scientifically guarded paired comparisons, decision/outcome inspection and
immutable single-run CSV inputs, plus a complete user README.
**Architecture:** Extend the existing local FastAPI/React application. Reuse
original CLI matrix fields and simulation_env.paired functions. No new science.
**Spec:** STAGED_PLAN.md Stage 5, API_CONTRACT.md and the user's request to proceed.
**Constraints:** Preserve current work; no commits, pushes, scientific edits,
long simulations, ROS/Gazebo or automatic Stage 6. Stay under dashboard_GUI.

The approved staged architecture is the chosen approach; rebuilding the UI or
adding unsupported runner flags would add risk without satisfying this stage.
Use existing HTTP/session/preview contracts, original artifact readers and
bounded input/page limits. Matrix configuration, quantum, window, speed and CSV
remain the original runner defaults and must not be editable matrix overrides.

## Tasks and interfaces

- [x] 1. Backend research adapters and tests. Add safe experiment discovery/detail
  and comparisons; validate actual artifacts, reject duplicate/mixed settings,
  retain identity strings, call original completeness/settings/paired math,
  expose actual metric n, missing runs and excluded rows. Add bounded CSV upload
  with exact five-column/map-aware validation and content hash addressing;
  single RunSpec input_id maps to original --orders-file and fingerprint checks.
  Run failing fixture tests, implement, run focused and existing backend gates.
- [x] 2. Matrix screen. Registered strategies, fleets/seeds, original workload
  choices, child jobs cap, new/resume/force intent, preview expanded actual grid
  and deliberate launch. Surface existing logs/status/cancel through Operations.
  Experiment browser displays completeness/provenance and comparison table with
  actual n, expected n, 95% CI, missing/error state, no fabricated ranking.
- [x] 3. Historical data inspector and CSV selection. Independent paginated
  decision/outcome tables and supported filters, exact strings/nulls, stale
  response protection. Upload only from explicit file selection and select an
  immutable validated input for a single run. Never write reference CSV.
- [x] 4. README and acceptance. Explain every screen/field, current/deferred
  capability, startup/storage/troubleshooting and robotics backend analogy.
  Frontend tests/build, backend suite, browser existing data and tiny fixture
  comparisons, one independent review, preservation audit and report.

Pre-flight: task 1 API fields are consumed by tasks 2/3. Keep Envelope version 1,
scoped opaque IDs and existing preview LaunchRequest unchanged; add optional
RunSpec.input_id only. Task 2 consumes server-generated grids rather than
reimplementing reference workload seed rules. Task 3 retains separate decision
and realized-outcome endpoints; no future-outcome field becomes an input.

## Review focus

- Matrix resume/force generation ownership and stale preview after edits.
- Missing/legacy/tampered files, metric-specific sample loss, identity hashes.
- Unsafe CSV/path/symlink data and upload/preview/launch input drift.
- Scope/selection/pagination changes while requests are pending.
- Mixed quantum/settings must refuse pooling and remain separate experiments.

## Ledger

User approved next-stage execution; preserve earlier dirty files in place.

Task 1: complete — fixture/API/CLI/observed input tests RED→GREEN; original math reused.
Task 2: complete — matrix preview/launch tests RED→GREEN; real four-run browser verification.
Task 3: complete — independent records, stale page and upload tests; no scientific changes.
Task 4: complete — README, review, report, hashes and final gates; see STAGE_5_REPORT.md.
Final: fixed CSV final fingerprint mismatch — custom CSV lifecycle failed before configuration resolution, CLI/observed pass after.
Final: fixed malformed git provenance — reader regression failed with AttributeError before type guard, passes after.
Ruling: prefer authoritative individual metrics to summary.csv; same original paired functions, no files rewritten.
Ruling: keep current checkout/no commits as explicitly requested; skill commit/worktree defaults do not apply.
