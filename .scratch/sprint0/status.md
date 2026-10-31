# Sprint 0: repository foundation and M2 corrections

Status: ready-for-human

## Preserved

`~/mrta_preservation/2026-10-31_pre_sprint0/`, 185 MB, verified by `sha256sum -c MANIFEST.sha256`.

Contains, per worktree: HEAD, status, binary patch, full copies of modified files, untracked archive. Also a full-history git bundle (all refs and the stash), research `runs/`, and the Phase 0 reports that existed only under `/tmp`. Both patches were applied successfully to clean exports. GUI `runs/` (1.1 GB, ignored) is hashed, not copied.

## Classification

| Class | Items | Action |
|---|---|---|
| Active source, tests, config | `src/`, `pixi.toml`, `pixi.lock`, `src/simulation_env/config` | Keep |
| Scientific evidence | research `runs/`, GUI `runs/`, `docs/handover/2026-10-28/` | Keep; never edit |
| GUI user work (uncommitted) | `my_map.xlsx`, `warehouse.yaml`, `orders.csv`, `warehouse.world`, `warehouse_nav_graph.yaml` | Keep; G2 turns these into a scenario |
| Useful documentation | Authoritative docs, `.scratch/` specs, `docs/PROJECT_HANDOVER.*`, `docs/figures/handover_*` | Commit on approval |
| Historical | `docs/archive/`, `docs/START_HERE.md`, `docs/REPO_ATLAS.html` (paused) | Keep as labelled |
| Removed 2026-10-31 | `26.2.2` in the GUI worktree: empty, created 27 Oct | Deleted with owner approval |
| Removed 2026-10-31 | stash `33a0a3ff` "recovered battery work before rebase" | Dropped with owner approval. Its 13 images are in HEAD and its unmatched code lines are the same code reflowed. Kept in `all_refs.bundle` as `refs/stash` |

Apart from these two, no source, evidence or user asset was moved or deleted.

## Baseline (independent QA, clean exports)

Full report: `.scratch/sprint0/baseline_2026-10-31.md`.

| Check | Result |
|---|---|
| Research `bf5c8d7` | 646 passed, 1 skipped; 645 + 1 failed without git metadata |
| Ruff `bf5c8d7` | 0 |
| mypy `ilp_learning` | 13 errors: 12 missing stubs or path, 1 real (`str` assigned to `dict[str, Any]`); no config |
| GUI science `ae832f5` | 561 passed + the same no-git failure |
| GUI backend `ae832f5` | 152 passed with root `.pixi`; 23 failed and 19 errors without |
| Frontend `ae832f5` | 22 passed; build and typecheck pass |

Environment facts: ROS-sourced shells abort pytest. The backend hardcodes the root `.pixi` interpreter. The real-dataset test depends on ignored `runs/`, so it skips in CI.

Not verified: fresh install, `npm ci`, either workflow on GitHub, colcon, Gazebo, C++ test.

## Re-test 2026-10-31 (evening)

| Tree | Backend or suite | Frontend |
|---|---|---|
| Research working tree | 813 passed; lint clean | n/a |
| GUI, clean export of `ae832f5` | 152 passed | not rerun |
| GUI, `~/rnd_mrta_ilp_ws` as it stands | 37 failed, 96 passed, 19 errors | 22 passed |
| G2 worktree | 167 passed, with FastAPI from a temporary folder | 26 passed |

All 56 failures in `~/rnd_mrta_ilp_ws` are one error, "Configured map is no longer the allowlisted W100 scenario", raised by `load_scenario` because the uncommitted `warehouse.yaml` there is not the W100 reference config.

## M2 corrections

1. Rank predicates are tie-inclusive and comparisons strict, including Tier B.
2. `best_cost_for_order` added to BK and bias.
3. Matcher refuses missing or blank tie-break data. `est_finish_s` is not recorded in `decisions.parquet`, so the BK builder derives it from the manifest as `DecisionTiming.finish_s` does.
4. Popper defaults are the spec budget (300 s, 0.05 s, 24 literals, noisy), with a hard worker limit and a `timed_out` flag.
5. Validation seeds are sealed from diagnostics. Supervision takes the run manifest and calls `check_run_is_induction`, which fails closed on an unknown workload or an unrecorded strategy seed. Empty seed lists are refused. `run_workload.py` and `run_experiment.py` accept every role through `--purpose`, and a declared purpose needs a workload of known seed. `outcome_signal.py` recognises the reference workload by hash, not by name.
6. Sampling follows spec 5.5: per-decision cap `k_neg * m+`, hard negatives across the matched robot or order, forced pairs excluded, one RNG per decision.

