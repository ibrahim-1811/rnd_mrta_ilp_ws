# M1: outcome signal and congestion analysis

Status: ready-for-human (M1 diagnostics implemented 2026-10-28, awaiting
review; measurement matrix to be run by the researcher)

Branch `research/outcome-aware-ilp`, from 163c6a4. Question: **what does a
realized execution outcome tell us that the conventional teacher's
estimated cost does not?** Outcome-Aware ILP is only a contribution if the
answer is "something learnable"; otherwise it reproduces the teacher.

Everything here is diagnostic. No allocation, routing, traffic, battery
or execution semantics changed; no dataset schema changed; the new code
only reads recorded runs or observes a run without altering it (tested).

## 1. Diagnostics added

```text
simulation_env/outcome_signal.py   per-order estimate vs outcome, decision
                                   margins, availability, window flow;
                                   run summary; rank associations; rollout
                                   summary
simulation_env/congestion.py       decision-time congestion and route-
                                   overlap features (section 3);
                                   FeatureObserver records them per
                                   candidate and decides nothing
simulation_env/rollout.py          clone just before a decision, force an
                                   alternative, resume (section 4)
scripts/outcome_signal.py          report over any run directories; never
                                   writes into a run; refuses evaluation
                                   and final seeds
scripts/m1_probe.py                one workload built exactly as
                                   run_workload.py builds it (same run_id
                                   and tables, tested), plus --features
                                   and --rollouts N
test/test_m1_diagnostics.py        12 tests
```

Tests prove: the observer leaves decision and outcome tables identical;
features computed in a run aborted right after decision k equal the full
run's features up to k (no future information); a clone resumed untouched
reproduces the run exactly; a forced branch equals the run before its
decision and differs at it; the probe's dataset equals run_workload.py's
for the same arguments; sealed seeds are refused.

## 2. Measures, defined

For an executed pair (r, o) decided at t:

```text
est_finish(r,o) = C_static(r,o) / v_r + n_shelves(o) * s_pick + s_drop
mission(r,o)    = retreat_finished_at - launched_at
residual(r,o)   = mission - est_finish
                = waiting + extra_travel (side steps) + service_diff
margin(r,o)     = min over other eligible o' of est_finish(r,o')
                  - est_finish(r,o)                     (robot r, decision d)
exceeds_margin  = residual > margin
launch_delay    = launched_at - t                       (> 0 only if deferred)
next_gap        = r's next launched_at - retreat_finished_at
charge_due_after= soc_at_completion <= charge_due
window_flow(d)  = mean over orders o in d's window of completed_at(o) - t
```

`exceeds_margin` is necessary, not sufficient, for an outcome to reverse a
choice: the alternative would have waited too, which a single run never
observes. Only a rollout observes it.

Per decision: free robots, window orders, eligible pairs, eligible orders
per robot, the single-robot share. Associations are Spearman rho of the
residual with each decision-time column, and with residual / estimate
(removing the trivial "longer route, more exposure").

## 3. Decision-time congestion and route-overlap features

All are functions of the state S_t at the decision instant t. Notation:

```text
r, o        the candidate robot and order; K the other robots
p_k         robot k's cell at t
sigma_k     k's remaining planned cells at t: rest of its current segment,
            then each later segment of the plan issued to it at or before t
delta_k     resolution / v_k, the time of one cell move
theta_k(j)  j * delta_k + configured service of each stop before sigma_k[j]
pi          the candidate route c_0..c_n (C_static's own plan)
tau(i)      i * delta_r + configured service of each stop before c_i
U           union of sigma_k over K;  P = {p_k : k in K}
h           2 delta_r (two cell moves)
```

```text
route_cells         n + 1
overlap_cells       |{i : c_i in U}|
overlap_ratio       overlap_cells / (n + 1)
occupied_now        |{i : c_i in P}|
st_conflicts        |{(i,k,j) : c_i = sigma_k[j], |tau(i) - theta_k(j)| <= h}|
st_conflict_robots  |{k : some such (i,k,j)}|
st_conflicts_30s    st_conflicts with tau(i) <= 30 s
dropoff_load        |{k : drop-off(o) is a remaining stop of k}|
shelf_load          sum over shelves s of o of |{k : s is a remaining stop of k}|
local_density       |{k : ||p_k - p_r||_1 <= 5}|
```

No leakage: a plan is information issued before t; its timing is the
uncongested estimate, not realized waiting; stops are those already in the
plan; no later decision, position or wait enters. The truncation test
enforces it. Known simplifications: other robots' times ignore their own
future waiting and side steps, and a robot mid-service is treated as
leaving now. Robots offered in the same decision are excluded from the
plans (they are idle and have none), but their cells count as occupied.

