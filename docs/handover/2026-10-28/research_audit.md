# MRTA research backend audit evidence — 2026-10-28

Audit workspace: `/home/ibrahim/rnd_mrta_ilp_research` (`R` below). Historical output workspace: `/home/ibrahim/rnd_mrta_ilp_ws` (`W` below). This is a read-only source/output audit for a documentation freeze. No source/test changes, commits, long experiments, services, or output overwrites were made by this auditor. Focused tests created fresh temporary outputs under `/tmp/mrta-research-audit-tests`.

## Most important findings

1. M0 is committed at `163c6a473aa1673176243a8a03a3bde5b0b8540b`, branch `research/outcome-aware-ilp`. M1 code is untracked/pre-existing working-tree material. Symbolic ILP is not implemented. The `milp` strategy is mixed-integer linear programming, not inductive logic programming.
2. Current focused verification: **57 passed, 1 failed in 28.06 s**, 58 collected cases, on M0+M1. `test_the_probe_records_what_run_workload_records` fails while reporting features because a merge produces `route_cells_x`/`route_cells_y`, but partial correlation requests `route_cells`. This is a current functional blocker, not merely a missing scientific evaluation. Root is independently running the full suite with its full log at `/tmp/mrta_research_tests_20261028.log`.
3. The five issues in `.scratch/m1-review/2026-10-28-review.md` have corresponding fixes and regression cases in the current untracked code, written after that review. The review's old **50 passed** result is not current acceptance evidence. Existing pilots predate these fixes and use the old names/semantics.
4. Recorded M1 signal: one workload seed, 101, 300 orders; six configurations only. Eight run datasets include a repeated rollout baseline and an interrupted duplicate. There is no recorded M1 grid experiment or trained policy. The proposed 144-run matrix is pending.
5. README's recent M1 claims and overview figure overstate the evidence. Residual greater than a static decision margin is a necessary condition for a reversal, not an observed reversal. No held-out predictive performance, learning, or policy benefit is established.

## Worktree and preservation state

`git worktree list --porcelain`:

- `W`: `traffic/rmf-integration`, HEAD `ae832f5d445b84683050365b2a239d950ca4111d` (GUI work).
- `R`: `research/outcome-aware-ilp`, HEAD `163c6a473aa1673176243a8a03a3bde5b0b8540b`.

Pre-existing research tracked modifications at audit start: `README.md`, `docs/CURRENT_PHASE.md`, `docs/DECISIONS.md`, `docs/ROADMAP.md`, `src/simulation_env/simulation_env/seed_split.py`.

Pre-existing untracked paths: `.scratch/m1-outcome-signal/`, `.scratch/m1-review/`, `docs/figures/m1_outcome_signal_and_scaling.png`, `scripts/`, `src/simulation_env/scripts/m1_probe.py`, `src/simulation_env/scripts/outcome_signal.py`, `src/simulation_env/simulation_env/congestion.py`, `src/simulation_env/simulation_env/outcome_signal.py`, `src/simulation_env/simulation_env/rollout.py`, `src/simulation_env/test/test_m1_diagnostics.py`.

Source mtimes (local Berlin, +0100) demonstrate fixes existed before this audit:

| File | mtime 2026-10-28 |
|---|---|
| `.scratch/m1-review/2026-10-28-review.md` | 13:17:22 |
| `simulation_env/rollout.py` | 13:28:20 |
| `simulation_env/seed_split.py` | 13:28:28 |
| `scripts/m1_probe.py` | 13:28:51 |
| `scripts/outcome_signal.py` | 13:28:59 |
| `test/test_m1_diagnostics.py` | 13:29:04 |
| `simulation_env/outcome_signal.py` | 13:29:17 |

`simulation_env/congestion.py` mtime is 2026-10-27 23:23:31.

Relevant history: `578c668` V2/B0-B3 checkpoint; `5d61c84` B4; `d6d67c1` B5; `503ca90` W100/S0/dataset/experiment checkpoint; `e7c6549` simulation pipeline/order/fleet limits; `5abf5c2` B6a and paired workloads; `e6d2f25` traffic demo retarget; `aaf0ab9` Popper toolchain; `aa77ee0` strategies/traffic/experiment framework; `badbc59` GUI checkpoint; `163c6a4` M0; sibling branch `ae832f5` GUI delivery.

`runs/`, `Mrta_experiments/`, `.pixi/`, build/install/log, and caches are ignored by Git. A source-only archive does not preserve local runs or the environment. Dirty manifests record a boolean, not a patch or source-content hash; preserving the working tree itself matters.

## Full source coverage assigned to this auditor

