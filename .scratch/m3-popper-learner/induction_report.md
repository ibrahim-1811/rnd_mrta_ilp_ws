# M3 induction report: arm A on seeds 104-110

Status: ready-for-human

Date 2026-10-31. Code at clean HEAD `6a9e0bc`. No seed outside 104-110 was used. All results are on induction data, so nothing here is a gate result: G1, G3, G4 and G5 are measured on validation.

## Summary

- At all three quantum 3 conditions and at 30 robots quantum 0, Popper returned exactly R∧O (`best_cost_for_order, nearest_static`), including in 7/7 LOSO folds.
- At 70 and 100 robots quantum 0, it returned a different 3-clause and 2-clause program. Frozen-key fidelity there is 0.962 and 0.946, under the 0.98 line, and distinct-cost is 0.981 and 0.987, under the required 1.0. Hand-written R∧O reaches 0.998 and 0.988 on the same data.
- Measured cause: the learned programs beat R∧O on Popper's own objective by 2 units (384 against 386, 904 against 906) on the sampled examples.
- The script's stop condition was not met: no rule is empty, the primary conditions pass the line, and the shortfall is 0.02-0.03. It is reported as a likely G1 failure at those two conditions, and nothing was tuned.

## What was run

| Step | Count | Result |
|---|---|---|
| Teacher runs, `runs/m3_induction/{q3,q0}` | 42 of 42 | All 300 orders complete, no failure.json, every manifest `6a9e0bc`, dirty false |
| Arm A, all seven seeds | 6 of 6 | Exit 0, no Popper timeout |
| Arm A, leave-one-seed-out | 42 of 42 | Exit 0, no Popper timeout |
| Closed-loop `ilp` on 104-110, for the paired SD only | 42 of 42 | All 300 orders complete, 0 reservation violations, manifests clean |

Settings: spec budget (300 s, `max_vars 4`, `max_body 4`, K_max 5, `k_neg 3`, 24 literals), corpus seed 20261029, decision cap 800. Each training took under one minute. One sampling seed only, so G6 is not measured here.

## Measurement definitions

The pre-registration and spec 6.2 name these quantities without a formula. The definitions used, in `src/ilp_learning/ilp_learning/fidelity.py`, need the researcher's approval:

- Replay is offline and open-loop: each recorded decision's eligible pairs go through the fixed iterative executor, on the teacher's own state.
- Frozen-key fidelity = |picks ∩ teacher| / |teacher|, pooled over choice-bearing decisions. Over all decisions it differs by about 0.0001 at most.
- Tie-neutral: the same, comparing c_static_m multisets, so an equal-cost swap agrees.
- Distinct-cost: the same, after adding 1e-5 × fifo_position + 1e-7 × robot rank to costs, so ties follow the teacher's own order. This follows the Phase 0 perturbation test, with larger steps because the predicates' tolerance is 1e-9. Robot rank is an engineering tie-break here only. The smallest gap between distinct costs is 1 m.
- Exact-decision: share of decisions whose whole pick set equals the teacher's.
- Rule coverage: share of picks made by a rule. Fallback is the rest.
- G5 training fidelity: pooled over all decisions of the six training seeds, not only the sampled ones.

## Learned rules

R∧O below means `assign(D,R,O):- best_cost_for_order(D,R,O),nearest_static(D,R,O).`

| Quantum | Robots | Program | Artifact sha256 |
|---|---|---|---|
| 3 | 30 | R∧O | `66bf6af4bfe2` |
| 3 | 70 | R∧O | `c9cd145a907e` |
| 3 | 100 | R∧O | `89d8f3e8f367` |
| 0 | 30 | R∧O | `5bc8e36b8f0c` |
| 0 | 70 | 3 clauses, below | `a50e120cbf5c` |
| 0 | 100 | 2 clauses, below | `00196cfab88b` |

Quantum 0, 70 robots:

