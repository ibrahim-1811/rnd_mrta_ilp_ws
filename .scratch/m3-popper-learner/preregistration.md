# M3 pre-registration: Popper recovery of nearest_astar

Status: ready-for-human

Written before any validation run. No seed in 111-115 has been used for M3. Revised 2026-10-31 with the researcher's decisions. Takes effect when the G5, G8 and battery values below are filled from induction data and the researcher approves them. After that, any change to a gate, arm or value makes the result exploratory.

Semantics: docs/DECISIONS.md, "M3 Execution Semantics Amendment (2026-10-31)".

## Question

From teacher labels alone, can Popper select the conjunction expressing a teacher from a vocabulary with distractors, so that the fixed iterative executor reproduces that teacher?

Not tested: whether ILP discovers a better policy, or learns the greedy procedure.

## Data

| Role | Seeds | Use |
|---|---|---|
| Exploratory | 101-103 | Debugging only; never in a reported fit |
| Induction | 104-110 | Rule learning, thresholds, noise margins |
| Validation | 111-115 | One run of this protocol |
| Backup | 116-120 | Reserved and sealed; used only if validation is spent, with researcher approval |
| Sealed | 1-30, 201-230 | Not touched by M3 |

Conditions: 30, 70 and 100 robots at quantum 3 (primary) and quantum 0 (secondary), W100, window fleet_size, 300 orders. Teacher runs on 104-115 are new data.

## Arms and baselines

- Primary: arm A, all teacher positives, noisy Popper, with `max_vars 4`, `max_body 4`, K_max 5, `k_neg 3`, 300 s, 24 literals.
- Contingency: arm C, teacher-forced residual. Not authorised: it needs separate researcher approval, even if the stop rule triggers.
- Excluded: arm B, because filtering to explained positives assumes the target rule.
- Baselines, same harness: nearest_astar, auction, empty rule, R only, O only, hand-written R∧O, teacher-key ceiling, random-key, M-Card(R∧O), M-Greedy(R∧O).

R is `nearest_static`, O is `best_cost_for_order`.

## Gates

Measured on validation seeds unless stated.

| Gate | Measure | Pass |
|---|---|---|
| G1 | Fidelity to nearest_astar | Frozen key at least 0.98 at q0, 0.90 at q3 (spec 6.2); distinct-cost 1.0; tie-neutral and exact-decision share reported |
| G1b | Rule quality | Precision, recall, rule coverage and fallback reported separately per clause and per program. High precision with low coverage is not a pass |
| G2 | Discrimination | Random-key fidelity beats the best single literal or distractor pair by at least 0.05 at 70 and 100 robots |
| G3 | Rule coverage | At least 0.98 of picks made by a rule (spec 6.2) |
| G4 | Fallback | At most 0.02, logged per pick |
| G5 | Generalization | Induction-to-validation drop in frozen-key fidelity within the limit below |
| G6 | Stability | Same rule text on at least 2/3 sampling seeds; junk clauses reported |
| G7 | Executable behaviour | Closed-loop runs complete with 0 infeasible assignments and 0 eligibility violations |
| G8 | Equivalence | TOST on paired log ratio of makespan and mean mission time, against nearest_astar, margin below |
| G9 | Decision time (provisional) | Worst decision at most max(1 s, 1.5 × teacher worst at that condition); median at most 5 × teacher. Same machine, same workload. A runtime failure is reported as runtime, never as a policy-correctness failure, and the reverse |
| G10 | Controls, induction seeds | earliest_finish relabel gives different rule text; permuted labels give empty or fidelity below 0.1; removing O drops fidelity by at least 0.3 |
| G11 | Integrity | Clean tagged commit; bk.pl byte-identical across teachers; allowlist test passes; rule hash in strategy identity |
| G12 | Sample complexity | Reported, not gated |

### G5 limit (induction only)

Leave-one-seed-out over 104-110: train on six seeds, measure frozen-key fidelity on the seventh, per condition.

- Drop for a fold = training fidelity minus held-out fidelity.
- Limit, per condition = max(worst fold drop, 2 × SD of held-out fidelity across folds).

Computed and frozen before validation. Values, measured on 2026-10-31 at `6a9e0bc`, awaiting researcher approval. Fidelity definition and fold detail: [induction_report.md](induction_report.md).

| Quantum | Robots | Worst fold drop | 2 × SD | Limit |
|---|---|---:|---:|---:|
| 3 | 30 | 0.0022 | 0.0036 | 0.0036 |
| 3 | 70 | 0.0083 | 0.0071 | 0.0083 |
| 3 | 100 | 0.0814 | 0.0628 | 0.0814 |
| 0 | 30 | 0.0000 | 0.0000 | 0.0000 |
| 0 | 70 | 0.0262 | 0.0382 | 0.0382 |
| 0 | 100 | 0.0888 | 0.0933 | 0.0933 |

