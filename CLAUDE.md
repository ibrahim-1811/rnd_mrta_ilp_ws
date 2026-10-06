# CLAUDE.md

## Project

This repository implements the R&D project:

> **Outcome-Aware ILP for Explainable Grid-Aware Task Allocation in Multi-Robot Warehouse Systems**

Before changing code, read:

```text
README.md
docs/CURRENT_PHASE.md
docs/DECISIONS.md
docs/ROADMAP.md
docs/EXPERIMENT_PROTOCOL.md
```

Treat those files as the development contract.

## Primary Rule

Do not optimize, redesign, or expand the project outside the current development phase unless explicitly requested.

Work one phase at a time.

## Scientific Boundary

The fixed infrastructure is:

```text
warehouse geometry
lane topology
directed A*
traffic controller
robot execution model
service behavior
battery model
charging scheduler
Gazebo visualization
```

The research variable is the MRTA allocation policy.

Every strategy must execute through the same fixed infrastructure.

Do not let a strategy alter routing, traffic arbitration, battery drain, charger policy, speed, or service duration.

## Active Research Model

Orders use:

```csv
order_id,item_id,shelf_id,quantity,dropoff_id
```

Do not add priority, release time, deadline, or battery fields to the order schema unless explicitly approved as a scope change.

Robots have battery state, but battery scheduling is fixed infrastructure, not a strategy-specific optimization layer.

## Routing Rules

Use directed A* for real warehouse cost.

Manhattan distance may be used as:

```text
1. A* heuristic
2. deliberately naive experimental baseline
```

Do not substitute Manhattan distance for the production directed route cost.

The same routing implementation must support allocation prediction and execution so predicted and realized comparisons remain meaningful.

## Traffic Rules

Normal transit aisles are one-way.

Structural head-on deadlock must not be reintroduced.

Traffic infrastructure may produce:

```text
waiting
queues
vertex conflicts
merge contention
intersection contention
```

These are valid realized outcomes.

Traffic infrastructure must never reassign robot-order ownership.

## Terminal Semantics

The following are terminal resources and must not become normal transit shortcuts:

```text
SHELF
DROPOFF
STAGE ZONE
CHARGING
CHARGE QUEUE / HOLDING
```

Service entry and retreat must remain legal.

## Gazebo Rule

Gazebo is a visualization and execution mirror of the scientific simulator.

Do not create a second planner, second allocator, or independent traffic policy in Gazebo.

The scientific engine remains the source of truth.

## Warehouse Family

The R&D will use:

```text
W20
W30
W50
```

These maps should preserve comparable semantics and reasonable robot density.

Do not simply place 50 robots into the small warehouse and call it scalability.

Each map must independently pass topology, routing, traffic, and terminal-reachability gates.

## Battery V1

Status: **designed, not yet implemented**.

Frozen default design:

```text
initial SOC             100%
travel drain            1% per 20 m
service drain           1% per 60 simulated s
waiting / idle drain    1% per 300 simulated s
charge rate             +1% per simulated s
reserve                 10%
charge due              SOC <= 10%
```

Policy:

```text
SOC <= 10% -> CHARGE_DUE
busy robot -> finish current order first
idle robot -> enter charging workflow immediately
charger is reserved when assigned
charging cells are dedicated non-transit service resources
holding cells are non-transit
normal charge target is 100%
if all chargers are occupied, highest-SOC charging robot may be preempted
preempted robot rejoins fleet only if energy-eligible
otherwise it moves to holding
robot becomes available only after physically leaving the charging cell
```

Do not turn Battery V1 into a research project. Keep it deterministic and fixed across strategies.

## Energy Eligibility

The intended safe assignment condition is based on:

```text
order travel energy
+ known service energy
+ directed A* energy to charger
+ fixed reserve
```

Do not include realized future waiting in decision-time energy features. Waiting is an uncertain realized outcome.

## MRTA Strategy Rules

Target strategies include:

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

Before accepting a new strategy, verify that it produces behavior that is meaningfully distinct from existing strategies on at least one controlled scenario.

Do not create multiple names for strategies that collapse to the same behavior.

## Outcome-Aware Rule

The core scientific separation is:

```text
State_t -> Decision_t -> Outcome
```

Decision-time features must not contain future realized outcomes.

Realized waiting, actual completion time, actual energy, charging events, and future congestion belong to the outcome record.

No future-outcome leakage.

## ILP Rules

ILP is not yet implemented in this repo.

Planned order:

```text
reproduce simple teacher rule
Single-Teacher ILP
Multi-Teacher ILP
Outcome-Aware ILP
explanations
```

The final learner should produce symbolic rules that can be inspected and used to explain allocation decisions.

Do not claim the system is outcome-aware until outcome-derived labels or weighting are actually implemented and evaluated.

## Identifiers

`robot_id`, `order_id`, and similar identifiers are for traceability.

`robot_id` may be used as an engineering-only deterministic tie-breaker when all meaningful values are equal.

Do not use identifiers as scientific predicates or meaningful learned features.

## Testing and Verification

During implementation, run focused checks first.

At phase boundaries, run the full relevant acceptance gate.

Never claim a phase is complete without command output showing the required checks passed.

For map, routing, or traffic changes, verify at minimum:

```text
strong connectivity
0 uncontrolled bidirectional transit edges
terminal reachability
route generation
traffic regression
workload regression
Gazebo / nav graph consistency if relevant
```

For battery changes, additionally verify:

```text
SOC bookkeeping
charge-due transitions
charger reservation uniqueness
charging completion
preemption behavior
holding behavior
availability gating
energy metrics
```

## Development Workflow

Before edits:

```text
1. read CURRENT_PHASE.md
2. inspect git status
3. inspect relevant files
4. state the current task and acceptance criteria
```

During edits:

```text
1. make the smallest coherent change
2. avoid unrelated refactors
3. keep scientific constants configurable where appropriate
4. keep production and validator logic from drifting apart
```

After edits:

```text
1. run targeted checks
2. run the phase acceptance gate
3. summarize changed files
4. report measured results
5. update CURRENT_PHASE.md if the phase actually changed
```

Do not commit unless explicitly requested.

## Documentation Discipline

Update documentation when behavior changes.

Use:

```text
docs/CURRENT_PHASE.md     current implementation state
docs/DECISIONS.md         frozen choices
docs/ROADMAP.md           phase plan
docs/EXPERIMENT_PROTOCOL.md experimental methodology
```

Do not allow README or docs to describe features that do not exist.

## Forbidden Regressions

Do not silently reintroduce:

```text
bidirectional ordinary transit
wall-clock scientific timing
priority/deadline/release-time order fields
strategy-specific traffic rules
strategy-specific battery rules
Gazebo-only planning behavior
outcome leakage
robot_id as scientific priority
```

If a requested change conflicts with a frozen research decision, stop and explain the conflict before editing.
