# Frozen Research and Engineering Decisions

This file records decisions that should not be silently redesigned during implementation.

## Research Scope

Project title:

> **Outcome-Aware ILP for Explainable Grid-Aware Task Allocation in Multi-Robot Warehouse Systems**

Core contribution:

```text
Grid-aware MRTA
+ realized outcome learning
+ symbolic explainability
```

Traffic control, Gazebo, battery scheduling, and charging are supporting infrastructure.

## Fixed Infrastructure vs Research Variable

Fixed across all strategies:

```text
warehouse geometry for a given experiment
one-way lane topology
directed A*
traffic controller
robot speed
service times
battery model
charging scheduler
order lifecycle
Gazebo mirror
```

Research variable:

```text
robot-to-order allocation strategy
```

## Order Model

Active schema:

```csv
order_id,item_id,shelf_id,quantity,dropoff_id
```

Do not add:

```text
priority
release_time
deadline
```

unless the research scope is explicitly changed.

Orders may contain one to three shelf visits.

## Robot and Motion Model

```text
scientific speed      1.5 m/s
grid resolution       1 m
pick service          2 s
drop service          2 s
```

Scientific time is simulated time, not wall time.

## Routing

Production route cost uses directed A*.

Manhattan distance is only:

```text
A* heuristic
or a deliberately naive baseline strategy
```

The one-way warehouse can have:

```text
d(A, B) != d(B, A)
```

Allocation prediction and execution should use the same routing semantics.

## Traffic

Ordinary transit is one-way.

Structural head-on traffic is prevented by topology.

The simulator must still preserve:

```text
waiting
queues
vertex conflicts
merge contention
intersection contention
```

These are valid realized outcomes.

Traffic infrastructure never changes robot-order ownership.

## Terminal Semantics

The following are non-transit terminal resources:

```text
SHELF
DROPOFF
STAGE ZONE
CHARGING
CHARGE QUEUE / HOLDING
```

They may be entered when explicitly targeted.

They must not be used as normal route shortcuts.

## Stage Zone Parking

The Stage Zone bank is a shared fleet resource, not a set of
robot-owned homes.

A robot's spawn cell defines only its starting position.

While orders remain:

```text
a robot that finishes an order stays available from the aisle cell it
retreated to and may take more work from there
it does not return to a Stage Zone between orders
```

When the fleet has no work left:

```text
each returning robot claims the nearest FREE Stage Zone
nearest means directed A* cost, never Manhattan distance
the claim is made when the return mission is assigned
if the cheapest cell is claimed, take the next cheapest reachable one
on arrival the claim becomes the final occupancy
```

Pairing within a batch is greedy on:

```text
(directed route cost, zone_id, robot_id)
```

Route cost is the only scientific quantity in that key. `zone_id`
separates an exact cost tie between bank cells; `robot_id` only ever
breaks a tie the first two left equal, so repeated runs park
identically. `robot_id` carries no parking priority.

Stage Zone cells remain non-transit.

Parking is fixed infrastructure shared by every MRTA strategy, and it
never changes robot-order ownership.

## Gazebo

Gazebo is a mirror of the scientific simulator.

No independent allocation, routing, or traffic logic should exist only in Gazebo.

## Warehouse Scalability

Use a map family:

```text
W20
W30
W50
```

Do not claim scalability by overcrowding the W20 map with 50 robots.

The maps should preserve comparable semantics and reasonable fleet density while scaling physical capacity.

## Battery V1

Battery is a normalized deterministic resource model, not a hardware-calibrated electrical model.

Defaults:

```text
initial SOC             100%
travel drain            1% per 20 m
service drain           1% per 60 simulated s
waiting / idle drain    1% per 300 simulated s
charge rate             +1% per simulated s
reserve                 10%
charge due              SOC <= 10%
```

All values should be configurable in YAML.

Battery drains from:

```text
travel
known service
waiting / idle
```

## Battery Eligibility

Before assigning an order, the robot should have enough estimated energy for:

```text
order travel
+ known service
+ directed A* travel to a charger
+ fixed reserve
```

