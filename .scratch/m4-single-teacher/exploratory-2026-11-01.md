# M4 exploratory results, 2026-11-01

Status: ready-for-human

Exploratory only. Uncommitted tree, quantum 3, 300 orders, induction seeds 104-110, closed-loop on seeds the rules were trained on (104, 106, 108, 110). No validation or sealed seed was used. Nothing here is a gate result. Runs: `runs/m4_teacher_screen`, `runs/m4_corpus_dev`, `runs/m4_dev_rules`.

## Teacher screen: paired change against nearest_astar, 7 seeds

| Teacher | Makespan 30 | 70 | 100 | Waiting 100 |
|---|---:|---:|---:|---:|
| multi_criteria | -0.5% | -1.7% | -1.4% | -1.2% |
| hungarian | -0.4% | -1.5% | +3.3% | +24.1% |
| earliest_finish | -0.3% | -0.8% | +4.5% | +11.8% |
| milp | +0.5% | -2.0% | +9.7% | +60.1% |
| auction | +2.6% | +1.8% | +6.5% | +31.5% |

126 of 126 runs finished. Most gaps at 100 robots lie inside their 95% intervals (for example multi_criteria makespan ±5.3%, hungarian ±9.4%). milp is the clear one: its makespan interval is ±7.6% and waiting ±38%.

Reading: optimising static cost stops paying once traffic dominates. That is the gap H2 and H3 are about.

## Teacher agreement on the shared corpus

| Robots | Picked pairs with at least 4 of 6 votes |
|---:|---:|
| 30 | 22.6% |
| 70 | 12.9% |
| 100 | 9.2% |

Pair overlap at 100 robots: nearest_astar with earliest_finish 0.69; with multi_criteria 0.14; auction with anything at most 0.04. The panel teachers are not close variants, except nearest_astar and earliest_finish. Majority consensus (M2 spec policy C1) keeps little at the densities that matter, so it should not be the only M5 arm, as M1 record item 6 already asked.

## First M4 attempt: labels from multi_criteria

- It did not imitate the teacher. Program recall on sampled examples: 0.42 at 30 robots, 0.12 at 70, 0.05 at 100. Precision about 0.6.
- Closed-loop it was worse than its teacher: makespan +3.5% at 30, +3.9% at 70, +4.2% at 100 (Tier A).
- At 30 and 70 robots the closed-loop run is **identical to `auction`** on all four seeds (makespan and mean completion to the decimal; pair agreement 1.0 on the seed checked). The first clause, `best_cost_for_order, oldest_fifo`, is that auction: oldest order to its cheapest robot, and the matcher repeats it until nothing is left.
- So "rule picks 1.000" is not evidence of a good rule here. Its first clause fires on every pick and the rest never matter. G3 and G4 would pass on this.
- Tier A+B made no difference at 30 or 70 robots (same closed-loop numbers), and +2.8% against +4.2% at 100, on four training seeds, which is noise-level.

Likely cause, not proven: multi_criteria is a weighted sum normalised within each decision, which rank predicates cannot state. The M2 spec lists 31 predicates; the code has 12 Tier A and 7 Tier B, so its relative-cost, order-age and load predicates do not exist yet.

## Decision time

A learned Tier B program with witness variables took **95 s at its worst decision** at 100 robots, on seed 106, and 94.7 s when re-run alone, so it is not CPU contention. Worst per seed: 67-96 s; median 0.04 s. Almost all of a run's allocator CPU is that one decision. Cause not yet profiled. The hand-written Tier B rule took 2.6 s. Any rule like this fails G9 and H4 until fixed.

## Update 2026-11-02: extended vocabulary and M5

Clean commits, corpus `runs/m4_corpus` (21 of 21 runs), `nearest_astar` states with panel labels and features. Rules trained on seeds 104-110, closed-loop on 104, 106, 108, 110, so these are training-seed numbers, not gate results. Rules: `runs/m4_rules`, `runs/m5_rules`.

The 95 s decision is fixed: relations are indexed in the witness join. The same run takes 2.9 s, with all 25,170 decision rows identical. Worst decision across the runs below: 3.2 s.

`bk.pl` and `bias.pl` are byte-identical between M4 and M5 at 30, 70 and 100 robots, so these conditions differ in labels only.

### M4 still does not imitate multi_criteria

Pair fidelity to the teacher on the corpus: 0.376 at 30 robots, 0.121 at 70, 0.079 at 100 (extended vocabulary). Tier B added nothing (0.220, 0.124, 0.083). At 70 robots the extended rule is still identical to `auction` on 4 of 4 seeds.

Cause, measured by replaying the teacher's own greedy sweep on parts of its score:

| Score used | 30 robots | 70 | 100 |
|---|---:|---:|---:|
| All four criteria | 1.000 | 1.000 | 1.000 |
| Finish + order age | 0.606 | 0.475 | 0.420 |
| Finish + age + energy margin | 0.723 | 0.616 | 0.526 |
| Finish + age + charger distance | 0.759 | 0.595 | 0.540 |

So a perfect rule over finish time and order age could reach at most about 0.42-0.61. The battery terms decide the rest, and no predicate exposes them. M4's failure is mostly missing information, not a Popper failure.

### Closed loop against nearest_astar, percent

| Robots | Condition | Makespan | Mean completion | Waiting | Recall / precision on sampled examples |
|---:|---|---:|---:|---:|---|
| 30 | M4 multi_criteria | +0.9 | +6.4 | +13.8 | 0.42 / 0.67 |
| 30 | M5 majority | -0.4 | -0.1 | -0.1 | 0.83 / 0.90 |
| 30 | M5 plurality | -0.4 | 0.0 | +9.9 | 0.72 / 0.86 |
| 70 | M4 multi_criteria | +1.5 | +7.6 | +4.7 | 0.14 / 0.63 |
| 70 | M5 majority | +2.1 | +8.8 | +21.8 | 0.70 / 0.91 |
| 70 | M5 plurality | +2.1 | +8.8 | +21.8 | 0.53 / 0.81 |
| 100 | M4 multi_criteria | +5.2 | +4.7 | +33.3 | 0.06 / 0.63 |
| 100 | M5 majority | +2.3 | +0.7 | +6.9 | 0.69 / 0.97 |
| 100 | M5 plurality | +2.3 | +0.7 | +6.9 | 0.40 / 0.93 |

- Consensus labels are far easier to learn: 2-3 short clauses at precision 0.90-0.97 under majority.
- Neither condition beats `nearest_astar` by more than noise, and at 70 and 100 robots both are worse. That fits M4 and M5 being baselines: votes say what teachers agree on, not what worked.
- The two M5 policies give the same closed-loop result at 70 and 100 robots, because their first clauses coincide.
- Rules with congestion literals appear, but nothing here shows they help.
