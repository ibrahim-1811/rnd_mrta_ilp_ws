# Gate 2 option A results (300 orders, wholerun oracle), 2026-11-12

Status: done. Verdict: **fail for all three teachers.** Frozen criterion and test:
DECISIONS.md, "Gate 2 Pass Criterion and Order" (one-sided, decided 2026-11-12 before training).

## Runs

| step | cluster jobs | output (cluster checkout, commit 6e46a51) |
|---|---|---|
| oracle demonstrations, seeds 104-110 | 477249-477251 | `runs/gate2/o300/wholerun/<teacher>/` (84 runs) |
| training, 6 rule sets | 477697-477702 (about 6 min each, Popper timed out at 300 s) | `runs/gate2/rules/o300/{oracle,imitation}_<teacher>/` |
| closed loop, seeds 116-120, 70/80/90/100 robots | 477706-477709 | `runs/gate2/eval/o300/r<N>/summary.csv` |
| verdict | - | `runs/gate2/verdict_o300.json` |

## Learned rules

For every teacher, the oracle-label and imitation-label rule sets have **identical clauses**,
thresholds and matcher; only training precision differs (oracle labels lower it, e.g.
nearest_astar 0.85 vs 0.91). `--workload` is not used by any rule.

- nearest_astar: `best_finish_for_order ∧ nearest_static`; `closer_static ∧ earlier_static_finish ∧ low_route_overlap`
- multi_criteria: `batch_contention ∧ best_finish_for_order ∧ oldest_fifo`; `best_finish_for_order ∧ best_margin_for_order ∧ earliest_static_finish`; `best_finish_for_order ∧ near_best_cost_for_robot ∧ near_charger_after_order`
- milp: `aging_window_order ∧ best_finish_for_order ∧ earliest_static_finish`

## Verdict (order makespan, paired Wilcoxon, one-sided "student lower", Holm, n = 20 each)

| student vs teacher | diff | W/L | p_holm | fallback | recovered share | verdict |
|---|---|---|---|---|---|---|
| oracle_nearest_astar vs nearest_astar | +0.7 s (+0.18 %) | 5/4 | 1.00 | 0.00 | -0.02 | fail |
| oracle_multi_criteria vs multi_criteria | +17.1 s (+4.45 %) | 5/15 | 1.00 | 0.00 | -0.54 | fail |
| oracle_milp vs milp | +13.3 s (+3.73 %) | 9/11 | 1.00 | 0.61 | -0.35 | fail (criterion 1 also fails) |
| H1 oracle vs imitation (all three) | 0.0 s | 0/0 | 1.00 | - | - | identical by construction |

## Why: what the oracle's overrides look like (diagnostic, 84 training runs)

Scripts (read-only, not in the repo): `override_analysis.py`, `free_soon_check.py`, run on the cluster.

- 808 overrides in 4,884 decisions (16.5 %). Only 286 gain at least 2 s, and they carry 96 % of the gain;
  the rest are tie-breaks on the sum of completion times.
- In those 286, the oracle's pair looks **worse** at decision time: longer static route in 77 %
  (median +10.5 m), more route overlap in 73 %, lower energy margin in 76 %. Traffic-conflict
  features split about 50/50. 52 % keep the robot and change the order.
- Decision context (free robots, window, pending, busy) is the same for override and keep
  decisions. Override rate grows with fleet size (12-15 % at 70-80 robots, 20-25 % at 100).
- "Robot free soon" hypothesis (realized finish times, diagnostic only): **not supported at
  decision level** (override decisions have slightly fewer robots finishing within 10 s: 8.9 vs 9.5).
  The teacher's robot that the oracle leaves free gets another job about 0.3 s later (median), and the
  teacher's order it leaves is taken within 3.7 s (median). So most overrides **reshuffle pairs inside
  the same dispatch round**: the gain is in the combination of pairs, which per-pair rules under a
  greedy matcher do not represent.

## Follow-up diagnostics (2026-11-12, read-only, scripts `regret_check.py`, `advantage_check.py`, `source_check.py`)

- **Regret / advantage does not explain the overrides.** advantage(R,O) = best est_finish of
  the other free robots for O minus R's. In decisive overrides the oracle's pair has *lower*
  advantage than the teacher's in 74 % (median -2.0 s vs +2.0 s). A regret-first greedy
  matches the oracle's decisive overrides in only 8 % of cases and breaks the teacher's
  kept pivot far more often (calibration: earliest-finish greedy reproduces 87 % of
  nearest_astar's own picks). A regret predicate was therefore **not built**.
- **The random control candidate wins most.** The oracle tries about 3.9 candidates per
  decision: the teacher's pair, the next-nearest robot for the order (alt_robot), the next
  earliest order for the robot (alt_order) and one uniformly random pair. Winners of the
  286 decisive overrides: random 49 %, alt_robot 29 %, alt_order 22 %. Random pairs carry
  **54 % of the total override gain**. Same pattern for every teacher (random 47-52 %).

## Reading

The oracle's gain exists for the exact future of each run (Gate 1), but about half of it
comes from arbitrary pairs that happened to land well in a deterministic, chaotic rollout
(the whole-run objective rewards any perturbation that dodges a later jam). Such labels are
largely noise for a decision-time learner, which explains the identical rule sets and why
the overrides look worse on every decision-time feature. Gate 1's headroom is a
clairvoyant bound, not a learnable one. Not yet decided: what to do next (see CURRENT_PHASE.md).