Read in full: `src/task_management/task_management/allocation_strategy.py` (1066 lines), `features.py` (347); `src/simulation_env/scripts/run_workload.py` (459), `run_experiment.py` (489), `compare_paired.py` (303), `m1_probe.py` (293), `outcome_signal.py` (151); `src/simulation_env/simulation_env/dataset.py` (452), `dataset_export.py` (302), `paired.py` (271), `failures.py` (133), `seed_split.py` (119), `congestion.py` (259), `outcome_signal.py` (378), `rollout.py` (386); `src/simulation_env/test/test_m0_integrity.py` (793), `test_m1_diagnostics.py` (528); `scripts/generate_overview_plots.py` (128); `generate_orders.py`, `pixi.toml`, config YAML, and `.scratch` issues/spec/review. Read AGENTS.md/CLAUDE.md contracts, full experiment protocol, current M0/M1/M2 milestone/decisions, strategy definitions, dataset/export and historic result sections. Broader legacy documentary coverage is shared with root audit.

## Frozen scientific boundary and current configuration

Contracts: `AGENTS.md`, `README.md`, `docs/CURRENT_PHASE.md`, `docs/DECISIONS.md`, `docs/ROADMAP.md`, `docs/EXPERIMENT_PROTOCOL.md`.

- Research variable is allocation only. Warehouse, one-way directed A*, traffic arbitration, speeds, service, battery, charging, and Gazebo mirror are fixed across policies.
- Order CSV: `order_id,item_id,shelf_id,quantity,dropoff_id`; no priority/release/deadline/battery fields.
- `src/simulation_env/config/warehouse.yaml`: W100, 1 m/cell, 100 robots by default, spread spawn, 1.5 m/s, 1 s per shelf visit, 1 s dropoff, 2000 reference orders seed42, `window=fleet_size`, **config quantum=0**, strategy seed42. Primary learning/evaluation explicitly overrides `--quantum 3`; secondary `--quantum 0`.
- W100: 40×54, 552 shelves, 36 dropoffs, 100 parking cells, 24 chargers, 12 holding cells. V2 fixture: 17×35, 72 shelves, 36 dropoffs, 22 parking, 8 chargers, 14 holding; use explicit V2 config for frozen regression numbers.
- Battery: initial100%, travel1%/20m, service1%/60s, idle/wait1%/300s, charge+1%/s, reserve10%, due<=10%. Eligibility estimates order travel+services+directed charger leg+reserve, excludes future waiting.
- All candidate scoring uses exhaustive sorted shelf permutations (1–3 shelves, <=6 routes). `features.py:72` includes all station retreats in C_static. Unreachable permutations are skipped; no legal route => infinity/planNone. Shared eligibility is reachability AND battery eligibility (`features.py:223`).

## Eight exact implemented strategy formulations

Source: `R/src/task_management/task_management/allocation_strategy.py`.

Let `C(r,o)` be the chosen whole-order static directed route in metres, `F(r,o)=C/v_r + shelf_visits*s_pick + s_drop`, and `p(o)` the original FIFO position. All policies receive the same available robots, offered FIFO candidate window, routes, timing, and eligibility mask. All returned costs remain C_static whatever was used for ranking. Selected assignments are reported in FIFO order.

1. **nearest_astar** (`:272`): sort eligible edges `(C, p, robot_id)`, then greedy take an edge if neither robot nor order was already taken.
2. **nearest_manhattan** (`:336`): minimum Manhattan chain over shelf permutations from robot through shelves to dropoff (ignores walls/lane direction and does not duplicate A* retreat accounting); sort `(Manhattan, C, p, robot_id)`, same greedy sweep. Reachability and battery still use A*; execution follows the A* candidate route.
3. **random** (`:424`): canonically sort eligible edges `(robot_id,order_id)`, `random.Random(seed).shuffle`, then same greedy sweep. `reset()` resets RNG. This is a shuffled-edge greedy matching, not a guarantee of uniformly sampling all feasible matchings or of maximum cardinality under a sparse mask.
4. **earliest_finish** (`:536`, `:548`): greedy sort `(F, C, p, robot_id)`. All offered robots are free now; no wait/traffic estimate enters.
5. **multi_criteria** (`:605`, `:624`, `:669`): sum `0.5*N(F)+0.2*N(p)+0.2*N(-energy_margin)+0.1*N(charger_distance)`, where each N is min-max over this decision's eligible pairs, all-equal=>0. Without all battery features, drop both battery terms and rescale remaining weights to original total. Sort score then `(F,C,p,robot_id)`, greedy. Weights are untuned fixed heuristics, configurable constructor parameters and recorded in identity. FIFO term is position/age, not a measured queue-delay duration; no congestion criterion exists.
6. **auction** (`:738`, `:770`): sequential single-item auction. Announce eligible orders oldest-first; among still-free eligible robots choose `(C,robot_id)` minimum. No opportunity cost, bundle auction, re-auction, or queued multi-order marginal insertion. With an empty robot workload marginal route cost is exactly C. A round-based cheapest-global-bid variant would collapse to nearest_astar and is deliberately absent.
7. **hungarian** (`:816`, `:834`, `:856`): maximum-cardinality minimum-sum C matching, solved by SciPy `linear_sum_assignment` (Jonker–Volgenant implementation). Canonical robot-id rows/FIFO columns. Ineligible entries cost `(max_weight+1)*(min(n_r,n_o)+1)`; solver pairs through them only if needed, and they are removed. Optimizes one current decision, not the full workload.
8. **milp** (`:904`, `:1014`): maximum feasible matching size K obtained from a min-total-F feasible matching. That matching gives an exact upper bound on latest finish; prune edges above bound+tolerance. Binary x for eligible pairs, continuous z>=0. Each robot/order sum x<=1; sum x=K; each robot/order sum F*x<=z. Stage1 minimize z; Stage2 minimize sum F*x subject to original constraints and z<=z*+`1e-6*max(1,z*)`. SciPy `milp`, HiGHS; no congestion constraints, future outcomes, or battery optimization beyond shared eligibility. Failed stages raise AllocationSolverError, no fallback. Tied optima resolve by solver/build; solver/SciPy version part of identity.

