# Outcome-Aware ILP for Explainable Grid-Aware Task Allocation in Multi-Robot Warehouse Systems

## Overview

This repository contains the simulator, routing, task allocation, traffic, battery, experiment, and Inductive Logic Programming stack for the R&D project:

> **Outcome-Aware ILP for Explainable Grid-Aware Task Allocation in Multi-Robot Warehouse Systems**

The research studies whether a multi-robot task allocation policy can use real warehouse geometry and realized execution outcomes to learn symbolic allocation rules that are both effective and explainable.

The core research pipeline is:

```text
Warehouse + Orders + Robot State
                |
                v
         MRTA Strategy
                |
                v
     Robot-to-Order Assignment
                |
                v
       Directed Grid A*
                |
                v
     Fixed Traffic System
                |
                v
      Battery / Charging
                |
                v
       Realized Outcomes
                |
                v
       Outcome-Aware ILP
                |
                v
     Explainable Allocation Rules
```

The simulator infrastructure is shared by every strategy. The research variable is the allocation policy.

## Research Objectives

### Grid-aware allocation

Straight-line and Manhattan distance are not sufficient in a one-way warehouse. The production cost model therefore uses directed A* over the actual warehouse graph.

The warehouse can satisfy:

```text
d(A, B) != d(B, A)
```

because legal road direction matters.

### Outcome-aware learning

The project separates predicted allocation cost from realized execution outcome.

```text
State_t -> Candidate Pair -> Decision_t -> Execution -> Outcome
```

The learner should eventually prefer decisions that produce better realized outcomes rather than only copying a hand-written teacher.

### Explainability

The final ILP policy should expose symbolic rules and a human-readable justification for each selected robot-order pair.

Example target form:

```prolog
assign(Robot, Order) :-
    available(Robot),
    reachable(Robot, Order),
    low_route_cost(Robot, Order),
    sufficient_energy(Robot, Order),
    low_congestion_risk(Robot, Order).
```

The final predicate vocabulary will be determined experimentally.

## Scientific Boundary

The following are fixed experimental infrastructure:

```text
warehouse geometry
one-way road topology
directed A* routing
traffic controller
robot motion model
service behavior
battery model
charging scheduler
order lifecycle
Gazebo visualization
```

All MRTA strategies must use the same infrastructure. Strategies must not silently modify lane rules, speed, service duration, charging policy, or order semantics.

## Current Simulator Status

The deterministic simulator and Gazebo mirror are complete and verified.

Current verified capabilities include:

```text
17 x 35 XLSX warehouse grid
one-way warehouse traffic
directed A* routing
strong-connectivity validation
1 to 3 shelf visits per order
best shelf visit permutation
2 s shelf/drop service
local traffic arbitration
deterministic simulated clock
Gazebo mirror of scientific execution
RMF graph generated from production lane rules
nearest_astar allocation baseline
```

Current V2 map state:

```text
72 shelf service cells
36 dropoff cells
22 Stage Zone home cells
8 charging cells
14 charge-queue / holding cells
```

Chargers and holding cells form two one-cell-deep terminal banks (y=2 and y=32) facing the aisle. They are reachable as leg endpoints and never used as transit.

The current V2 map is not yet the final multi-map scalability family. It is the basis for the W20-class warehouse.

Battery state of charge is accounted and reported (Battery V1 B1), and gates allocation (B2): a robot may take an order only if it can finish it and still reach a charger with the 10% reserve, a hard constraint for every strategy. A robot that needs charging reserves a free charger (or, when all are taken, a holding cell) and drives there by directed A* (B3). On a charger it then charges to exactly 100%, reverses into the aisle and rejoins the fleet, where the same eligibility test applies again (B4). The configured charge rate is the net gain, so a charging robot is not also billed idle drain. A robot that reached a *holding* cell stays there: queue promotion and preemption are **not implemented yet** (B5).

ILP, outcome logging, experiment orchestration, and explanations are **not implemented yet**.

## Current Baseline

The current grid-aware baseline is `nearest_astar`.

The latest verified 100-order run completed 100/100 orders with no structural traffic failure, makespan 282.0000 s over 6430.0 m, and parked all 22 robots on 22 distinct Stage Zone cells. Use `docs/CURRENT_PHASE.md` for the exact current acceptance state and next work item.

## Warehouse Semantics

The map is loaded from XLSX and contains semantic cells.

```text
WHITE FLOOR
normal directed transit

RED
blocked / obstacle

SHELF
task service terminal

DROPOFF
order completion terminal

STAGE ZONE
shared fleet parking terminal

CHARGING
dedicated charging service terminal

CHARGE QUEUE
dedicated charging holding terminal
```

Terminal resources are not normal transit shortcuts.

### Stage Zone parking

A Stage Zone cell is where a robot is deployed; it does not own it. The bank is a shared fleet resource.

While orders remain, a robot that finishes one stays available from the aisle cell it retreated to and takes more work from there. It never drives back between orders.

When the fleet runs out of work, each returning robot claims the nearest **free** Stage Zone by directed A* cost over the one-way road graph, reserved as the return mission is assigned so two robots cannot select the same cell. Nearest is a property of the legal route, not the straight line.

Parking is fixed infrastructure and never changes robot-order ownership.

## Directional Traffic Model

Normal warehouse traffic follows one-way aisles.

```text
>>>>>>>>>>>>>>>>

<<<<<<<<<<<<<<<<

>>>>>>>>>>>>>>>>
```

The traffic layer must prevent structural head-on deadlock while preserving scientifically useful waiting, queueing, merge contention, and vertex conflicts.

