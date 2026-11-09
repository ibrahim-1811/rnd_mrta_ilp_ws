# Current Development Phase

## Status — 6 November 2026

Active milestone: **M6 Outcome-Aware ILP**, chosen by the researcher on 2026-11-06. Deadline 2026-12-20.

| Milestone | State |
|---|---|
| M0-M2 | done, committed |
| M3 | built; validated once from tag `m3-evidence-1` with mixed gates ([validation_gates.md](../.scratch/m3-popper-learner/validation_gates.md)) |
| Tier B online | done, committed `56f495f` |
| M4 Single-Teacher | built, committed; exploratory on training seeds only; no gate run; validation seeds 116-120 unused |
| M5 Multi-Teacher | built (majority, plurality), committed `9757d28`; exploratory only; no gate run |
| M6 Outcome-Aware | M6-1, M6-2 and M6-MP built and tested, uncommitted: corpus-aligned rollouts, multi-pair candidates, jw2 objective (validated on 24 decisions), battery predicates, stratified sampling, multi-policy continuation with cross-policy labels, Slurm workflow (not submitted). Cross-policy pilot (2026-11-07): 0 positives at 70 and 100 robots; best candidate agrees across the three continuation policies in 1 of 12 decisions; gate not passed; resolved 2026-11-07: agreement is a sensitivity experiment, primary D is pooled per-teacher outcome learning (`--continuation behaviour`), `nearest_astar` continuation a controlled baseline. Groups A-D pipeline smoke-tested end to end. No outcome-aware rule learned yet |
| M7 explanations | not built; rule trace exists |
| M8 statistics | Friedman, Wilcoxon, Holm not implemented (F9) |
| M9 report | not started |

Next (2026-11-07, DECISIONS.md "Campaign Decisions Before the 300-Order Run"): commit the research version without the GUI work, then the 300-order teacher corpus, single- and multi-teacher training, outcome labels, and a first closed-loop comparison. Cluster-scale training is decided after that.

Two fixes found by the 300-order campaign (2026-11-07, after `551b6bf`; training and rollouts unaffected):

- `fidelity` did not join `features.parquet` and scored clauses with Tier A facts only, so it failed on every learned rule using a Tier B, extended or battery predicate (7 of 8 teachers). It now joins the features as `train_rules` does and uses the program's own `DecisionFacts`. Tier A scores unchanged.
- Popper checks its timeout only between search steps; at a 1200 s budget 5 of 6 runs overran the 60 s grace, were stopped and returned no rules. The worker now reports each new best program, and a stopped run keeps the last (`popper.stopped_by_runner` in the artifact). Validated on the failed nearest_astar case: rules and score identical to its 300 s run. hungarian, which completed at 1200 s, also learned rules identical to 300 s.

Outcome-corrected labels for group D (2026-11-09, uncommitted; DECISIONS.md "Long Workload, Workload Fact and Outcome-Corrected Labels"):

- `supervision/outcome_corrected.py` and `train_rules --outcome-correct`: the teacher's picks of the probed decisions, corrected where its own continuation shows its pair lost by delta. 9 tests; research suite 1058 passed.
- 300-order experiment A, development outputs in the campaign checkout (`runs/teachers/dev/corrected/o300`): all 9 rule sets non-empty (before: 6 of 9 empty), using congestion, battery and load predicates; about 70% of probed decisions corrected.
- Closed loop on validation seeds 116-120 (20 paired runs each): no corrected policy beats its teacher except random (-40.2 s); nearest_astar +8.3 s, multi_criteria +17.3 s, the others within noise. Problem A gate: rules non-empty, but fallback above 50% for auction, hungarian and milp, and no gain.
- Explanation prototype (`runs/teachers/analysis/explain_picks.py` in the campaign checkout): every pick of a run in words, clause re-checked against the pairs left at that round.
- Oracle rollout policy (`ilp_learning.m6_oracle`, 2026-11-09): the teacher drives the run, and at every decision the mp1 candidates are simulated from the identical state and the decisive jw2-best pair is taken; no rules. Upper bound for outcome-aware rules. 3 tests (pause/resume replays exactly; an oracle that decides nothing is the teacher; overrides are the pairs the run then makes). Results: see below.
- Workload vocabulary (`train_rules --workload`, predicate `least_driven_robot(D,R)`): the robot's cumulative distance at the decision, recorded as `robot_distance_m` in features.parquet (new recordings only) and read live by the ilp strategy. 7 tests incl. online/offline parity. Not used in any trained artifact yet.
- ROS fleet node takes `rule_artifact:=` and `rule_matching:=` with `strategy:=ilp` (as run_workload.py); checked headless with 100 robots, 300/300 orders. The ROS Python needs pandas (`sudo apt install python3-pandas`).
- Demo (`scripts/demo_compare.py`, `scripts/demo_deeper.py`): every teacher and rule set once on one unseen workload, with copy-vs-teacher, crossover results, crossover choices, eight question-driven graphs, an optional oracle (`--oracle`) and a battery/workload-length section; one self-contained report.html.
- Explanations (`ilp_learning/explain.py`): every pick of a learned policy in words, clause re-checked against the pairs left at that round; used live by the ROS node (`explain:=N`).
- ROS node: `orders_file:=` and `seed:=` replay a recorded run exactly (checked: identical makespan, distance and waiting); smooth Gazebo playback on its own thread with a pre-roll (`simulation_env/playback.py`, `playback_buffer_s:=`); richer status lines (decisions, distance, traffic waiting, ILP rule/fallback share). Display only: results unchanged.
- Oracle on demo seed 116 (nearest_astar, 100 robots): 431.7 s vs teacher 425.3 s (+6.3 s), 13 overrides: a 120 s window gain does not always shorten the whole run. Next: oracle on validation seeds 116-120 for nearest_astar, multi_criteria and milp, and a whole-run objective at 300 orders.
- Research suite 1074 passed; ruff clean.

### Dashboard control plane — 6 November 2026 (uncommitted)

`src/dashboard_GUI` is now present in this research worktree and aligned with
the current runners. It controls observed/CLI single runs, purpose-aware
experiment matrices, logs, artifacts, metrics and paired-performance views.
It also supervises the fixed W100 `warehouse_sim.launch.py` ROS/Gazebo path
through `simulation_env.fleet_node`; Gazebo remains a mirror and does not add a
planner, allocator or traffic policy. Launch is preview-gated, loopback-only,
single-active-job, and cancellation targets only the owned process group.

Measured acceptance: dashboard backend **173 passed** (one dependency
deprecation warning), frontend **27 passed**, TypeScript/Vite production build
passed, tiny observed/CLI parity had **56 deterministic metric keys equal** with
real HTTP/WebSocket startup and shutdown, full research suite **991 passed in
197.48 s**, and `pixi run lint-src` passed. The ROS packages required by the
mirror build successfully in this checkout and the read-only launcher preflight
finds ROS Jazzy plus `install/setup.bash`. No uncontrolled graphical Gazebo
session or large experiment was launched during acceptance.

| Check, HEAD `9757d28` | Measured 2026-11-06 |
|---|---|
| `pixi run test` | 917 passed in 176.88 s |
| `pixi run lint-src` | all checks passed |

### M6-1: outcome labels and cost profile (2026-11-06, uncommitted)

`ilp_learning/supervision/outcome_aware.py` turns rollout J scores into labels under the frozen contract (H=120 s, lambda=0.5, delta=2 s). The unique best branch is positive if it beats the second best by at least delta. A branch at least delta worse than the best is negative; this includes the teacher's own pair. Everything else abstains, and stalled or unfinished branches are excluded. Only induction runs (104-110) with a verified clone are read. `rollout.py` now also records `pivot_done_s` and `rollout_status` (a stalled branch is recorded, not fatal); J is unchanged. Not yet wired into `train_rules`. Profile: `python -m ilp_learning.m6_profile --out runs/m6_profile`.

| nearest_astar, q3, seed 104, 300 orders, 20 probed decisions | 70 robots | 100 robots |
|---|---|---|
| mean / max wall per branch | 3.71 / 6.51 s | 4.31 / 7.00 s |
| probe wall per branch (pass 1, clone check, copies) | 4.27 s | 5.03 s |
| pos / neg / abstain / excluded branches | 18 / 55 / 4 / 3 | 17 / 50 / 4 / 9 |
| decisions with an example | 19 / 20 | 19 / 20 |
| coverage, probed / choice-bearing | 20 / 70 | 20 / 47 |
| teacher pair labelled negative | 12 / 20 | 13 / 20 |
| labels SHA-256, reproduced | `19f8825b…` | `d8127cf2…` |

Tests: `test_outcome_aware.py` 37 passed; ILP and M1 rollout suites 348 passed; `ruff check src` clean. One seed: not evidence for H1 or H2.

### M6-2: outcome supervision in train_rules, on the M4/M5 corpus states (2026-11-06, uncommitted)

`ilp_learning/m6_rollouts.py` replays each `runs/m4_corpus` run from its manifest and refuses to probe unless the replayed decision table equals the corpus in every column but `run_id`. `train_rules --outcome-rollouts DIR [--outcome-mode conservative|m6_1]` takes every example from rollout labels and writes `outcome_labels.csv` with provenance. bk.pl, bias.pl and thresholds come from the unchanged code path. Label rules: DECISIONS.md, "M6 Outcome Label Rules". Bias and the M6-B/M6-C designs: [.scratch/m6-outcome-aware/design.md](../.scratch/m6-outcome-aware/design.md).

| Smoke, seed 104, 2 probed decisions per fleet size | 70 robots | 100 robots |
|---|---|---|
| replay vs corpus, 38 columns | identical (18,676 rows) | identical |
| clone check | passed | passed |
| conservative: pos / neg / abstain / excluded (censored) | 1 / 3 / 2 / 2 (2) | 1 / 5 / 1 / 1 (1) |
| m6_1: pos / neg / abstain / excluded | 1 / 3 / 2 / 2 | 2 / 5 / 0 / 1 |
| bk.pl, bias.pl, thresholds vs M4 `_eb` and M5 majority | identical | identical |
| learned clauses (1 positive) | 0 | 0 |

Closed loop with the 70-robot artifact, dev seed 42, 60 orders: 60/60 completed, rule_trace 0 rule picks and 60 fallback picks, as an empty program must give. Tests: 976 passed (`pixi run test`), `ruff check src` clean.

Full corpus (the researcher runs it; estimated 4 h on one core from the M6-1 profile, run in parallel):

```bash
export PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management:src/ilp_learning
ls -d runs/m4_corpus/q3/nearest_astar/r{70,100}_s1* | xargs -P 6 -I{} \
  pixi run python -m ilp_learning.m6_rollouts {} --out runs/m6_rollouts/q3
for r in 70 100; do for mode in conservative m6_1; do
  pixi run python -m ilp_learning.train_rules runs/m4_corpus/q3/nearest_astar/r${r}_s1* \
    --out runs/m6_rules/r${r}_eb_${mode} --extended --tier-b \
    --outcome-rollouts runs/m6_rollouts/q3 --outcome-mode $mode
done; done
```

### 2000-order battery check (2026-11-06, exploratory, seed 104, q3)

`runs/m6_orders2000`: nearest_astar with features and teacher panel, then `m6_rollouts --limit 4`. Not training data.

| | 70 robots | 100 robots |
|---|---|---|
| orders completed / makespan | 2000 / 2810 s | 2000 / 2302 s |
| charge sessions / min SOC / charger wait | 91 / 5.7% / 5661 s | 99 / 5.4% / 1439 s |
| battery-ineligible candidate pairs | 12.8% | 11.5% |
| replay identical / clone check | yes / yes | yes / yes |
| mean / max wall per branch | 54 / 94 s | 102 / 166 s |
| conservative pos / neg / abstain / excluded (censored) | 4 / 12 / 0 / 0 | 1 / 6 / 3 / 6 (6) |

At 100 robots, the decision with battery-blocked pairs (decision 328, 100 ineligible pairs) had 3 of 4 branches censored.

Defect found (fixed 2026-11-06, see below): `robots_charging` (`workload_executor.decision_load`) is `len(self.charge)`. Finished trips are never removed from `self.charge`, so it counts robots that have ever charged, not robots charging now. It feeds `busy_ratio` and `queue_pressure`, and so `high_fleet_busy_ratio` and `high_queue_pressure`. It is 0 in every 300-order run, so M3-M6 results so far are unaffected. It must be fixed before any training on runs where charging happens.

### robots_charging fix and multi-pair prototype (2026-11-06, uncommitted)

`decision_load()["robots_charging"]` now counts charge records in `BUSY_STATES` (in the charging workflow, never offered an order) instead of `len(self.charge)`. Read only by the dataset recorder and the ILP strategy's load facts. Replays against recorded runs: 300 orders r70_s104 identical in every decision and outcome column; 2000 orders r70_s104 differs in `robots_charging` only, outcomes identical, and `free + busy + charging = 70` at every decision. The 2000-order run shows a synchronized charging wave: 69 of 70 robots in the workflow at t = 1374 s with 914 orders pending. Recorded 2000-order datasets keep the old value.

Multi-pair arm `mp1` (`ilp_learning/m6_candidates.py`, `rollout.ForcedPair`, `m6_rollouts --arm mp1`): forces any eligible pair, records every window order's completion. Data only: the frozen J is not comparable across orders, so `outcome_aware` refuses mp1 rollouts until an objective is approved. Analysis and proposed J_W: `.scratch/m6-outcome-aware/design.md`, "M6-MP".

Tests: `pixi run test` 991 passed. The dashboard is now the active control-plane
work in this worktree; after mechanical formatting, `pixi run lint-src` is clean
for all of `src`.

### M6-MP: battery vocabulary, sampling, jw1 objective and pilot (2026-11-06, uncommitted)

Built: battery predicates `best_margin_for_order`, `low_energy_margin`, `near_charger_after_order`, `high_charging_pressure` (`train_rules --battery`, off by default, same switch for M4/M5/M6); stratified decision sampling (`ilp_learning/m6_sampling.py`, `m6_rollouts --sample stratified --quota K --shard I/N`); matched training states (`train_rules --decisions-from DIR`); objective jw1 (`supervision/outcome_common.py`); Slurm arrays (`scripts/slurm/`, not submitted).

Pilot `runs/m6mp_pilot`, 70 robots, 2000 orders, seed 104, fixed-code corpus: replay identical, clone verified, 6 sampled decisions (one per stratum + random control), 30 branches, 0 failed or unavailable. Wall time: replay 112 s, rollouts 1,794 s (mean 51.9 s per branch, max 100.6 s). jw1: 6 pos / 20 neg / 0 abstain / 4 duplicate. Completion rule "horizon" (every evaluated order done within 120 s) censors 30 of 30 branches.

**Blocker:** J_W over the fixed set A(d) disagrees with the whole-window completion sum on the best branch in 4 of 6 decisions (Spearman -0.68 to 0.90); orders outside A(d) move by up to 2,706 s. jw1 does not fairly compare candidate assignments. Details: `.scratch/m6-outcome-aware/design.md`, "jw1 pilot result". M4 and M5 on the same 6 decisions with the battery vocabulary: bk.pl, bias.pl, thresholds identical.

Tests: `pixi run test` 1010 passed before the last predicate fix; ILP suite + charging tests 382 passed after it; `ruff check src --exclude src/dashboard_GUI` clean.

### M6-MP continuation policies (2026-11-06, uncommitted, pilot not yet run)

`rollout.ForcedPair` takes a continuation policy: decisions before and at the forced one stay the behaviour policy's (identical forced action for every policy), every later decision is the continuation's. `m6_rollouts --continuations nearest_astar,multi_criteria,auction` rolls each candidate out once per policy on the same clone and the same jw2 window. `outcome_common.cross_policy_labels`: positive only if positive under every policy, negative only if negative under every policy, abstain on any disagreement, excluded if censored or failed under any policy; `cross_policy_report` gives best-branch agreement, Kendall tau and regret between policies. `train_rules` uses cross-policy labels automatically for multi-policy rollouts. Tested on the 30-robot acceptance run: same forced action and window under every policy, no continuation identical to an explicit nearest_astar continuation, a re-run identical. Tests: ILP suite + rollout + charging 415 passed; lint clean. Pilot commands: `scripts/m6_continuation_report.py`, CURRENT_PHASE below.

### Live progress for rollouts (2026-11-06)

`m6_rollouts` prints live status on stdout (flushed): a header, a replay heartbeat every 15 s (simulated time, orders done, robots busy and charging, decisions), the shard's sampled decisions, the clone check, then one line per branch (decision, candidate source, forced pair and swap, continuation policy, status, window orders done, flow, 120 s waiting, wall time, ETA). `--quiet` turns it off. Printing only; results unchanged (continuation and candidate tests: 19 passed).

### Four research groups: strategy-specific and pooled ILP (2026-11-07, uncommitted)