The first six are greedy/maximal algorithms; Hungarian and MILP maximize feasible cardinality. With one free robot Hungarian largely agrees with nearest_astar and MILP with earliest_finish. Distinctness evidence in CURRENT_PHASE (historical controlled seed42 W10030r300o): at q0 A*/Hungarian 0.4/0.8% disagreement and EF/MILP 2.3/4.9%; q3 every pair >5%, minimum EF vs A*8.1/10.4%. This is behavior distinctness, not superior performance.

## Dataset, provenance, and ILP readiness

- `dataset.py:147`: **39 decision columns**, one row per scored pair, including unselected/ineligible rows; `:208`: **22 outcome columns**, one completed order each; dataset schema1. Exact rationals stored as floats plus exact strings for time/SOC where specified.
- Row join is `(run_id,decision_id,robot_id,order_id)` conceptually. M0 stores original deciding ID in executor before deferral; outcome launch time can be later than decision time. Outcomes measure mission from launch, so deferral delay is separate.
- Only selected pairs have outcomes. `success` is true on every written outcome in a successful run; it does not supply negative/failure examples. Failed runs do not normally write their partial tables. Fleet-level failures are in failure.json.
- `dataset_export.py:215` emits raw `bk.pl` and pos/neg `assign(dN,robot,order)` examples. Pos=selected eligible, neg=unselected eligible; ineligible pairs are not negative training examples. This is teacher imitation supervision, not outcome-aware supervision. Numeric raw facts, no threshold discretization, mode declarations, bias, learner execution, or rule compilation.
- Prolog `d0`, `robot_001`, order IDs repeat between runs and exporter does not scope them by run. Do not concatenate raw multiple-run exports as a training dataset. M2 must add run-scoped atoms, predicates, bias, sampling, offline teacher relabelling, and experiment-purpose filtering.
- `build_manifest` records config/map/orders hashes, commit+dirty+branch, effective window/quantum/seed/speed/services, battery configuration, strategy identity+hash; run_id is deterministic SHA256 prefix16, excluding only order_limit. It does not capture uncommitted patch contents. A changed dirty tree can have same commit/boolean provenance.
- Per-order actual distance/travel/wait/service/energy come from robot counter differences. `yield_events_during` and `charge_sessions_before` are fleet counters and must not be used as per-order labels (F10). Minimum per-order SOC, event-based intervening charging, next physical availability and external waiting caused remain missing or require explicit design.
- Current congestion observer is separate sidecar features.parquet, not dataset-schema columns or policy inputs.

## M0 failures, runner, paired statistics

`failures.py:65`: DeadlockError=>traffic_deadlock; WorkloadStalledError=>workload_stall; InvalidAssignmentError=>invalid_assignment; IneligibleAssignmentError=>eligibility_violation; AllocationSolverError=>solver_failure; ChargingSaturationError=>charging_saturation; everything else=>execution_error. Only first two print STALLED. Other failures print FAILED; execution_error prints traceback. failure.json records kind, stalled, exception, message, sim exact/float time, total/completed/pending/inflight/deferred counts and strategy/run identity.

`run_experiment.py`: seeded mode one generated workload per seed, default2000 orders, shared across every strategy and fleet. Random also receives that seed as RNG seed. `--orders` chooses first N, distinct from workload file size. Reference mode runs deterministic policies only at first seed and random at all seeds; cannot be paired by current comparer. Defaults are only three baseline strategies, robots30/40/50, seeds1/2/3; explicitly name all eight and primary fleet set for a full campaign.