```prolog
assign(D,R,O):- best_cost_for_order(D,R,O),earliest_static_finish(D,R,O),nearest_static(D,R,O).
assign(D,R,O):- earlier_static_finish(D,R,V0,O),nearest_static(D,R,O),prior_fifo(D,O,V0).
assign(D,R,O):- closer_static(D,R,O,V0),earliest_static_finish(D,R,V0),nearest_static(D,R,O).
```

Quantum 0, 100 robots:

```prolog
assign(D,R,O):- best_cost_for_order(D,R,O),earliest_static_finish(D,R,O),nearest_static(D,R,O).
assign(D,R,O):- best_cost_for_order(D,R,O),closer_static(D,R,O,V0),earliest_static_finish(D,R,V0),nearest_static(D,R,O).
```

## Fidelity on induction data

All seven seeds pooled, choice-bearing decisions. The rule was fitted on these seeds, so this is training fidelity.

| Quantum | Robots | Decisions | Teacher pairs | Frozen key | G1 line | R∧O by hand | Tie-neutral | Distinct-cost | Exact decision | Rule coverage | Fallback |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 3 | 30 | 959 | 2,096 | 0.9986 | 0.90 | 0.9986 | 1.0000 | 1.0000 | 0.9969 | 1.0 | 0.0 |
| 3 | 70 | 492 | 2,098 | 0.9971 | 0.90 | 0.9971 | 0.9990 | 1.0000 | 0.9919 | 1.0 | 0.0 |
| 3 | 100 | 401 | 2,099 | 0.9828 | 0.90 | 0.9828 | 0.9929 | 1.0000 | 0.9800 | 1.0 | 0.0 |
| 0 | 30 | 1,775 | 2,094 | 1.0000 | 0.98 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0 | 0.0 |
| 0 | 70 | 1,363 | 2,095 | **0.9623** | 0.98 | 0.9976 | 0.9957 | **0.9809** | 0.9589 | 1.0 | 0.0 |
| 0 | 100 | 1,085 | 2,096 | **0.9461** | 0.98 | 0.9881 | 0.9919 | **0.9866** | 0.9502 | 1.0 | 0.0 |

Bold marks values under the G1 requirement. By amendment item 5, high fidelity is not evidence of learning on its own: the greedy sequence comes from the executor.

Frozen-key fidelity is dominated by few decisions at high fleet size. Seed 104 scores 0.913 at 100 robots quantum 3, and hand-written R∧O scores 0.917 there at quantum 0, where the other six seeds all score 1.0. For R∧O, exact-decision share stays at or above 0.98, so one large initial batch appears to carry most of the loss (inferred from the decision and pick counts; the cascade itself was not traced).

## Rule quality (G1b)

Static precision and recall: clause coverage on the full eligible pair set, before any pick is removed, against all teacher pairs of choice-bearing decisions. Recall is low by construction at batch decisions, because many teacher picks only become R∧O pairs after earlier picks. Unexplained-positive share is 1 minus program recall. Picks are made in the iterative replay; a pick is correct if the teacher made it.

| Quantum | Robots | Clause | TP | FP | FN | Precision | Recall | Picks | Correct picks |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 3 | 30 | 0 / program | 1,195 | 52 | 901 | 0.958 | 0.570 | 2,096 | 2,093 |
| 3 | 70 | 0 / program | 833 | 77 | 1,265 | 0.915 | 0.397 | 2,098 | 2,092 |
| 3 | 100 | 0 / program | 789 | 84 | 1,310 | 0.904 | 0.376 | 2,099 | 2,063 |
| 0 | 30 | 0 / program | 1,826 | 114 | 268 | 0.941 | 0.872 | 2,094 | 2,094 |
| 0 | 70 | 0 | 1,346 | 121 | 749 | 0.918 | 0.642 | 1,954 | 1,878 |
| 0 | 70 | 1 | 18 | 4 | 2,077 | 0.818 | 0.009 | 14 | 14 |
| 0 | 70 | 2 | 119 | 17 | 1,976 | 0.875 | 0.057 | 127 | 124 |
| 0 | 70 | program | 1,459 | 139 | 636 | 0.913 | 0.696 | 2,095 | 2,016 |
| 0 | 100 | 0 | 1,099 | 104 | 997 | 0.914 | 0.524 | 1,921 | 1,813 |
| 0 | 100 | 1 | 133 | 18 | 1,963 | 0.881 | 0.063 | 175 | 170 |
| 0 | 100 | program | 1,216 | 120 | 880 | 0.910 | 0.580 | 2,096 | 1,983 |