Group B already worked: `train_rules RUNS --teacher X` trains from X's own runs and picks. Added: `ilp_learning/teachers.py` (the eight teachers and the matcher each one's rules run under: M-Card for hungarian and milp, iterative otherwise; `python -m ilp_learning.teachers matching NAME|ARTIFACT`); `fidelity --matching` defaulting to the artifact teacher's matcher (existing artifacts resolve to iterative, unchanged); `train_rules --pooled` for group C (runs of several conventional strategies, each labelled by its own picks, the decision cap shared per strategy, a `pool` block recording each teacher's runs, decisions and examples); Slurm `scripts/slurm/make_teacher_tasks.sh`, `teacher_corpus.sbatch`, `teacher_train.sbatch` (448 corpus runs: 8 strategies x 4 sizes x 7 seeds x 300 and 2,000 orders; not submitted). Single-teacher strata and artifacts are unchanged. Tests: ILP suite 397 passed; lint clean.

Closed-loop evaluation (2026-11-07, uncommitted): `run_experiment.py --rules LABEL=ARTIFACT ...` runs each learned rule set as its own arm `ilp_<label>` beside `--strategies`, on the same paired workloads. Each arm has its own directory, identity hash and `summary.csv` key, so `compare_paired.py` pairs it like a teacher. The matcher comes from `teachers.matching_for` (M-Card for hungarian/milp rules). An artifact whose `source_runs` share a seed with the experiment is refused. `--strategies ilp` is refused. `summary.csv` adds `rule_picks`, `fallback_picks` and `fallback_rate` from each run's `rule_trace.parquet`. Slurm: `scripts/slurm/teacher_eval.sbatch` (one task per fleet size; validation seeds corrected to 116-120 below; not submitted). Tests: `test_learned_arms.py` 11 passed; runner integrity, paired-seed and ILP suites 476 passed in 169 s; `ruff check src --exclude src/dashboard_GUI` clean. No campaign run.

M6 continuation choice and per-teacher outcome supervision (2026-11-07, uncommitted; DECISIONS.md, "M6 Continuation and Per-Teacher Outcome Supervision"): `train_rules --continuation POLICY|behaviour` takes `mp1` labels from one continuation policy instead of requiring all to agree. `nearest_astar` is the approved baseline. `behaviour` is each run's own strategy (per teacher), and with `--pooled` it pools the teachers' outcome labels. Each label is relative within its own state. Multi-policy rollouts without the flag still give cross-policy labels (sensitivity). The artifact's `supervision` records `continuation` and `continued_by`. Slurm: `teacher_rollouts.sbatch` (new), `teacher_train.sbatch SUPERVISION=outcome|demo MATCHED=1`, `teacher_corpus.sbatch` now records the teacher panel, and `teacher_eval.sbatch` defaults to seeds 116-120 (`--purpose backup`).

| Smoke, W100, 10 robots, 40 orders, quantum 3 (not evidence) | Result |
|---|---|
| corpus, 8 strategies x seeds 104-105 | 16/16 ok, 2-3 s each |
| single-teacher training, 8 teachers, 30 s Popper limit | 8/8 trained; random 0 clauses, others 1-2 clauses |
| pooled demonstrations, 8 teachers | trained, 1 clause; pool block lists all 8 |
| per-teacher `mp1` rollouts, 3 decisions x 3 candidates | 8/8 replay identical, clone verified, all 9 branches continued by the run's own strategy |
| outcome training, 8 teachers + pooled, `--continuation behaviour` | 9/9 trained; 3 pos / 6 neg per teacher, 24 / 48 pooled; 0 clauses (too few examples) |
| cross-policy pilot `xpolicy_r70`, 6 decisions, read only | unanimity: no positive, training refused; `--continuation nearest_astar`: 5 pos / 19 neg, 0 clauses |
| closed loop, 8 teachers + 18 artifacts, seed 116, `--purpose backup` | 26/26 completed, 26 distinct identities; hungarian/milp rules under M-Card; single-teacher rules made 65-100% of their own picks; the 9 empty programs fell back on 100% and are one policy in closed loop |

Open before M8: F9 statistics code, F10 per-order outcome attribution, F12 spatial workload. Deferred: held-out map, G6 partial charging, rest of B6.

## Tier B online parity — 1 November 2026

Committed as `56f495f` (was uncommitted when first written). The seven Tier B congestion predicates now have one definition (`DecisionFacts` in `ilp_learning/predicates.py`), read by `bk.pl` and by rule execution. Online values come from `pair_features` in `simulation_env/congestion.py`, the code that records `features.parquet`. A rule artifact using Tier B carries its thresholds, and a rule without its features or thresholds is refused. `run_workload.py` and `run_experiment.py` take `--features`; `train_rules` takes `--tier-b`. Default training stays Tier A. Proposal mapping: [.scratch/research-alignment/proposal-map.md](../.scratch/research-alignment/proposal-map.md).

| Check | Measured |
|---|---|
| Research suite with `runs/m2_acceptance`, `pixi run test` equivalent | 883 passed |
| Lint, `ruff check src` | clean |
| Live picks against offline replay, 30 and 100 robots, q0 and q3, seed 42, 300 orders | 0 mismatches over 607 decisions |
| Replay test with corrupted live values | fails on all five clauses, as it should |
| Tier A before and after: `bk.pl`, `bias.pl`, `exs.pl`, 4,097 decision rows, 150 outcome rows | identical |

Decision time on that run, Tier A against Tier A+B at 100 robots: median 0.029 s against 0.106 s at q3, 0.007 s against 0.037 s at q0; worst 1.3 s against 2.6 s. That is a hand-written one-clause rule with no witness variable; a learned rule was far slower (see M4 groundwork). None of this is evidence for H2 or H3.

## M4 groundwork — 1 November 2026

Committed as `56f495f`, `402343c` and `9757d28` (was uncommitted when first written). `simulation_env/teacher_panel.py` records what each of the six teachers would pick at every decision of one run, without acting; `--teacher-panel` on both runners, `--label-teacher` on `train_rules`. A teacher in the panel reproduces its own live picks exactly, and the panel changes no result. Research suite: 890 passed; `ruff check src` clean.

M4 and M5 are not done, and no gate has run. Research suite on 2026-11-02: 917 passed; `ruff check src` clean. The 95 s decision is fixed (2.9 s, identical decisions). The extended vocabulary and both M5 label policies are built, off by default, and `bk.pl` and `bias.pl` are byte-identical between M4 and M5. On training seeds, M4 reaches 0.08-0.38 fidelity to `multi_criteria`, because finish time and order age explain at most 0.42-0.61 of its picks; the rest are its battery terms. Neither M4 nor M5 beats `nearest_astar`. Results: [.scratch/m4-single-teacher/exploratory-2026-11-01.md](../.scratch/m4-single-teacher/exploratory-2026-11-01.md). Decisions: DECISIONS.md, "M4-M6 Decisions (2026-11-02)". M6 is not started; rollout cost is unprofiled, and Friedman, Holm and Wilcoxon code does not exist.

## Sprint 0 status — 31 October 2026

Uncommitted on `research/outcome-aware-ilp` at `bf5c8d7`, awaiting review. Details, classification and proposed commits: [.scratch/sprint0/status.md](../.scratch/sprint0/status.md).

| Check | Command | Measured |
|---|---|---|
| Research suite, HEAD `bf5c8d7` in a git checkout | `pixi run test` | 646 passed, 1 skipped (`runs/` absent) |
| Research suite, working tree with `runs/m2_acceptance` and M3 code | `pixi run test` | 813 passed |
| Lint | `pixi run lint-src` | clean; `ruff check .` also reports 52 in untracked handover scripts |
| GUI backend, `ae832f5` | see `.github/workflows/gui.yml` on the GUI branch | 152 passed; needs root `.pixi` |
| Frontend, `ae832f5` | `npm test`, `npm run build` | 22 passed; build and typecheck pass |

Run tests in a shell without ROS sourced: the `launch_testing` pytest plugin aborts startup otherwise. A tree without `.git` fails one provenance test by design. Gazebo, colcon and the C++ test are not covered by these checks.

M2: six corrections and the permitted-column firewall are implemented with regression tests. D1 was approved on 2026-10-31 (DECISIONS.md, "M3 Execution Semantics Amendment"), and the matcher implements it as `iterative` mode. The M3 learner code is implemented and tested: `train_rules.py`, the rule compiler, artifact and trace, and `ILPAllocationStrategy` (`ilp`). Induction results are in [.scratch/m3-popper-learner/induction_report.md](../.scratch/m3-popper-learner/induction_report.md). The pre-registration is frozen by its 2026-11-01 amendment. `pixi run m3-induction` and `pixi run m3-validation` reproduce the evidence and the gates. Validation ran once, on seeds 111-115, from tag `m3-evidence-1` (`393e011`); gates are in [.scratch/m3-popper-learner/validation_gates.md](../.scratch/m3-popper-learner/validation_gates.md). The quantum 3 stop rule was not triggered and R∧O was recovered at all quantum 3 conditions. Failures, reported as measured: G5 at q3/30 and q0/30 (both original and amended); G1, G6 and G9 at q0/70 and q0/100; G2 at q0/70; G10 removing-O at four of six conditions; G8 inconclusive at q0/70 makespan and both q0/100 metrics.

## Documentation freeze — 28 October 2026

[PROJECT_HANDOVER.md](PROJECT_HANDOVER.md) and [PROJECT_HANDOVER.html](PROJECT_HANDOVER.html) consolidate both worktrees, exact demos, strategy formulations, results and known failures. This summary supersedes historical status text below; it is a documentation update, not a new scientific phase approval.

| Worktree / component | Checked state |
|---|---|
| GUI `traffic/rmf-integration` | HEAD `ae832f5`; complete dashboard Stages 1–6 and reports; science suite 562 passed |
| Research `research/outcome-aware-ilp` | HEAD `163c6a4`; M0 committed; uncommitted M1 diagnostics/follow-up fixes preserved |
| Current research verification | **634 passed, 0 failed in 78.83s**: M1 accepted; M2 outcome-independent data layer implemented with 14 unit tests; full suite green |
| Foundations | W100, directed A*, shared local traffic/battery, eight conventional strategies, datasets and experiments implemented |
| GUI verification | 152 backend and 22 frontend tests passed; typecheck/build and launcher preflight passed |
| Existing GUI pilot | 24/24, 50 robots/200 orders/seeds1–3/q0; conventional/pre-M0, no charging |
| Existing M1 pilot | Six main seed101/300-order probes plus one rollout; exploratory, predates latest fixes; full matrix pending |
| Symbolic learner | M2 data/bias/predicates and Single-/Multi-Teacher/Outcome-Aware ILP/explanations remain unimplemented |
| Integration | GUI is not yet compatible with research M0 identity/purpose/runner interfaces |
| Deferred | Held-out transfer map, G6 partial recovery charging; remaining B6 metrics incomplete |

Active defaults are W100/100 robots/2,000 orders/q0. Research primary condition explicitly overrides q3; q0 is secondary. Earlier V2 numbers need the fixture config. Congestion diagnostics exist as M1 sidecars; they are not yet allocator features or accepted learned predicates. Popper is installed, not an integrated learner.

No implementation fix, merge, commit, push or new large experiment was performed in this documentation freeze. Current known limitations and fresh logs are in the dated handover bundle.

---


## Historical current-position snapshot (see freeze summary above)

Updated 2026-10-27, M0, on the research backend branch
`research/outcome-aware-ilp`. The deterministic warehouse simulator is
complete and verified; the research learner is not yet implemented. The
milestone plan to 20 December 2026 (M0-M9) is in ROADMAP.md.

```text
Deterministic simulator       DONE
Directional traffic          DONE; rotation right-of-way committed
                             aa77ee0, accepted foundation 2026-10-27
Directed A* routing          DONE
Gazebo mirror                DONE
V2 warehouse map             DONE; the test-suite and frozen-baseline
                             reference
W100 research map            DONE, committed 503ca90; one config,
                             fleet sweep 10-100
Held-out transfer map        DEFERRED requirement (2026-10-27), not
                             built; not cancelled
Battery scheduler V1         B0-B5 and B6a DONE, committed
                             B6 rest (battery decision/outcome
                             metrics) NOT STARTED
                             G6 end-of-work partial charging DEFERRED
                             requirement (2026-10-27), not built
Candidate window S0          S0a + S0b DONE, committed 503ca90
MRTA baselines (Phase 7)     random, nearest_manhattan, nearest_astar,
                             earliest_finish, multi_criteria, auction,
                             hungarian, milp; committed aa77ee0,
                             accepted foundation 2026-10-27
Decision/outcome dataset     DONE (Phase 8), committed 503ca90,
                             accepted foundation 2026-10-27; F5
                             linkage defect fixed in M0
Experiment runner            DONE: paired seeds, E1 integrity
                             (accepted foundation); M0 adds --quantum,
                             --window, --purpose, strategy identity,
                             classified failures
M0 integrity                 DONE, verified and committed 163c6a4
M1 outcome signal            ACCEPTED and CLOSED 2026-10-29; 300-order pilot
                             accepted, parameters H=120s, lambda=0.50,
                             delta=2.0s frozen (DECISIONS.md)
M2 ILP data layer            ACTIVE; run-scoped identifiers, State_t
                             fact generation, Popper bias, sampling
Primary condition            dispatch quantum 3 s for learning and
                             evaluation; quantum 0 secondary
                             (researcher, 2026-10-27)
Statistical protocol         PROPOSED in EXPERIMENT_PROTOCOL.md; Holm
                             and Wilcoxon not implemented (F9)
Decision-time congestion     APPROVED FOR INVESTIGATION (2026-10-27),
  and route-overlap features not implemented
ILP / Popper                 NOT IMPLEMENTED; Popper v4.4.0 installed
                             and smoke-tested (see "Popper environment")
Outcome-aware learning       NOT IMPLEMENTED
Explanations                 NOT IMPLEMENTED
```

## Current Map

The current V2 map is a 17 x 35 XLSX warehouse with:

```text
72 shelf service cells
36 dropoff cells
22 Stage Zone home cells
8 charging cells
14 charge-queue / holding cells
134 blocked cells
```

Chargers and holding cells form two one-cell-deep terminal banks,
y=2 and y=32, reading `C Q Q C Q Q C Q Q C Q` over x=4..14 (B0).

V2 is the test-suite and frozen-baseline reference. The research map
is W100 (see "W100: the research map, and one config"); the W20/W30/W50
family was replaced by it on 2026-10-23.

## Current Scientific Baseline

`nearest_astar` is the reference baseline every paired comparison is
made against. Eight conventional strategies are implemented (Phase 7).

The simulator uses directed A* over the one-way warehouse graph.

Gazebo replays the scientific execution rather than running an independent planner.

## Measured Baseline

Measured on 2026-09-13 after B0, branch `traffic/rmf-integration`,
working tree. Counts are read from the XLSX, not from documentation.

| Gate | Result |
|---|---|
| map load `CSV_To_Gazebo_v2.xlsx` | 17 x 35, 134 obstacles |
| shelves / dropoffs / Stage Zone homes | 72 / 36 / 22 |
| chargers / charge-queue holding | 8 / 14 |
| transit core cells | 309 |
| directed core edges | 485 |
| two-way (uncontrolled bidirectional) core edges | 0 |
| strong connectivity | 309/309 forward, 309/309 reverse |
| terminal access, enter from core and leave to core | shelf 72/72, dropoff 36/36, staging 22/22, charging 8/8, holding 14/14 |
| station legs / unreachable | 22952 / 0 |
| legs through a terminal / terminal->terminal moves / moves not in nav graph | 0 / 0 / 0 |
| nav graph vs lane policy | 973 lanes, 0 illegal, 0 missing, 0 terminal chain, 0 stranded |
| RMF `graph_check` | 461 waypoints, 973 lanes, OK |
| 100-order workload | 100/100, 22/22 parked, makespan 282.0000 s, 6430.0 m, waiting 264.6667 s, 0 violations |
| 500-order workload | 500/500, 22/22 parked, makespan 1123.3333 s, 29338.0 m, waiting 1242.6667 s, 0 violations |
| charger / holding cells ever occupied in those runs | 0 and 0; 0 terminal->terminal moves executed |
| Stage Zone occupancy | 22 distinct cells on both runs; 22/22 and 21/22 parked off spawn |
| unit tests | 155 passed |
| traffic behaviour suite | 8/8 PASS |
| lint | clean |

B0 changed the transit core (was 353 cells, 569 edges) and the nav
graph (was 483 waypoints, 1013 lanes). Every workload number is
bit-identical, because no route ever used the charging apron.

The superseded V1 map (`CSV_To_Gazebo.xlsx`, 18 homes, 40 dropoffs)
has the same two-deep apron and now fails the gate under the
battery-era terminal policy: 331 transit cells, not strongly connected,
14/16 chargers usable, 289 unreachable legs. V1 is not maintained.

## Known Loose Ends Before New Feature Work

The current V2 changes are not committed.

Resolved during the Phase A audit:

```text
practice-map tests repaired   test_nearest_astar.py now uses a
                              policy-legal one-band miniature of the
                              production map; 5/5 pass
openpyxl declared             added to pixi.toml; the
                              /usr/lib/python3/dist-packages
                              PYTHONPATH workaround is no longer needed
pixi run test fixed           it only put mrta_core on the path, so the
                              task_management tests never ran at all
final parking policy          the Stage Zone bank is now a shared
                              fleet resource. A robot claims the
                              nearest FREE cell by directed A* when
                              the fleet runs out of work, reserved at
                              assignment time so two robots cannot
                              pick the same cell. Spawn position
                              confers no title. See DECISIONS.md.
nav graph terminal fidelity   the generated graph carried 232
                              terminal -> terminal lanes, letting a
                              static RMF route walk the home bank or a
                              shelf column. Both halves of the lane
                              policy now resolve in one place,
                              traversable_lane_edges(); the graph is
                              1013 lanes and mirrors the scientific
                              planner cell for cell.
```