Predicted future waiting is not included in decision-time energy demand. It remains a realized outcome.

Pinned for implementation (B2, approved 2026-09-13):

```text
required = travel(static route: robot cell -> shelves in the chosen
                  order -> dropoff -> post-dropoff retreat cell)
         + service(2 s per shelf + 2 s dropoff, as configured)
         + travel(directed-A* leg from the retreat cell to the nearest
                  reachable charger)
         + reserve
eligible = SOC >= required        (equality is eligible)
```

Eligibility is a hard feasibility mask for every MRTA strategy. The
executor re-checks every returned pair and raises on a violation; it
never substitutes another robot.

## Charge-Due Behavior

```text
SOC <= 10% -> CHARGE_DUE
```

If busy:

```text
finish current complete order
then enter charging workflow
```

If idle:

```text
enter charging workflow immediately
```

A robot may temporarily fall below 10% while completing an already-active order in V1.

V1 should remain simple. Do not add critical-SOC optimization unless later evidence requires V2.

## Charge-Needed Behavior

Approved 2026-09-13. Two distinct states:

```text
CHARGE_DUE      SOC <= 10%
CHARGE_NEEDED   SOC > 10%, but no energy-eligible order exists among ALL
                currently dispatchable pending orders
```

The CHARGE_NEEDED test is per robot, over every currently pending
dispatchable order, never over a temporary allocator batch:

```text
does at least one pending dispatchable order satisfy the complete
battery eligibility equation for this robot?

  YES -> the robot remains eligible for allocation
  NO  -> the robot enters the charging workflow as CHARGE_NEEDED
```

The eligibility equation is the one in Battery Eligibility above, with
no predicted traffic waiting. Realized waiting still drains the battery
during execution.

## Charger Semantics

Each charging grid cell is one independent charging slot.

Charging cells are dedicated service resources and are not normal transit.

Charging and holding cells form one-cell-deep terminal banks facing an
aisle. A robot enters a bank cell from the aisle cell directly outside
it and leaves by reversing into that cell. No move ever goes directly
from one terminal to another. (B0, 2026-09-13: a two-deep apron against
a wall cannot serve its outer row under the terminal rule.)

Current V2 capacity:

```text
chargers        8     (4,2) (7,2) (10,2) (13,2) (4,32) (7,32) (10,32) (13,32)
holding cells  14     x = 5, 6, 8, 9, 11, 12, 14 on rows y=2 and y=32
```

This ratio applies to the current V2 map only. "Chargers + holding >=
fleet size" is NOT a design rule. Capacities for W20/W30/W50 will be
determined experimentally after Battery V1 works.

A charger is reserved when assigned to a robot.

A robot should not travel toward an already-reserved charger.

Normal charge target:

```text
100% SOC
```

A robot becomes available only after leaving the charging cell.

## Charger Selection

Choose among feasible free chargers using directed route cost and expected charger availability.

The initial implementation should remain deterministic and simple.

Pinned for implementation (B3, 2026-09-13). On entering REQUEST, before
the robot moves:

```text
1. a FREE charger exists     -> reserve the cheapest by directed A*
2. else a FREE holding cell  -> reserve the cheapest by directed A*
3. else                      -> record SATURATION, claim the nearest
                                free Stage Zone (safe non-transit
                                fallback)
tie order                       directed A* cost, zone_id, robot_id
```

Never Manhattan. A REQUEST robot is never left standing on a transit
cell: it departs at the instant it requests. Robots requesting at the
same instant are paired greedily over (route cost, zone_id, robot_id),
as Stage Zone parking is, so they reserve distinct cells.

Each charging and holding cell is one slot:

```text
FREE -> RESERVED -> OCCUPIED -> VACATING -> FREE
```

A slot is offered only while FREE. It stays unavailable until its robot
has physically cleared the terminal access edge, so an entering and a
leaving robot never meet on that edge.

A trip to a charger, holding cell or fallback Stage Zone is a recovery
move, like parking: order missions keep right of way over it, and its
arrival is not an order completion. It never changes order ownership.

Confirmed by the user, 2026-09-15:

