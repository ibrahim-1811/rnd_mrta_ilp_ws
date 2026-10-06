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

Exact fleet sizes should be frozen before the final experiment campaign.

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

Use fixed seeds for stochastic strategies.

For repeated experiments, use the same seed set across all stochastic strategies.

Store the seed with each result.

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