### G8 bounds and precision

Bound: delta = between-seed CV of nearest_astar on 104-110 for that metric and condition. Reason: a policy difference smaller than the spread caused by changing the workload is not one an operator could act on, and any tighter bound would be set inside workload noise.

Measured on 7 induction seeds, 300 orders, all completed:

| Quantum | Robots | delta, `order_makespan_s` | delta, `mean_completion_time_s` |
|---|---|---:|---:|
| 3 | 30 | 1.55% | 1.79% |
| 3 | 70 | 2.11% | 2.61% |
| 3 | 100 | 8.47% | 7.87% |
| 0 | 30 | 1.32% | 1.66% |
| 0 | 70 | 2.67% | 1.44% |
| 0 | 100 | 2.16% | 2.12% |

Recomputed from clean HEAD `6a9e0bc`, 42 teacher runs, all completed: every value above is unchanged to two decimals.

The 100-robot quantum 3 row is real spread, not one outlier: order makespan runs 347-431 s and total waiting 3,426-10,272 s across seeds, which shows congestion varying strongly by workload. A pass at that condition is weak evidence, because the bound allows about an 8% difference. Researcher decision needed: keep it, or cap delta (for example at 5%), which would make an inconclusive result more likely there.

Test: 90% CI of the mean paired log ratio, ILP against nearest_astar, 5 pairs, same workload.

| CI | Verdict |
|---|---|
| Inside ±delta | Equivalent |
| Entirely outside ±delta | Non-equivalent |
| Crosses a bound | Inconclusive, reported as such |

Precision: with 5 pairs the CI half-width is 0.953 × SD of paired differences. If that SD estimated on induction seeds exceeds delta / 0.953, G8 cannot show equivalence at that condition, and this is stated before validation. Identical behaviour gives zero paired spread; that is reported as identical, not as a statistical result.

Stated before validation, from 7 closed-loop induction pairs of arm A's full-data rule: G8 cannot show equivalence on `order_makespan_s` at 100 robots, quantum 0 (SD 2.285% against a maximum of 2.27%). All other rows can, on SD alone. 30 robots, quantum 0 was identical to nearest_astar on 7/7 seeds. Table: [induction_report.md](induction_report.md).

### Battery stress probe

Purpose: exercise eligibility and charging, which no probe so far has done (all had 0 ineligible rows and no charging).

| Item | Value |
|---|---|
| Configuration | Temporary copy of the one config with `battery.initial_soc_percent: 20`. All other battery values frozen. No engine change, and the copy is not added to the repo |
| Run | Seed 104 (induction), 30 robots, quantum 3, 300 orders, window fleet_size |
| Policies | nearest_astar and the learned policy, same configuration |
| Expected | Many pairs need about 18% SOC or more, so some are ineligible from the start; robots enter charging early; the learned rule covers fewer pairs, fallback rises |

Success, all required:
- Run finishes every order with no failure classification.
- 0 ineligible assignments, and no robot or order assigned twice.
- SOC stays above 0.
- Informative: at least one ineligible candidate row and at least one charging session. If either is absent, the probe is uninformative, not passed, and the starting SOC is lowered once.

Reported, not gated: fallback, rule coverage, charging sessions, teacher agreement.

## Stop rule

- If arm A fails G1 or G6 on at least 2/3 sampling seeds, stop and ask the researcher about arm C.
- If any G7 or G11 check fails, stop and report the limit.
- Validation runs once. A rerun needs a fresh pool and researcher approval.

## Reporting

Report every gate, including failures. Report unexplained-positive share, tie share, and rule lift over the best single literal. Name a passing result "ILP-recovered explanation of nearest_astar", and leave it out of strategy rankings.

## Researcher decisions (2026-10-31)

| # | Decision | Status |
|---|---|---|
| 1 | Training labels | All teacher positives approved. Arm C needs separate approval |
| 2 | Clause rank | Approved provisionally; metrics reported separately |
| 3 | M3 vocabulary | The 12 Tier A predicates, provided each is correctly defined, decision-time safe and tested |
| 4 | G5 | 0.02 rejected; limit to come from the rule above |
| 5 | Backup pool | 116-120 reserved and sealed |
| 6 | Battery probe | Approved in principle; specification above awaits approval |
| 7 | Evidence commit | `m3-evidence-1`, created only after the implementation and reproducibility gates pass |

G8 kept with justified bounds and a precision assessment. G9 accepted provisionally.

## Still needed before validation

Researcher approval of the G5 and G8 values above and of the battery specification, and a clean evidence commit.