`--resume` checks full spec equality (strategies, robots, seeds, orders, workloads, workload_orders, quantum, window, purpose, strategy hashes); existing output without experiment.json refused. `--force` renames old folder to `.replaced-UTC`, does not delete it. One child process per run, ThreadPoolExecutor jobs, full run.log per run. successful manifest+metrics => finished. summary row adds workload/settings/strategy hashes and run_dir.

`paired.py`: E1 completeness exact strategy×robots×seed grid; one common settings signature with permitted varying fields removed; one identity per strategy; pair order hash/orders/line_items/window_width equal. Difference is strategy-baseline. Mean difference, t95% CI, percent of baseline. No CI with n<2. Current metrics: order_makespan_s, order_throughput_per_h, mean_completion_time_s, productive_distance_m, productive_waiting_s, total_distance_m, energy_consumed_percent. No Holm correction, Wilcoxon, dz, explicit t-test p values, median differences, or grouped failure-count statistics yet (F9).

`compare_paired.py`: strict refusal by default; `--allow-incomplete` writes prominent INCOMPLETE banner, missing run kinds, extra rows excluded, per-comparison expected/actual paired n, and expected_n/complete CSV columns. It still rejects mismatched settings/strategy identities. Pre-M0 summaries lack strategy_sha256 and are refused. Historical reference fleet_sweep also lacks experiment.json and is not a current paired study.

Source-level limitations not separately reproduced in this audit:

- Reusing a previously successful `run_workload --dataset DIR` is unsafe if the new attempt fails: code clears only old failure.json at start, not old run.json/metrics/tables. A new failure can coexist with stale successful files; `run_experiment.finished()` checks only run.json+metrics existence. Normal fresh-run tests prove no new tables/results are written on failure, not that stale files are removed. Use fresh dataset directories.
- Failure classification wraps `executor.run()`, not prior config/fleet/setup errors or later recorder/metrics/export writes. Do not claim every CLI/process error necessarily creates failure.json.
- Resume spec does not include full config/map/code content; changed effective settings across missing/resumed runs are caught later by settings signatures, rather than guaranteed refused before rerun. Dirty changes aren't differentiated by settings signature.

## Seed roles and leakage limits

`seed_split.py`: training101–110; validation111–115; evaluation1–30; final201–230; other seeds development (42 reference). `check_purpose` refuses a mismatched explicit role; no purpose refuses final only, allowing evaluation/training seeds in development. `run_experiment --purpose` records role in experiment.json, not every run.json.

M1 `sealed_inputs` refuses **both** evaluation and final roles, checking effective strategy seed from config/CLI and workload basename `orders_sN.csv`. Current probe records workload origin (`generated`, `orders_file`, `config`), path/hash/inferred seed separately from strategy_seed. Unknown/renamed/custom files still have unknown workload seed; filenames are not verified provenance. `run_workload` itself has no purpose/seed guard. Do not infer safe workload role just from a safe strategy RNG seed.

DECISIONS at1618 initially says final proposed, then explicitly records adoption by researcher on2026-10-27; protocol still calls it proposed. Seeds1–30 have been inspected during development; their continuing role is open. Final201–230 must remain sealed until frozen evaluation. No final-seed data was read by this auditor.

## M1 current code versus historic review/results

Review at `.scratch/m1-review/2026-10-28-review.md` is a historical review of earlier untracked diagnostics, not current source acceptance. Current changes address its five findings:

1. `rollout.py:297 verified(checks)` requires nonempty checks and every identical; m1_probe refuses `--rollouts` with verify<1, records clone_verified, unavailable branches, returns1 if verification absent/fails.
2. m1_probe resolves config strategy_seed and actual workload origin/path before writes, rejects sealed effective inputs. Reports both seeds separately. Workload unknown provenance remains an honest limitation.
3. ForcedDecision snapshots booked waiting at actual decision; horizon_stop captures first tick at/after t+H; result renamed **waiting_in_h_s**, replacing waiting_added_s. It counts booked waiting only; unfinished waits at horizon aren't booked, and first tick can overshoot t+H by branch-dependent event timing. Treat it as an approximate/booked horizon metric, not exact event-integrated same-instant waiting.
4. SOC increase called **soc_rose_before_next**, explicitly proxy; final orders censored NaN and excluded by mean. Cannot detect every charge; event ground truth absent.
5. Ratio-normalization claim replaced with exploratory rank correlation and **partial Spearman adjusting est_finish_s**; no claim of held-out predictive value.

Current failure: feature_associations merges per_order rows (already include route_cells) with feature sidecar (also route_cells), default suffixes remove unsuffixed name. associations silently skips missing column, but new partial_spearman calls frame[[x,y,z]], raises KeyError. Exact lines `outcome_signal.py:368-376`, inner `:342`. Regression test line376 asserts successful CLI report and fails. Features probe itself writes valid tables and feature sidecar; reporter over them fails. No production fix made.

Focused verification command actually executed:

