# M6 Outcome-Aware ILP: design, bias controls and limitations

Status: ready-for-human (M6-2 implemented 2026-11-06, uncommitted; not trained at scale)

## M6-A, primary (implemented in M6-2)

| | M4 | M5 | M6-A |
|---|---|---|---|
| States | `runs/m4_corpus/q3`, nearest_astar, seeds 104-110 | same | same; replayed and proven identical |
| Decisions in bk.pl | `stratified_decisions`, cap 800, corpus seed 20261029 | same | same |
| bk.pl, bias.pl, thresholds | extended + Tier B (`_eb`) | same bytes | same bytes (checked at 70 and 100 robots) |
| Labels | multi_criteria picks | panel votes | rollout J, `supervision/outcome_aware.py` |

Pipeline:

```text
runs/m4_corpus/q3/nearest_astar/r<N>_s<seed>     (read only)
  -> python -m ilp_learning.m6_rollouts            replay, compare every decision column
                                                   but run_id, refuse on any difference;
                                                   probe teacher pivot + 3 alternatives,
                                                   H = 120 s, one exact clone check
  -> runs/m6_rollouts/q3/r<N>_s<seed>/{rollouts.csv, probe.json, replay/}
  -> python -m ilp_learning.train_rules ... --extended --tier-b --outcome-rollouts DIR
       outcome_labels -> outcome_labels.csv (provenance per branch)
       outcome_examples -> exs.pl (pos / neg only)
       bk.pl, bias.pl, thresholds: unchanged code path, same bytes as M4/M5
  -> rule_artifact.json, teacher "outcome_aware", "supervision" block
  -> run_workload.py --strategy ilp --rule-artifact ... (rule_trace: rule vs fallback)
```

Label rules (frozen contract J = pivot_mission_s + 0.5 * waiting_in_h_s, H = 120 s, delta = 2 s):

```text
valid      completed; censored (window or pivot unfinished in H, J not finite) and
           failed (stalled) branches are never examples
pos        unique best valid branch, beating the second best by >= delta
neg        valid branch >= delta worse than the best, also without a unique winner
abstain    everything else; untested pairs are not examples at all
conservative (primary)   no positive at a decision with any censored or failed branch
m6_1 (sensitivity)       positive among valid branches regardless
```

## What prevents imitation of the teacher

- Label calculation reads rollout rows only: pair, J inputs, status. It does not import
  the teacher panel, single- or multi-teacher supervision, or read `selected`.
- Which branch is the teacher's never decides a label: rows are ranked by score, then
  by order id. Tests rename the teacher branch and flip `selected`; labels are identical.
- In the smoke run the teacher's own pair was labelled negative in 3 of 4 probed
  decisions. One seed, four decisions: not evidence.

## Bias that remains (state it in the report)

1. **State distribution.** Every state comes from a nearest_astar trajectory. Rules are
   learned for situations nearest_astar gets into.
2. **Decision pool.** Probed decisions are those where nearest_astar's pick was a
   choice pair (`labelled_decisions`); decision 0 cannot be cloned and has no label.
3. **Action sampling.** Only one robot per decision is varied: the pivot, the robot
   nearest_astar gave its oldest order. The alternatives are that robot's next orders by
   finish estimate. Pairs nearest_astar would never consider are rarely tested.
4. **Continuation policy.** After the forced pair, every later decision in the rollout is
   made by nearest_astar. J therefore measures "this pair, then nearest_astar", not the
   value of the pair under the learned policy.
5. **Censoring.** Censored branches are slower outcomes; dropping them, and withholding
   positives in conservative mode, removes the decisions where traffic was worst.

## M6-B, mixed-policy states (designed, not built)

- Collect states with run_experiment on seeds 104-110, 70 and 100 robots, quantum 3,
  from nearest_astar, multi_criteria, auction, and random restricted to eligible pairs.
- Probe each with the same rollout and label code; continuation is each run's own
  strategy, recorded in probe.json.
- For a direct comparison with M4/M5, those must be retrained on the same mixed pool
  (teacher panel labels exist for any driven run), so the three still differ in labels
  only. Report M6-B against M6-A as a state-distribution sensitivity, not a new method.

## M6-C, learned-policy states (optional, designed, not built)

- Run the M6-A rules as the driving strategy on 104-110, probe its states, retrain.
  One or two rounds, DAgger-style. Continuation would then be the learned policy, which
  answers bias 4 above. Risky in time; only if M6-3 shows M6-A is usable.