Cost: one fleet snapshot per decision, then O(route length x users per
cell) per candidate.

## 4. Counterfactual rollouts

Clone point: WorkloadExecutor's external tick is the last statement of
FleetSimulationEngine.step_once, so the simulator there is exactly between
event batches. `rollout.clone` deep-copies the executor, sharing the
warehouse (never written) and the route cache (a pure memo), optionally
with empty row lists. `rollout.resume` runs the copy's engine and, if the
clock runs dry with work left, the executor's own recovery loop, which is
exactly WorkloadExecutor.run's sequence.

Counterfactual: `ForcedDecision` lets the strategy decide, then, at one
decision only, replaces the pair of the oldest order (the pivot) with the
pivot robot's i-th best other eligible order by decision-time estimate.
Everything after it is the unchanged strategy, so a branch measures
Q^pi(s, a): the value of action a in state s, followed by policy pi. The
teacher branch is a = pi(s).

Exactness: the probe resumes `--verify` clone points untouched and requires
identical decision and outcome tables (tested; also checked in every probe
run, which exits 1 otherwise).

Per branch: window_flow_s, completed_in_h (orders done in (t, t+H]),
waiting_added_s (fleet waiting from t to the stop), pivot_mission_s. A
branch stops when its decision's window orders are all done and t+H has
passed.

## 5. Results

### 5.1 Existing datasets (read only)

All on the reference workload (seed 42, a development seed), quantum 0.
W100, nearest_astar, 2000 orders:

```text
robots                          30      40      50
decisions with one free robot   97%     96%     94%
eligible orders per robot       28      37      47
residual share of mission time  1.0%    1.4%    1.7%
residual p99                    5.9 s   7.9 s   8.7 s
residual from waiting           100%    100%    100%
median decision margin          14.0 s  12.0 s  11.7 s
residual > margin               4.9%    8.3%    9.3%
largest |rho| residual vs any
  existing decision-time column 0.06    0.12    0.09
window flow mean                1254 s  947 s   804 s   (random: 183-189 s)
```

Travel and service are predicted exactly; the whole residual is traffic
waiting. No existing decision-time column predicts it. Downstream, the
greedy teacher's window orders wait ~6x longer than under random: far
orders starve in the window. That is an outcome the teacher's own cost
cannot see.

### 5.2 Pilot probes (seed 101, 300 orders, nearest_astar)

Runs in runs/m1_pilot (research worktree, gitignored). A 300-order run
gives 100 robots three orders each, so it never reaches a steady state;
the matrix uses 1000 orders.

```text
                                30 q0  30 q3  70 q0  70 q3  100 q0  100 q3
decisions with one free robot   91%    31%    87%    14%    83%     7%
eligible pairs per decision     34     63     93     256    149     442
residual share of mission       0.8%   2.0%   2.5%   5.6%   5.6%    23.9%
residual p90                    2.7 s  5.0 s  5.3 s  12.0 s 11.4 s  96.6 s
orders with residual > 5 s      1%     10%    11%    28%    29%     48%
median decision margin          8.5 s  9.7 s  3.0 s  3.3 s  2.3 s   1.7 s
residual > margin               6%     8%     27%    37%    43%     59%
```

At 100 robots and quantum 3 the residual stays at 25-42% of mission time
through the first three quarters of the run and drops to 3% in the last.

Association of the residual (Spearman rho) with the congestion features
of section 3, selected pairs:

```text
                                30 q0  30 q3  70 q0  70 q3  100 q0  100 q3
st_conflicts                    0.26   0.16   0.11   0.12   0.07    0.06
st_conflict_robots              0.27   0.15   0.10   0.09   0.15    0.09
occupied_now                    0.14   0.17   0.03   0.04   0.10    0.04
overlap_cells                   0.12   0.14   0.00  -0.18  -0.05   -0.24
local_density                   0.04   0.07   0.06   0.13   0.05    0.18
--- existing columns, for comparison
est_finish_s                    0.08   0.07   0.09  -0.16   0.04   -0.11
pending_orders                 -0.03   0.03   0.02   0.24   0.07    0.30
window_orders                   0.06  -0.04  -0.04   0.24   0.05    0.40
robot_soc_percent              -0.03   0.03   0.02   0.25   0.07    0.29
```

The features carry some information, but it is weak (|rho| <= 0.27) and
decays with fleet size. Where the residual is largest, load and run-phase
proxies associate most. SOC correlates only because it is a clock: robots
start full and drain. A learner given SOC and no explicit load predicate
would learn "high SOC, expect waiting", which is spurious.

### 5.3 Rollouts

Clone exactness: every verified clone point (30 robots: 2; 70 robots: 1)
resumed to the identical decision and outcome tables (18 687 decision
rows at 70 robots).