Also: permitted-column firewall replaces the stale blocklist; `selected` and `strategy` are dropped before BK; provenance ticks read `sim_time` (they were all 0.0).

`run_id` is unaffected: `run.json` and `dataset.py` are unchanged, and `--purpose` is not recorded.

## Known deviations and gaps

- `closer_static` stays bounded; see `.scratch/m2-ilp-data-layer/predicate_names.md`.
- `max_literals = 24` is the spec value, but 5 clauses of 4 body literals need 25.
- `fit_thresholds_from_frames` still fills `[0.0]` for an absent column. `fleet_load` and `free_robots` hit this when fed features only. Fix in M3.
- Stratified decision sampling is not implemented (M3).
- `check_run_is_induction` trusts the workload file name; a file deliberately renamed to an induction seed passes.
- `fit_thresholds_from_frames` still trusts its declared `seeds_used`. The M3 pipeline must pass seeds taken from checked manifests.
- `run_experiment.py --purpose` now needs paired workloads, because a purpose run on the reference workload is refused.
- Spec 3.1 asks for `route_cells` equality between tables, but they differ on 2,582/2,582 real rows (2-4 cells; different counts of the same route). No predicate reads it. The spec line is wrong; the code drops the features copy.
- The features merge has no `validate=`, and `features.parquet` has no `run_id`. M3 must load both tables from one run directory.
- M-Card can discard a rule-covered pair to gain cardinality: DECISIONS item 4 and spec 4.4 disagree. Owner decision, part of D1.
- M-Card's nested cost is not strictly lexicographic at the FIFO level against spec `W`. On one real run, 23 multi-robot decisions gave the same served orders; 7 differ in pairing, all with equal total cost.
- GUI `observed_runner.py` pins a source hash that was already stale, and duplicates CLI setup, so it does not get the new seed check. Handle in G1.
- `DECISIONS.md` item 5 says "k_neg = 3 per decision"; spec 5.5 says `k_neg * m+`. They agree for one positive. The code follows the spec.
- The M2 acceptance run has `dirty: true`. It is a smoke fixture, not evidence.

## Proposed commits

Commits 1-7 are made on `research/outcome-aware-ilp`. Held by the owner: commit 7 on the GUI branch and commit 8. The G8 bound at 100 robots, quantum 3 is still open.

Re-split 2026-10-31 to separate M2 from M3. Commits 2-4 were dry-run in a throwaway clone, each with the full suite, without `runs/`:

1. `docs: preserve handover bundle and M1/M2 specs`: `README.md`, `docs/00_RUNBOOK.md`, `docs/START_HERE.md`, `docs/README.md`, `docs/handover/`, `docs/figures/`, `docs/PROJECT_HANDOVER.*`, `.scratch/m1-*`, `.scratch/m2-*`, `scripts/`
2. `fix(seeds): seal validation and enforce roles on direct runs`: `seed_split.py`, `m1_probe.py`, `outcome_signal.py`, `run_workload.py`, `run_experiment.py`, `test_m1_diagnostics.py`. 649 passed, 1 skipped
3. `fix(ilp): correct M2 data layer`: M2 hunks of `matcher.py`, the other changed `src/ilp_learning` modules, `predicates.py` and their tests. 699 passed, 2 skipped
4. `feat(ilp): add M3 learner, iterative executor and ilp strategy`: rest of `matcher.py`, `rule_*.py`, `train_rules.py`, `allocation_strategy.py`, `conftest.py`, `test_predicates.py`, the M3 tests, `docs/DECISIONS.md`, `.scratch/m3-popper-learner/`. 808 passed, 5 skipped; `src/` equals the working tree
5. `ci: add research workflow and pixi tasks`: `.github/workflows/research.yml`, `pixi.toml`
6. `docs: record Sprint 0 status`: `docs/CURRENT_PHASE.md`, `docs/ROADMAP.md`, `.scratch/sprint0/`
7. `chore(agents): run phase-coder on sonnet`: `.claude/agents/phase-coder.md`, on both branches
8. GUI branch: `ci: add GUI workflow`: `.github/workflows/gui.yml`

The `matcher.py` in commit 3 is an intermediate that never existed in the tree; it is tested at that stage. `test_predicates.py` sits in commit 4 because it needs `conftest.py`. Commit 1 adds lint violations in untracked scripts to git; CI lints `src` only, so it stays green.
