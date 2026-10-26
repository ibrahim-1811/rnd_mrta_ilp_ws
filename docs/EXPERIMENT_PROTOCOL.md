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

## Warehouse Family

Use the controlled map family:

```text
W20
W30
W50
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

Evaluate scalability across:

```text
W20
W30
W50
```

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

## Outcome-Aware Supervision

The final outcome-aware target is not yet frozen.

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
seed
battery configuration
traffic configuration
result file paths
```

No final thesis result should depend on an undocumented local configuration.