Still open:

```text
lane policy is hard-coded to V2 coordinates
    EASTBOUND_ROWS, WESTBOUND_ROWS, NORTHBOUND_COLS, SOUTHBOUND_COLS,
    SHELF_COLS, OPEN_BANDS and the frontage lane columns in
    warehouse_routing.py are absolute 17 x 35 indices. A map of any
    other shape cannot currently be legal. This must be parameterised
    before W30 and W50 exist; it is a Phase B prerequisite, not a
    cosmetic cleanup.

docs/images/ holds 19 MB of generated PNGs referenced by nothing.
    Keep them out of the research code commit.

docs/archive/16_CURRENT_STATUS.md still carries its 2026-08-29 component
    table, which lists "Battery / charging COMPLETE". That describes
    the pre-freeze simulator deleted in 7a07208; battery is not
    implemented. The file is marked superseded but the row misleads.
```

Resolved in Battery V1 increment B0 (2026-09-13):

```text
charging apron migration      With chargers and holding cells as
                              terminals, 16 of the 32 apron cells
                              could not be both entered and left, not
                              the 4 "stranded chargers" previously
                              reported: that count came from the nav
                              graph generator's local lane check, and
                              two of the four were holding cells. The
                              4 corners had no aisle neighbour; the
                              rest of each outer row hung off one-way
                              stubs. The 12-cell relabel formerly in
                              runbook 2.7a sealed 22 terminals and was
                              wrong. Fix: one-cell-deep banks (outer
                              rows y=1, y=33 walled), and the lane
                              policy forbids terminal->terminal edges.
                              See 00_RUNBOOK.md 2.7a.
```

## Immediate Phase

### Phase A: Freeze the verified baseline

Acceptance, as measured on 2026-09-13:

```text
V2 map loads normally                          MET
current topology checks pass                   MET
current routing checks pass                    MET
current traffic checks pass                    MET
100-order workload passes                      MET
working tree changes are understood            MET
practice-map tests are repaired                MET
openpyxl is declared in project environment    MET
baseline commit created only when explicitly requested
                                               PENDING APPROVAL
```

### Phase B: Create the warehouse scalability family

Create:

```text
W20
W30
W50
```

Each warehouse must use the same semantic rules while scaling capacity.

Each map must independently pass:

```text
map load
strong connectivity
0 uncontrolled bidirectional transit edges
all terminals reachable
all chargers reachable
all holding cells reachable
nav graph verification
traffic stress test
```

Do not begin battery implementation on W30/W50 until the maps are structurally valid.

### Phase C: Battery Scheduler V1

Status: CURRENT, started 2026-09-13 on the V2 map, ahead of Phase B.

Implement only the approved V1 policy from `docs/DECISIONS.md`,
including the decisions approved on 2026-09-13 (CHARGE_NEEDED,
preemption rights and guard, queue discipline, end of workload).

Do not redesign charging while implementing it.

One increment at a time. After each: focused tests, full regression
gate, report, stop for approval.

```text
B0  charging-apron topology              DONE, visually approved
B1  SOC accounting                       DONE, approved
B2  eligibility + CHARGE_DUE / NEEDED    DONE, approved
B3  charger reservation + travel         DONE, approved 2026-10-06
B4  charging + leaving + fleet rejoin    COMPLETE, VERIFIED, APPROVED 2026-10-06
                                         committed 5d61c84
B5  preemption + holding promotion       DONE, APPROVED 2026-10-11,
                                         committed d6d67c1
B6  decision / outcome battery metrics   NOT STARTED
```

### Phase S0: Candidate window and dispatch

Status: S0a and S0b IMPLEMENTED 2026-10-22, committed in checkpoint
503ca90; formal approval not recorded.

S0a is the candidate window, the FIFO liveness rule and the
decision-shape instrumentation. S0b is the dispatch quantum; it
followed S0a because the frozen decision sets its T from the free-robot
distribution S0a measures.

S0 exists because Phase 7 cannot start without it: 62 of 71 allocator
calls on the 100-order run are 1 robot x 1 order, and on a 1x1 decision
every strategy returns the same assignment. See docs/ARCHITECTURE.md
section 12, P0-1.

Settled, recorded in docs/DECISIONS.md:

```text
candidate window        W = fleet_size, configurable, replaces
                        min(available_robots, pending)
dispatch batching       hybrid: continuous by default
                        (dispatch_quantum = 0); one controlled scenario
                        with a quantum so Hungarian / MILP face real
                        m x n decisions. T set from the measured
                        free-robot arrival distribution, not guessed.
FIFO liveness           if the prefix holds zero assignable pairs,
                        extend down the FIFO sequence until one exists
baseline                re-freeze 100 / 500; keep a window=legacy
                        regression test asserting 282.0000 s, 6430.0 m
distinctness gate       pairwise decision divergence over
                        choice-bearing decisions (robots x orders > 1),
                        threshold 5%, plus outcome delta
Hungarian vs MILP       separated by objective: min-sum vs min-max
                        completion with battery / congestion
                        constraints. scipy 1.17.1 supplies both.
strategy order          slice 1 = Random, Nearest Manhattan,
                        Nearest Directed A*; stop for approval
```

S0a acceptance, measured 2026-10-22:

```text
1. stall reproduced as a regression test first              MET
2. W configurable and recorded; default W = fleet_size      MET
3. decision shape measured and reported, not assumed        MET
4. window=legacy reproduces the pre-S0 baselines            MET  exact
5. new 100 / 500 baselines measured                         MET
6. full regression gate                                     MET
7. Gazebo run reproduces the headless numbers               PENDING user
8. no change to routing, traffic, battery, eligibility,
   order schema                                             MET
```

The stall, reproduced before the dispatch path was touched. One robot
at 14% SOC, which can afford `O0053` (42 m, requires 12.4667%) but not
`O0099` (108 m, requires 16.8833%), and `O0099` at the head of the
queue:

```text
queue [O0099, O0053]   RuntimeError: workload stalled at t=0.0000:
                       2 pending, 0 deferred, 0 in flight, 1/1 parked
queue [O0053, O0099]   100% complete, makespan 186.4667 s, 0 deferrals
```

Same robot, same SOC, same orders; only the CSV sequence differs. The
fleet can finish both -- take the cheap order, charge, take the dear one
-- so this was a liveness defect, not an infeasible workload. The
control case is what proves that. Both are kept in
`test_s0_window.py`, together with a `window=legacy` case that still
stalls, so the defect stays reproducible.

Decision shape, the measurement S0 exists for:

```text
                        100 orders            500 orders
                        legacy  fleet_size    legacy  fleet_size
decisions                 71        64          411      409
choice-bearing (m*n>1)     9        63           68      408
forced 1x1                62         1          343        1
forced share           87.3%      1.6%        83.5%     0.2%
```

The 62 of 71 figure in `docs/ARCHITECTURE.md` section 12 is confirmed
exactly. Note that legacy is not forced everywhere: at t=0 the whole
fleet is free, so the opening dispatch is a genuine 22x22 batch. It is
every decision after it that collapses, because robots then come free
one at a time.

Baselines. `window=legacy` reproduces the pre-S0 frozen numbers
bit-identically, which is the anchor proving S0 changed the window and
nothing else:

```text
                  legacy (= pre-S0 frozen)   fleet_size (new default)
100 orders        100/100, 22/22 parked      100/100, 22/22 parked
  makespan            282.0000 s                 268.6667 s
  distance              6430.0 m                   5798.0 m
  waiting             264.6667 s                 305.3333 s
  violations                   0                          0
500 orders        500/500, 22/22 parked      500/500, 22/22 parked
  makespan           1123.3333 s                1057.3333 s
  distance             29338.0 m                  26992.0 m
  waiting            1242.6667 s                1436.6667 s
  violations                   0                          0
```

Measured side effect, in the fleet's favour and explainable: a greedy
allocator with 22 orders to choose from instead of 1 finds cheaper
pairings, so makespan and distance both fall (-4.7% and -9.8% on 100
orders). Waiting rises, because the better pairings concentrate robots
on the shared aisles. No reservation violation in either mode.

Regression gate, measured 2026-10-22:

```text
unit tests                         MET  216 passed (207 + 9 new)
lint                               MET  clean
traffic behaviour suite            MET  7/7 scripts OK
check_map_topology.py              MET  OK, 0 unreachable legs
check_battery_eligibility.py       MET  OK
check_batch_reservations.py        MET  OK
check_charger_evacuation.py        MET  5/5
check_b4_charging.py               MET  6/6
check_b5_queue.py                  MET  5/5
determinism, two runs              MET  identical simulated output
```

Two controlled battery scenarios are pinned to `window="legacy"`,
in both the unit test and the matching validator so the two cannot
drift:

```text
B4 recycle / test_charger_is_reused_by_a_later_request
    Tests the *direct* charger reuse path, where the later requester
    finds the charger FREE and departs in the instant it requests.
    Under the wider window the allocation differs, robot_002 requests
    while robot_001 is still charging, and it is correctly routed
    through holding and promoted -- three charge records, and
    requested_at != departed_at. That is the B5 queued path, covered
    by check_b5_queue.py.

B5 preempting / the preemption scenario pair
    robot_002 must run the dear FAR order to cross 10% late enough for
    robot_001 to be mid-charge. With a wide window the allocator
    correctly prefers the cheaper SHORT, robot_002 crosses early and
    finds a RESERVED rather than a charging charger, so there is
    nothing to preempt. Sound allocation and sound battery behaviour;
    simply not this scenario.
```

One B2 test was retargeted rather than deleted, because S0 makes its
assertion wrong by design:

```text
test_charge_needed_scans_all_pending_orders
    asserted dispatches == [] -- the robot stays available but gets
    nothing, because its batch was the unaffordable FIFO head alone.
    That standstill is the stall the liveness rule removes. It now
    asserts the opposite: the window extends past the head and the
    robot is given the order it can run. The B2 behaviour under test,
    that the CHARGE_NEEDED scan covers all pending orders rather than
    the batch, is unchanged and still asserted.

_MaskBlindAllocator
    now takes the dearest reachable pair rather than the cheapest.
    With a wide window the cheapest pair is usually affordable anyway,
    so a cheapest-first blind allocator would accidentally behave
    itself and never exercise the executor's refusal. The guard is
    therefore still tested under the production window, not pinned.
```

S0b acceptance, measured 2026-10-22. The candidate window fixes the
*order* side of a decision. The robot side is the free pool, and under
continuous dispatch robots come free one at a time, so almost every
decision offers a single robot and the batch-assignment strategies
(Hungarian, MILP) reduce to argmin and collapse onto greedy nearest.

The free-robot arrival distribution, measured before choosing T:

```text
inter-decision gap    min 0.667  p25 0.667  median 2.000  p75 3.333  max 18.000 s
                      (500 orders: p25 1.333, median 2.000, p75 3.333)
```

T swept against it, 100 orders:

```text
T        decisions   m>1   mean m   makespan     distance   waiting
0.0 s           64    13     1.56   268.6667 s   5798.0 m   305.3333 s
1.0 s           54    21     1.85   260.3333 s   5726.0 m   325.3333 s
2.0 s           41    23     2.44   272.6667 s   5770.0 m   332.6667 s
3.0 s           33    25     3.03   264.6667 s   5716.0 m   376.3333 s
5.0 s           24    21     4.17   274.0000 s   5714.0 m   398.6667 s
10.0 s          15    14     6.67   292.6667 s   5672.0 m   533.3333 s
```

All six complete 100/100 with 0 reservation violations. **T = 3.0 s**
is frozen: it triples the robots per decision at no makespan cost,
while 10.0 s costs 9% makespan and 75% more waiting. The quantum is
therefore not a free parameter to maximise.

```text
zero quantum is exactly continuous dispatch       MET  268.6667 s,
                                                       5798.0 m, 0 holds
quantum batches robots: mean m 1.56 -> 3.03       MET
batched scenario is 100% choice-bearing           MET  33/33, 0 forced
no robot held longer than one quantum             MET  window opens on
                                                       the oldest waiter
nothing held when holding cannot pay              MET  3 refusals
no order dropped or duplicated at any T           MET  0.0/1.0/3.0/10.0
determinism at T=3.0                              MET  identical
negative quantum refused                          MET
```

The batched scenario is the reference config with `--quantum 3.0` --
`warehouse_sim.yaml` with `dispatch_quantum_s: 3.0` and nothing else
changed:

```text
100/100, 22/22 parked, makespan 264.6667 s, 5716.0 m,
waiting 376.3333 s, 0 violations
33 decisions, 33 choice-bearing (100.0 %), 0 forced,
309 dispatch attempts held to batch robots
```

Continuous dispatch remains the default and the headline configuration;
the batched scenario exists only to make the batch strategies
separable.

Implementation note. `EventType.DISPATCH_WINDOW` is a neutral timer: it
advances the clock, produces a tick and touches no robot. It is
deliberately **not** a `WAIT`, because a robot held for allocation
batching is idle, not blocked by traffic, and must not book waiting
time. Idle drain over the held interval is booked exactly as any other
idle interval, so the battery model is untouched.

Regression gate after S0b, measured 2026-10-22:

```text
unit tests                         MET  223 passed (216 + 7 new)
lint                               MET  clean
traffic behaviour suite            MET  7/7 scripts OK
check_map_topology.py              MET  OK
check_battery_eligibility.py       MET  OK
check_batch_reservations.py        MET  OK
check_charger_evacuation.py        MET  5/5
check_b4_charging.py               MET  6/6
check_b5_queue.py                  MET  5/5
100 / 500 baselines, both windows  MET  unchanged from S0a
```

Deliberately NOT in S0: any new strategy, decision/outcome logging,
the `order_makespan` / `fleet_recovery_time` split, and W30/W50.
Routing, traffic, lane policy, the battery model, the eligibility
equation and the order schema are untouched.


B0 acceptance, measured 2026-09-13:

```text
every charger enterable and leavable              MET  8/8
every holding cell enterable and leavable         MET  14/14
no terminal-to-terminal move                      MET  0 of 22952 legs
no terminal used as intermediate transit          MET  0 of 22952 legs
directed topology strongly connected              MET  309/309
nav graph matches production routing              MET  973/973 lanes
100-order baseline unchanged                      MET
500-order baseline unchanged                      MET
traffic suite                                     MET  8/8
Gazebo worlds regenerated                         MET  warehouse_grid, warehouse_10
visual inspection in Gazebo                       MET  (user, 2026-09-13)
```

B1 acceptance, measured 2026-09-13. Observation only: one
`BatteryLedger` in the fleet engine, booked at the engine's existing
travel / service / wait call sites with the clock's own Fraction
durations; idle is the remainder of elapsed time. Nothing reads SOC.

```text
20 m travel / 60 s service / 300 s wait / 300 s idle = 1%   MET  exact
combined hand calculation, yield + rejoin as travel         MET
service not double-counted as waiting                        MET
SOC identity, cross-check vs engine counters                 MET  <= 6.4e-14 %
deterministic repeat, per-robot initial-SOC override         MET
battery on vs off: dispatches, completions, totals, parking  MET  identical
100-order output vs B0 (RESULT + Stage Zone table)           MET  identical
500-order output vs B0 (RESULT + Stage Zone table)           MET  identical
100 orders  final SOC min 80.7222  mean 84.7322  max 86.6256   fleet 335.8911 %
500 orders  final SOC min 27.8989  mean 30.7151  max 33.6000   fleet 1524.2689 %
```

B2 acceptance, measured 2026-09-13. Eligibility is a hard mask for every
strategy: `required = travel(route incl. post-dropoff retreat) + service
+ directed-A* leg from the retreat cell to the nearest charger + 10%
reserve`, eligible when SOC >= required; no predicted waiting. The
executor re-derives every returned pair and raises
`IneligibleAssignmentError` rather than execute or re-assign one.

```text
zero margin eligible, negative margin refused                MET
closer but ineligible robot skipped                          MET
charger leg is directed A* (28 m), not Manhattan (4 m)       MET
no predicted waiting term; realized waiting drains SOC        MET
busy robot crossing 10% finishes whole order, then REQUEST    MET
CHARGE_NEEDED judged over all pending orders, not the batch  MET
no pending work -> no CHARGE_NEEDED                          MET
allocator returning an ineligible pair -> explicit error     MET
deterministic eligibility fields                             MET
100 and 500 orders vs B1, incl. every SOC row                MET  identical
  ineligible candidate pairs      0 / 0
  CHARGE_DUE / CHARGE_NEEDED      0 / 0 and 0 / 0
  lowest margin over an assigned pair  71.2722 % / 19.1922 %
controlled 50/20/14/12/10/9 % fleet
  t=0: 12% CHARGE_NEEDED, 10% and 9% CHARGE_DUE, 14% fits 36/100
  40-order run: 40/40, 156 ineligible pairs, 0 ineligible assignments
```

