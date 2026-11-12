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
