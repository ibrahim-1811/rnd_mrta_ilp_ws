# Experimental Protocol

## Purpose

This document defines the rules required for fair comparison between MRTA strategies and ILP policies.

## Experimental Principle

Only the allocation strategy may change within a controlled comparison.

Keep fixed:

```text
warehouse map
robot model
traffic controller
routing policy
battery model
charging scheduler
service model
order workload
initial robot states
seed
```

## Warehouse

Decided 2026-10-23 (DECISIONS.md, "Warehouse Scalability"); replaces
the earlier W20 / W30 / W50 family:

```text
main map       W100, fleet-size sweep (see Fleet Size)
held-out map   one map of a different layout and size, never trained
               on, used only to test whether learned rules transfer
               (planned, not built)
```

Do not mix results from different maps as if they were the same operating condition.

Record the map identifier in every run.

## Fleet Size

Within each warehouse, test valid subsets up to the designed capacity.

Do not exceed the number of available home positions.

Recommended pattern:

```text
low utilization
medium utilization
full designed fleet
```

Frozen 2026-10-26 for the main study on W100:

```text
10, 30, 50, 70, 100 robots
```

## Workloads

Use deterministic workload files for direct strategy comparison.

The same workload instance must be reused across strategies.

Do not regenerate orders independently for each strategy.

Recommended workload scenarios:

```text
balanced spatial demand
left-heavy demand
right-heavy demand
shelf hotspot
multi-item high-dispersion orders
high sustained load
```

## Seeds

