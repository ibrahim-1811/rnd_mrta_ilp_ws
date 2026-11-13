# Robust oracle results (2026-11-12)

Rule and stop rule: DECISIONS.md, "Robust Oracle Labels and the Combined Campaign".
A pick counts only if it beats the teacher's pair in all three futures (continued by
nearest_astar, multi_criteria, milp). Cluster checkout `b6ee9a9`.

## Pilot: nearest_astar, 100 robots, 300 orders, seeds 104-110 (job 477724, 74 min, 7/7 exit 0)

Output: `runs/gate2/o300/wholerun_robust/nearest_astar/r100_s<seed>/`.
Same 7 runs under the plain oracle (option A) for comparison.

| seed | teacher makespan | plain oracle | robust oracle | robust overrides |
|---|---|---|---|---|
| 104 | 347.3 | -10.0 | -6.0 | 2 |
| 105 | 427.7 | -68.7 | -15.7 | 5 |
| 106 | 430.7 | -35.3 | -51.0 | 5 |
| 107 | 369.7 | -18.7 | 0.0 | 0 |
| 108 | 409.0 | -32.7 | -26.7 | 9 |
| 109 | 362.3 | -28.0 | -4.3 | 1 |
| 110 | 407.7 | -72.0 | -44.3 | 5 |
| **mean** | | **-37.9 s (-9.4 %), 7/7 wins** | **-21.1 s (-5.1 %), 6/7 wins, 1 tie** | 27 (20 decisive) |

- Overrides: plain 70 (33 decisive), robust 27 (20 decisive). Waiting: plain -30 %, robust -12 %.
- Winner of the 20 decisive robust overrides: alt_robot 35 %, alt_order 35 %, **random 30 %**
  (plain oracle over all 84 option-A runs: random 49 %). The smallest per-future gain of a
  decisive robust override has median 2.3 s, just above delta.
- **Stop rule: 20 decisive robust overrides >= 8 -> GO** (full robust oracle, 84 runs).

Reading: about half of the plain oracle's gain does not survive a change of future; the
rest (-5 %) does, with about 3 overrides per run. The random control still wins 30 % of
the robust overrides, about its share of the alternatives, so the sensible alternatives
are not better sources of real improvements than a random pair. Whether these few robust
labels are learnable is what the full run and Gate 2 training answer.

## Full robust oracle: 3 teachers x 70-100 robots x seeds 104-110 (jobs 477725-477727, 69-83 min, 84/84 exit 0)

| teacher | plain oracle | robust oracle | decisive overrides plain -> robust | random share of robust decisive |
|---|---|---|---|---|
| nearest_astar | -6.6 %, 28/28 | -4.3 %, 27/28 (1 tie) | 104 -> 84 | 33 % |
| multi_criteria | -5.8 %, 28/28 | -4.3 %, 27/28 (1 tie) | 90 -> 75 | 43 % |
| milp | -8.2 %, 28/28 | -5.9 %, 27/28 (1 tie) | 92 -> 80 | 42 % |

Robust gain grows with fleet size (milp -1.3 % at 70 robots, -11.0 % at 100). The filter
removed mostly the small tie-break overrides; most decisive ones survived.

## Training on robust labels (jobs 479205-479210, about 6 min each, Popper timed out at 300 s)

Rules in `runs/gate2/rules/o300_robust/`. Oracle-label and imitation rule sets are identical
for nearest_astar and milp. multi_criteria differs in one clause (about 1 % recall):
oracle `best_finish_for_order ∧ near_best_cost_for_robot ∧ oldest_fifo` vs imitation
`batch_contention ∧ best_finish_for_order ∧ oldest_fifo`; the other two clauses are equal.

## Closed loop on 116-120 (jobs g2r-eval-r70..r100), verdict `runs/gate2/verdict_o300_robust.json`

| student vs teacher | diff | W/L | p_holm | fallback | verdict |
|---|---|---|---|---|---|
| oracle_nearest_astar vs nearest_astar | +0.7 s (+0.18 %) | 5/4 | 1.00 | 0.00 | fail |
| oracle_multi_criteria vs multi_criteria | +5.3 s (+1.47 %) | 7/13 | 1.00 | 0.02 | fail |
| oracle_milp vs milp | +13.3 s (+3.73 %) | 9/11 | 1.00 | 0.61 | fail |
| H1 oracle vs imitation, multi_criteria | **-11.8 s** | 12/8 | 0.21 | - | not significant |
| H1 oracle vs imitation, nearest_astar / milp | 0.0 s | - | 1.00 | - | identical rules |

Reading: robust labels keep a real oracle gain (-4 to -6 %), but with about 3 real
improvements per run against about 150 ordinary picks the learner still mostly learns the
teacher. The only rule set that changed (multi_criteria) is 11.8 s better than its
imitation control (12 of 20 pairs), not significant, and still 5.3 s behind its teacher.
116-120 has now been used for selection three times; this is development evidence only.