B3 acceptance, measured 2026-09-13. On entering REQUEST a robot
reserves, before it moves, the FREE charger with the cheapest
directed-A* route, else the cheapest FREE holding cell, else it records
SATURATION and takes the nearest free Stage Zone; it then drives the
costed route by the production router. Simultaneous requesters are
paired greedily on (route cost, zone_id, robot_id). Slots are FREE /
RESERVED / OCCUPIED / VACATING (`simulation_env/charging.py`) and are
offered only while FREE. Charger trips are recovery moves, like
parking: order missions keep right of way over them. No charging: the
robot stays on its terminal, never becomes AVAILABLE again in B3, and
its SOC keeps draining at the idle rate.

```text
nearest charger chosen by directed A*; Manhattan disagrees,   MET
  does not decide
simultaneous requests -> distinct chargers; reserved twice    MET  ResourceError
robot drives the costed route onto its reserved charger        MET  realized == planned
no terminal as intermediate transit, no terminal->terminal    MET
arrival: RESERVED -> OCCUPIED at the arrival instant           MET
all chargers OCCUPIED -> holding; holding reservations unique  MET
no REQUEST robot ever left standing on a transit cell          MET  REQUEST -> departure 0 s
no opposite-edge swap on a charger / holding access edge      MET  0 exits, 1 entry each, by holder
occupied slot stays unavailable to the end of the run          MET
saturation -> nearest free Stage Zone (holding cut to 2)       MET  1 SATURATION event
order ownership untouched; deterministic repeat               MET
SOC never rises; on a charger it drains exactly 1 % / 300 s    MET
controlled runs (check_charger_evacuation.py), 100 orders each
  four   (4 at 9%)     100/100, 4 on chargers,          314.6667 s, 0 yields
  twelve (12 at 9%)    100/100, 8 chargers + 4 holding, 496.6667 s, 0 yields
  late   (8 at 9%, one 14% robot turns CHARGE_NEEDED at (14,5) at
         t=67.3333 with every charger OCCUPIED -> holding (14,2))
                       100/100, 390.0000 s, 0 yields
  saturated (10 at 9%, holding cut to 2; a 14% robot asks at (14,4)
         -> Stage Zone staging_015)   100/100, 460.6667 s, 0 yields
  b2_block (50/20/12/10/9/14 %, deadlocked at t=391 in B2)
                       100/100, 5 on chargers, 304.0000 s, 0 yields
100 and 500 orders vs B2, incl. every SOC row                  MET  identical
  0 requests, 0 reservations, 0 yields
```

B3 visual acceptance in Gazebo, 2026-10-06 (user), with
`warehouse_sim_b3_twelve_low.yaml` and `orders:=true`. The Gazebo run
reproduced the headless run of the same config cell for cell: the same
charger and holding cell per robot, the same reservation and arrival
instants, the same arrival SOC.

```text
12 CHARGE_DUE at t=0, all while idle; 0 CHARGE_NEEDED
reserved and departed at t=0.0000, max REQUEST -> departure 0.0000 s
0 REQUEST robots left standing on a transit cell
8 chargers OCCUPIED, 4 holding OCCUPIED, 0 Stage Zone fallback
100/100 orders, 10/22 parked, 496.6667 s, 0 reservation violations
0 saturation events, 0 yield events
final SOC min 5.0989 mean 34.5432 max 71.5589; below 0 % none
SOC identity exact; cross-check vs engine counters <= 2.842e-14 %
allocator pairs 196 evaluated, 0 ineligible; lowest margin 56.6378 %
```

The lowest SOC is the expected B3 consequence, not a fault:
`robot_005` reached `charging_008` at 6.6367 % at t=35.3333 and then
idled on it to the end of the run, 461.3334 s at 1 % / 300 s =
1.5378 %, leaving 5.0989 %. Nothing recharges it until B4.

B3 traffic-safety argument (the two B2 blockers):

```text
1. equal-right-of-way yield livelock -- not triggered by charging paths
   A yield needs a head-on: two robots requesting opposite directions
   on one edge (a swap). Ordinary transit edges are one-way, so a swap
   can only happen on a two-way terminal access edge, with one robot
   entering terminal T while another leaves T.
   - A charging-workflow robot's leg ends at its own reserved terminal,
     and every other terminal is blocked in its navigation grid. So
     each of its moves is a one-way transit edge or its final entry
     into its own terminal. It never enters a shelf, dropoff or Stage
     Zone (other than a claimed fallback), so it can never be the
     entering half of a swap anywhere else. Its only terminal exit is
     its first move off the Stage Zone it spawned on, and nobody can be
     entering that cell: a Stage Zone somebody stands on is never
     claimed.
   - Only the reservation holder may enter a charger or holding cell,
     and the slot is not offered again until the robot leaving it has
     cleared the access edge (VACATING -> FREE on clearance). In B3
     nothing ever leaves a charger or holding cell at all.
   - A fallback Stage Zone is claimed only while nobody stands on it,
     and no robot enters a Stage Zone without a claim.
   Hence no swap on any charging path; measured 0 yields in all five
   controlled runs and both standard runs. The generic traffic / yield
   controller is unchanged. The livelock itself remains a known issue
   for head-ons elsewhere (not reachable on the production map today);
   B4 must keep VACATING until the access edge is cleared.

2. a REQUEST robot holding still in an aisle -- resolved
   REQUEST now reserves and departs at the same instant; the B2
   deadlock fleet completes 100/100.
```

B4 acceptance, measured 2026-10-06. A robot that reaches its reserved
charger now charges to exactly 100%, reverses into the aisle cell it
came from, and rejoins the fleet. The charge is a timed dwell on the
engine's own Fraction clock, booked as its own ledger category:

```text
duration = (100 - SOC_arrival) / charging_percent_per_second
SOC(t+dt) = min(100, SOC(t) + charging_percent_per_second * dt)
```

The configured rate is the NET gain -- no idle drain is subtracted over
a charge interval (docs/DECISIONS.md, "Charging Rate"). The slot goes
OCCUPIED -> VACATING before the robot moves and becomes FREE only once
it physically stands on the access aisle cell, so an entering and a
leaving robot can never meet on that edge. Holding cells are unchanged:
promotion is B5.

```text
71% at 1%/s charges for exactly 29 simulated s          MET  exact Fraction
every session lands on exactly 100%                     MET  all scenarios
60 s of charging adds exactly 60%, no idle subtracted   MET
charging time excluded from the idle remainder          MET
conservation: final = initial - travel - service
  - waiting - idle + charge                             MET  exact
SOC clamped at 100%, never above                        MET
live mid-charge SOC read rises and is exact             MET  ledger-read tests
minimum SOC latched before the charge raises it         MET
cross-check vs the engine's float counters              MET  <= 1.4e-14 %
no order dispatched between request and rejoin          MET
slot OCCUPIED for the whole charge, VACATING while
  reversing out, FREE only after clearance              MET  sampled per instant
two robots never overlap on one charger cell            MET
a freed charger is reused only after full clearance     MET  recycle scenario
a charged robot rejoins and completes further orders    MET  single scenario
holding robots are NOT promoted (B5 scope guard)        MET
deterministic repeat                                    MET

unit + integration tests                                189 passed (179 before correction)
ruff                                                    clean
map topology / highways / nav graph                     OK
traffic behaviour suite                                 7/7
check_battery_eligibility.py                            OK
check_charger_evacuation.py                             5/5
check_b4_charging.py                                    6/6
  single / four / twelve / recycle / contested / slow

100-order scientific run vs the B0-B3 checkpoint        identical
  100/100, 22/22 parked, 282.0000 s, 6430.0 m,
  waiting 264.6667 s, 0 violations,
  final SOC min 80.7222 mean 84.7322 max 86.6256,
  0 charge sessions
500-order original B4 result (not rerun for correction)  identical
  500/500, 22/22 parked, 1123.3333 s, 29338.0 m,
  waiting 1242.6667 s, 0 violations,
  final SOC min 27.8989 mean 30.7151 max 33.6000,
  0 charge sessions
```

Controlled B4 runs on the V2 map, 22 robots unless stated:

```text
single    1 robot at 9%, 6 orders
          6/6, 1 session, 1 rejoin, 441.7667 s, 0 violations
four      4 at 9%, 40 orders
          40/40, 4 sessions, 4 rejoins, 167.3333 s, 0 violations
twelve    12 at 9%, 100 orders
          100/100, 8 sessions, 8 rejoins, 4 still in holding,
          375.2633 s, 0 violations, 0 yields, 0 saturation
          (the same fleet settled at 496.6667 s in B3, where nothing
           could charge or come back)
recycle   A at 9%, B at 18%, 4 robots, 12 orders, 1 usable charger
          12/12, 2 sessions, 2 rejoins, 272.3367 s, 0 violations
          both charge on charging_001; B requests after A clears
contested 12 at 9%, 100 orders, departures merging into order traffic
          100/100, 8 sessions, 8 rejoins, 375.2633 s, 0 violations
slow      1 robot at 9%, 6 orders, charge rate 0.1%/s
          arrival t=28, SOC 6.9%; 931 s charge, full t=959
          rejoin t=959.6667, SOC 99.95%; all 6 orders complete
          fleet-end makespan 1279.6667 s, 0 violations
```

B4 correction: the 600-second mission watchdog previously rejected
that valid slow charge at t=959. It now excludes only elapsed intervals
where all in-flight work consists of CHARGING robots whose exact finite
completion events remain queued. Existing waiting/unfinished traffic
disables that exemption; elapsed no-progress debt before and after a
charge is preserved, and the event budget still detects zero-time loops.
Missing completion events cannot exempt a session, and a drained queue
with an open charge session raises an error.

The reuse regression observes A clear charging_001 at t=123.2000, then
B reserve it at t=140.6667 and physically occupy it at t=165.3333. Clearance tests
inspect both the physical move and zero-delay mission-completion batches;
`_rejoin()` verifies the expected access cell and completed idle exit
before freeing the slot. Post-rejoin assignment is non-empty and the
later order completes. A controlled six-order run also completes two
charge/rejoin sessions for the same robot without stale reservations.

Live charging SOC tests validate rational ledger reads, not live 2 Hz
ROS publication. During synchronous execution FleetNode publishes via
engine ticks; its periodic timer is created after the workload returns.
Gazebo visual acceptance PASSED on 2026-10-06, verified and approved by
the user with `warehouse_sim_b3_twelve_low.yaml` and `orders:=true`:

```text
12 low-SOC robots entered the charging workflow
8 reached chargers, charged, physically reversed out and rejoined
rejoined robots resumed warehouse work
4 remained in holding until the end (promotion is B5)
100/100 orders completed; 8 charge sessions; 8 rejoins
all 8 charger slots FREE at the end
0 reservation violations; 0 yield events
SOC identity exact; gain matched the configured 1%/s net rate
no visible collision or opposite-edge swap
no completed charging robot remained on a charger
```

B4 is COMPLETE, VERIFIED, APPROVED, and committed as `5d61c84`
("feat: complete Battery V1 B4 charging and fleet rejoin", 2026-10-06).
B5 is reported below, approved 2026-10-11 and committed as d6d67c1;
B6 remains NOT STARTED.

Worked example from the `twelve` run, exactly as reported:

```text
robot_011 charging_005 start t=21.3333 SOC 7.4000%  92.6000 s
          full t=113.9333  +92.6000%  rejoined t=114.6000 SOC 99.9500%
```

7.4000 + 92.6000 = 100 exactly. It rejoins at 99.9500% because
reversing one cell off the charger is real driving and costs one move's
travel energy (1 m at 1% / 20 m = 0.05%). Fleet total: 743.6344% gained
over 743.6344 simulated seconds, i.e. exactly 1%/s net.

Deliberately NOT in B4, and verified absent: holding-cell promotion and
preemption (B5), the `order_makespan` / `fleet_recovery_time` split and
battery metrics (B6), G6 end-of-work partial charging (B6). The
eligibility equation, order schema, strategy set, FIFO window, traffic
and lane policy are untouched.

B3/B4 test supersession, for the record. Nine tests written in B3
asserted that charging does not exist. They were retargeted, not
deleted, because B4 makes the asserted behaviour wrong:

```text
test_no_soc_increase_exists_yet        -> test_soc_rises_only_while_charging
                                          plus two new B4 invariants
test_robot_physically_reaches_the_reserved_charger
                                       -> asserts arrival from the record
                                          and the sampled history, since
                                          the robot no longer ends there
test_charger_arrival_makes_the_resource_occupied
                                       -> OCCUPIED during the charge,
                                          then VACATING, then FREE
test_holding_cells_receive_unique_reservations
                                       -> counts charger trips, not slots
                                          still occupied at the end
test_no_request_robot_remains_stopped_on_a_transit_cell
                                       -> a REJOINED robot in an aisle is
                                          legal; CHARGING must be on a
                                          terminal
test_terminal_access_cannot_create_an_opposite_edge_swap
                                       -> an exit is legal only by the
                                          slot's own holder, and never at
                                          the same instant as an entry
test_resource_remains_unavailable_while_occupied
                                       -> unavailable through the charge
                                          AND the vacating move
test_charging_scheduler_never_modifies_order_ownership
                                       -> no dispatch between request and
                                          rejoin, rather than none ever
test_busy_robot_crossing_ten_percent_finishes_its_order
                                       -> scoped to the charge request;
                                          the robot may work again after
```

B5 acceptance, measured 2026-10-11. A holding cell is now a waiting
room, not a destination. Two policies decide who gets a charger, both
frozen in docs/DECISIONS.md:

```text
promotion   when a charger is FREE, the best waiting robot is promoted
            onto it: lowest SOC, then earliest charge request, then
            robot_id as the final engineering tie-break. One idempotent
            pass per dispatch, so it fires both when a charger is
            released and when a waiting robot arrives in holding.
preemption  when every charger is occupied and a CHARGE_DUE robot needs
            one, the highest-SOC charging robot gives it up, and only
            if its SOC is strictly above the requester's. A
            CHARGE_NEEDED robot never preempts. victim_soc_ge_80 is
            recorded, never branched on.
```

A promoted robot leaves holding exactly as a full robot leaves a
charger: the slot goes OCCUPIED -> VACATING before it moves and is
released only once the robot physically stands on its access aisle
cell. It then drives to the charger by the production router, from that
aisle cell -- a holding cell is non-transit, so no route starts on one.
The holding trip and the charger trip are separate `ChargeRecord`s
carrying the same original request, so every record still describes
exactly one terminal.

```text
12 requesters, 8 chargers, 14 holding: all 4 waiters promoted  MET
  12 charge sessions, 12 rejoins, every session exactly 100%
charger occupancy never above 8; one robot per cell             MET
no robot ever holds two charger or two holding reservations     MET
no robot waits in holding beside a free unpromised charger      MET
promotion goes to the lowest-SOC waiter of that instant         MET
promotion in the same instant the charger frees, and in the
  same instant a waiter arrives next to a free one              MET
CHARGE_NEEDED robot (14%) waits and is promoted, never preempts MET
preempted session books only the interval it charged            MET  exact
  gained = elapsed * rate; charge_completed_at stays None
preempted robot fit for work rejoins the fleet and works again  MET
preempted robot at or below CHARGE_DUE waits in holding         MET
the charger freed by a preemption is held for its requester     MET
no promise outstanding at the end of a run                      MET
100-order default baseline vs B4                                MET  identical
  100/100, 22/22 parked, makespan 282.0000 s, 6430.0 m,
  waiting 264.6667 s, 0 violations
unit tests                                                      MET  207 passed
check_charger_evacuation.py                                     MET  5/5
check_b4_charging.py                                            MET  6/6
check_b5_queue.py                                               MET  5/5
lint                                                            MET  clean
visual inspection in Gazebo                                     MET  (user, 2026-10-11)
```

Gazebo visual acceptance PASSED 2026-10-11, verified by the user with
`warehouse_sim_b3_twelve_low.yaml` and `orders:=true`. The ROS run
reproduces the scientific engine exactly -- same makespan, same
distance, same promotion instants:

```text
12 low-SOC robots entered the charging workflow
8 reached chargers, charged, reversed out and rejoined
4 waited in holding and were PROMOTED onto the chargers the first
  eight vacated, each in the instant that charger became FREE:
    t=114.6000  robot_001  charge_queue_002 -> charging_005
    t=116.7500  robot_002  charge_queue_001 -> charging_001
    t=118.1833  robot_003  charge_queue_010 -> charging_006
    t=120.3333  robot_004  charge_queue_008 -> charging_002
all 12 charged to exactly 100% and rejoined; 12 sessions, 12 rejoins
100/100 orders completed; 22/22 parked off spawn (B4 run: 18/22)
holding bank 14/14 FREE at the end (B4 run: 4 OCCUPIED for good)
makespan 357.1378 s, 6850.0 m, 0 reservation violations, 0 yields
0 preemptions: with 8 chargers free at t=0 nothing had to be taken
```

Measured side effect, in the fleet's favour: the makespan fell from
375.2633 s to 357.1378 s against the same scenario under B4, because
four robots that used to sit in the queue bank for the whole run
rejoined the fleet and took orders. Total distance rose 6732 -> 6850 m
for the same reason.

The B3 invariant line in the `CHARGING` report is now measured over
un-promoted trips only. A promoted robot departs when its holding cell
is clear, not when it first requested, and it waited on a terminal
rather than in an aisle, so counting it there read as a 121 s aisle
wait that never happened. Its wait is reported separately as
`request -> charger`, which is a B5 outcome, not an invariant.