## Grid-Aware Routing

Manhattan distance is used only as the A* heuristic. The assignment cost comes from the legal directed route through the warehouse.

For an order with multiple shelf visits, the router evaluates valid shelf permutations and chooses the minimum-cost directed route before the final dropoff.

## Order Model

Input schema:

```csv
order_id,item_id,shelf_id,quantity,dropoff_id
```

The active research model intentionally excludes priority, release time, and deadline.

Order lifecycle:

```text
PENDING -> ASSIGNED -> IN_PROGRESS -> COMPLETED
```

A busy robot completes the full assigned order before it can receive another order.

## Robot Model

Scientific defaults:

```text
speed             1.5 m/s
grid resolution   1 m
pick service      2 s
drop service      2 s
```

Scientific time is independent of visualization wall time.

## Warehouse Scalability Family

The R&D will use a controlled family of warehouse maps rather than forcing every fleet size into one layout.

```text
W20    designed for 20 active robots
W30    designed for 30 active robots
W50    designed for 50 active robots
```

Across maps, keep constant:

```text
robot model
speed
service times
traffic semantics
battery model
charging policy
order schema
routing algorithm
MRTA strategy interface
```

Scale only what is necessary:

```text
warehouse dimensions
home capacity
shelf capacity
dropoff capacity
charger capacity
holding capacity
traffic-lane capacity
order volume
```

The goal is to study MRTA scalability, not artificial congestion caused by squeezing 50 robots into a small map.

## Battery and Charging Model

Status:

```text
B1 SOC accounting                      IMPLEMENTED
B2 eligibility, CHARGE_DUE / NEEDED    IMPLEMENTED
B3 charger / holding reservation,
   travel to the reserved cell         IMPLEMENTED
B4 charging to 100%, leaving the
   charger, rejoining the fleet        IMPLEMENTED
preemption, holding promotion (B5)     NOT IMPLEMENTED
battery metrics, end-of-work G6 (B6)   NOT IMPLEMENTED
```

Battery V1 is a normalized deterministic experimental model.

Default design:

```text
initial SOC             100%
travel drain            1% per 20 m
service drain           1% per 60 simulated s
waiting / idle drain    1% per 300 simulated s
charging rate           +1% per simulated s
reserve                 10%
charge-due threshold    SOC <= 10%
```

A busy robot marked `CHARGE_DUE` finishes its current order before entering the charging workflow.

Charging cells are dedicated service resources. Holding cells are non-transit. A robot normally charges to 100%.

The charging scheduler is fixed infrastructure and must be identical for every MRTA strategy.

## Planned MRTA Strategies

The target comparison set is:

```text
Random
Nearest Manhattan
Nearest Directed A*
Earliest Finish
Multi-Criteria Grid-Aware
Auction / Marginal Cost
Hungarian
MILP
Single-Teacher ILP
Multi-Teacher ILP
Outcome-Aware ILP
```

Only `nearest_astar` is currently the implemented research baseline.

## Decision and Outcome Dataset

At decision time, record candidate-state information such as:

```text
decision_id
simulation time
robot_id as identifier only
order_id
robot position
SOC
A* route cost
estimated travel time
route cells
route overlap
predicted congestion
estimated completion time
strategy
selected flag
```

After execution, record realized outcomes such as:

```text
actual distance
actual travel time
waiting time
service time
completion time
traffic conflicts
energy consumed
SOC after order
charging events
charging-related unavailable time
success / failure
```

Identifiers such as `robot_id` are for traceability and deterministic engineering tie-breaks only. They must not become meaningful scientific features.

## ILP Development

The ILP phases are:

```text
1. Reproduce a simple grid-aware teacher rule
2. Single-Teacher ILP
3. Multi-Teacher ILP
4. Outcome-Aware ILP
5. Explanation generation
```

Outcome-aware learning must use realized execution quality while preventing future-outcome leakage into decision-time features.

## Evaluation Metrics

Task-allocation metrics:

```text
makespan
throughput
order completion time
travel distance
allocation runtime
```

Traffic metrics:

```text
waiting time
vertex conflicts
traffic contention
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
performance
runtime
explanation complexity
```

## Experimental Fairness

Every strategy receives the same:

```text
warehouse
orders
robot starts
battery configuration
traffic controller
service model
random seed
```

Only the allocation strategy changes.

Predicted features and realized outcomes must remain separate.

## Development Documentation

Use these files as the persistent project contract:

```text
README.md
CLAUDE.md
docs/CURRENT_PHASE.md
docs/DECISIONS.md
docs/ROADMAP.md
docs/EXPERIMENT_PROTOCOL.md
```

`README.md` explains the project to humans.

`CLAUDE.md` defines agent development rules.

`CURRENT_PHASE.md` records the exact implementation state.

`DECISIONS.md` records frozen research and engineering choices.

`ROADMAP.md` defines phasewise development.

`EXPERIMENT_PROTOCOL.md` defines fair experimental methodology.

## Immediate Development Direction

The next development sequence is:

```text
1. Freeze and commit the verified V2 baseline
2. Create the W20 / W30 / W50 warehouse family
3. Implement Battery Scheduler V1
4. Implement MRTA baselines
5. Add decision and outcome logging
6. Build experiment runner
7. Integrate Popper / ILP
8. Build Single-Teacher and Multi-Teacher ILP
9. Build Outcome-Aware ILP
10. Add explanations
11. Run full evaluation
12. Write final R&D report
```

See `docs/ROADMAP.md` for detailed phase gates.