```bash
cd /home/ibrahim/rnd_mrta_ilp_research
PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management pixi run python -m pytest \
  src/simulation_env/test/test_m0_integrity.py \
  src/simulation_env/test/test_m1_diagnostics.py -q -p no:cacheprovider \
  --basetemp=/tmp/mrta-research-audit-tests
```

Result57passed/1failed28.06s. M0 has38cases, M1 now20 (old docs say12). Synthetic partial association test passes, end-to-end report catches actual merge failure. Root's full-suite result supersedes any historical claim600/612.

## M1 recorded artifact inventory (actual filesystem)

`R/runs`: **72 files**, **30,368,421 bytes**. Only top-level `m1_pilot`. No experiment.json, paired.csv/md, or failure.json. Counts:8 decisions.parquet,8 outcomes.parquet,8 run.json,8 metrics.json,8 orders_s101.csv,7 probe.json,6 features.parquet,1 rollouts.csv,3 per_order.csv,3 summary.csv,3 summary.md,9 log files.

- Six feature probes: `m1_pilot/r30_q0`, `r30_q3`, `r70_q0`, `r70_q3`, `r100_q0`, `r100_q3`.
- One completed rollout: `m1_pilot/rollout_r70_q3`,32 branches/8 decisions, verify1.
- One `rollout_r70_q3.interrupted` folder contains complete base run metrics+tables but **no probe.json or rollouts.csv**: incomplete diagnostic job, not an additional outcome experiment. Same baseline values as completed70q3.
- Analysis outputs `existing_signal` (18 historical datasets from W), `signal` (6 feature probes), `rollout_signal` (one completed rollout baseline).
- Logs `pilot.log`, `existing_signal.log`, `r30_q0.log`, `r30_q3.log`, `r70_q0.log`, `r70_q3.log`, `r100_q0.log`, `r100_q3.log`, `rollout_r70_q3.log`. Pilot log has six exit0 entries plus historical report exit0.

All8 manifests: nearest_astar, seed101,300orders, schema1/Parquet, metrics schema2, dirty=true, commit163c6a4. All8 tables verified:300 selected eligible pairs,300 outcomes, one-to-one selected/outcome key join, success=true on all, **max absolute realized-predicted distance error0**,0reservation violations,0plan deferrals,0yield events,0charge sessions. Thus these pilots do not test charging-driven learning.

| Probe | decisions | scored rows | order makespan s | fleet settled s | productive distance m | total distance m | productive waiting s | total waiting s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| r30_q0 |249|8567|898.333333|920.333333|35561|36300|206|210.333333|
| r30_q3 |137|8588|909.666667|931.666667|34970|35712|488.666667|492.666667|
| r70_q0 |200|18596|424.333333|459|34324|36857|613|649.333333|
| r70_q3 |73|18687|448|481.333333|34034|36543|1399.666667|1485|
| r100_q0 |168|25063|329|401.666667|33758|38456|1394.333333|1604.666667|
| r100_q3 |57|25215|391.666667|461.333333|33669|38300|7345|7404.333333|

`signal/summary.csv` records old diagnostic summaries (residual math remains same):

| Probe | single robot share | mean eligible pairs | residual mission share | residual p90 s | median nonnegative margin s | residual>margin share |
|---|---:|---:|---:|---:|---:|---:|
| r30_q0 |0.911647|34.405622|0.008300|2.666667|8.5|0.064000|
| r30_q3 |0.313869|62.686131|0.019777|5.033333|9.666667|0.080460|
| r70_q0 |0.870000|92.980000|0.025120|5.333333|3|0.266667|
| r70_q3 |0.136986|255.986301|0.055996|12|3.333333|0.368852|
| r100_q0 |0.833333|149.184524|0.056208|11.4|2.333333|0.428571|
| r100_q3 |0.070175|442.368421|0.239266|96.633333|1.666667|0.586777|

Waiting accounts for all residuals (100%, float tolerance); travel and service exactly estimated. Exceedance denominator is selected orders with a nonnegative margin and an alternative, **not all decisions**. A 300-order100robot run averages only3orders/robot, no steady-state claim.

Congestion feature associations in spec: |rho|<=0.27, weak and lower at high fleet. Load/run-phase proxies matter. SOC starts100 and drains so SOC/wait association may proxy time, not energy causation. No validation-seed predictive analysis exists.

## Recorded rollout details and interpretation

`rollout_r70_q3/probe.json`: args70robots,300orders,workload101,q3,rollouts8,alternatives3,H120s,verify1; sampled decision IDs5,14,23,32,41,50,59,68.32rows. Clone check tick116 identical=true,18687decisionrows/300outcomes. Base run11.4038s wall, rollout124.1842s, mean branch2.8039s.