Controlled B5 runs, 22 robots unless stated:

```text
twelve        12 at 9%, 40 orders   4 promotions, 0 preemptions
late          8 at 9% + 1 at 14%    1 promotion,  0 preemptions
preempt       2 robots, 1 charger   1 preemption, victim rejoined
              robot_002 at 11.5%, reserve 0, charge 1%/s
preempt_wait  the same at 0.1%/s    1 preemption, victim to holding
contested     12 at 9%, 100 orders  4 promotions, 0 preemptions
```

The `preempt` worked example, exactly as reported:

```text
t=29.3333  robot_001 starts charging on charging_001 at 6.8000%
t=56.0000  robot_002 requests CHARGE_DUE at 7.7667% after its order;
           every charger OCCUPIED -> holding charge_queue_006, and it
           preempts robot_001 at 33.4667% (> 7.7667%)
           robot_001 gained 26.6667% in 26.6667 s, i.e. exactly 1%/s
t=56.6667  robot_001 clear of the charger at 33.4167%, fit for work,
           rejoins the fleet
t=74.0000  robot_002 reaches holding and is promoted onto charging_001
           in that same instant
t=179.0500 robot_002 full; rejoins at t=179.7167
```

Two defects were found and fixed while measuring B5, both in the
interaction between preemption and the rules it leans on:

```text
1. the victim re-took the charger it had just given up
   It ends its retreat one cell from the charger with the lowest SOC in
   the bank, so the queue discipline handed it straight back and the
   requester waited out a whole further charge. Fixed by assigning the
   charger to its requester at the instant of preemption; see
   docs/DECISIONS.md, "Preemption Hands the Charger Over".
2. a victim at or below CHARGE_DUE was declared fit for work
   It rejoined the fleet and the B2 gate pulled it out again on the
   same tick. Fixed: the rejoin test now requires SOC above CHARGE_DUE
   as well as an eligible order.
```

Deliberately NOT in B5, and verified absent: the `order_makespan` /
`fleet_recovery_time` split and battery metrics (B6), G6 end-of-work
partial charging (B6), promotion out of a Stage Zone saturation
fallback, and retargeting a robot already in flight to a holding cell.
The eligibility equation, order schema, strategy set, FIFO window,
routing, traffic and lane policy are untouched, and the default
100-order baseline is bit-identical.