Decided 2026-10-26 (DECISIONS.md, "Workload Seeds and Paired
Comparison"). A seed selects a workload:

```text
workload     seed s -> runs/<name>/workloads/orders_s<s>.csv, generated
             by generate_orders.py from the current map, 2000 logical
             orders; seed 42 is byte-identical to config/orders.csv
pairing      every strategy runs that file at every fleet size; a
             stochastic strategy also takes s for its own choices
main study   seeds 1-30
baseline     nearest_astar
reporting    compare_paired.py: per fleet size and metric, the mean of
             (strategy - baseline) over seeds with a 95% paired
             t-interval, and that mean as a % of the baseline mean
```

Each run's run.json records the effective seed and the order file's
path and sha256; summary.csv carries `workload` and `orders_sha256`,
and compare_paired.py refuses a pair whose workloads differ.

```bash
python3 src/simulation_env/scripts/run_experiment.py --name main \
    --robots 10 30 50 70 100 --seeds $(seq 1 30)
python3 src/simulation_env/scripts/compare_paired.py --name main
python3 src/simulation_env/scripts/plot_experiment.py --name main
```

Integrity (E1), checked before any paired difference is reported:

```text
complete    runs/<name>/experiment.json records the grid; compare_paired
            refuses missing or extra runs (--allow-incomplete warns)
settings    every run's run.json, minus strategy / seed / fleet size /
            workload / outputs, hashes to one settings_sha256: same map,
            config, speed, service times, quantum, window rule, battery,
            order count and git commit
pairs       the two runs of a pair share orders_sha256, orders,
            line_items and window_width
overwrite   run_experiment refuses an existing runs/<name>; --resume
            runs only missing or failed runs (grid and workload hashes
            must match), --force renames the old one to
            <name>.replaced-<UTC time>
```

Uncommitted changes are recorded (`git.dirty` in run.json and
experiment.json) and reported, not refused.

Seed roles (DECISIONS.md, "Seed Split"; M0): training 101-110,
validation 111-115, evaluation 1-30, a proposed sealed final set
201-230. `--purpose` declares an experiment's role and refuses any other
seed. The held-out map H100 has its own sealed set, 301-330
(`--purpose transfer`, which also requires `--map`; added 2026-11-13).

## Dispatch Conditions

Revised by the researcher 2026-10-27, after the audit (DECISIONS.md,
"Dispatch quantum conditions"; the first decision of that day named
quantum 0 primary and is kept there as history):

```text
primary learning and evaluation condition   --quantum 3
secondary comparison                        --quantum 0   (config default)
```

Run them as separate experiments (separate `--name`). The quantum is an
experiment option since M0 (`run_experiment.py --quantum T`, likewise
`--window W`); the one config is not copied. A quantum change changes
`settings_sha256`, so compare_paired refuses to mix them.

```bash
python3 src/simulation_env/scripts/run_experiment.py --name main_q3 \
    --quantum 3 --purpose evaluation \
    --robots 10 30 50 70 100 --seeds $(seq 1 30)
python3 src/simulation_env/scripts/run_experiment.py --name main_q0 \
    --quantum 0 --purpose evaluation \
    --robots 10 30 50 70 100 --seeds $(seq 1 30)
```

## Stalled Runs

A run the engine stops as STALLED (a traffic deadlock) is a failed run
of that strategy at that seed and fleet size. It is never counted as a
success, never dropped from the paired statistics without being
reported, and never rescued by a fallback. It has no metrics.json, so
the experiment is incomplete and compare_paired refuses it; the stall
and its diagnostics stay in the run's run.log.

Since M0 every failed run is classified (DECISIONS.md, "Run Failure
Classification") and leaves failure.json. STALLED means exactly the
kinds `traffic_deadlock` and `workload_stall`. An
`invalid_assignment`, `eligibility_violation` or `solver_failure` is a
defect of the strategy and is reported as such, never as the warehouse
jamming; an `execution_error` is a defect to fix before any result is
reported. compare_paired.py `--allow-incomplete` lists each missing
run with its kind.

`--workloads reference` runs the committed order file instead, as
experiments before 2026-10-26 did; it cannot be compared with
compare_paired.py.

## Decision-Time Features

Allowed features include information available at the instant of allocation, such as:

```text
robot position
robot availability
SOC
directed A* distance
whole-order route cost
estimated static travel time
route cells
route overlap
current occupancy
current congestion estimates
estimated completion time
```

Do not include information from the future execution of that decision.

## Realized Outcomes

Outcome data may include:

```text
actual distance
actual travel time
actual waiting
service time
completion time
traffic conflicts
energy consumed
minimum SOC
charge cycles
charging time
charging-related unavailable time
holding time
success / failure
```

Outcome data may be used later to create Outcome-Aware ILP supervision, but it must never leak into the original decision-time feature vector.

## Candidate Logging

For each decision, log every eligible robot-order candidate considered by the strategy when practical.

Each record should contain:

```text
decision_id
sim_time
robot_id
order_id
features
strategy
selected
```

The selected pair must be traceable to its eventual execution outcome.

## Predicted vs Realized Separation

Maintain explicit names for predicted and realized quantities.

Examples:

```text
predicted_static_distance
actual_distance

predicted_travel_time
actual_travel_time

predicted_congestion
actual_waiting
```

Do not overwrite predictions with outcomes.

## Strategy Fairness

All strategies use the same executor after assignment.

The traffic system must not repair a poor strategy by changing robot-order ownership.

The traffic system may delay movement for safety.

## Strategy Distinctness

Before accepting a new baseline, verify that it produces a distinct allocation pattern on at least one controlled test scenario.

A strategy with a different name but identical behavior should not be counted as an independent baseline.

## Battery Fairness

Battery and charging configuration must be identical across strategies.

Record the full battery configuration with every experiment.

Charging is infrastructure, not a strategy-specific advantage.

## Timing

Use simulated scientific time for performance metrics.

Wall-clock time is used only for computational runtime measurements such as allocator CPU time.

Do not mix simulated makespan with wall-clock execution duration.

## Core Metrics

Primary MRTA metrics:

```text
makespan
throughput
mean order completion time
total travel distance
total waiting time
allocation runtime
```

Traffic metrics:

```text
vertex conflicts
traffic waiting
contention events
route overlap
```

Battery metrics:

```text
energy consumed
minimum SOC
charge cycles
charging duration
charging-related unavailable time
preemptions
holding time
```

ILP metrics:

```text
rule count
rule length
coverage
fidelity
policy performance
learning runtime
inference runtime
```

## Run Metrics Schema

Every recorded run writes `metrics.json` beside its decision and outcome
tables (`simulation_env/run_metrics.py`); `run_experiment.py` collects
one row per run into `summary.csv` with the same field names. Units are
in the names: `_s` simulated seconds, `_m` metres, `_percent` SOC
percentage points, `_per_h` per simulated hour. Wall-clock fields are
`wall_clock_s`, `allocator_cpu_s`, `mission_build_cpu_s` and
`worst_dispatch_s` only (see Timing).

`metrics_schema_version` says which schema a file follows. Version 1
has no such field.

### Phases (version 2, B6a)

Every robot is in exactly one phase at every instant, so for each
counter (distance, travel time, waiting, service, energy, charging) the
three phases add up to the run total:

```text
productive   executing an order mission: from launch at the robot's
             cell to the end of its drop-off retreat
support      not executing an order while orders are still owed: start,
             between orders, charger trips during the workload,
             liveness repositioning
recovery     after the workload is exhausted (the last order has been
             allocated) and the robot has retired its last order:
             final parking, and any charging done then
```

A robot that owes no order when the workload is exhausted enters
recovery at that instant; a robot still executing or owing one enters it
when that order retires. The productive totals are the sum of the
outcome rows: one calculation, not two.

### Fields

```text
identity     metrics_schema_version, strategy, seed, robots, orders

MRTA         orders_completed
             order_makespan_s          final drop-off              (v2)
             fleet_recovery_time_s     every robot settled         (v2)
             recovery_tail_s           fleet_recovery - order      (v2)
             orders_exhausted_at_s     last order allocated        (v2)
             order_throughput_per_h    on order_makespan_s         (v2)
             makespan_s                = fleet_recovery_time_s     (v1)
             throughput_orders_per_h   on makespan_s               (v1)
             mean_completion_time_s, median_completion_time_s
             total_distance_m, distance_per_order_m
             productive_distance_m, support_distance_m,
             recovery_distance_m, productive_distance_per_order_m  (v2)
             total_travel_time_s, total_service_s
             total_waiting_s
             productive_waiting_s, support_waiting_s,
             recovery_waiting_s                                    (v2)
             decisions, choice_bearing_decisions
             allocator_cpu_s, mission_build_cpu_s, worst_dispatch_s,
             wall_clock_s                                   (wall clock)

traffic      reservation_violations, waiting_per_order_s,
             productive_waiting_per_order_s                        (v2)
             waiting_share, yield_events, plan_deferrals,
             peak_in_progress

battery      battery_enabled
             energy_consumed_percent, energy_per_order_percent
             productive_energy_percent, support_energy_percent,
             recovery_energy_percent                               (v2)
             min_soc_percent, mean_final_soc_percent, below_zero_robots
             charge_requests, charge_sessions, charging_time_s,
             recovery_charging_s                                   (v2)
             charge_energy_percent, out_of_fleet_time_s,
             charger_wait_time_s, saturation_events, promotions,
             stage_promotions, preemptions
```

### Version 1 and version 2

Version 2 adds fields and changes none: every version-1 field keeps its
name and its value. Two of them are not what their names suggest:

```text
makespan_s                the engine's end time, end-of-work parking and
                          charging included; = fleet_recovery_time_s
throughput_orders_per_h   orders per hour of that end time
```

Headline metrics, decided 2026-10-26 (DECISIONS.md, "End of
Workload"):

```text
makespan     order_makespan_s
throughput   order_throughput_per_h
makespan_s   reported as "fleet settled time", never as makespan
distance,    full-fleet totals as operational metrics, always beside
energy       their productive / support / recovery components
```

Compare order work with `order_makespan_s`, `order_throughput_per_h`
and the `productive_*` fields. The `total_*` and version-1 per-order
fields include support and recovery, so a strategy whose robots happen
to finish far from the Stage Zone bank is charged for its parking
drive there.

## Scalability Evaluation

Evaluate scalability across the W100 fleet-size sweep (10, 30, 50, 70,
100 robots) on the same map, so only fleet size and the congestion it
brings change.

Measure both:

```text
system performance scaling
allocator computational scaling
```

Do not attribute a failure to MRTA if the underlying map or traffic infrastructure is structurally invalid.

## Statistical Analysis

For deterministic strategies and deterministic workloads, repeated runs should reproduce exactly unless the experiment intentionally introduces stochasticity.

For stochastic strategies, use multiple fixed seeds and report aggregate statistics.

At minimum consider:

```text
mean
median
standard deviation
confidence interval where appropriate
```

The final statistical method should be frozen before the main experiment campaign.

## Statistical Evaluation Protocol

Decided 2026-11-02 by the researcher (DECISIONS.md, "M4-M6 Decisions",
item 5), as the proposal states:

```text
omnibus test    Friedman across policies; blocks are the paired
                workload seeds, at one fleet size and one condition
post-hoc        pairwise Wilcoxon signed-rank on the same pairs,
                Holm-Bonferroni adjusted
reported beside the paired t-interval of mean d, as below
```

Not yet decided, needed before M8: which policies enter each Friedman
test, and which pairs the post-hoc family contains. The families listed
below (every strategy against nearest_astar, and the ILP family) are the
2026-10-27 proposal and are the default until the researcher decides.

Implementation state (2026-11-06): the paired t-interval exists in
`simulation_env/paired.py`. Friedman, Wilcoxon, Holm and d_z are **not
implemented** anywhere in `src` or `scripts` (F9).

The block below is the 2026-10-27 proposal. Where it differs from the
2026-11-02 decision above, the decision wins: the paired t-test is no
longer the primary test, and Wilcoxon is no longer only a sensitivity
check.

```text
unit            one workload seed. d_s = metric(strategy, s) -
                metric(nearest_astar, s), same fleet size, same
                condition, same workload (E1 checks this)
primary metric  order_makespan_s (DECISIONS.md, "End of Workload")
secondary       mean_completion_time_s, productive_distance_m,
                productive_waiting_s, energy_consumed_percent;
                order_throughput_per_h is reported but not tested
                separately (a monotone transform of the primary);
                allocator CPU per decision as the computational metric
estimate        mean d with its 95% paired t-interval; median d beside
                it; mean d as % of the baseline mean
effect size     d_z = mean(d) / sd(d)
primary test    two-sided paired t-test of mean d = 0
multiplicity    Holm-Bonferroni, family-wise alpha 0.05, over the
                primary family: every strategy against nearest_astar at
                every fleet size (10, 30, 50, 70, 100) on
                order_makespan_s in the primary condition (quantum 3).
                With 7 conventional and 3 ILP strategies: 50 tests.
                Each secondary metric is its own Holm family, reported
                as secondary.
ILP family      a second, pre-listed family, Holm within it: each ILP
                strategy against its teacher(s); Outcome-Aware ILP
                against Single-Teacher and Multi-Teacher ILP
sensitivity     Wilcoxon signed-rank on the same pairs, Holm-adjusted on
                the same families. A conclusion that holds under the
                t-test but not under Wilcoxon is reported as not robust.
failed runs     a failed or stalled run is a missing pair, never
                imputed. Each comparison reports its expected and actual
                n, and every strategy's failure count by kind is a
                reported outcome in its own right.
secondary condition
                quantum 0: the same analysis, reported separately, with
                its own families.
n               30 evaluation seeds per fleet size
```

## Data Split Protocol

Frozen seed partition, as decided (DECISIONS.md, "M1 Acceptance Record",
item 3, 2026-10-29, and "M4-M6 Decisions", item 2, 2026-11-02):

```text
104-110   ILP rule induction pool (M3-M6 training)
101-103   development and exploratory probes (M1)
111-115   M3 validation; spent by m3-evidence-1, not reused
116-120   M4, M5 and M6 validation, one pool, --purpose backup
201-230   sealed final evaluation set, untouched until M8
1-30      conventional strategy reference sweep
```

The block below is the 2026-10-27 proposal, kept for the record. Where
it differs from the partition above, the partition wins.

```text
training     seeds 101-110: teacher runs whose decision rows become ILP
             examples; nothing else is learned from
validation   seeds 111-115: every learner setting is chosen here --
             discretisation thresholds, bias and hypothesis-space
             limits, example sampling, label aggregation, the choice
             between learner variants
evaluation   seeds 1-30 (or the proposed sealed final set 201-230):
             run once per strategy after every learner setting and rule
             set is frozen. Nothing may be chosen by looking at them.
freeze       a learned rule set is frozen as a file before any
             evaluation run; its content hash is in every evaluation
             run.json (strategy identity)
thresholds   fitted on training seeds only
fleet sizes  which fleet sizes training data comes from is declared
             before training
enforced     run_experiment.py --purpose refuses seeds outside the
             declared role; the ILP pipeline (M2) must refuse decision
             rows from any experiment whose purpose is not training
```

## Requirements Before Outcome-Aware Training

Recorded 2026-10-27 (M0) from the audit. Each must be resolved before
Outcome-Aware ILP is trained, and F9 before any final comparison.

State on 2026-11-06:

```text
F9         NOT implemented; method decided 2026-11-02 (above)
F10        NOT implemented
F12        NOT implemented; uniform demand only
M1         DONE, accepted 2026-10-29
features   DONE: Tier B congestion predicates run online with
           offline/online parity (56f495f)
supervision  objective and parameters frozen 2026-10-29 (below); how
           J becomes examples is not yet frozen
```

Original list, 2026-10-27:

```text
F9   statistics       Holm correction, Wilcoxon signed-rank, d_z and
                      failure counts in paired.py / compare_paired.py,
                      as the protocol above; the protocol frozen
F10  outcome          yield_events_during and charge_sessions_before in
     attribution      outcomes are fleet-wide counter differences, not
                      per order, and must not be read as per-order
                      labels. Missing per-order values: minimum SOC
                      (needs a running minimum in the ledger), whether
                      the order left the robot needing a charge, and
                      when the robot was next available. Whether an
                      order's effect on other robots (waiting it caused)
                      is an outcome must be decided.
F12  workload         only uniform random demand exists. At least one
     variety          spatial variant (a shelf hotspot or a skewed
                      side) is needed before any robustness claim; the
                      held-out transfer map is a deferred requirement
M1   outcome signal   measure predicted vs realized outcomes at 70 and
                      100 robots: at 30-50 robots waiting is 1-2% of
                      mission time and distance is predicted exactly,
                      so per-order outcomes barely differ from what the
                      teacher already predicts
features             decision-time congestion and route-overlap features
                      (approved for investigation 2026-10-27)
supervision           the outcome-aware supervision definition frozen
                      ("Outcome-Aware Supervision" below)
```

## Outcome-Aware Supervision

Frozen 2026-10-29 (DECISIONS.md, "M1 Acceptance Record", item 2):

```text
objective   J(b) = pivot_mission_s(b) + lambda * waiting_in_h_s(b),
            over the horizon [t_d, first tick >= t_d + H], measured by
            counterfactual rollout from the decision state
primary     H = 120 s, lambda = 0.50, delta = 2.0 s
sensitivity lambda = 0.25, delta = 5.0 s
censoring   a branch whose pivot mission does not finish inside H, or
            whose rollout stalls or fails, gives no label of either sign
```

Not yet frozen: the rule that turns J into ILP examples (which branches
become positives and negatives, and the role of delta). Until that is
frozen, M6 label generation is not implemented
(`ilp_learning/supervision/outcome_contract.py` is a contract only).

Before Phase 13 begins, define and document exactly how realized outcomes are converted into:

```text
positive / negative examples
weights
rankings
or pairwise preferences
```

The supervision definition must be fixed before large-scale training and comparison.

## Reproducibility Record

Every experiment should record:

```text
run_id
timestamp
commit hash
warehouse map
fleet size
workload file
strategy
strategy identity (parameters, solver, artifacts by hash; M0)
seed, and the experiment's seed purpose (M0)
dispatch quantum and candidate window
battery configuration
traffic configuration
result file paths
failure kind, for a run that did not finish (M0)
```

No final thesis result should depend on an undocumented local configuration.