Rule coverage of 1.0 with 0 fallback holds under iteration: something always fires on what remains. On the raw pair set, the rules cover only 0.5-3.2% of eligible pairs (artifact metrics).

## Why quantum 0 did not return R∧O

Scores on the sampled examples, with Popper's cost as FN + FP + size. The learned program's counts match Popper's reported score.

| Condition | Program | TP | FN | FP | Size | Cost |
|---|---|---:|---:|---:|---:|---:|
| q0, 70 | R∧O | 866 | 283 | 100 | 3 | 386 |
| q0, 70 | Learned | 842 | 307 | 65 | 12 | 384 |
| q0, 100 | R∧O | 930 | 791 | 112 | 3 | 906 |
| q0, 100 | Learned | 908 | 813 | 82 | 9 | 904 |

Measured: Popper finished inside its budget and the learned programs score 2 units better than R∧O on this objective. The noisy objective trades 22-24 more FN for 30-35 fewer FP.

Inferred: those FP are mostly pairs that are correct under iteration or lost only on a tie, and the static score cannot see this, because hand-written R∧O has higher replay fidelity on the same seeds. The margin is thin enough to flip with the training subset, as the LOSO text shows below.

## G5: leave-one-seed-out

Drop = training fidelity minus held-out fidelity. SD is the sample SD (n-1) of held-out fidelity over 7 folds. Limit = max(worst drop, 2 × SD).

| Quantum | Robots | Worst drop | 2 × SD | Limit | Mean held-out | Lowest held-out | Folds returning R∧O |
|---|---|---:|---:|---:|---:|---:|---:|
| 3 | 30 | 0.0022 | 0.0036 | 0.0036 | 0.9986 | 0.9967 | 7/7 |
| 3 | 70 | 0.0083 | 0.0071 | 0.0083 | 0.9971 | 0.9900 | 7/7 |
| 3 | 100 | 0.0814 | 0.0628 | 0.0814 | 0.9828 | 0.9130 | 7/7 |
| 0 | 30 | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 1.0000 | 7/7 |
| 0 | 70 | 0.0262 | 0.0382 | 0.0382 | 0.9651 | 0.9398 | 1/7 |
| 0 | 100 | 0.0888 | 0.0933 | 0.0933 | 0.9642 | 0.8700 | 3/7 |

Per-fold values are in `runs/m3_induction/derived_choice_bearing.json`.

- Quantum 0, 30 robots: the rule gives a limit of exactly 0, so one wrong pick at validation fails G5 there.
- Quantum 3, 100 robots and quantum 0, 100 robots: seed 104 alone sets the limit in both.
- Quantum 0, 70 and 100 robots: the limit also absorbs rule-text instability between folds. The folds returned 6 and 4 distinct programs. This is a preview of a G6 risk, though LOSO folds are not G6 sampling seeds.

## G8: bounds and precision

delta is the sample CV (n-1) of nearest_astar over 104-110. All 12 values match the earlier development table to two decimals, so the clean and dirty teacher runs give the same deltas.

The paired SD needs an ILP run, so the 42 closed-loop induction runs were made with the full-data artifacts. Log ratio = ILP against nearest_astar, same workload. These SDs are in-sample for the rule, use 7 pairs while validation uses 5, and are therefore estimates. Maximum SD = delta / 0.953. Half-width = 0.953 × SD.