70 robots, quantum 3, 8 decisions, teacher + 3 alternatives each:

```text
measure            spread across branches   decisions where an alternative
                   (mean, relative)         beats the teacher by > margin
window_flow_s      1.3 s   (0.7%)           1/8  (margin 1 s)
pivot_mission_s    14.3 s  (17.6%)          2/8  (margin 2 s)
waiting_added_s    68 s    (10.4%)          3/8  (margin 30 s)
```

Example, decision 5: the teacher's order (estimate 54.0 s) waited 9.7 s;
the third-ranked alternative (estimate 56.7 s) waited none, and fleet
waiting over the horizon fell from 1416 s to 1247 s. The estimate ranks the
teacher first; the outcome ranks the alternative first.

Cost: 2.8 s per branch at 70 robots (copy 0.05-0.5 s), 1.4 s at 30. Pass 1
plus each verification is one full run.

### 5.4 What this says

- Below 50 robots, outcomes add almost nothing to the teacher's estimate.
  Outcome-aware labels there would reproduce the teacher; that is a
  finding to report, not a failure.
- At 70-100 robots, and above all at quantum 3, realized waiting is large
  against the decision margins, so outcomes can disagree with the teacher.
  The rollouts observe that disagreement directly.
- The window flow is too diluted to label one decision; the pivot's own
  realized mission and the fleet waiting it adds are not.
- The proposed congestion features predict waiting only weakly. They are
  candidate predicates, not a solution. Load predicates must be explicit.

## 6. Experiment matrix

See CURRENT_PHASE.md, "M1", for the exact commands.

## 7. Recommendation: Outcome-Aware ILP labels

### Rejected as primary: relabelling the executed pairs by their outcome

Only the selected pair has an outcome. Calling it "bad" because it waited
says nothing about which alternative would have been better, since that
alternative was never executed and would have waited too. The residual is
driven mostly by fleet load and run phase, not by the pair, so such labels
would teach a time-of-run effect. They are also biased by the teacher's
own selection. Expected result: labels close to the teacher's, plus noise.

### Primary: rollout-evaluated one-step improvement over the teacher

For a decision state s with teacher pi:

```text
A(s)     candidate actions: the teacher's answer; the answers of the other
         conventional teachers in the same state (multi-teacher); and the
         pivot robot's top-k other eligible orders by estimate
Q(s,a)   the outcome score J of action a in s followed by pi, measured
         by a rollout from a clone taken just before s (section 4)
label    a* = argmin over A(s) of Q(s,a), if Q(s,pi(s)) - Q(s,a*) > delta;
         otherwise a* = pi(s) (the teacher is kept on a tie)
```

```text
positive   (s, a*)
negative   every evaluated a with Q(s,a) - Q(s,a*) > delta
none       pairs never evaluated: no outcome, nothing imputed
```

This is one step of policy improvement (Bertsekas-style rollout). Exactly
where a* differs from the teacher is where the outcome contributed
information. The learned rules are then an interpretable approximation of
the improved policy.

Outcome score J, to be frozen on validation seeds 111-115 before training:

```text
J(a) = pivot realized mission time + lambda * fleet waiting added in (t, t+H]
```

Window flow and full-run makespan are not used: the first is diluted
(0.7% spread), the second is chaotic. H (60-120 s), lambda and delta are
chosen on validation seeds by label stability: labels must agree when H
moves by a step, and when the clone point is taken from a nearby seed.
Popper weights every example 1, so confidence is expressed by delta
(only clearly separated decisions become examples), not by weights.

Where: decisions at 70 and 100 robots, quantum 3 (primary), and quantum 0
(secondary), on training seeds 101-110 only. 30-50 robots are reported as
the region where outcomes add little.

### What M6 must show to claim genuine outcome information

```text
1  label disagreement   share of decisions with a* != pi(s), with a
                        confidence interval over seeds
2  beyond the estimate  share with a* != the estimate-best action (rules
                        out "this is just earliest_finish")
3  learnable            disagreement predictable from decision-time
                        predicates on validation seeds, above the base rate
4  useful               Outcome-Aware ILP against teacher-reproducing
                        Single-Teacher ILP on paired metrics at 70-100
                        robots (Holm-corrected)
5  ablation             the same learner on shuffled outcome labels does
                        no better than Single-Teacher ILP
```

If 3 fails, outcome labels are not learnable from the current features;
the feature work (better congestion predicates, explicit load) is then the
blocker, not the learner.

Budget: about 5 branches x 3-6 s per decision. 50 decisions x 2 fleet
sizes x 10 training seeds is roughly 1.5-3 CPU hours per condition,
parallel by seed.

## Comments