## M6-3 (next)

1. The user runs the full rollout corpus (commands in CURRENT_PHASE.md).
2. Train M6-A at 70 and 100 robots, conservative, plus m6_1 sensitivity.
3. Closed-loop on validation seeds 116-120 (`--purpose backup`), with M4 `_eb`, M5 and
   nearest_astar, multi_criteria on the same seeds; report rule vs fallback share from
   rule_trace, not only makespan.
4. Rule-coverage analysis: share of picks made by a rule, per clause.
5. Optional Tier A vs Tier A+B (H3) with the same labels.

## M6-MP: multi-pair arm, candidate set mp1 (prototype 2026-11-06, data only)

### Why: the frozen J does not compare different orders or robots fairly

Measured on every probed decision so far (M6-1 profile, 2000-order check; 48 decisions):

| | 300 orders, 70 r | 300 orders, 100 r | 2000 orders |
|---|---:|---:|---:|
| Spearman, `pivot_mission_s` vs decision-time finish estimate | 0.80 | 0.62 | 0.60 |
| Spearman, `waiting_in_h_s` vs finish estimate | 0.40 | 0.00 | -0.04 |
| median spread across branches: mission / 0.5 x waiting (s) | 13 / 29 | 15 / 104 | 39 / 57 |
| best J is the shortest-estimate order | 35% | 30% | 50% |

- Same workload in every branch: yes (clones of one state).
- `pivot_mission_s` is the forced pair's own mission, so between different orders it mostly
  measures how long that order is. The order the strategy would have assigned instead is
  still pending and is not charged to the branch. J rewards a short order even when it only
  defers the long one.
- Between different robots on the same order, `pivot_mission_s` is comparable.
- `waiting_in_h_s` is fleet-level and carries most of the spread; it is not a function of
  order length.
- Continuation is the run's own strategy (nearest_astar) in every branch.
- 120 s against charging: at 2000 orders, 100 robots, 6 of 16 branches censored, including
  3 of 4 at the one decision with battery-blocked pairs.

So the frozen J stays valid for M6-A as specified (one robot, J over its own mission plus
fleet waiting), with the bias above stated. It is **not** a valid label for mp1, where
branches move orders between robots. `outcome_aware` refuses mp1 rollouts.

### Proposed, not adopted: a common-workload objective J_W (needs approval)

```text
A(d)    the orders any branch of decision d assigns or displaces (forced, displaced, swap)
J_W(b)  = sum over o in A(d) of (completed_at_o(b) - t_d)  +  lambda * waiting_in_h_s(b)
```

Every o in A(d) is a window order, every branch runs until its window is done, so every
term exists in every completed branch: the same work is charged in each. Same lambda,
delta, censoring rules. mp1 rows already record `window_completion_s` (every window
order), so J_W, or any other common-set objective, can be computed from them afterwards
without re-running. Open: lambda for |A| > 1 (mission terms grow with |A|, waiting does
not); whether delta scales with |A|.

### Dataset identity (reusing existing files)

```text
state        (run_id, decision_id), state_sha256 over every decisions.parquet column but
             run_id, selected, strategy                       outcome_aware.state_hashes
candidate    (state, robot_id, order_id), eligible in decisions.parquet
proposal     teacher_labels.parquet: each panel strategy's pair at this same state
             -> candidate_sources (provenance only)
action       ForcedPair: forced pair, displaced pairs, swap, pairs before/after
outcome      rollouts.csv row: status, pivot_mission_s, waiting_in_h_s, window_completion_s
sampling     probe.json: decisions probed, pool, candidate_set, max_actions, seed;
             per row candidate_rank, candidate_p_select (1/n for the random control)
replay       probe.json: replay_identical, clone_checks, rollouts_sha256
```

### Stratified decision sampling (designed, not built)

Strata from decision-time columns only: `robots_charging > 0` (corrected), any
battery-ineligible pair, min `energy_margin_percent` tercile, `st_conflicts` / local density
tercile (features.parquet), choice size (eligible pairs) tercile, and a uniform control
stratum. Fixed quota per stratum, seeded draw, selection probability recorded per decision.
The same sampled decisions serve M4, M5 and M6 (bk.pl built from them).

## jw1 pilot result: BLOCKER (2026-11-06)