| Quantum | Robots | Metric | delta | Range, s | Paired SD | Maximum SD | Half-width at 5 pairs | Mean log ratio | Pairs that differ | Can show equivalence |
|---|---|---|---:|---|---:|---:|---:|---:|---:|---|
| 3 | 30 | order_makespan_s | 1.55% | 895.3-938.3 | 0.727% | 1.63% | 0.693% | -0.389% | 3/7 | Yes |
| 3 | 30 | mean_completion_time_s | 1.79% | 425.7-447.8 | 0.198% | 1.88% | 0.189% | -0.042% | 3/7 | Yes |
| 3 | 70 | order_makespan_s | 2.11% | 436.3-462.3 | 0.080% | 2.21% | 0.076% | -0.064% | 3/7 | Yes |
| 3 | 70 | mean_completion_time_s | 2.61% | 204.1-219.9 | 0.224% | 2.74% | 0.213% | -0.099% | 4/7 | Yes |
| 3 | 100 | order_makespan_s | 8.47% | 347.3-430.7 | 3.532% | 8.89% | 3.366% | +1.204% | 4/7 | Yes, cap undecided |
| 3 | 100 | mean_completion_time_s | 7.87% | 172.0-213.7 | 0.628% | 8.26% | 0.599% | +0.379% | 4/7 | Yes, cap undecided |
| 0 | 30 | order_makespan_s | 1.32% | 868.7-906.7 | 0 | 1.38% | 0 | 0 | 0/7 | Identical behaviour |
| 0 | 30 | mean_completion_time_s | 1.66% | 415.4-433.9 | 0 | 1.74% | 0 | 0 | 0/7 | Identical behaviour |
| 0 | 70 | order_makespan_s | 2.67% | 417.7-451.0 | 1.374% | 2.80% | 1.310% | -0.046% | 7/7 | Yes |
| 0 | 70 | mean_completion_time_s | 1.44% | 196.1-203.3 | 0.263% | 1.51% | 0.251% | +0.035% | 7/7 | Yes |
| 0 | 100 | order_makespan_s | 2.16% | 323.0-342.7 | 2.285% | 2.27% | 2.178% | -2.336% | 7/7 | **No** |
| 0 | 100 | mean_completion_time_s | 2.12% | 151.0-160.1 | 0.683% | 2.22% | 0.651% | -0.125% | 7/7 | Yes |

Precision statement: G8 cannot show equivalence on `order_makespan_s` at 100 robots quantum 0, because SD 2.285% exceeds 2.27%. The margin is small, so this could change with the estimate. The mean log ratio there, -2.336%, is also outside delta, in the ILP's favour, so that row is likely non-equivalent or inconclusive regardless.

SD alone does not promise a pass: the CI must also clear the mean offset. At 100 robots quantum 3, `order_makespan_s` sits at +1.204% ± 3.366%, and the delta cap there remains the researcher's open decision.

## Not done

- Validation, backup and sealed seeds: not touched.
- Battery probe, arm C, and any change to bias, budget or vocabulary: not run.
- G2, G6, G9, G10, G12 and the other baselines: not measured.
- No simulator, routing, traffic, battery or strategy code changed.

## Researcher decisions

1. Accept or change the measurement definitions above, especially frozen-key pooling and the distinct-cost jitter.
2. The quantum 0 rule at 70 and 100 robots is likely to fail G1 on validation, frozen key and distinct-cost. If it does, the pre-registered stop rule asks about arm C. Decide before validation whether quantum 0 stays as is, knowing this.
3. The G5 limit of 0 at 30 robots quantum 0: keep it or not. Any change needs a pre-registration amendment.
4. The G8 delta cap at 100 robots quantum 3 stays open. Measured: delta 8.47% and 7.87%, paired SD 3.532% and 0.628%. At the 5% example cap, maximum SD would be 5.25%.
5. The G8 row at 100 robots quantum 0 for `order_makespan_s` cannot show equivalence. Accept it as stated.
6. Approve the battery specification, still pending.
7. `m3-evidence-1` must be cut from a commit containing the same behaviour as `6a9e0bc`. `fidelity.py` and its test are uncommitted, so if the tag includes them, the teacher runs carry an older commit id. Decide whether to regenerate.

## Reproduction

Outputs live under `runs/m3_induction/` (ignored by git): `q3/`, `q0/`, `rules/`, `closed_loop/`, `fidelity.json`, `derived_choice_bearing.json`, `logs/`.