Old rollouts.csv uses **waiting_added_s**, not current **waiting_in_h_s**. Old probe.json lacks workload origin, strategy_seed, clone_verified, unavailable count. Old per_order.csv uses **charged_before_next** rather than current censored proxy. Old summaries lack partial-Spearman columns. Preserve as historical diagnostic outputs, do not silently relabel old columns as fixed semantics.

Recomputed directly from old32rows (teacher gain uses min over teacher+alternatives):

| metric | mean branch spread | spread/teacher mean | mean best gain over teacher | improvement above margin |
|---|---:|---:|---:|---:|
| window_flow_s |1.254533s|0.690526%|0.438004s|1/8 above1s|
| pivot_mission_s |14.25s|17.645748%|1.375s|2/8 above2s|
| waiting_added_s |68.25s|10.385308%|39.166667s|3/8 above30s|

Window-flow any flip6/8=75%; only1/8 >1s,0/8 >5s. Outcome-vs-estimate share0.75 is also on window flow. **68seconds is mean spread, not mean waiting reduction.** Its old waiting interval differs by branch and starts before decision; not a validated common-horizon result.

Decision5 teacher estimate54s, realized pivot63.6667s, waiting_added1416.3333s, windowflow193.042857s, stop438s. alt3 estimate56.6667s, realizedpivot56.6667s, waiting_added1247s, windowflow191.685714s, stop434s. Shows one counterfactual can reverse the pair estimate; does not prove general learnability or policy improvement.

## Historical output locations and incompatibilities

`W/runs`: **168 files**, **1,132,103,503 bytes**,20manifests,17metrics,19Parquet decision/outcome pairs,18CSV pairs,17bk/exs pairs,15run.log,1summary pair,5figures,1report.txt.

- `astar`: V2 nearest_astar22robots100outcomes,1973decisionrows,seed42,q0; no metrics.json (old schema). CSV/Parquet+bk/exs. Dirty d6d67c1.
- `batched`: V2 nearest_astar22robots100outcomes,1983rows,q3; no metrics. Dirty d6d67c1.
- `legacy`: V2 nearest_astar22robots100outcomes,578rows,q0 legacywindow; no metrics. Dirty d6d67c1.
- `fleet_sweep`:15complete runs =3A*+3Manhattan+9random (robots30/40/50, reference2000orders). Directory seeds s1/s2/s3 and summary seed values are strategy seeds, but old run.json records config seed42 for all; old provenance defect later fixed. No experiment.json, no workload generation/index, no paired output; old reference experiment, not a pairedseed study. Every run has run.log, metrics and CSV/Parquet/bk/exs.
- `fleet_sweep/figures`: battery.png,completion_curves.png,mrta.png,order_times.png,traffic.png. Old makespan field is fleet settled. Historical summary remains useful as recorded evidence, not current E1-compatible campaign.
- `gazebo_10_50`:10robots50orders,455decisionrows, CSV fallback+metrics+report.txt, dirty503ca90; old metrics (no order-makespan split), fleet settled449.333333s,distance5680m,waiting7.333333s.
- `learn_astar_30r_300o`:30robots300orders,seed42,q0,8566decisionrows, cleanbe596c3, metrics schema2: order873.333333s,fleet888s,productive34345m,total35134m,productivewait237s,total244.666667s. **Name does not mean learning occurred**; it is one exported teacher dataset,300positive/8266negative examples, no rules/bias/training outputs.

Historical fleet_sweep nearest_astar rows:30r fleet6157.079967s,distance245102m,wait1702.821704s;40r4646.207387s,243614m,2290.075706s;50r3733.350135s,243477m,2878.306960s. Values lack schema2 productive/recovery split; don't compare as current order_makespan.

`R/runs/m1_pilot/existing_signal` analyzes18oldruns: astar,batched,15fleet_sweep,learn_astar; deliberately not legacy or Gazebo. Its successful old report log lists those inputs. Original fleet_sweep current manifest seeds42 are why the M1 sealedseed guard treats these as development rather than blindly using folder labels.

## Overview figure and README discrepancies

`R/scripts/generate_overview_plots.py` is untracked; full source hardcodes all numeric arrays and absolute R output path. It reads **no datasets/manifests** and has no declared evidence/version metadata.

