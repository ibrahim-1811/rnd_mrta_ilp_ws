# Stage 5 — experiment and research dashboard

Completed the authorized Stage 5 under `src/dashboard_GUI/`. The full user guide
is `src/dashboard_GUI/README.md`; frontend/README links to it. Existing scientific
phase documents and simulator/runners remain unchanged.

## Delivered

- Experiments screen: registered strategies, fleet/seed lists, seeded/reference
  workloads, orders and generated workload count, bounded child concurrency,
  optional original exports, explicit new/resume/force, actual server-expanded
  grid preview and deliberate launch. Existing Operations owns status/log/cancel.
- Scoped experiment library/detail: actual expected grid, complete/missing/
  failed/invalid artifact evidence, outside-grid directory disclosure and source
  provenance. No process success inferred from historical file presence.
- Read-only paired analysis: validated original individual run metrics and
  identity/settings/workload guards, original `simulation_env.paired` functions,
  actual metric-specific n, expected n, baseline means, differences, percentages
  and original 95% paired t intervals. Explicit incomplete analysis never bypasses
  mixed-setting/workload guards. Summary/paired files are not rewritten.
- Independent decision/outcome inspector: supported filters and 50-row bounded
  pages, exact original strings, physical order, null values and stale-response
  protection. Decision-time features remain separate from realized outcomes.
- Single-run CSV ingestion: exact five-column header, original map-aware loader,
  2 MiB / 50,000-line bound, positive quantities, content-addressed read-only
  copies, original --orders-file mapping, preview/launch/final hash checks.
  Both original CLI and observed execution accept the validated copy.
- Comprehensive README: current/deferred features, startup, all screen workflows,
  field semantics, recording, matrices/new-resume-force, comparison interpretation,
  scientific boundaries, backend robotics analogy, storage and troubleshooting.

## Evidence

- Full backend suite: 139 passed in 39.49 seconds on the final run. One existing
  Starlette/AnyIO deprecation warning.
- Frontend: 21 tests passed; TypeScript and production build passed. Covers explicit
  matrix preview/launch fields, separate historical tables, stale-page rejection,
  explicit/oversize CSV upload, plus Stage 4 coverage.
- Browser: real four-run seeded matrix (`stage5_browser_20261028`): Nearest A* /
  Random, 2 robots, seeds 1/2, 3 orders. Job
  `6400bb912e3744719e7f3349469172cd` completed with valid artifacts in 4.5 s wall.
  Paired order makespan displayed actual/expected n=2/2, baseline mean 163.667 s,
  mean difference 6.5 s and CI [-50.678, 63.678] s. This is a plumbing check, not a
  research conclusion. Existing historical outcomes displayed exact `386/3`.
- Browser CSV file selection/upload validated a one-order copy and preview showed
  one effective order despite requested limit 3. Returned to reference workload
  after verification. No extra run launched for this browser upload.
- Two tiny custom-CSV lifecycle tests exercise CLI and observed completion and
  final validation. Independent review caught the initial reference-path final
  fingerprint mismatch; regression failed before the fix and passed afterward.
- Independent review also caught malformed nested git provenance causing a server
  error. Regression failed before the fix; malformed run is now invalid/excluded.
  Experiment spec type checks avoid the analogous malformed-object issue.
- A focused test ordering first loaded the native PyArrow extension in a temporary
  TestClient lifespan thread, then crashed a later worker after that thread ended.
  Its fixture now imports the native reader on pytest's owner thread, consistent
  with the existing backend initialization contract. Focused and full gates pass.
- Browser console: no errors/warnings during checked flow. The backend was safely
  restarted only after verifying no active owned job; original server process
  identity was checked. Loopback-only binding and original session guards retained.

## Preservation and limits

Baseline audit: 483 existing files. All 168 original repository historical
artifacts unchanged, no missing baseline files, no changes outside dashboard_GUI,
HEAD and Git index unchanged. Earlier dirty files/untracked work retained. The
12 changed baseline paths are existing dashboard implementation/integration files;
new research modules/tests/docs are additions. Detailed hashes/results remain in
ignored `var/stage5-audit/`. No commits/pushes, science edits or large campaigns.

Matrix quantum/window/speed/config/CSV overrides are absent because the original
matrix runner lacks them. Single-run CSV and observation do not imply matrix
support. Reference workloads are not paired seeded experiments. Dirty-source
contents remain a provenance limitation. Large artifact sets may take time to
validate and reader bounds are explicit. Resume/force ownership/drift protections
are inherited from Stage 2 and covered by its existing tests; no force replacement
was performed in the browser. No plotting/export math was reimplemented.

Stage 6 remains the separately requested integration QA/one-command startup stage;
ROS/Gazebo remains separately approved Stage 7. This stage stops after its report.

## Implementation rulings

Use original individual metric artifacts rather than trusting summary.csv values;
this avoids silently analyzing a stale/replaced summary and calls the same paired
functions. Cost if wrong: users expecting analysis of edited summaries must use
their original CLI; the GUI deliberately analyzes source run artifacts.

No scientific phase change: root CURRENT_PHASE/DECISIONS/ROADMAP stay unchanged.
The user's dashboard plan/location and no-commit instruction override skill
worktree/commit defaults. Runtime evidence, snapshots and audit state stay ignored.