Known interaction, recorded rather than fixed: a robot requesting in
the few seconds between a preemption and the requester's arrival in
holding can still be offered that charger by the B3 rule ("a FREE
charger exists -> reserve the cheapest") if it is not the promised
robot. The promise prevents exactly this, so the window is closed for
the preemption case; for an ordinary release the frozen B3 rule governs
and a fresh requester may take a charger ahead of a waiting robot only
when promotion has already run in that same dispatch and declined it.

B5/B4 test supersession, for the record. Six B4 assertions asserted
that holding robots stay put. They were retargeted, not deleted,
because B5 makes the asserted behaviour wrong:

```text
test_holding_robots_are_not_promoted_in_b4
                                       -> test_a_holding_robot_is_promoted
                                          _onto_a_freed_charger, plus
                                          test_promotion_takes_the_lowest
                                          _soc_waiting_robot
test_all_chargers_occupied_sends_the_robot_to_holding
                                       -> asserts the holding trip as it
                                          was taken, which then ends in
                                          promotion
test_holding_cells_receive_unique_reservations
                                       -> holding cells stay one trip
                                          each; a charger may serve more
                                          than one robot per run
test_no_request_robot_remains_stopped_on_a_transit_cell
                                       -> a promoted robot departs when
                                          its holding cell is clear, not
                                          when it first requested
test_terminal_access_cannot_create_an_opposite_edge_swap
                                       -> a holding cell may be left, by
                                          its own holder only
test_charging_scheduler_never_modifies_order_ownership
                                       -> the no-work window closes at
                                          the end of each trip, since a
                                          holding trip hands the robot to
                                          a charger trip
check_charger_evacuation.py            -> the same three assumptions in
                                          the validator
check_b4_charging.py                   -> a freed charger no longer stays
                                          FREE at the clearance instant;
                                          it must only stop being the
                                          leaving robot's
```

### Phase 8: Decision and outcome dataset

Status: implemented, committed in checkpoint 503ca90; accepted by the researcher as an implemented foundation 2026-10-27.
The deferred-launch decision_id defect (F5) is fixed in M0, below.

The first phase whose purpose is not simulator behaviour but evidence.
Without it there is nothing for an ILP learner to read, and no way to
compare what a strategy predicted against what the warehouse actually
did.

#### What was added

```text
src/simulation_env/simulation_env/dataset.py         new
src/simulation_env/simulation_env/dataset_export.py  new
src/simulation_env/test/test_dataset.py              new, 22 tests
src/simulation_env/simulation_env/workload_executor.py
                                                     recorder hooks
src/simulation_env/scripts/run_workload.py           --dataset, --export
pixi.toml                                            pyarrow
```

Two tables and a manifest per run:

```text
decisions.parquet   one row per scored (robot, order) candidate pair
outcomes.parquet    one row per order
run.json            provenance, including a content hash of the map,
                    the order CSV and the config, plus git HEAD and
                    whether the tree was dirty
```

The `run_id` is a hash of the manifest itself, so two runs collide
exactly when every input that could change a result is identical.

#### Measured

Shipped 22-robot / 100-order workload, `nearest_astar`,
`window=fleet_size`, `quantum=0`:

```text
decision rows            1973 over 39 columns
outcome rows              100 over 22 columns
positive examples         100
negative examples        1873
makespan, recording on    268.6667 s
total distance            5798.0 m
```

Recording changes nothing: the instrumented run and the bare run agree
on makespan, distance, waiting, service, the decision log and the
dispatch sequence. That is asserted directly, not assumed.

#### No leakage, proven rather than documented

A decision row is built before any mission is planned and never
revisited. The truncation test aborts a run immediately after a
decision and again 40 dispatches in, and asserts the decision rows are
identical to the same run carried through to completion. If any field
were populated from post-decision state, the two would disagree.

The decision schema is also asserted not to name any outcome column.

#### Both execution paths record the same dataset

`fleet_node.py` takes `dataset` and `dispatch_quantum_s` parameters and
records through the same `RunRecorder`. For the shipped workload the
live ROS node and the headless runner produce **byte-identical**
decision and outcome tables and the same `run_id`, verified with `cmp`.
One scientific engine, one dataset; there is no Gazebo-side recording
path.

#### Three bugs found and fixed during implementation

```text
1 m attribution bias   C_static includes the post-drop-off retreat,
                       but the outcome row was written at the drop-off
                       service, before the retreat was driven. Every
                       order read exactly one cell short of its
                       prediction. Fixed by writing the outcome at
                       retirement and adding retreat_finished_at;
                       completed_at still means the drop-off, which is
                       when the order is fulfilled. Predicted and
                       realized distance now agree exactly, which is
                       also an independent check that the router's
                       prediction and the executor's realization agree
                       cell for cell.

soc repeated per pair  SOC is a property of the robot at t, not of the
                       pair, so the Prolog export stated it once per
                       order in the window. Now once per robot per
                       decision.

live run lost entirely The first live ROS run simulated all 100 orders,
                       printed its full report, then died in
                       recorder.write with ModuleNotFoundError: No
                       module named 'pyarrow'. The node runs on the
                       system interpreter under /opt/ros/jazzy, not in
                       the pixi environment where pyarrow is declared.
                       RunRecorder now falls back to CSV and the
                       manifest records which format was used. The
                       fallback is lossless -- the exact columns are
                       already strings -- and a Parquet run and a CSV
                       run read back equal row for row.
```

#### An S0b gap closed on the way

`fleet_node.py` never forwarded `dispatch_quantum_s` to the executor,
so a live run of `warehouse_sim_batched.yaml` would have silently used
continuous dispatch while the headless runner used the configured
quantum. Found while wiring the dataset into the node. The node and the
launch file now both carry it, with a negative value meaning "keep the
configured value" so that `0.0` remains a real override meaning
continuous dispatch.

#### Fields not recorded, and why

`est_travel_time_s` (the executor accepts `speed_mps` but does not use
it), `predicted_wait_s` (`Dispatch.planned_wait_s` is always 0.0), a
route-congestion feature (`OrderCandidate` has none, and adding one
would be a strategy-visible change to fixed infrastructure), and a
per-order minimum SOC (the ledger exposes launch and completion SOC
only). Recorded in docs/DECISIONS.md, "Phase 8 Schema Gaps To Close",
rather than fabricated.

#### Commands

```bash
# record a run, headless
python3 src/simulation_env/scripts/run_workload.py \
    --quiet --progress 0 --dataset runs/astar --export

# record a live run through the ROS node
ros2 launch simulation_env warehouse_sim.launch.py \
    orders:=true dataset:=runs/live

# the dataset tests, including the truncation proof
PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management \
    python3 -m pytest src/simulation_env/test/test_dataset.py -q
```

### Phase 7 slice 1: Random and Nearest Manhattan

Status: implemented, committed in checkpoint 503ca90; as part of Phase 7,
accepted by the researcher as an implemented foundation 2026-10-27.

The first real strategy comparison. Until now `nearest_astar` was the
only registered strategy, so no claim about allocation policy could be
supported by anything.

#### What was added

```text
src/task_management/task_management/features.py       manhattan_order_cost
src/task_management/task_management/allocation_strategy.py
                                                      greedy_pairing,
                                                      NearestManhattanAllocator,
                                                      RandomAllocator
src/task_management/test/test_strategies.py           new, 24 tests
src/simulation_env/scripts/check_strategy_distinctness.py   new gate
src/simulation_env/scripts/run_workload.py            --seed
src/simulation_env/simulation_env/fleet_node.py       allocation_seed param
```

#### One sweep, three rankings

All three strategies are the same `greedy_pairing` sweep over a
different ordering. That is deliberate and it is what makes the
comparison controlled: two strategies now differ by their ranking and
by nothing else -- not by how they resolve a conflict, not by how many
pairs they emit, not by what they do when one side runs out.

`nearest_astar` was refactored onto the shared sweep and its baseline
is **bit-identical**: 268.6667 s / 5798.0 m / 305.3333 s / 64 decisions
/ 1 forced.

#### Measured, 22 robots / 100 orders / seed 42

| Strategy | Makespan | Distance | Waiting | Forced 1x1 |
|---|---|---|---|---|
| `nearest_astar` | 268.6667 s | **5798.0 m** | 305.3333 s | 1/64 |
| `nearest_manhattan` | 265.3333 s | 5974.0 m | 325.3333 s | 0/68 |
| `random` | 266.6667 s | 6488.0 m | 258.0000 s | 1/65 |

All three complete 100/100.

Distance orders as theory predicts: grid-aware < naive < none, by +3.0%
and +11.9%.

**Makespan does not order the same way.** `nearest_manhattan` finishes
*earlier* than `nearest_astar` despite driving 176 m further. This is
reported rather than explained away: the printed makespan is the
engine's end time including end-of-work recovery (P0-4), and it is set
by whichever robot finishes last after traffic, so it is a noisy
signal for allocation quality on a single seed. Distance is the clean
one. The `order_makespan` / `fleet_recovery_time` split (B6) is what
would make makespan interpretable here, and it is not implemented.

Do not read the single-seed makespan column as a ranking.

#### Distinctness gate

Comparing two independent runs cannot measure distinctness: after the
first decision where they differ the fleets are in different states, so
their decision N is not the same decision. The gate measures a
**counterfactual** instead -- one strategy drives, and at every decision
each rival is asked the same question with the same free robots,
window, positions, SOC and route cache. Probing is read-only; only the
driving strategy's answer reaches the executor.

```text
driving with nearest_astar      vs nearest_manhattan   48/63   76.2 %
                                vs random              57/63   90.5 %
driving with nearest_manhattan  vs nearest_astar       41/68   60.3 %
                                vs random              66/68   97.1 %
driving with random             vs nearest_astar       60/64   93.8 %
                                vs nearest_manhattan   61/64   95.3 %

threshold 5.0 %                 status: OK
```

#### Manhattan decides the pairing, never the route

The split is the whole point of the naive baseline:

```text
Manhattan      decides WHICH robot takes which order
directed A*    decides HOW the robot gets there
```

So the strategy changes allocation and nothing else; route, traffic,
battery drain, service and speed are identical to `nearest_astar`'s.
`OrderAssignment.cost` carries C_static forward, not the Manhattan key,
or every predicted-vs-realized comparison for this strategy would be
wrong. Asserted by `test_manhattan_reports_the_astar_cost_not_its_own_ranking`.

Eligibility is untouched: reachability and the battery mask are decided
by directed A*, and Manhattan is never consulted for them.

#### Random is stochastic, not unreproducible

Seeded from `simulation.seed` (42), overridable with `--seed`.
Candidates are sorted into a canonical order *before* they are
shuffled, because shuffling a list built from dict iteration is not
seeded however it looks. Verified: two runs at seed 42 produce
byte-identical datasets; seed 7 gives 288.6667 s / 6508.0 m.

#### Long-run soak, 2000 orders — and a traffic gap it exposed

22 robots, 2000 orders, seed 42. This is the first run that actually
exercises the battery: at 100 orders the fleet never drops near the
charge-due threshold and **zero** charge sessions occur.

```text
                     nearest_astar    nearest_manhattan   random
completed            2000/2000        2000/2000           STALLED
makespan             4296.2591 s      4294.6516 s         -
total distance       107306.0 m       107406.0 m          -
wall clock           39.6 s           -                   -
```

`nearest_astar` battery report:

```text
charge sessions      44 started, 44 robot(s) back in the fleet
holding promotions   18 out of holding onto a freed charger
preemptions          0
saturation events    0
reservation viol.    0
plan deferrals       0
final SOC            min 16.6448  mean 26.9711  max 37.6824
lowest minimum SOC   7.9263
below 0 % SOC        none
SOC identity         exact (rational arithmetic)
battery-ineligible   4452 of 47052 pairs (9.5 %)
```

Two things to read carefully:

- **Lowest minimum SOC 7.9263% is below the 10% reserve, and that is
  correct.** The reserve is a *decision-time* guarantee: a robot is
  only given a new order if it can finish it and still reach a charger
  on 10%. It may legitimately dip below while finishing a committed
  order and driving to the charger. The invariant that matters is
  `below 0 % SOC: none`, and it holds.
- **Preemption (B5) is still not exercised**, even at 2000 orders.
  8 chargers served 44 sessions without ever saturating. Exercising
  preemption needs more robots or fewer chargers, not more orders.

**`random` deadlocks.** All 22 robots end in a cyclic wait:

```text
STALLED at t=1160.6667 (2000 orders) / t=1138.6667 (500 orders)
CYCLES  robot_007 -> robot_019 -> robot_005 -> robot_016 -> robot_007
```

This is **not** the structural head-on deadlock the one-way policy
removes; every robot is moving in a legal direction. It is a circular
wait around the circulation loop. It is load- and policy-dependent, not
systematic: at 500 orders `random` completes on seeds 1, 7 and 99 and
stalls on 42, while both nearest policies complete at 500 and at 2000.

**It is pre-existing and not caused by slice 1.** The traffic
controller, lane policy and engine are untouched, and `nearest_astar`
is bit-identical. A strategy only chooses which robot takes which
order; keeping any legal assignment set live is the traffic layer's
job. `random` is simply the first strategy that scatters work across
the hall, so it is the first to raise loop occupancy enough to close a
cycle.

**It blocks high-load strategy comparison, and it is biased.** The
deadlock is likelier under policies that scatter, so a throughput table
built without resolving it would flatter the grid-aware strategy for a
traffic reason rather than an allocation reason. It also bounds the
W30/W50 scalability study, where density is higher by design.

Tracked in `.scratch/traffic-liveness/issues/01-cyclic-deadlock-under-scattered-allocation.md`.
Out of phase for slice 1: it belongs to the traffic controller and must
be solved identically for every strategy.

#### Commands

```bash
# the three strategies
for s in nearest_astar nearest_manhattan random; do
    PYTHONPATH=$PP $PY src/simulation_env/scripts/run_workload.py \
        --quiet --progress 0 --strategy $s
done

# the distinctness gate
PYTHONPATH=$PP $PY src/simulation_env/scripts/check_strategy_distinctness.py

# the unit tests
PYTHONPATH=$PP $PY -m pytest src/task_management/test/test_strategies.py -q
```

### Any map: lanes drawn in Excel (unblocks Phase 5)

Status: implemented, committed in checkpoint 503ca90; formal approval not recorded.

Requested 2026-10-23: anyone should be able to create a map in Excel,
by following an SOP, and run the system on it. The researcher plans one
new map with capacity for 10 to 100 robots.

#### Why it was impossible before

The lane policy was hard-coded to V2's 17 x 35 coordinates. Measured on
a 25 x 35 test map: 52.3% of road cells strongly connected, and shelves
in columns the policy did not know were 0/6 reachable.

#### What was added

```text
mrta_core/warehouse_map.py        WarehouseMap.lane_edges (optional)
mrta_core/warehouse_map_xlsx.py   arrows in road cells; S D C H P codes;
                                  errors name the Excel cell
mrta_core/warehouse_routing.py    drawn maps routed by their own arrows
mrta_core/map_builder.py          new: blank / example / export, writer
mrta_core/map_check.py            new: the drawing checker
mrta_core/sim_config.py           resolve_map_path()
mrta_core/tests/test_map_lanes.py new, 46 tests
scripts/map_tool.py               new: new, example, check, show, export
scripts/generate_orders.py        --config: stations read from the map
scripts/build_scenario.py         one nav graph per scenario
scripts/check_strategy_distinctness.py   --robots
maps/templates/                   blank_30x40, example_20, example_100
docs/START_HERE.md                new: step 0 to running, simple words
docs/MAKE_A_MAP.md                new: the map SOP
```

#### Measured

```text
V2 exported as arrows, loaded back    775 = 775 blocked moves, identical
V2 baseline, nearest_astar            268.6667 s / 5798.0 m, unchanged
V2 orders regenerated via --config    byte-identical to orders.csv

generated 100-robot map (40 x 54, 100 P, 552 S, 36 D, 24 C, 12 H)
  map_tool.py check                   OK, no warnings
  check_map_topology.py               OK; 523,452 station legs, 0 unreachable
                                      (1 min 34 s)
  build_scenario.py                   world with 100 robots; nav graph verified

on that map                           completed   makespan     distance
  10 robots, 200 orders  astar        200/200     1849.0267 s  23652.0 m
                         manhattan    200/200     1863.2922 s  24246.0 m
                         random       200/200     1919.3211 s  24850.0 m
  50 robots, 200 orders  astar        200/200      429.3333 s  23642.0 m
                         manhattan    200/200      442.0000 s  24600.0 m
                         random       200/200      450.6667 s  25802.0 m
 100 robots, 500 orders  astar        500/500      628.6667 s  60658.0 m
                         manhattan    500/500      637.3333 s  63130.0 m
                         random       500/500      684.6667 s  66488.0 m
 100 robots, 1000 orders astar       1000/1000    1122.0000 s 120288.0 m

distinctness gate, 50 robots          72.1 % to 97.5 %, status OK
```

On the drawn map the ordering is clean on both makespan and distance at
every fleet size: grid-aware < naive < random.

#### Found on the way: the deadlock has a geometric cause

The first 100-robot run **deadlocked** at t=268 s. The cause was the
generated layout: every cross-band met each pair of neighbouring aisles
in a **rotary**, a 2 x 2 square of roads whose arrows chase each other
round it, where four robots each wait for the next forever. Flipping
each band removed all of them, and the same 100 robots then completed
1000/1000.

The same check found **33 rotaries on V2**, in its open bands, and the
V2 deadlock under `random` (500 orders, seed 42) is four robots on one
of them: (13,3) (14,3) (14,4) (13,4). That issue now has its root cause
(.scratch/traffic-liveness/issues/01-...). `map_tool.py check` warns on
every rotary; generated maps have none. V2 itself is unchanged, so every
frozen baseline holds.

#### Scalability decision, resolved 2026-10-23

The researcher chose one main map, **W100**, swept from 10 to 100
robots, plus a held-out map for rule transfer. DECISIONS.md, "Warehouse
Scalability", now says so. See the W100 section below.

### W100: the research map, and one config

Status: implemented, committed in checkpoint 503ca90; formal approval not recorded.

The researcher chose one main map for the R&D, with a fleet-size sweep
from 10 to 100 robots (DECISIONS.md, "Warehouse Scalability").

#### What was added

```text
maps/W100.xlsx                  40 x 54; 100 P, 552 S, 36 D, 24 C, 12 H
config/warehouse_W100.yaml      the research scenario; every setting
                                explained and marked FREE or FIXED
config/orders_W100.csv          2000 orders, seed 42; --orders N = first N
fleet.initial_position_mode: spread   new start rule, see below
run_workload.py --orders-file   use another order list without a config
test/test_spawn_spread.py       new, 13 tests
```

#### Start positions: `spread`

With the old rule robot k started on parking cell k, so 10 robots on
W100 all started in the top five rows. `spread` shares robots across
every parking bank in proportion to its size and spaces them evenly
along it: 10 robots start 5 per wall at rows 6, 16, 26, 36, 46. A full
fleet uses every cell under either rule, V2's default is unchanged, and
parking at the end is still the nearest free cell.

#### Measured

```text
map_tool.py check             OK, no warnings
check_map_topology.py         OK; 523,452 station legs, 0 unreachable

fleet sweep, nearest_astar, 300 orders
  robots   completed   makespan      total waiting   violations
    10     300/300     2860.7969 s     105.9056 s    0
    20     300/300     1463.3844 s     219.7089 s    0
    30     300/300      931.3333 s     380.6667 s    0
    50     300/300      616.6667 s     687.3333 s    0
    70     300/300      483.3333 s    1910.6667 s    0
   100     300/300      416.6667 s    3399.3333 s    0

three strategies, 300 orders       makespan      distance
   10  nearest_astar               2860.7969 s   36368.0 m
       nearest_manhattan           2885.3609 s   36962.0 m
       random                      2930.8578 s   37612.0 m
  100  nearest_astar                416.6667 s   37416.0 m
       nearest_manhattan            460.6667 s   39106.0 m
       random                       483.3333 s   41404.0 m
```

The ordering is grid-aware < naive < random at both ends, and the gap
widens with congestion: random takes 2.4% longer than A* at 10 robots
and 16.0% longer at 100.

#### One config

`warehouse_W100.yaml` is the config to use. Configs that existed only
to change one setting were removed after each was shown to give
identical results through a command-line option:

```text
warehouse_sim_500.yaml       --orders-file orders_500.csv   1057.3333 s, as before
warehouse_sim_batched.yaml   --quantum 3.0                  264.6667 s / 5716.0 m, as before
warehouse_sim_10.yaml        --robots 10 (and its world, warehouse_10.world)
warehouse_sim_2000.yaml, orders_2000.csv   generated soak files
```

`warehouse_sim.yaml` stays: the V2 reference the test suite and every
frozen baseline use. The two `warehouse_sim_b3_*` files stay: battery
test fixtures used by `check_charger_evacuation.py`.

### One map, one config, one world

Status: implemented, committed in checkpoint 503ca90; formal approval not recorded. Supersedes
the per-fleet-size worlds and the `warehouse_W100*.yaml` configs of the
W100 section above.

The researcher's design (2026-10-23): "I'll create a map, that map will
be converted into a Gazebo world, and that will take robots and orders
and everything from the config file. That would be just one."

#### Layout

```text
config/warehouse.yaml    the one config (default everywhere)
config/orders.csv        the current map's orders, 2000, seed 42
maps/W100.xlsx           the current map
worlds/warehouse.world   the one world: the warehouse only
test/fixtures/           V2 map, v2_reference.yaml, its orders, b3 configs
```

#### What changed

```text
build_scenario.py --map X     check map -> rewrite config map/robot_count
                              -> regenerate orders -> world -> nav graph
generate_gazebo_grid_world    writes no robots any more
gazebo_bridge                 robot_models / create_robots / remove_robot
fleet_node                    robots param; sets Gazebo's fleet at start
launch                        robots:=N; config and world from source tree
generate_orders               default: the current map, into config/
sim_config.rewrite_scenario   changes only the map and fleet lines
test/test_one_config.py       new, 15 tests
```

#### Measured

```text
build_scenario --map W100 (unchanged map)     config, orders, world byte-identical
new 30-robot map built, run, switched back    config, orders, world byte-identical
Gazebo, one command, robots:=10               10 created, 27 orders in 70 s
Gazebo, same window: robots:=30 then :=10     0 -> 30 -> 10 robots (20 removed)
tests                                         344 passed
simulator checks (V2 fixture)                 14/14
V2 baseline via the fixture                   268.6667 s / 5798.0 m, unchanged
W100, 10 robots, 300 orders                   2860.7969 s, unchanged
```

#### Found on the way

A fresh `colcon build` fails in `rmf_grid_traffic` with
`No module named 'catkin_pkg'`: CMake finds `~/.local/bin/python3.10`
before the ROS Python. It had only worked because an old build cache
remembered the right one, which is a likely reason the Gazebo steps
failed for the researcher. Every build command now passes
`--cmake-args -DPython3_EXECUTABLE=/usr/bin/python3`.

`docs/00_RUNBOOK.md` §2.6 would have regenerated the single nav graph
from the V2 config, overwriting the current map's graph. Fixed to the
one config.

### Gazebo pauses and the silent end of a long run

Status: fixed 2026-10-24, committed in checkpoint 503ca90; formal approval not recorded.
Performance only: every decision, outcome and metric is unchanged.

Reported with 100 robots in Gazebo: every robot stopped together for a
moment, again and again; and once all 2000 orders were done the node
printed nothing more.

#### Causes

```text
pauses   the fleet node sends Gazebo frames only between allocation
         decisions. Each decision scores robots x orders with directed
         A*, and every A* call rebuilt the non-transit cell set by
         scanning all 724 zones (29 million steps per frozen window).
         At 100 robots a decision took up to 0.47 s: a visible stop.
silence  the final report calls ReservationTable.audit(), which
         compared every logged move with every other move:
         242,140 moves, ~2.9e10 pairs, about 28 minutes.
```

#### Fix

```text
warehouse_map.py      zone_by_id / zones_of_type are dict lookups;
                      cells_of_types() builds each cell set once.
                      The map is frozen, so nothing can go stale.
warehouse_routing.py  service_cells / non_transit_cells use it
reservations.py       audit compares only holds of the same cell
                      (start-time sweep) and moves over the same cell
                      pair in opposite directions; same problems,
                      same order, as the all-pairs scan
test_map_lookups.py        new, 7 tests (red on the old code)
test_reservation_audit.py  new, 4 tests, incl. the all-pairs scan as
                           reference on 300 seeded logs (red on old)
```

#### Measured

```text
frame gaps, 100 robots, 300 orders      21 >= 0.25 s (worst 0.47 s) -> 0 (worst 0.17 s)
full run, 100 robots, 2000 orders       6 min 7 s -> 72.9 s; slow dispatches 5,
                                        worst 0.60 s at t=0 (first assignment,
                                        before anything moves), others <= 0.125 s
allocator CPU, 100 robots, 300 orders   12.339 s -> 1.820 s
decisions + outcomes, 300 orders        identical tables (25,090 + 300 rows)
full run makespan                       2441.2910 s before and after
audit after the full run                ~28 min -> 0.33 s, 0 violations both
tests                                   355 passed (344 + 11 new)
simulator checks (V2 fixture)           14/14
V2 baseline via the fixture             268.6667 s / 5798.0 m, unchanged
```

#### Found on the way: decided, see "Stage Zone promotion" below

At 100 robots, 36 of the fleet leave work for good. All 100 robots
start at 100% and drain alike, so they all ask for a charger around
t=1270-1600 s. With 24 chargers and 12 holding cells full, 36 requests
are saturation events, and those robots wait on a Stage Zone. By the
approved B5 rule ("Charger Queue Discipline" in DECISIONS.md) a Stage
Zone fallback robot is never promoted, so all 36 stay parked to the end
of the run, at 7.7% SOC and above, while every charger is FREE again
by then. After the charging wave the 100-robot run works with 64
robots. Not changed: battery behaviour is frozen infrastructure.

### Stage Zone promotion

Status: implemented 2026-10-24, approved by the researcher as a scope
change to Battery V1 (DECISIONS.md, "Stage Zone Promotion"). Nothing
committed.

A robot waiting on a Stage Zone after saturation is now in the same
queue as the holding robots: lowest SOC, earliest request, robot_id as
the final tie-break. When a charger frees, the first robot in the queue
that can reach it on its SOC gets it, drives there by directed A*,
charges to 100% and rejoins.

Changed: `charging.py` (WAITING_STATES, record fields),
`workload_executor.py` (`_promote`, `_promotion_start`,
`_grant_charger`, `_drive_to_charger`, promises, report),
`check_charger_evacuation.py`, and the saturation test in
`test_charger_evacuation.py`, which encoded the old rule. New:
`test_stage_promotion.py`, 12 tests; 8 of them fail when Stage Zone
robots are left out of the queue again.

Measured, W100, 100 robots, 2000 orders, nearest_astar:

```text
                              before     after
charge sessions               64         100   (all 100 rejoined)
promotions                    35         76    (12 holding, 64 Stage Zone)
left on a Stage Zone at end   36         0
saturation events             36         64
makespan                      2441.29 s  2133.49 s
total distance                242140 m   245130 m
reservation violations        0          0
robots below 0% SOC           none       none
out of reach                  -          0
longest wait for a charger    -          461.98 s
```

Unchanged: V2 baseline 268.6667 s / 5798.0 m; every controlled B4/B5
scenario without saturation gives identical events; in the saturated
scenario robot_011 now charges and rejoins (makespan 328.67 ->
320.90 s). 367 tests pass, 14/14 validators, lint clean.

The battery rates in `config/warehouse.yaml` had been halved to 0.5
for a demo; they are back at the frozen 1.0.

### Gazebo opens with the robots parked; one Gazebo at a time

Status: implemented 2026-10-24, committed in checkpoint 503ca90.
Visualization only; nothing in the simulation changed.

The researcher saw no robots in Gazebo with the one-command launch, and
robots that appeared late with two terminals. Robots were created by
the fleet node after Gazebo was up, and each run spent about 10 s on a
`gz model -p` query that only read back the robots' fixed height.

Now:

```text
gazebo.launch.py          stops any gz sim of this world on this
                          GZ_PARTITION, writes the world plus the
                          run's robots parked on their start cells to
                          /tmp/mrta_gazebo_<uid>/warehouse.world, and
                          starts Gazebo on it
warehouse_sim.launch.py   includes gazebo.launch.py unless
                          start_gazebo:=false; the fleet node finds the
                          robots ("Gazebo already shows these N
                          robot(s)") and makes them move
```

The repo still has one world file, warehouse only. New:
`simulation_env/gazebo_world.py`, `launch/gazebo.launch.py`,
`test/test_gazebo_world.py` (18 tests). Changed: `gazebo_bridge.py`
(one robot model, with an optional pose), `fleet_node.py` (no height
query; a line when Gazebo already shows the fleet),
`warehouse_sim.launch.py`.

Checked end to end on a private GZ_PARTITION, headless: 100 robots
exist as soon as the server is up; a second start stopped the first
server in 1 s and left exactly one; a run found the 100 robots and
created none; first robot move 18.9 s -> 8.9 s after the run started;
the one-command launch with 50 robots stopped the old server and
started a new one with 50. Not checked by me: the Gazebo window itself
(that needs the researcher's display).

### Experiment tooling, and a quiet terminal with ROS topics

Status: implemented 2026-10-25, committed in checkpoint 503ca90.
Reporting only: nothing in the simulation changed.

Experiments:

```text
run_metrics.py        the protocol's run-level metrics (MRTA, traffic,
                      battery) from a finished run -> metrics.json in
                      the dataset directory (run_workload --dataset and
                      the ROS node dataset:=)
run_experiment.py     strategies x fleet sizes x seeds, in parallel;
                      each run keeps run.log, run.json, metrics.json,
                      decisions/outcomes; one summary.csv
plot_experiment.py    figures for every metric + summary.md
```

The first version of "time out of the fleet" counted a holding or Stage
Zone trip to the end of the run, because such a trip never rejoins; the
new test (test_run_metrics.py) caught it before the numbers were used.

Terminal and topics (fleet_node): by default a short header, one status
line every `progress` simulated seconds (60), warnings, and a short
summary; the full report goes to `$ROS_HOME/mrta/<stamp>_<strategy>_<N>robots.txt`
or `<dataset>/report.txt`. New: `/mrta/fleet_status`
(robot_interfaces/FleetStatus: order and robot counts, chargers, SOC,
who charges and who waits), `/mrta/events` (std_msgs/String, one line
per assignment, completion and battery event), and `charge_state` and
`current_order` filled in on `/mrta/robot_states`. `verbose:=true`
restores the full trace. Tested on a private ROS domain with 300 orders.

#### Experiment fleet_sweep, W100, 2000 orders

`random` over seeds 1-3; makespan in simulated s:

```text
robots   nearest_astar   nearest_manhattan   random
  30        6157.08          6251.47         6345.98
  40        4646.21          4743.84         4844.08
  50        3733.35          3821.41         3899.17
```

A* beats Manhattan by 1.5-2.4% and random by 3.1-4.4%, every fleet
size; distance 243-245 km vs 250 km vs 254-256 km. 0 reservation
violations and 0 robots below 0% in all 15 runs. The 50-robot A* run
matches the researcher's Gazebo run to the last digit (3733.3501 s,
243477.0 m, 2878.3070 s waiting).

#### Found on the way: charging comes in synchronized waves

Charge sessions are 120 / 120 / 100 for every strategy at 30 / 40 / 50
robots, and time on chargers is the same too. Not a bug: every robot
starts at 100%, drains by the same rules and is kept equally busy, so
all of them reach "no eligible order" (SOC 12.6-15.0%) within about two
minutes of each other and charge 88-93 s to 100%. At 30 robots the
charge starts fall in four waves (t = 1200-1300, 2500-2600, 3800-4000,
5100-5300 s), visible as flat steps in the completion curves. Energy
per robot differs by strategy (412 / 421 / 429% at 30 robots) but not
by enough to change the whole number of refills, so the charging
metrics cannot tell the strategies apart in this setup. Changing that
(staggered initial SOC, the same for every strategy) touches the frozen
"initial SOC 100%" and is the researcher's decision.

### One order limit for headless and Gazebo; honest run provenance

Status: implemented 2026-10-25, committed in checkpoint e7c6549; formal approval not recorded.

```text
headless   run_workload.py --orders N          (unchanged)
Gazebo     warehouse_sim.launch.py orders:=true order_limit:=N   (new)
```

Both go through `mrta_core.orders.select_orders()`: the first N logical
orders in FIFO sequence, an order's rows never split, 0 (the default)
= every order, more than the file holds = every order. The order file
is never rewritten and no temporary file is made. `orders:=true` stays
the switch that runs the workload. Both print the same line, for
example `first 50 of 2000 logical orders (92 line items) from
orders.csv, limit 50`.

Provenance fixed on the way: `run.json` took `seed`, `speed_mps` and
`dispatch_quantum_s` from the config, not from the run, and did not
record the order count. In fleet_sweep the three `random` seeds all
recorded seed 42 and shared one run_id (metrics.json had the right
seeds). `build_manifest()` now requires the effective seed, speed,
quantum and executed orders (keyword-only, no config fallback) and
records `orders`, `line_items`, `orders_available`, `order_limit` and
`battery.initial_soc_overrides`. The run_id covers all of them except
`order_limit`, which is recorded but not hashed: a limit above the
file runs the same orders as no limit. The fleet_sweep datasets from
before this change keep the old manifests.

Tests: `test_order_limit.py` (10), `test_order_limit_run.py` (8: real
headless runs of 5 and 3 orders, run.json, metrics.json and outcome
rows, a limit above the file naming the same run as no limit, and the
ROS node, launch file and runner using the same rule, read from their
source), `test_dataset.py` (run_id changes with seed, speed, quantum
and order count). The ROS node run with `order_limit:=5` against the
2000-order file executed the first 5 orders (11 line items) and
recorded them.

#### Decisions, 2026-10-25

```text
charging waves   keep initial SOC 100% as the frozen reference; later a
                 seeded heterogeneous initial SOC as a separate
                 experimental condition, the same distribution for every
                 strategy at a given seed; charging policy unchanged
Popper / ILP     not yet: first the remaining MRTA baselines, experiment
                 validation and B6; then design predicates, bias and
                 evaluation. Order: Single-Teacher, Multi-Teacher,
                 Outcome-Aware, explanations
clean-up         delete nothing for now; keep history, ICAPS material,
                 V2 fixtures and research artifacts
strategies       one at a time, in this order: Earliest Finish,
                 Multi-Criteria Grid-Aware, Auction / Marginal Cost,
                 Hungarian, MILP; each through the same window, battery
                 mask, roads, traffic and execution, and through the
                 distinctness gate
next             Battery B6 (separate order_makespan from
                 fleet_recovery_time), experiment reproducibility, then
                 Earliest Finish
```

### Battery B6a: order work vs end-of-work recovery

Status: approved, committed 5abf5c2. Reporting only:
nothing in allocation, routing, traffic, battery, charging or execution
changed.

DECISIONS.md, "End of Workload", keeps `order_makespan` (the final
drop-off) apart from `fleet_recovery_time` (every robot settled), and
productive distance apart from recovery distance. Until now the run
metrics had one `makespan_s`, the engine's end time, so parking and
charging after the last order counted as order work.

#### What was added

```text
src/simulation_env/simulation_env/work_accounting.py   new: WorkAccounting,
                                      one phase per robot per instant
                                      (support / productive / recovery)
src/simulation_env/simulation_env/workload_executor.py feeds it at launch,
                                      retirement and exhaustion;
                                      phase_totals(); outcome rows take
                                      the same mission difference
src/simulation_env/simulation_env/run_metrics.py       schema version 2:
                                      18 new fields, every v1 field kept
src/simulation_env/test/test_work_accounting.py        new, 23 tests
docs/EXPERIMENT_PROTOCOL.md                            "Run Metrics Schema"
```

`summary.csv` takes its columns from `metrics.json`, so the new fields
reach it with no change to `run_experiment.py`.

#### Measured

V2 reference, 22 robots, 100 orders, seed 42:

```text
strategy            makespan_s   order_makespan_s   tail     distance = productive + support + recovery
nearest_astar       268.6667     245.3333           23.3333  5798.0 = 5269.0 + 0.0 + 529.0
nearest_manhattan   265.3333     242.0000           23.3333  5974.0 = 5454.0 + 0.0 + 520.0
random              266.6667     240.0000           26.6667  6488.0 = 6011.0 + 0.0 + 477.0
```

**The split does not explain the Phase 7 makespan ordering.** The
recovery tails are nearly equal, and on order work alone the single-seed
ranking is still random < Manhattan < A*. The last drop-off is a max
over 100 orders, set by one late order. It is not a stable signal for
a single workload instance. Distance still orders A* < Manhattan <
random, in productive distance too.

W100 (`warehouse.yaml`), nearest_astar, seed 42:

```text
100 robots,  50 orders   182.6667 s vs order 124.6667 s; 7228 m = 4639 + 0 + 2589
 30 robots, 300 orders   888.0000 s vs order 873.3333 s; 35134 m = 34345 + 0 + 789
```

With more robots than orders, 36% of the distance is the drive back to
the Stage Zones. The v1 `distance_per_order_m` counted it as order
work.

Test scenarios with the battery on (four robots start at 9%):

```text
busy   100 orders   the four charge while orders are owed: all
                    charging is support; support distance 176 m
tail     5 orders   all allocated at t=0, so the four charge in
                    recovery: recovery_charging_s 372.6 s,
                    order_makespan 59.3333 s, fleet recovery 151.3567 s
```

#### Proven, not documented

- **Partition.** support + productive + recovery equals the run total
  for distance, travel, waiting, service, energy and charging, for
  every robot and over the fleet.
- **One number.** The productive totals equal the sum of the outcome
  rows, because both use the same mission difference.
- **Observation only.** Four runs on a clean copy of HEAD e7c6549 and on
  this tree: V2 reference; W100 with 50 orders under nearest_astar and
  under random (seed 7); W100 with 30 robots and 300 orders.
  - Decision and outcome tables are identical in every column except
    `run_id`. That differs only because the archived copy has no git
    metadata.
  - `bk.pl` and `exs.pl` are byte-identical.
  - Every v1 metric field is identical (38 fields), except the four
    wall-clock timings.

#### Read with care

Recovery starts when the workload is exhausted (the last order
allocated), not at the final drop-off. A robot that retires its last
order early parks in recovery while others are still delivering. A
charge that starts at the exhaustion instant is recovery even if it
starts at t=0. Under the DECISIONS.md wording, that is "post-work".

`plot_experiment.py` still plots `makespan_s`, which is the fleet
recovery time.

#### Commands

```bash
# the B6a tests
PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management \
  python -m pytest src/simulation_env/test/test_work_accounting.py -q

# a run's split
python src/simulation_env/scripts/run_workload.py --orders 50 --dataset runs/b6a
#   -> runs/b6a/metrics.json: order_makespan_s, fleet_recovery_time_s,
#      productive_/support_/recovery_ fields
```

### Paired workload seeds

Status: approved, committed 5abf5c2.

B6a showed that one workload cannot rank strategies by makespan: one
late order sets the final drop-off. The researcher decided that a seed
selects a workload, every strategy runs the same workload per seed,
and strategies are compared by paired differences with 95% confidence
intervals (DECISIONS.md, "Workload Seeds and Paired Comparison").
Frozen with it: fleet sizes 10/30/50/70/100, 2000 orders per workload,
seeds 1-30, baseline `nearest_astar` (EXPERIMENT_PROTOCOL.md, "Seeds").
Also approved: recovery starts at the final dispatch; headline metrics
are `order_makespan_s` and `order_throughput_per_h`; `makespan_s` is
shown as "fleet settled time"; `throughput_orders_per_h` keeps its v1
value.

#### What was added

```text
scripts/run_experiment.py      --workloads seeded (default) | reference;
                               write_workloads(): one order file per
                               seed + workloads.json; every strategy
                               runs every seed; summary.csv gets
                               workload, orders_sha256
simulation_env/paired.py       paired differences, 95% t-interval,
                               refuses pairs with different workloads
scripts/compare_paired.py      -> runs/<name>/paired.csv, paired.md
scripts/plot_experiment.py     headline order makespan / throughput,
                               fleet settled time, per-phase distance
                               and energy panels
test/test_paired_seeds.py      new, 11 tests
```

#### Measured, W100, 200 orders, seeds 1-5

30 runs in 20 s wall clock. Difference to `nearest_astar`, mean with
95% CI:

```text
                     robots   order makespan              productive distance
nearest_manhattan      10     +36.6 s [0.3, 73.0]   +2.0%  +626 m [407, 844]     +2.6%
                       30     +15.5 s [1.1, 29.8]   +2.5%  +817 m [528, 1106]    +3.5%
random                 10     +53.2 s [15.9, 90.4]  +2.9%  +996 m [710, 1281]    +4.2%
                       30     +28.9 s [16.8, 40.9]  +4.7%  +1740 m [1479, 2001]  +7.4%
```

Every interval excludes 0: on paired workloads A* finishes earlier
than both, which the single V2 workload could not show.

#### Commands

```bash
python3 src/simulation_env/scripts/run_experiment.py --name main \
    --robots 10 30 50 70 100 --seeds $(seq 1 30)
python3 src/simulation_env/scripts/compare_paired.py --name main
```

### E1: paired experiment integrity

Status: implemented 2026-10-26, committed in checkpoint aa77ee0;
accepted by the researcher as an implemented foundation 2026-10-27. Analysis tooling only: nothing in the simulation
changed.

Four guarantees before a paired comparison is reported
(EXPERIMENT_PROTOCOL.md, "Seeds"): complete seed sets against
`experiment.json`; one map and simulator setting across the experiment
(`settings_sha256`); the same workload within each pair; no accidental
overwrite (`--resume`, `--force` keeps the old directory). A dirty tree
is recorded and reported, not refused (researcher, 2026-10-26).

```text
simulation_env/paired.py         settings_signature, check_complete,
                                 check_settings; pairs also compare
                                 line_items and window_width
scripts/run_experiment.py        experiment.json, --resume, --force,
                                 workload hashes checked on resume;
                                 summary.csv: line_items, window_width,
                                 settings_sha256
scripts/compare_paired.py        enforces all four; --allow-incomplete;
                                 identity columns kept as text
test/test_experiment_integrity.py  new, 19 tests (2 end to end)
```

Found on the way:

- run.json carries three outputs (`format`, `decision_rows`,
  `outcome_rows`); the first signature included them, so every real
  run looked different. Caught by an 18-run, two-fleet-size check, not
  by the unit tests; outputs are now excluded and tested.
- `compare_paired.py` parsed every CSV value that looked numeric,
  so a digest like `12e4...` or all digits would have become a float.

Measured: 18 runs (3 strategies x 10/30 robots x seeds 1-3, 100 orders)
share one signature; `--resume` on it finds 18 done, 0 to run. Full
suite 459 passed.

### Phase 7 slice 2: the five remaining conventional strategies

Status: implemented 2026-10-26, committed in checkpoint aa77ee0;
accepted by the researcher as an implemented foundation 2026-10-27.

Definitions in DECISIONS.md, "Conventional strategy definitions".
Every strategy gets the same window, battery mask, directed A* routes,
traffic and execution; the executor now also hands every strategy a
`DecisionTiming` (each robot's speed, configured shelf and drop-off
service), built from the values the engine executes with. The first
three ignore it and reproduce their frozen V2 numbers exactly
(268.6667 s / 5798.0 m, 265.3333 s / 5974.0 m, 266.6667 s / 6488.0 m).

```text
task_management/features.py            DecisionTiming (travel, service,
                                       finish estimates)
task_management/allocation_strategy.py earliest_finish, multi_criteria,
                                       auction, hungarian, milp; timing=
                                       accepted by every strategy
simulation_env/workload_executor.py    passes DecisionTiming per decision
scripts/check_strategy_distinctness.py --quantum; default = every
                                       registered strategy (as its help
                                       already said)
test/test_phase7_strategies.py         new: controlled scenarios per
                                       strategy, properties x 8,
                                       25 brute-force MILP checks
```

#### Distinctness, W100, 30 robots, 300 orders, seed 42

Percent of choice-bearing decisions where the rival would choose
differently (row drives, column is asked):

```text
quantum 0 s (config default)       FAIL
        auc    EF    hun   milp    MC    A*    man   rnd
auc      -    96.0  96.4  96.0  89.7  96.4  96.4  97.6
EF     98.5    -     6.5   2.3  45.2   6.1  63.2  96.9
hun    97.7   6.2    -     6.6  44.2   0.8  61.6  96.9
milp   97.6   4.9   7.3    -    48.0   8.9  63.0  97.6
MC     98.8  25.6  26.4  26.4    -    26.0  65.9  95.7
A*     97.7   6.6   0.4   8.2  47.5    -    61.9  96.1
below 5 %: hungarian ~ nearest_astar (0.4 / 0.8 %),
           milp ~ earliest_finish (2.3 / 4.9 %)

quantum 3 s                        OK, every pair above 5 %
        auc    EF    hun   milp    MC    A*    man   rnd
EF     98.5    -    24.3  35.3  65.4   8.1  77.9  97.1
hun   100.0  28.4    -    27.6  64.9  24.6  73.9  99.3
milp   99.3  32.4  27.9    -    66.2  33.8  75.0 100.0
A*     99.3  10.4  20.0  40.0  65.2    -    74.1  95.6
lowest: earliest_finish vs nearest_astar 8.1 / 10.4 %
```

The quantum-0 failures are structural, not a defect: with dispatch on
every free robot most decisions offer one robot, and for one robot the
minimum total is the nearest order (Hungarian = nearest_astar) and the
minimum latest finish is the earliest finish (MILP = earliest_finish).
They separate once decisions hold several robots. Nothing was tuned to
pass the gate. Which quantum the main study uses is the researcher's
decision (see fifo-window note in S0b).

#### Headless, W100, 30 robots, 300 orders, seed 42 (one workload)

```text
                 quantum 0                       quantum 3
                 order mk   distance  waiting    order mk   distance  waiting
nearest_astar     873.33    35134 m   244.7      896.67    34738 m   482.0
earliest_finish   862.00    35200 m   232.0      884.67    34660 m   520.3
multi_criteria    859.33    34882 m   250.7      887.67    34360 m   465.0
auction           874.67    36562 m   271.0      917.00    36292 m   511.7
hungarian         861.00    35116 m   215.3      872.67    34446 m   465.0
milp              865.33    35104 m   244.3      888.67    34770 m   585.3
```

All 300/300, 0 reservation violations. One workload: not a ranking.

#### Found on the way

- MILP was slow: per-pair bottleneck rows, 118 s for the opening
  100 x 100 decision. Rewritten with per-robot and per-order rows and
  an exact upper-bound prune; 100 robots / 300 orders / quantum 3:
  allocator CPU 132 s -> 3.6 s, worst decision 118 s -> 1.9 s.
  Exactness checked against brute force on 25 random decisions.
- hungarian, 100 robots, 300 orders, quantum 3 stalls at t = 450.7 s
  in a 6-robot waiting circle on W100 (no rotaries). Traffic
  infrastructure, not the strategy; recorded in
  .scratch/traffic-liveness/issues/01-cyclic-deadlock-under-scattered-allocation.md.
- MILP ties between equally optimal matchings are resolved by HiGHS;
  pixi has scipy 1.17, the ROS node's system Python 1.11.4, so an exact
  tie could resolve differently in Gazebo than headless.

### Traffic liveness: diagnosis and proposed fix

Status: approved and implemented 2026-10-27, committed in checkpoint
aa77ee0; accepted by the researcher as an implemented foundation 2026-10-27. Rule recorded in DECISIONS.md, "Rotation Right-of-Way".
Full trace in
.scratch/traffic-liveness/issues/01-cyclic-deadlock-under-scattered-allocation.md.

Cause. A controller rule, not a physical jam. Six robots fill a 6-cell
ring (W100 has 24; one-way grids always have cycles). All six ask to
advance in the same batch, and the reservation table would accept the
rotation. But the contention rule hands one ring cell to a robot
*outside* the ring (closer to its goal), the ring member waits, so the
whole ring waits, so the outsider's target never frees: the identical
decision repeats at every retry. The original V2 rotary deadlock is the
same mechanism on a 4-cell ring.

Proposed change, `local_traffic_controller.py` only:

```text
resolve(), after the head-on step:
  rotation members = robots on a closed cycle (length >= 3) of
                     robot -> holder of its target, within the batch
contention for a cell:
  a rotation member wins the cell it is rotating into; otherwise the
  existing priority (busy, remaining distance, robot id) is unchanged
propagation, begin_moves_batch, reservations, engine: unchanged
```

At most one rotation member can contend for a cell (each robot has one
target), so the rule is well defined and deterministic. It never
reassigns orders, reroutes, or depends on the strategy.

Why recovery-by-contention rather than prevention: capping occupancy of
each ring needs every cycle enumerated (longer cycles are
combinatorial), changes throughput in runs that complete today (moving
every frozen baseline), and still leaves the long cycles. This rule
changes a decision only in a state that today can only end in STALLED,
so every run that completes today stays byte-identical.

Prototype evidence: the W100 stall (hungarian, 100 robots, quantum 3)
completes 300/300 and the V2 rotary stall 500/500, 0 violations; four
completed runs and the V2 frozen baseline are byte-identical with the
rule fired 0 times.

Residual risk, kept loud: a wait cycle through a robot outside the
movement batch (servicing, charging, side-stepping, a protected service
move) is not a rotation and still ends as STALLED, a failed run
(EXPERIMENT_PROTOCOL.md, "Stalled Runs"). None observed.

#### Implemented

```text
simulation_env/local_traffic_controller.py   rotation_members(); a
                                             member wins its target;
                                             two members claiming one
                                             cell raises (cannot happen)
simulation_env/test/test_traffic_rotation.py new, 17 tests
scripts/compare_paired.py                    --allow-incomplete report
                                             (E1 gap, below)
```

Measured after implementation:

```text
W100 hungarian 100 robots 300 orders q3   300/300, 0 violations, 2 overrides
V2 random seed 42 orders_500              500/500, 0 violations, 1 override
17 previously completed W100 runs         rule fired 0x; decisions,
  (8 strategies, q0/q3, 30/100 robots)    outcomes, metrics byte-identical
V2 frozen baselines                       268.6667 s / 5798.0 m,
                                          265.3333 s / 5974.0 m,
                                          266.6667 s / 6488.0 m
traffic verification scripts              8/8 pass
full suite                                562 passed; ruff clean
```

The rotation tests fail with the rule switched off (traced W100 stall,
4- and 6-cell ring vs outsider, two rings); the unchanged-contention
test passes either way.

#### E1 reporting gap closed

`compare_paired.py --allow-incomplete` used to print a warning and
write an ordinary-looking paired.md. paired.md now opens with
"INCOMPLETE EXPERIMENT -- not a complete result", the present/expected
run count, every missing run with its status (stalled / failed / not
run), runs outside the grid (left out, never compared), and per
comparison the expected n, paired n and seeds left out; paired.csv
gains expected_n and complete. Strict mode is unchanged.

### Popper environment

Status: installed and smoke-tested 2026-10-26. Environment only; no
ILP code.

```text
swi-prolog    10.0.0, conda-forge (janus-swi links to this one)
clingo        5.8.2, conda-forge
setuptools    80.10.2, conda-forge, capped <81: Popper 4.4.0 imports
              pkg_resources, which newer setuptools removes
popper-ilp    git tag v4.4.0 = commit 860cd0de (pixi pypi git dep).
              Its metadata says 4.2.0: upstream did not bump it, so
              cite the commit. main / v5.x needs Python >= 3.14.
janus-swi     1.5.3; python-sat 1.9.dev17 and bitarray via Popper
```

Smoke test (scratch problems, one Popper problem per process):

```text
grandparent   grandparent(A,B) :- parent(A,C), parent(C,B).   4/0/5/0
assign-shaped assign(D,R,O) :- nearest(D,R,O).               6/0/30/0
              same rule with noisy=True (MaxSAT via python-sat)
```

Runtime facts for the ILP code:

- Run one learning problem per Python process: a second
  `learn_solution` in the same process failed inside Popper
  (`KeyError: ('neg_fact', ...)`) although each problem alone works.
- Popper emits a harmless `pkg_resources is deprecated` UserWarning.
- The shell's ROS `PYTHONPATH` makes the pixi Python see ROS packages;
  Popper works either way, but ILP scripts are cleanest run with it
  unset.

Verified on the way (W100, nearest_astar, seed 42, 300 orders):

```text
robots  completed  decision rows  outcome rows  violations  order makespan  fleet settled
  30    300/300         8566           300           0          873.33 s       888.00 s
 100    300/300        25071           300           0          314.00 s       380.67 s
```

Phase partition holds (productive + support + recovery distance =
total) in both. exs.pl at 30 robots: 300 pos, 8266 neg. Distinctness
gate at 30 robots / 300 orders: every pair distinct, status OK
(makespan 888.0 / 928.3 / 955.3 s for A* / Manhattan / random).

### M0: research integrity and experimental reproducibility

Status: implemented 2026-10-27 on `research/outcome-aware-ilp`; verified
by the researcher and committed 163c6a4. Scope: F5-F8 of the 2026-10-27 audit and the seed
split guard. Nothing in routing, traffic, battery, charging, execution or
any strategy's choices changed.

#### What was added

```text
simulation_env/workload_executor.py  F5: _decision_of, order -> the
                                     decision that assigned it, read at
                                     launch. F6: InvalidAssignmentError,
                                     WorkloadStalledError,
                                     _check_assignment_contract (every
                                     strategy, with or without battery)
simulation_env/failures.py           new: failure kinds, failure.json
simulation_env/seed_split.py         new: seed roles, check_purpose
simulation_env/dataset.py            build_manifest(strategy_identity=):
                                     run.json strategy_identity and
                                     strategy_sha256
simulation_env/paired.py             check_strategies; identity fields
                                     left out of the settings signature
simulation_env/fleet_node.py         the same identity, classification
                                     and failure.json in the ROS node
task_management/allocation_strategy.py
                                     identity() = parameters, execution,
                                     artifacts; artifact_record,
                                     identity_sha256;
                                     AllocationSolverError (milp)
scripts/run_workload.py              classified failures, failure.json,
                                     a stale failure.json cleared
scripts/run_experiment.py            --quantum, --window, --purpose;
                                     spec records them and every
                                     strategy's identity; summary.csv
                                     strategy_sha256; failure kind shown
scripts/compare_paired.py            check_strategies; a missing run is
                                     reported with its failure kind
test/test_m0_integrity.py            new, 38 tests
test/test_dataset.py                 helper passes the identity
test/test_experiment_integrity.py    pre-F6 log wording
```

#### Evidence

```text
F5  deferred launch keeps its decision_id, quantum 0 and 3: 40 V2
    orders, every third order's plans refused until two further
    decisions; >= 5 orders launch under a later decision, some after
    >= 2 refused attempts, deferred assignments of >= 2 decisions
    outstanding together. Every outcome row joins its selected decision
    row 1:1. With the old stamp (decisions - 1) restored, both tests
    fail at the decision_id assertion.
F6  each kind classified; end to end through run_workload.main:
    invalid assignment, solver failure, traffic deadlock, workload
    stall, execution error, eligibility violation. STALLED is printed
    for the two stall kinds only; a failed run writes failure.json and
    no metrics.json, run.json or tables.
F7  --quantum 3 --window 5, --quantum 0 and no flag reach every run.json
    and experiment.json; the two conditions get different settings
    signatures; --resume with another quantum is refused; a negative
    quantum, window 0 or an unknown strategy is refused before
    anything runs.
F8  every registered strategy has an identity; the seed is not part of
    it; multi_criteria weights, milp and hungarian solver builds and a
    rule artifact's content are; compare_paired refuses a strategy that
    ran as two identities, --resume refuses a changed identity.
seeds  roles disjoint; --purpose training refuses seed 5; the reserved
    final seeds are refused without --purpose final.
```

Full suite: 600 passed (562 before M0 + 38). ruff clean. The ROS
node's system interpreter (Python 3.12.3, scipy 1.11.4) imports every
changed module.

Frozen baseline compatibility, before (badbc59) against after (M0 tree),
same commands:

```text
V2 reference, 100 orders            makespan / distance       tables
  nearest_astar                     268.6667 s / 5798.0 m     identical
  nearest_manhattan                 265.3333 s / 5974.0 m     identical
  random                            266.6667 s / 6488.0 m     identical
W100, 30 robots, 120 orders, q 3    hungarian, milp, earliest_finish,
                                    multi_criteria, auction   identical
```

Identical means: decisions and outcomes equal row for row (run_id
aside), every non-CPU metric in metrics.json equal. run.json differs
only in git state, run_id, strategy_identity and strategy_sha256.

#### What a reader of older runs must know

- `run_id` of a new run differs from an older run of the same inputs:
  the manifest now names the strategy identity. The tables are
  unchanged and `schema_version` stays 1.
- milp and hungarian record their scipy build, so a headless run (pixi,
  1.17.1) and a ROS node run (system, 1.11.4) are different strategy
  identities with different run_ids. The others still agree.
- compare_paired.py refuses a summary.csv without strategy_sha256, that
  is, any experiment made before M0. Rerun it, or compare it with the
  pre-M0 code.
- Run logs print `STALLED:` only for traffic_deadlock and
  workload_stall; any other failure prints `FAILED:` and every failure
  prints `FAILURE KIND: <kind>`.
- The ROS node now catches every exception at the end of a workload
  (previously RuntimeError only), logs its kind, logs the traceback of
  an execution_error and writes failure.json when recording.

#### Outcome schema: completed, deferred, incomplete and failed orders

Checked against the code, not changed:

```text
completed   one outcome row, written at retirement; success = true
deferred    one outcome row like any other. decision_id is the deciding
            decision (F5); launched_at is the real launch, so the
            deferral delay is launched_at minus that decision's
            sim_time, by join. realized_duration_s starts at launch and
            excludes the deferral wait.
incomplete  cannot occur in a written dataset: run() raises unless every
            order completed.
failed run  no tables are written; failure.json counts completed,
            pending, in-flight and deferred orders. No outcome is
            invented for an order that did not finish.
```

So `success` is true on every written row and is not a failure
indicator: failure is a run-level record. A failed run's partial
decision table is not kept; keeping it for failure analysis would be a
separate decision.

#### Not done in M0, required before outcome-aware training

F9 (statistics), F10 (per-order outcome attribution) and F12 (workload
variety) are recorded as requirements in EXPERIMENT_PROTOCOL.md,
"Requirements Before Outcome-Aware Training". The statistical and
data-split protocols are proposed there and in DECISIONS.md.

#### Backend changes the GUI contract should know

`src/dashboard_GUI/docs/control_center/API_CONTRACT.md` is Codex-owned
and was not edited. Additive changes it may need to reflect:

```text
run_experiment.py   new flags --quantum, --window, --purpose (the
                    contract currently accepts no experiment quantum or
                    window; GET /capabilities will now see them)
run directories     may hold failure.json: kind, stalled, exception,
                    message, order counts
run.json            strategy_identity (object), strategy_sha256 (text)
summary.csv         strategy_sha256 column, text
experiment.json     spec gains quantum, window, purpose, strategy_sha256
run.log             "STALLED:" only for stall kinds; "FAILED:" and
                    "FAILURE KIND:" lines are new
comparisons         pre-M0 summaries are refused (no strategy_sha256)
```

#### Commands

```bash
cd ~/rnd_mrta_ilp_research
export PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management
PY=python3          # inside the pixi environment

# M0 tests, then the whole suite and lint
$PY -m pytest src/simulation_env/test/test_m0_integrity.py -q
$PY -m pytest src/mrta_core/tests src/simulation_env/test \
    src/task_management/test -q
ruff check .

# frozen V2 baselines: 268.6667 s / 5798.0 m, 265.3333 s / 5974.0 m,
# 266.6667 s / 6488.0 m
for s in nearest_astar nearest_manhattan random; do
  $PY src/simulation_env/scripts/run_workload.py --strategy $s \
      --config src/simulation_env/test/fixtures/v2_reference.yaml \
      --quiet --progress 0 | grep -E "makespan  |total distance"
done

# the two conditions, a small grid each
$PY src/simulation_env/scripts/run_experiment.py --name m0_q3 \
    --quantum 3 --strategies nearest_astar hungarian milp \
    --robots 10 30 --seeds 101 102 103 --orders 100 --purpose training
$PY src/simulation_env/scripts/run_experiment.py --name m0_q0 \
    --quantum 0 --strategies nearest_astar hungarian milp \
    --robots 10 30 --seeds 101 102 103 --orders 100 --purpose training
$PY src/simulation_env/scripts/compare_paired.py --name m0_q3
```

### M1: outcome signal and congestion analysis

Status: diagnostics implemented 2026-10-28, awaiting review; not
committed. The measurement matrix below is the researcher's to run. Full
analysis, definitions and the label recommendation:
`.scratch/m1-outcome-signal/spec.md`.

Nothing in allocation, routing, traffic, battery, execution or the
dataset schema changed. The new code reads runs or observes a run without
altering it (tested).

```text
simulation_env/outcome_signal.py   estimate vs outcome per order, margins,
                                   availability, window flow, summaries
simulation_env/congestion.py       decision-time congestion / route-overlap
                                   features; FeatureObserver
simulation_env/rollout.py          clone before a decision, force an
                                   alternative, resume
scripts/outcome_signal.py          read-only report; refuses sealed seeds
scripts/m1_probe.py                run_workload-identical run + --features,
                                   --rollouts; refuses sealed seeds
test/test_m1_diagnostics.py        20 tests
```

Headline from the pilot (seed 101, 300 orders, nearest_astar):

```text
                                30 q0  30 q3  70 q0  70 q3  100 q0  100 q3
residual share of mission       0.8%   2.0%   2.5%   5.6%   5.6%    23.9%
median decision margin          8.5 s  9.7 s  3.0 s  3.3 s  2.3 s   1.7 s
residual > margin               6%     8%     27%    37%    43%     59%
```

The residual is 100% traffic waiting. The proposed congestion features
associate with it only weakly (|rho| <= 0.27). Clones resume exactly.
70-robot rollouts show alternatives beating the teacher on realized
pivot mission time and fleet waiting, but not on window flow (0.7%
spread).

Measured: 620 tests pass (600 + 20); ruff clean.

#### Matrix (the researcher runs it)

```bash
cd ~/rnd_mrta_ilp_research
export PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management
PY=~/rnd_mrta_ilp_ws/.pixi/envs/default/bin/python

# A. conventional strategies, both conditions: 6 x 4 x 3 = 72 runs each
for q in 3 0; do
  $PY src/simulation_env/scripts/run_experiment.py --name m1_q$q \
      --quantum $q --purpose training \
      --strategies nearest_astar earliest_finish multi_criteria hungarian milp random \
      --robots 30 50 70 100 --seeds 101 102 103 --orders 1000 --jobs 8
done

# B. outcome signal over A (read only)
$PY src/simulation_env/scripts/outcome_signal.py runs/m1_q3 runs/m1_q0 \
    --out runs/m1_signal

# C. congestion features, teacher nearest_astar: 4 x 2 x 3 = 24 probes
for q in 3 0; do for r in 30 50 70 100; do for s in 101 102 103; do
  echo $r $q $s
done; done; done | xargs -P 6 -n 3 sh -c \
  "$PY src/simulation_env/scripts/m1_probe.py --out runs/m1_probe/r\$0_q\$1_s\$2 \
     --robots \$0 --orders 1000 --workload-seed \$2 --quantum \$1 --features"

# D. rollouts: quantum 3, 70 and 100 robots, 30 decisions each
for r in 70 100; do
  $PY src/simulation_env/scripts/m1_probe.py --out runs/m1_rollout/r${r}_q3_s101 \
      --robots $r --orders 1000 --workload-seed 101 --quantum 3 \
      --rollouts 30 --verify 2
done

# E. reports
$PY src/simulation_env/scripts/outcome_signal.py runs/m1_probe --out runs/m1_probe_signal
$PY src/simulation_env/scripts/outcome_signal.py runs/m1_rollout --out runs/m1_rollout_signal
```

Expected cost: A is 144 runs (about 1 h on 8 jobs, MILP at 100 robots
the slowest). C is 24 runs of about 1-2 min. Each D probe runs about
120 branches at 3-6 s, plus three full runs. Every probe exits 1 if a
clone check fails.

#### M1 Acceptance and Transition to M2 (2026-10-29)

M1 accepted by the researcher based on the 300-order pilot matrix and counterfactual checks.
Frozen in DECISIONS.md:
- 1,000-order charging-active matrix deferred as a separate check before final evaluation.
- Primary outcome parameters: H = 120 s, lambda = 0.50, delta = 2.0 s (sensitivity: lambda=0.25, delta=5.0 s).
- Score J = pivot_mission_s + lambda * waiting_in_h_s over H=120s; window_flow_s rejected.
- Seeds 104-110 reserved for ILP induction; 101-103 development probes; 111-115 validation; 201-230 sealed final.
- Primary matching: M-Card; ablation: M-Greedy. Queue-first fallback tie-break.
- Target: assign(D,R,O) with max_vars=4, max_body=4, K_max=5, k_neg=3.
- M5 multi-teacher >=4/6 consensus open; plurality comparator to be evaluated.
- Active milestone is M2: ILP Data Layer & Pipeline Infrastructure (outcome-independent).

## Definition of Done for Phase B (superseded)

Superseded 2026-10-23 by the one-map W100 decision (DECISIONS.md,
"Warehouse Scalability"); kept as history. It read:

```text
1. baseline simulator state is clean and reproducible
2. W20, W30, and W50 maps exist
3. all three maps pass topology and reachability gates
4. their active robot capacities are validated
5. docs reflect final map names and paths
```

After that, move to Battery Scheduler V1.