- PanelA hardcoded makespans2860.8,1463.4,931.3,616.7,483.3,416.7 and waits105.9,219.7,380.7,687.3,1910.7,3399.3 for10/20/30/50/70/100. They match historic W100 development tables in CURRENT_PHASE1462ff/START_HERE, predating B6a; plot labels them **Order Makespan** even though old makespan was **fleet settled**. No original current run artifacts located for that exact sweep in either runs tree.
- PanelB hardcoded total distances A*36368/37416,Manhattan36962/39106,Random37612/41404. A source comment says Productive Distance but axes Total Driven. They are historical single-workload development data, not means/statistically established minima or current M1 runs.
- PanelC combines sixpilot p90residuals and nonnegative margin medians, dissimilar distributions; useful descriptive graphic, not a test of ranking reversal.
- PanelD uses6.4,8.0,26.7,36.9,42.9,58.7% exceedance and labels static ranking inversion/all decisions. Actual quantity is selected-order residual>margin under restricted denominator, alternative outcomes unobserved in these runs.
- README recent experiments says70–100q3waiting23.9–42%: actual pooled70q3=5.5996%,100q3=23.9266%;42% appears only as phase/quartile claim in spec. No overall42%70q3 result.
- README claims75% pivot/wait improvement; reported0.75is windowflow flipshare. README mean68s reduction is actually spread68.25s; old average best waiting gain39.17s with invalid historical interval.
- README assertion genuine learnable supervision is unsupported: no held-out predictor/learner or outcome-aware policy has been evaluated.

Other staleness: README quickstart says default22robots100orders/frozen268.6667s but default config isW100/100robots2000orders. Explicit V2 fixture required. README says317tests; current source larger and one failure. CURRENT_PHASE historical early text says lane policy hardcoded/not committed/no battery, superseded later. Top congestion-not-implemented contradicts diagnostic observer, though not in policies/schema. M1 table says12tests, now20. Protocol still calls finalset proposed though later decision adopts it. Preserve historical sections but clearly mark current top status and chronology.

## Popper toolchain verification and ILP status

Verified installed environment read-only with `env -u PYTHONPATH pixi run python ...`, `pixi run swipl --version`, `pixi run clingo --version`:

- Python3.12.13, NumPy2.4.6,pandas3.0.3,SciPy1.17.1,PyArrow25.0.0.
- Popper git v4.4.0, full commit **860cd0deabf20f02934b3dbe147444e5f1207214** (direct_url.json); package metadata incorrectly says4.2.0.
- SWI-Prolog10.0.0,clingo5.8.2,janus-swi1.5.3,python-sat1.9.dev17,bitarray3.12.3,setuptools80.10.2 capped<81 because Popper imports pkg_resources.
- `pixi.toml` pins gitv4.4.0,Python>=3.12<3.13,clingo>=5.8.2<6,setuptools<81,janus>=1.5.3<2.
- CURRENT_PHASE2257ff records prior isolated smoke tests: grandparent rule4/0/5/0; assign-shaped nearest rule6/0/30/0; noisyMaxSAT same. One learning problem per Python process: second learn_solution caused neg_factKeyError. Import smoke checked now; those prior learning problems not rerun/newly reproduced in this audit.
- No learner integration, learned-rule artifact, rule-executing allocator, M2data layer, fidelity/coverage results, explanation trace, or outcome-derived label campaign exists. `LearnedStub` only a test fixture for identity, not implemented research policy.
- Old ICAPS talk results belong to earlier/different workspace and cannot be represented as results from this repository.

## Current milestone plan, scientific acceptance, and immediate next steps

ROADMAP timeline frozen2026-10-27, intended target20December2026:

| Milestone | Dates | Status at freeze |
|---|---|---|
| M0 integrity/reproducibility |Oct27–30|committed163c6a4; focusedM0passing|
| M1 outcome/congestion signal; F9/F10/F12 scope |Oct30–Nov2|diagnostics and one-seedpilot exist; current feature report failure; matrixpending; scientificacceptancepending|
| M2 learner-side predicates/run-scoped atoms/sampling/bias/offline relabel |Nov2–7|planned/design-level only|
| M3 nearest_astar reproduction via Popper, compile rules to Python |Nov7–12|notimplemented|
| M4 Single-Teacher ILP |Nov12–17|notimplemented|
| M5 Multi-Teacher ILP |Nov17–22|notimplemented|
| M6 Outcome-Aware ILP, supervision frozen first |Nov22–Dec2|notimplemented|
| M7 explanations |Dec1–4|notimplemented|
| M8 final campaign q3primary/q0secondary |Dec4–10|notrun|
| M9 report |Dec8–20|planned|

M1 matrix in CURRENT_PHASE2534ff: sixstrategies(A*,EF,MC,Hungarian,MILP,random)×fourfleets30/50/70/100×seeds101/102/103×twoquantums=144runs,1000orders. Featureprobes4×2×3=24, rolloutprobes70/100q3,30decisions each,H120,verify2. Matrix intentionally omits Manhattan/auction for this diagnostic subset. Full conventional campaign must explicitly include all8.

Before declaring M1 accepted: fix reporter safely in future authorized source work; rerun regression; regenerate diagnostics in new directories with new schema; bounded pilot then intended sustained-load matrix; freeze statistically defensible signal/feature interpretation. F9stats,F10attribution,F12spatialworkloads remain. Held-out transfermap and G6 end-of-workpartialcharge are deferredrequirements, not cancelled. B6restmetrics notcomplete.