```text
1. simultaneous requesters are paired exactly as Stage Zone parking
   pairs returning robots: greedy on (route cost, zone_id, robot_id)
2. a charger, holding or fallback trip is never counted as an order
   or mission completion, and must not count toward order makespan;
   order work keeps right of way over it
3. saturation: a requester already standing on a free Stage Zone
   claims that cell and stays (route cost 0)
```

Note, measured 2026-09-15: the `makespan` printed today is the
engine's end time, i.e. the last robot to settle -- end-of-work parking
included (100 orders: last dropoff 256.0000 s, printed 282.0000 s). A
charger trip that settled last would extend it too; none has so far.
The separate `order_makespan` / `fleet_recovery_time` metrics of End of
Workload below are what enforce point 2.

## Charger Saturation and Preemption

Approved 2026-09-13.

Only a CHARGE_DUE robot may cause preemption. A CHARGE_NEEDED robot
never preempts; if every charger is occupied it uses a holding cell.

When every charger is occupied and a CHARGE_DUE robot needs one:

```text
victim      = the charging robot with the highest SOC
allowed     only if victim_SOC > requester_SOC
record      victim_soc_ge_80 = true / false
```

Never replace a lower-SOC charging robot with a higher-SOC requester.

The earlier wording "prefer a robot at or above 80%, otherwise the
highest-SOC robot" always selects the maximum-SOC robot, so it is one
rule, not two. The 80% threshold is recorded, not branched on.

The selected robot leaves early.

`robot_id` has no scientific priority meaning.

## Charger Queue Discipline

Approved 2026-09-13. When a charger becomes free, the waiting robots
are ordered by:

```text
1. lowest SOC
2. earliest charge-request time
3. robot_id, only as the final deterministic engineering tie-break
```

## Preempted Robot

After leaving the charger:

```text
if energy-eligible for work -> rejoin available fleet
otherwise -> move to dedicated holding cell
```

"Energy-eligible for work" is the CHARGE_NEEDED test above: at least
one pending dispatchable order satisfies the eligibility equation.

## Holding Cells

Holding cells are dedicated non-transit charging support cells.

They are not ordinary traffic cells.

If all chargers and holding cells are saturated, V1 records the saturation event and uses a safe non-transit fallback, with Stage Zone as the last fallback.

Until queue promotion exists (B5), a holding cell is simply the safe
overflow destination for a REQUEST robot that found every charger taken
(B3).

Do not build a complex charger queue optimizer in V1.

## End of Workload

Approved 2026-09-13. Do not park blindly regardless of SOC. When the
final order has completed:

```text
enough SOC to safely reach its nearest free Stage Zone
    -> return to the nearest free Stage Zone directly

not enough SOC to safely reach a Stage Zone
    -> use the charging infrastructure first
    -> charge only enough to make final parking feasible
    -> then return to the nearest free Stage Zone
```

Do not force a charge to 100% after the workload merely because the
robot is below the normal allocation threshold.

Metrics are kept separate:

```text
order_makespan        time of the final order / dropoff completion
fleet_recovery_time   time when all robots are safely parked
productive_distance   task-related execution distance
recovery_distance     post-work parking and charging distance
```

Charging after the final order must not change `order_makespan`.

## MRTA Strategy Set

Target strategies:

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

Every new strategy must be checked for distinct behavior.

Avoid multiple strategy names that collapse to the same allocation policy.

## Outcome-Aware Learning

Core causal structure:

```text
State_t -> Decision_t -> Outcome
```

Decision-time features may contain only information available at the decision time.

Realized outcomes include:

```text
actual distance
actual travel time
waiting
service time
completion time
energy
charging effects
traffic contention
```

No future-outcome leakage.

## ILP

Planned sequence:

```text
simple teacher reproduction
Single-Teacher ILP
Multi-Teacher ILP
Outcome-Aware ILP
explanations
```

Do not claim Outcome-Aware ILP until outcome-derived supervision is implemented and evaluated.

## Identifiers

Identifiers such as `robot_id` are not scientific features.

They may be used only for:

```text
traceability
logging
exact deterministic tie-breaks
```

They must not become learned semantic predicates.
