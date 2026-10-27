# Scientific integrity and future parity gates

> Stage 0 design only — audited 2026-10-27. No application or telemetry implementation exists from this stage. The user's subsequent layout instruction overrides the supplied plan: all Control Center development belongs under `src/dashboard_GUI/`; these documents belong in its `docs/control_center/` subdirectory. Scientific sources and historical artifacts remain unchanged. Later stages require separate authorization.

## Non-negotiable separation

State_t -> Decision_t -> Outcome. UI is an observer and process launcher. It cannot allocate, change routes, arbitrate traffic, drain batteries, schedule charging, change service duration or advance the Fraction clock. Fixed infrastructure remains shared by every strategy. Identifiers are traceability only; no learned identifier priority. No order priority/release/deadline/battery columns. Gazebo is a later mirror, not authority. No claim of outcome-aware learning before implementation and evaluation.

Decision inspector joins selected rows to outcomes by run_id + decision_id + robot_id + order_id, scoped to the physical run directory. Nonselected candidates have unobserved outcomes, never inferred counterfactual outcomes. Show prediction and realized panels separately. Preserve original table schemas/rows; API joins are presentation views only.

## Existing experiment safeguards and their limits

prepare() refuses nonempty output unless resume/force. Resume requires experiment.json and exact spec equality (including ordered strategy/robot/seed lists, orders, workload mode and workload_orders). Force renames the old directory with a UTC suffix; it is a deliberate operation, not recovery from validation error. Empty directories are allowed. finished() checks only presence of run.json and metrics.json; it does not validate exit code, JSON contents, table completeness or metric success.

write_workloads() regenerates seeded bytes from DEFAULT_CONFIG and checks generated SHA-256 against workloads.json. It then rewrites CSV/index; it does not first verify the existing CSV bytes. A tampered CSV can therefore be replaced rather than diagnosed by that routine. prepare() does not compare current config/git to earlier run settings, and experiment.json gets a resume entry before later workload checks. New and old settings may mix on resume and be refused only at comparison. The dashboard must not overstate these as immutable or transactional guarantees. Before offering resume, read/hash actual existing inputs, validate existing completed artifacts and disclose drift; never silently repair it. Preserve failed-attempt logs/partial files in an app-owned attempt archive before a user-authorized retry, since the original runner opens run.log with mode w. All such behavior is future operational work, not changes to science.

settings_signature() removes VARIES_BY_RUN fields and uses git commit only (dirty flag/branch excluded). Different dirty source contents at the same commit are not distinguished. Record an app-side source fingerprint for provenance, without altering the scientific manifest/signature; same-signature dirty runs are not proof of identical source bytes. A dirty tree is reported, not categorically refused by the research policy.

Comparisons require seeded workloads, experiment.json, complete expected grid, one settings signature, no duplicates and equal orders_sha256/orders/line_items/window_width within each pair. check_complete is set-based; duplicate protection comes later in paired_differences. Reuse compare_paired.load numeric conversion: paired_differences selects float metric pairs. Missing/non-numeric values may shrink per-metric n even with a complete grid; validate and display actual n, expected n and excluded data. Do not turn missing numeric metrics into zero.

Default baseline nearest_astar; differences are strategy minus baseline. 95% paired Student t interval, not bootstrap; n<2 yields null bounds. Percent is mean difference / baseline mean, null for zero baseline. --allow-incomplete is explicit: missing and extra rows listed, extras excluded, per-comparison sample sizes shown. Settings/workload mismatches still refuse. For future GET comparison use read-only functions and reproduce these guard/reporting steps, not the writing CLI main(). Reference-mode and legacy history lacking experiment manifests remain browsable but not validated paired studies. Do not invent an expected grid for historical data.

## Future small-fixture parity matrix (not executed in Stage 0)

| Check | Acceptance |
|---|---|
| Original CLI vs app subprocess | same effective inputs, interpreter and source; same deterministic outputs |
| Original CLI vs optional adapter | same input resolution, chosen assignments, candidate order/labels, outcomes, exact times, final cells/SOC, charging/traffic counts |
| Observer off vs on | same event kind/time/seq order, dispatch sequence, decision log, final counters and deterministic metrics |
| Observer overload/disconnect/error | no science change; bounded queues, explicit telemetry gaps, no exceptions entering simulator |
| Repeat seed | deterministic outputs for stochastic policy in same environment |
| Same-time event fixture | no inserted timers, no event reordering; repeated exact timestamps supported |
| Charge and deferred assignment fixture | authoritative SOC/charge states; no ownership changes; no fake availability before charger exit |
| Decision truncation | decision prefix identical with run stopped after decision vs completed run; no outcome leakage |
| Artifact readers | typed CSV/Parquet equivalence, legacy/missing versions, immutable files and bounded pagination |
| Resume/cancel | explicit incomplete status; no silent success based on filenames; original attempt evidence preserved |

Use tiny controlled fixtures for future observation parity, not full fleet campaigns. Existing test_dataset.py tests recording parity and truncation; they are useful references, not proof of an unbuilt adapter. Existing test_experiment_integrity.py and test_paired_seeds.py cover pairing safeguards. Full topology/traffic/battery gates are unnecessary for Stage 0 because none of that code changes; this stage does not certify the dirty scientific implementation.

Canonicalization: compare parsed records and declared schema, not Parquet container bytes. Preserve row/decision order and every scientific field; rational strings compare as Fraction, never float rounding. Separate run_id/path provenance differences caused by different output/input locations; validate mapped identities and identical input hashes instead of deleting arbitrary fields. Metrics deterministic floats compare under the same environment/serialization; do not add tolerances to hide a difference. Exclude only identified host-dependent fields wall_clock_s, allocator_cpu_s, mission_build_cpu_s, worst_dispatch_s from exact equality and report their overhead separately. Profiling output/slow-dispatch counts can also vary; no exemption for simulated timestamps or counters.

## Stage 0 lightweight checks and evidence

- Both runner --help invocations succeeded (exit 0), using explicit existing pixi Python and PYTHONDONTWRITEBYTECODE=1.
- Registry/config/map/schema import inspection succeeded; W100 40×54 / 100 homes / 4,244 lane edges; 8 registered policies; decision/outcome columns 39/22.
- Read-only historical JSON/header inventory: 20 manifests, 17 metrics (16 v1 + 1 v2), no experiment manifests. No historical files regenerated.
- Focused existing pairing tests and final documentation/preservation validation are recorded in the final verification section below after execution.
- No simulation, ROS/Gazebo launch, server, GUI/backend implementation, install, large test suite, commit or push in Stage 0. Initial unqualified python attempt failed because command absent; explicit interpreters resolved the audit tooling without installing anything.