Recommended supervision in `.scratch/m1-outcome-signal/spec.md:252ff`, **proposal only**: clone state, evaluate teacher action, other teacher actions, top-k alternatives, same downstream policy; score J=pivot realizedmission+lambda*fleetwaiting overH; choose clearly better action if gain>delta else teacher; positivebest, negativesonlyevaluatedclearlyworse, neverlabelunevaluatedpairs. H60–120/lambda/delta to be selected/frozen onvalidation111–115; trainingonly101–110, highload70/100q3primary/q0secondary. Current code only top-k same-robotpivot alternatives, not allmulti-teacheractions or finalJlabels. Desired evidence: labeldisagreement, beyondestimate, held-outlearnability, pairedpolicybenefit, shuffledlabelablation.

## Commands suitable for handover

Verified help parsing (all exit0): `pixi run python src/simulation_env/scripts/{m1_probe,outcome_signal,run_experiment,compare_paired}.py --help`. Use research worktree for M0/M1 backend. `run_experiment` adds package paths itself; direct `run_workload` needs PYTHONPATH.

Small demo, no longcampaign, explicit V2/freshoutput (the focused parity test actually exercised the same30orderq3 path):

```bash
cd /home/ibrahim/rnd_mrta_ilp_research
export PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management
pixi run python src/simulation_env/scripts/run_workload.py \
  --config src/simulation_env/test/fixtures/v2_reference.yaml \
  --orders 30 --quantum 3 --quiet --progress 0
```

Frozen V2reference100orders (historically reproduced M0; root may rerun now): explicitV2config, each strategy A*/Manhattan/random, seed42, q0. Engine/fleetsettled values268.6667/265.3333/266.6667s,distances5798/5974/6488m; headlineordermakespans245.3333/242/240s.

Small current-format paired experiment example (command validated structurally by focused runner tests, not this exactgrid):

```bash
pixi run python src/simulation_env/scripts/run_experiment.py \
  --name freeze_demo_q3 --out /tmp/mrta_freeze_new_outputs \
  --quantum 3 --purpose training --strategies nearest_astar hungarian \
  --robots 10 --seeds 101 102 --orders 30 --jobs 2
pixi run python src/simulation_env/scripts/compare_paired.py \
  --name freeze_demo_q3 --out /tmp/mrta_freeze_new_outputs
```

Use a new name/parent if alreadyexists; do not overwrite oldresults. This is a tooling demonstration with n2/30orders, not scientificcampaign.

Read-only statistics of existing non-feature rolloutdataset with newreportdirectory can work because it has no features.parquet, but oldwaiting semantics remain historical. **Do not advertise `outcome_signal.py runs/m1_pilot --out ...` as currently working**: it includes sixfeature probes and hits route_cellsbug. Generated featureprobes themselves work; downstreamreportblocked. No workingresearchlearner command exists yet.

M1 primary probe after future bugrepair (not executed here; freshoutputneeded):

```bash
pixi run python src/simulation_env/scripts/m1_probe.py \
  --out runs/m1_next/r70_q3_s101 --robots 70 --orders 1000 \
  --workload-seed 101 --quantum 3 --features --rollouts 30 --verify 2
pixi run python src/simulation_env/scripts/outcome_signal.py \
  runs/m1_next --out runs/m1_next_signal
```

Longmatrix commands remain proposal/researcher-run operations; this audit launched none. Do not touch seeds201–230 for demos/diagnostics.

## Final verification supplement from root

Root independently reports the full research suite: **619 passed, 1 failed**, the same feature-report failure. Full log: `/tmp/mrta_research_tests_20261028.log`. This supersedes historical claims of 600 or 612 passing tests. Root owns documentation updates and the wider 606-file inventory.

Current M1 source SHA256 snapshot (read only, audit end; sources were not edited by this auditor):

```text
736eb2dd62a25a3b9f43828a4b7add1ec246c4e46f20e73bce2af405fc27963b  scripts/m1_probe.py
5da734fa7c0c334ace4cd05cb3f5ba23ca3aed0f16594913dadd7dedcf20e393  scripts/outcome_signal.py
f047c2be027fea4641f91f6567ef5963f901efcd13a7e1d70986e28d89813b16  simulation_env/congestion.py
d62dc76d5d9fc3f10dcb8ddeabfe32e9380591fae22fddc73d544b961f5a0055  simulation_env/outcome_signal.py
d9f356d26f6aee8fb8415b9dc7cd34d6717b4bf8add9b3b7db697c5b736f335d  simulation_env/rollout.py
705c803e7bd8b92bee631ab6b04d42e44d74cf50e8f335d91c7f05091798880b  simulation_env/seed_split.py
dafa774c907d2e9d78c54d5bbcd070f0d0a591955d4eb0240de005b1ec019f4a  test/test_m1_diagnostics.py
```

Paths above are relative to `R/src/simulation_env/`.