Pilot: `runs/m6mp_pilot` (fixed-code corpus, 70 robots, 2000 orders, seed 104; mp1,
stratified quota 1, 5 actions; 6 decisions, 30 branches, all completed, replay identical,
clone verified). jw1 labels: 6 pos, 20 neg, 0 abstain, 4 duplicate joint actions.

Every window order (70) completed in every branch, so the whole-window sum can be
compared with J_W over A(d):

| decision | best by J_W | best by window sum | Spearman J_W vs window |
|---:|---:|---:|---:|
| 34 | c1 | c4 | -0.16 |
| 85 | c1 | c0 | -0.05 |
| 280 | c4 | c0 | -0.68 |
| 439 | c4 | c4 | 0.90 |
| 571 | c1 | c0 | -0.37 |
| 624 | c3 | c3 | 0.90 |

Orders outside A(d) move by up to 2,706 s between branches (decision 280), more than the
orders inside it. A fixed A(d) therefore leaves out most of what a decision changes: a
branch that serves an old order now (auction's proposal, best by J_W in 3 of 6) pushes
the delay onto orders J_W does not count. jw1 does not fairly compare candidate
assignments. No M6 rule is trained on it. Choosing a replacement is the researcher's
decision (options in CURRENT_PHASE.md).

## jw2 validation (2026-11-06): passes comparability checks, with stated limits

24 stratified decisions (12 at 70 robots, 12 at 100; seed 104, 2000 orders, mp1, 5 actions),
112 branches, replay identical and clone verified in all 8 shards; output
`runs/m6mp_pilot/jw2_validation.txt`.

- Same window set in every branch (mean 63 orders at 70 robots, 100 at 100); every window
  order completed in every branch: 0 censored, 0 failed; 6 exact duplicate joint actions
  scored identically.
- Labels: 70 robots 12 pos / 39 neg / 0 abstain; 100 robots 12 pos / 43 neg / 0 abstain.
  Conservative and m6_1 identical (nothing censored).
- lambda sensitivity: label agreement with lambda 0.5 is 0.94-1.00 for lambda 0, 0.25, 1;
  best branch unchanged at lambda 0 and 1 in 22 of 24 decisions. Flow spread (8-6,279 s)
  dwarfs 0.5 x waiting spread (3-1,026 s): the waiting term barely matters.
- Periods differ: waiting over 120 s, completions over 214-2,641 s. Flow already contains
  the window orders' own waiting, so the mismatch changes little (previous point).
- delta = 2 s never binds: gaps to the second-best branch are 2.4-3,145 s, 0 abstentions.
- jw1 vs jw2: same best branch in 3 of 12 decisions at each size.
- Behaviour (nearest_astar) branch best: 4 of 12 at 70 robots, 0 of 12 at 100.
- jw2 vs orders completed within 120 s (all orders): median Spearman 0.0 and 0.15.

Limits: long-horizon credit assignment (a branch's effect is measured over up to 44 min of
nearest_astar continuation); orders entering after the window are not counted; one seed.

## Cross-policy pilot (2026-11-07): GATE NOT PASSED for positive labels

`runs/m6mp_pilot/xpolicy_r70`, `xpolicy_r100`; report `runs/m6mp_pilot/xpolicy_report.txt`.
6 stratified decisions per size, 5 candidates, continuations nearest_astar, multi_criteria,
auction (90 branches per size). Replay identical and clone verified in all 8 shards; 0
unavailable, 0 censored.

| | 70 robots | 100 robots |
|---|---:|---:|
| same best candidate under all three policies | 1 of 6 | 0 of 6 |
| Kendall tau, median, per policy pair | -0.33 to 0.10 | -0.40 to 0.30 |
| regret of one policy's best under another, median | 0 to 797 s | 200 to 3,277 s |
| cross-policy labels pos / neg / abstain / excluded | 0 / 14 / 12 / 4 | 0 / 13 / 15 / 2 |
| policy_conflict (pos under one, neg under another) | 10 | 13 |
| per-policy positives (auction, multi_criteria, nearest_astar) | 6, 6, 5 | 5, 6, 6 |
| branch wall time mean: auction / multi_criteria / nearest_astar | 10 / 38 / 51 s | 25 / 85 / 109 s |

Peak memory 1.6-2.2 GB per process. Reading: which candidate is best under jw2 depends on
the continuation policy almost entirely; only "worse under every policy" is consistent
(14 and 13 negatives). No positive survives the all-policy rule, so Popper cannot learn
assign rules from these labels. Decision needed before any campaign.
