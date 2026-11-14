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

Where robots start (decided 2026-10-23):

```text
home, staging   robot k starts on the k-th home cell in id order
                the original rule; every frozen V2 baseline uses it
spread          robots shared across every parking bank in proportion
                to its size, then evenly spaced along each bank
                used by W100, for fleet-size sweeps
```

Without `spread`, 10 robots on W100's 100-cell bank would all start in
its top five rows; with it they start five per wall, evenly up the full
height. A full fleet uses every cell under either rule. Only the start
changes: parking at the end is always the nearest free cell.

## Gazebo

Gazebo is a mirror of the scientific simulator.

No independent allocation, routing, or traffic logic should exist only in Gazebo.

## Warehouse Scalability

Revised 2026-10-23 by the researcher. Supersedes the earlier plan of a
W20 / W30 / W50 map family.

```text
main map       W100: one warehouse with 100 parking cells
               fleet-size sweep on it, 10 to 100 robots
held-out map   one second map of a different layout and size, never
               trained on, used only to test whether learned rules
               transfer (planned; not built yet)
```

Why one main map rather than a family:

- **One variable at a time.** On a fixed map only the fleet size
  changes, so a change in a strategy's advantage across the sweep can
  be attributed to congestion. Across a family, size and layout change
  together and cannot be separated.
- **Congestion is the research question.** The outcome-aware
  contribution is about learning from realized waiting and congestion,
  which is exactly what rises with fleet size on a fixed map.
- **The range is realistic at both ends.** W100 has 1424 road cells. At
  100 robots it is 7.0% occupied, the same density as V2 at 22 robots;
  at 10 robots it is under 1%.

What this design may claim, and what it may not:

```text
may      robustness of a strategy to fleet size and congestion
may not  scalability to larger warehouses -- the building never grows
```

The held-out map is what guards the explainability claim: rules learned
on W100 that still win, unchanged, on a layout never seen in training
are evidence of real allocation logic rather than a memorised layout.

Do not crowd a small map with a large fleet and call it scalability.
A fleet larger than a map's parking is refused at load time.

Fleets smaller than the parking bank use `fleet.initial_position_mode:
spread` (see "Stage Zone Parking"), so the start condition does not
change in kind across the sweep.

## One Map, One Config, One World

Decided 2026-10-23 by the researcher: "one config for everything, one
world for all, one map for all".

```text
config/warehouse.yaml    the one config; the default for every script,
                         the ROS node and the launch file
config/orders.csv        the orders for the current map, 2000, seed 42
maps/W100.xlsx           the current map (new maps are added beside it)
worlds/warehouse.world   the one Gazebo world: the warehouse only
```

Switching map is one command, `build_scenario.py --map X.xlsx`. It
checks the map, rewrites only the `warehouse.map` and
`fleet.robot_count` lines of the config (every comment kept), sets the
robot count to the map's parking cells, regenerates the orders from the
map's stations, rebuilds the world and nav graph, and verifies the
graph. Building the same map again is byte-identical in config, orders
and world.

**Robots are not in the world.** Until 2026-10-23 the world baked in
`robot_count` robot models, so every fleet size needed its own config
and world. Now the fleet node asks Gazebo which robot models exist
(`scene/info`), removes any beyond this run's fleet and creates the
missing ones (`create_multiple`) before it streams poses. One world
serves every fleet size; `robots:=N` chooses it. Robots stay
visualization only: static models with no collision, placed by the
engine. Measured: a fresh world showed 0 robots, 30 after `robots:=30`,
and 10 after a second run with `robots:=10` in the same window.

**The launch reads the source tree.** The default config and the world
come from `src/simulation_env` when the workspace has one, so a map built
with `build_scenario.py` is used without a colcon rebuild.

**The V2 reference moved, it was not deleted.** Every frozen baseline
and most tests are measured on V2, so its config (`v2_reference.yaml`),
map, order lists and the two battery test configs live in
`test/fixtures/`. They are not for experiments. V1, the demo CSV, the
map templates, the per-fleet-size configs and worlds, and their nav
graphs were removed.

## Building the ROS Packages

Decided 2026-10-23. A fresh `colcon build` on this machine fails in
`rmf_grid_traffic` with `No module named 'catkin_pkg'`: CMake's Python
search finds `~/.local/bin/python3.10` before the ROS Python, and that
interpreter lacks the ROS build tools. It only worked before because an
old build cache remembered the right Python. Every build command in the
docs therefore passes

```text
--cmake-args -DPython3_EXECUTABLE=/usr/bin/python3
```

## Drawn Lane Maps

Decided 2026-10-23, at the researcher's request that anyone be able to
create a map in Excel and run the system on it.

The one-way lane policy used to be hard-coded to the 17 x 35 V2 layout
(`EASTBOUND_ROWS`, `NORTHBOUND_COLS`, ... in `warehouse_routing`). On
any other layout most of the road network had no legal direction:
measured on a 25 x 35 test map, only 52.3% of road cells were strongly
connected, and shelves in columns the policy did not know were 0/6
reachable.

Frozen rule:

```text
a map may carry its lanes as arrows typed into its road cells
    >  <  ^  v   or   →  ←  ↑  ↓,  several in one cell at a junction
a map with any arrows is routed by its own arrows, and only by them
a map with no arrows is routed by the built-in V2 policy, unchanged
arrows run floor to floor only; terminals keep the fixed terminal rules
```

What did not change, because it is the policy rather than its layout:
transit is one-way, terminals are entered and left from any neighbouring
road and never driven through or hopped between, and every map must
pass the same gates. The terminal rules are applied word for word the
same on both paths (`_drawn_edge_allowed` and
`_directional_edge_allowed`).

Arrows are read as they look on screen and converted through the
sheet's own coordinate labels, so they stay correct whichever way the
axes are numbered.

**The format is proven complete on a real design.** V2 exported with
its lanes drawn in as arrows (`map_tool.py export`) and loaded back is
routed on the identical blocked-edge set: 775 = 775. V2's frontage
lanes, shelf-block rungs and band-limited shelf columns are all
expressible.

The reference V2 workbook itself is untouched and still carries no
arrows, so every frozen baseline is unaffected.

## Rotaries

Decided 2026-10-23.

A rotary is four road cells in a 2 x 2 square whose arrows chase each
other round it. It is one-way and connected, so it passes every
structural rule, and it is still a trap: with a robot on each cell, each
waits for the next forever.

Measured, not hypothesised:

```text
V2, random strategy, 500 orders, seed 42
    STALLED; cycle robot_007 -> robot_019 -> robot_005 -> robot_016
    on cells (13,3) (14,3) (14,4) (13,4) -- one of V2's 33 rotaries

generated 100-robot map WITH rotaries, nearest_astar, 1000 orders
    STALLED at t=268 s
the same map with its bands flipped so it has NO rotaries
    1000/1000 completed, 0 reservation violations
```

V2 keeps rotaries out of its shelf blocks (westbound rungs) but has 33
in its open bands, where alternating rows cross alternating columns.

Frozen rule:

```text
map_tool.py check reports every rotary as a WARNING, naming its cells
a warning does not stop a map from running; an error does
a map meant for a large fleet must have none
generated example maps have none, by construction
```

A warning rather than an error, because the risk scales with robot
density and because the frozen V2 baseline has 33. Since 2026-10-27 the
controller also keeps a full ring moving (see "Rotation Right-of-Way"
below), which resolves both measured stalls; the map rule stands,
because a map without tight loops still queues less.

## Rotation Right-of-Way

Approved by the researcher 2026-10-27; implemented in
`LocalTrafficController.resolve`.

Cause. Every one-way grid that is strongly connected has loops: W100
has no 2 x 2 rotary but 24 six-cell rings (aisle down, row east, aisle
up, row west). With a robot on every cell of a loop, each wanting the
next, the reservation table accepts moving all of them at once
(`begin_moves_batch`: every target is vacated in the same batch). The
controller did not: a robot outside the loop, closer to its goal, won
one loop cell on priority; the loop could then not move, so that cell
was never vacated, and the identical decision repeated forever.

Rule:

```text
rotation members   robots on a closed loop of >= 3 within one batch,
                   each wanting the cell the next is leaving in that
                   same batch; robots required to side-step, and every
                   robot not moving in the batch (servicing, charging,
                   mid-transit, stationary), can never be members
contention         a member wins the cell it rotates into; at most one
                   member can want a given cell; every other
                   contention keeps busy > remaining distance > id
unchanged          head-on swap handling, chain propagation,
                   begin_moves_batch validation, reservations, engine,
                   routing, order ownership
```

Strategy-independent. It changes a decision only in a state that before
could end only in STALLED: on 17 completed W100 runs (all eight
strategies across quantum 0 and 3, 30 and 100 robots) and the V2 frozen
baselines it never fired, and every dataset and metric is
byte-identical. Measured:

```text
W100 hungarian, 100 robots, 300 orders, quantum 3
    before: STALLED t=450.67, 6-robot ring      after: 300/300, 0 violations
V2 random, seed 42, 500 orders (the rotary above)
    before: STALLED                              after: 500/500, 0 violations
```

Not covered, and still loud: a waiting loop through a robot that is not
moving in the batch is not a rotation and still ends as STALLED, a
failed run (EXPERIMENT_PROTOCOL.md, "Stalled Runs").

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

## Charging Rate

Approved 2026-10-06, for implementation in B4.

`charging_percent_per_second` is the **net** SOC increase while a robot
is physically charging:

```text
SOC(t + dt) = min(100%, SOC(t) + charging_percent_per_second * dt)
```

Waiting / idle drain is **not** additionally subtracted while charging.
A robot standing on a charger is charging, not idling, so the charging
interval is booked as its own category and is excluded from the idle
remainder in the battery ledger.

Consequence, with the default 1 %/s:

```text
arrival SOC 71 %  ->  charge duration exactly 29 simulated seconds
arrival SOC  9 %  ->  charge duration exactly 91 simulated seconds
```

The duration is exact rational arithmetic:

```text
duration = (100 - SOC_arrival) / charging_percent_per_second
```

so `SOC(arrival + duration)` is exactly 100 %, and the 100 % clamp is a
guard rather than a rounding rule.

## Charge Target

Approved 2026-10-06.

B4 charges to exactly 100 %. There is no partial-charge policy in B4.

End-of-workload partial charging is G6 (End of Workload below) and
stays in B6. Its "enough SOC to safely reach a free Stage Zone" rule is
deliberately not a fixed percentage; the intended form is

```text
minimum SOC before leaving the charger
    = directed-A* energy to a safe reachable Stage Zone
    + configured reserve
```

to be implemented and tested in B6, not B4.

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

Implemented in B5 as one idempotent pass per dispatch, so it is applied
both when a charger is released and when a waiting robot arrives in
holding. Without the second trigger a robot that arrives next to an
already-free charger would wait for the *next* release, which is the
starvation the rule exists to prevent.

A waiting robot is one standing in a holding cell. The robot priority
is the rule above; the cell it is given is its cheapest FREE charger by
directed A* from the aisle cell it will set off from, never Manhattan.
A robot already in holding has priority over a robot requesting at this
same instant, because it has by definition been out of the fleet longer
and the queue is ordered on SOC, not arrival.

B5 first promoted holding robots only. That was superseded on
2026-10-24 by Stage Zone Promotion below.

## Initial SOC: Reference and Heterogeneous Condition

Decided by the researcher 2026-10-25.

```text
reference condition   every robot starts at 100% SOC (frozen, as above)
later condition       seeded heterogeneous initial SOC, the same
                      per-robot distribution for every strategy at a
                      given seed, recorded in run.json
                      (battery.initial_soc_overrides)
charging policy       unchanged by either
```

Why: with every robot at 100%, the fleet drains together and charges in
synchronized waves, so charge sessions and time on chargers come out the
same for every strategy (fleet_sweep, 2026-10-25). The reference stays
as it is; the heterogeneous condition is a separate experiment, not a
replacement.

## Stage Zone Promotion

**APPROVED 2026-10-24 by the researcher**, replacing the B5 rule that
a robot on a Stage Zone saturation fallback is never promoted.

Why: at 100 robots on W100, 36 robots asked to charge while all 24
chargers and 12 holding cells were taken. They went to a Stage Zone
and stayed there for the rest of the run, even after every charger was
free again, so the fleet was effectively 64 robots. The old reason,
"no recorded access cell to reverse into", did not hold: a Stage Zone
is left the way every robot leaves one for its first order, by the
directed A* route from the Stage Zone cell.

```text
1. a Stage Zone fallback robot stays in the charge-request queue
2. waiting robots = holding robots + Stage Zone fallback robots,
   ordered by the unchanged queue discipline above
   (lowest SOC, earliest request, robot_id as the final tie-break)
3. the promoted robot gets its cheapest FREE charger by directed A*
   from the cell it sets off from (holding: its access cell; Stage
   Zone: the Stage Zone cell), never Manhattan
4. battery reachability: a charger is offered only if
   travel energy of the real route <= current SOC
   (no reserve: the reserve exists to reach a charger)
   a robot with no reachable FREE charger keeps waiting, and this is
   recorded once (OUT_OF_REACH); it is never sent off to run flat
5. the charger is reserved before the robot moves (exclusive, as
   before); the Stage Zone claim is handed back as the robot drives
   off, and nobody can be given that cell while it still stands on it
6. a preemption promise is owed to a Stage Zone robot as to a holding
   robot
```

Same for every strategy; it touches no order ownership, routing or
traffic rule. Tests: `src/simulation_env/test/test_stage_promotion.py`.

## Preempted Robot

After leaving the charger:

```text
if energy-eligible for work -> rejoin available fleet
otherwise -> move to dedicated holding cell
```

"Energy-eligible for work" is the CHARGE_NEEDED test above: at least
one pending dispatchable order satisfies the eligibility equation.

Clarified in B5, 2026-10-11, after both cases were observed to be
reachable:

```text
a robot still at or below CHARGE_DUE is never fit for work, whatever
the pending orders look like: the B2 gate would pull it straight back
out of the fleet on the same tick. It waits in holding instead.
```

## Preemption Hands the Charger Over

**APPROVED 2026-10-11 as frozen B5 policy**, subject to three
conditions, all of which the implementation satisfies and the gates
check:

```text
1. exclusive reservation ownership
   a slot carries at most one assignment; promising an already
   promised slot, or promising a slot to the robot holding it,
   raises. Reserving a promised slot by any other robot raises.
2. safe cancellation of stale promises
   an assignment is released as soon as its robot can no longer
   collect it, i.e. it is no longer on its way to, or waiting in,
   a holding cell. No charger is ever held empty for a robot that
   is not coming, and no run ends with an assignment outstanding.
3. physical access-edge clearance before charger entry
   the assignment does not shorten the slot lifecycle. The holder
   still goes OCCUPIED -> VACATING -> FREE, and the promised robot
   may not enter until the previous holder physically stands on
   the access aisle cell.
```

Two frozen rules already in this document have to be read together:

```text
"A charger is reserved when assigned to a robot."
"A slot is offered only while FREE."
```

So a preemption assigns the charger to its requester at the instant it
preempts, while the requester still may not enter until the victim has
physically cleared the access edge. The slot carries that assignment
through VACATING and through FREE until the requester collects it.

Measured reason, not a preference: without it the victim -- which ends
its retreat standing one cell from the charger, with the lowest SOC in
the bank -- simply wins the queue discipline and re-reserves the
charger it just gave up, while the requester that displaced it waits
out a whole further charge. Preemption would be a no-op.

The assignment is released if the requester can no longer collect it,
so a charger is never held empty for a robot that is not coming.

Consequence, accepted: the requester still drives to the holding cell
it reserved at request time and is promoted from there, so it makes one
short extra trip. Handing a reservation to a robot already in flight
would mean retargeting a mission mid-route, which is new machinery and
not B5.

## Holding Cells

Holding cells are dedicated non-transit charging support cells.

They are not ordinary traffic cells.

If all chargers and holding cells are saturated, V1 records the saturation event and uses a safe non-transit fallback, with Stage Zone as the last fallback.

A holding cell is the safe overflow destination for a REQUEST robot
that found every charger taken (B3), and since B5 it is a waiting room
rather than a destination: see Charger Queue Discipline above.

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

### Phase boundary and headline metrics

Decided by the researcher 2026-10-26, on the B6a implementation:

```text
recovery starts    when the final order is dispatched (allocated), not
                   at the final drop-off. A robot that retires its last
                   order while others still deliver is in recovery.
headline makespan  order_makespan_s
headline throughput order_throughput_per_h: completed orders per hour
                   of order_makespan_s
makespan_s         kept for backward compatibility, labelled "fleet
                   settled time" (= fleet_recovery_time_s)
distance, energy   full-fleet totals stay as operational metrics; their
                   productive, support and recovery components are
                   reported beside them
```

`throughput_orders_per_h` keeps its version-1 value (per hour of fleet
settled time) so earlier runs stay comparable; it is not a headline
metric.

## Workload Seeds and Paired Comparison

Decided by the researcher 2026-10-26, after B6a showed that one
workload cannot rank strategies by makespan (V2, seed 42: random
240.0 s < Manhattan 242.0 s < A* 245.3 s, set by one late order).

```text
seed           selects a deterministic order workload; every strategy
               runs the same order file for a given seed
main study     30 workload seeds, subject to the frozen experiment budget
reporting      paired per-seed differences between strategies with 95%
               confidence intervals, not individual rankings
```

No general conclusion is drawn from a single workload.

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

### Conventional strategy definitions (Phase 7)

Reviewed by the researcher 2026-10-26/27: Earliest Finish's estimate
and multi_criteria's weights approved; auction, hungarian and milp
reviewed with the wording below. Every strategy uses the same candidate window, battery mask,
directed A* routes, traffic and execution; each also receives the same
DecisionTiming (robot speeds, configured service durations).

```text
earliest_finish  greedy on estimated finish = C_static / robot speed
                 + shelf service x shelf visits + drop-off service;
                 no waiting, no outcome data (approved 2026-10-26)
multi_criteria   greedy on a weighted sum of per-decision min-max
                 normalised criteria: estimated finish 0.5 (lower),
                 FIFO age 0.2 (lower), energy margin 0.2 (higher),
                 charger distance after the mission 0.1 (lower).
                 UNTUNED HEURISTIC WEIGHTS, fixed 2026-10-27; never
                 tuned on evaluation workloads. A criterion equal
                 across the decision contributes 0. Normalisation is
                 per decision, so the weights trade relative positions,
                 not units: a 1 m spread weighs as much as a 100 m one
auction          sequential single-item auction, orders announced in
                 FIFO order, bid = the route length the order adds to
                 the robot's plan, which with no other order held is
                 C_static. No opportunity cost, no re-auctioning. The
                 round-based variant reduces exactly to nearest_astar
                 when a robot holds one order, so it is not used
hungarian        maximum matching of minimum total C_static
milp             maximum matching of minimum latest estimated finish
                 (bottleneck), then minimum total estimated finish;
                 HiGHS via scipy.optimize.milp. Battery only through
                 the shared mask; no congestion or traffic constraint
```

Hungarian and MILP optimise different objectives: total route length
against the latest finish of the batch.

Estimated finish (earliest_finish, multi_criteria, milp) was checked
against execution on 600 orders (W100, 30 robots, quantum 0 and 3):
predicted service and travel time equal the realized values exactly;
only waiting differs, as intended.

### Dispatch quantum conditions

Decided by the researcher 2026-10-27:

```text
primary operational experiment   dispatch quantum 0 s
controlled batch experiment      dispatch quantum 3 s
```

At quantum 0 most decisions offer one free robot, and then
hungarian chooses as nearest_astar and milp as earliest_finish
(distinctness 0.4-0.8 % and 2.3-4.9 %, W100, 30 robots, 300 orders).
That near-equivalence is reported as a scientific observation, not
engineered away. At quantum 3 every pair passes the 5 % gate.

Revised by the researcher 2026-10-27, after the audit; the decision
above is kept as history:

```text
primary learning and evaluation condition   dispatch quantum 3 s
secondary comparison                        dispatch quantum 0 s
```

Both remain separate experiments (`run_experiment.py --quantum 3` and
`--quantum 0`). In the primary condition hungarian and milp are
distinct from every other strategy; at quantum 0 their near-equivalence
to nearest_astar and earliest_finish is reported as observed.

## Allocation Candidate Window

Approved 2026-10-22. The FIFO window is widened so that an allocation
decision has choice in it. The rule becomes:

```text
eligible_count = min(W, pending)
W              = fleet_size (the configured robot_count)
```

replacing `min(available_robots, pending)`. The eligible set is still a
prefix of `pending()` in CSV sequence, so no order is skipped and no
priority is introduced.

Why this and not a multiple of the free-robot count: the window must be
wide at exactly the instants when only one robot is free, and those are
the majority. A window proportional to `available_robots` stays narrow
there and so fails to fix the problem it exists to fix. Why not all
pending orders: allocator cost per call would grow to
`robots x pending` (up to 500 orders), and a window equal to the whole
queue makes the CSV sequence meaningless, which weakens the frozen
no-priority decision.

Measured cause, 100-order run: 71 allocator calls, 62 of them 1 robot
x 1 order. On a 1x1 decision every strategy -- Random, Hungarian, MILP,
ILP -- returns the same assignment, so no strategy comparison was
defensible before this change. See docs/ARCHITECTURE.md section 12,
P0-1.

`W` is configurable and recorded in the experiment manifest. It is
infrastructure: identical for every strategy.

## Dispatch Batching

Approved 2026-10-22. Widening the window fixes the order side of the
decision only. The robot side is the number of free robots, and under
continuous dispatch robots become free one at a time, so most decisions
are 1 robot x W orders. That is enough for Random, Nearest Manhattan,
Nearest Directed A*, Earliest Finish and Multi-Criteria to diverge, but
Hungarian and MILP are batch assignment methods: at m=1 they reduce to
argmin and collapse onto greedy nearest.

The approved resolution is hybrid:

```text
headline runs        continuous dispatch, dispatch_quantum = 0
                     allocate whenever at least one robot is free
quantum scenario     dispatch_quantum = T simulated seconds
                     a freed robot is held up to T so that several
                     robots are allocated in one decision
```

`dispatch_quantum` is fixed infrastructure with a default of 0, applied
identically to every strategy, and recorded with every experiment.

T = 3.0 simulated seconds, measured 2026-10-22 as this decision
requires, not guessed. The inter-decision gap on the 100-order workload
has median 2.0 s and p75 3.33 s, so 3.0 s bridges most of the
distribution:

```text
T       consecutive decisions merged   mean robots/decision   makespan
0.0 s                              -                   1.56   268.6667 s
1.0 s                          28.6 %                  1.85   260.3333 s
2.0 s                          58.7 %                  2.44   272.6667 s
3.0 s                          71.4 %                  3.03   264.6667 s
5.0 s                          88.9 %                  4.17   274.0000 s
10.0 s                         96.8 %                  6.67   292.6667 s
```

3.0 s triples the robots per decision at no makespan cost. 10.0 s
batches harder but costs 9% makespan and 75% more waiting, so the
quantum is not a free parameter to maximise.

The window opens on the robot that has been free **longest**, not the
latest arrival, so a steady trickle of freed robots cannot postpone a
decision indefinitely: every robot is dispatched within one quantum of
becoming free. Nothing is held when holding cannot pay -- no robot in
flight to come free, or no more orders than robots already in hand.

The batched scenario is the reference config run with `--quantum 3.0`,
which changes this one value and nothing else.

Realism stays in the headline results; separability of the batch
strategies is established in a declared controlled scenario rather than
by changing the default simulator behaviour.

## FIFO Liveness Rule

Approved 2026-10-22. A FIFO prefix can contain only orders that no free
robot is energy-eligible for, while a feasible order sits just past the
window. The allocator then returns nothing, the dispatch defers, and
the run can stall with work outstanding.

The rule:

```text
the window is the FIFO prefix of width W
if it contains zero assignable (robot, order) pairs, extend the window
  further down the FIFO sequence until at least one assignable pair
  exists, or pending is exhausted
```

This is a liveness rule, not a priority rule. It is infrastructure:
uniform across strategies, and a strategy never chooses to skip an
order. FIFO preference is preserved, because the extension happens only
when the prefix offers nothing at all.

The stall is to be reproduced as the validation case before the
dispatch path is changed, and the reproduction kept as a regression
test.

## Baseline Re-Freeze Policy

Approved 2026-10-22. Changing the window changes the frozen workload
baselines, so:

```text
W is configurable, and the new W = fleet_size is the default
the 100-order and 500-order baselines are re-measured and re-frozen
a window=legacy regression test asserts the pre-S0 numbers
  bit-identically: 100 orders, 22/22 parked, makespan 282.0000 s,
  6430.0 m, waiting 264.6667 s, 0 violations
```

The default configuration is therefore the configuration the
experiments use, and B0-B5 keep the bit-identical anchor that currently
proves the battery work changed no allocation behaviour. Both baseline
sets are recorded, the pre-S0 one marked as superseded rather than
deleted.

## Strategy Distinctness Gate

Approved 2026-10-22. A new baseline is accepted only when it is shown
distinct from every already-accepted strategy.

```text
primary    pairwise decision divergence: the percentage of
           choice-bearing decisions at which two strategies select a
           different pair set, on a fixed controlled scenario and seed
secondary  outcome delta: makespan, total distance, total waiting
```

A *choice-bearing* decision is one where `robots x orders > 1`. Forced
decisions are excluded from the denominator: a strategy must not be
credited for agreeing where there was nothing to decide, and including
them would let the gate be passed by widening the window alone.

Threshold: divergence of at least 5% of choice-bearing decisions
against every accepted strategy, on at least one controlled scenario.
The 5% is an engineering default set 2026-10-22, not a measured value,
and may be revised once the first three strategies are measured.

Outcome delta alone is not sufficient evidence: two strategies can
diverge on many decisions and still produce the same makespan, and the
reverse also happens through traffic noise.

## Hungarian and MILP Objectives

Approved 2026-10-22. Hungarian and MILP both solve min-sum assignment
on `C_static`, so implemented naively they are the same allocation
policy under two names, which the strategy-set rule forbids. They are
separated by objective, not by solver:

```text
Hungarian   min sum of C_static over the assignment
            optimal, scipy.optimize.linear_sum_assignment
MILP        min-max completion (makespan-oriented)
            scipy.optimize.milp (HiGHS)
```

As implemented (2026-10-26; see "Conventional strategy definitions"):
battery feasibility is the shared eligibility mask -- an ineligible
pair is not a variable -- and there is **no congestion constraint**.
The 2026-10-22 plan named one, but no decision-time congestion feature
exists (DECISIONS "Phase 8 Schema Gaps To Close"), so none was built.

Both are optimal for their own stated objective, and they disagree on
the same decision whenever a min-sum assignment loads one robot
heavily. Neither needs a new dependency: scipy 1.17.1 is already
declared and provides both (verified 2026-10-22). PuLP and OR-Tools
remain unnecessary.

Each still has to pass the distinctness gate above against the other
and against the greedy baseline.

## Phase 7 Implementation Order

Approved 2026-10-22. Strategies are implemented in two slices, not one
pass:

```text
slice 1    Random
           Nearest Manhattan
           Nearest Directed A*   (already implemented)
slice 2    Earliest Finish
           Multi-Criteria Grid-Aware
           Auction / Marginal Cost
           Hungarian
           MILP
```

Slice 1 is cheap and maximally distinct, and it exercises the candidate
window, the decision/outcome logging and the distinctness gate end to
end before any effort is spent on the optimising strategies. Stop for
approval after slice 1.

Nearest Manhattan is the deliberately naive baseline permitted by the
routing rules: Manhattan distance decides the pairing, while the
executed route remains the production directed A*.

## Decision and Outcome Dataset

Approved 2026-10-22. Phase 8 is the data layer the whole research
contribution reads from: without it there are no learning examples, and
"outcome-aware" cannot be claimed at all.

Two tables and a manifest, one directory per run:

```text
<run_dir>/decisions.parquet   one row per scored (robot, order) pair
<run_dir>/outcomes.parquet    one row per order
<run_dir>/run.json            the manifest that makes them meaningful
```

Format: **Parquet**, typed and compact, chosen so the dataset scales to
W50 x 500 orders x 11 strategies x seeds without a rewrite. `pyarrow`
is declared in `pixi.toml` for it.

Parquet is not readable in a terminal, which is a real cost on a
research dataset, so a small export helper accompanies it: any run can
be dumped to CSV for inspection and to Prolog facts for Popper. The
Parquet files remain the record; the exports are derived and
disposable.

The manifest is JSON rather than a Parquet row because it is read by
humans and by the experiment runner before any data is loaded.

## Dataset Granularity and Labels

Approved 2026-10-22. One decision row per **scored candidate pair**,
not per decision and not per chosen pair:

```text
eligible = true,  selected = true     the assignment that was made
eligible = true,  selected = false    the ILP's negative examples
eligible = false, selected = false    refused by the energy mask
```

Inductive logic programming learns from positive *and* negative
examples, so "eligible but not chosen" is the row that carries the
signal. The battery-ineligible rows are kept as well: they are what
demonstrates the energy mask is shared infrastructure rather than a
strategy's own policy, which the battery-fairness rule requires.

Measured volume: 1973 scored pairs on the 100-order workload, 10771 on
500 orders, 1983 on the batched scenario. Not a scale concern.

Only the selected pair has a realized outcome. A non-selected pair's
outcome is unobserved and must never be imputed into the decision
table. How Outcome-Aware ILP derives supervision from observed
outcomes alone is a Phase 13 decision; this schema deliberately does
not foreclose it, and deliberately does not pre-answer it either.

## Decision-Time Features

Approved 2026-10-22. **Raw numeric values only.** No discretized
symbolic predicates in the dataset.

Thresholds such as "battery high" or "congested" are learner-side
choices that will be tuned many times. Baking them into the stored
dataset would mean re-running every experiment whenever one moves, and
would create two sources of truth. Discretization belongs in the ILP
pipeline, downstream of the Parquet files.

`mrta_core/symbolic.py` holds legacy predicates (`battery_high` and
friends) from the pre-freeze simulator. They are not the Phase 8
schema and must not be wired into it.

Exact time and SOC are stored twice where exactness matters: a
`float64` column for analysis and an exact `"numerator/denominator"`
string beside it, because the clock and the SOC ledger are exact
rationals and Parquet has no rational type. The float column is for
plotting; the string column is what makes a run reproducible.

Identifiers (`robot_id`, `order_id`, `nearest_charger_id`) are stored
for traceability and are never to be used as learned features, per the
frozen identifier rule.

## No-Leakage Enforcement

Approved 2026-10-22. Documented discipline is not enough; the boundary
is enforced structurally and tested.

```text
1. the decision row is built inside the allocation call, before any
   mission is planned or any robot moves
2. it is immutable once built -- nothing downstream may touch it
3. truncation test: abort a run immediately after a decision and
   assert its decision rows are byte-identical to the same run carried
   through to completion
```

The truncation test is the one that cannot be argued with. If any
decision field were populated from post-decision state, the aborted
run and the full run would disagree.

The split is the frozen one:

```text
decision-time   position, SOC, directed A* cost, predicted travel time,
                route, predicted energy terms, reserve, margin,
                eligibility, fleet and queue counts at t, strategy,
                window, quantum
outcome         realized distance, travel time, waiting, service time,
                completion time, energy consumed, minimum SOC,
                charging events, traffic events, success
```

`EnergyRequirement` already states this contract in its own docstring
and is reused unchanged as the decision-time energy block.

## Phase 8 Schema Gaps To Close

Recorded 2026-10-22, before implementation, so neither is silently
invented:

```text
per-order realized attribution does not exist yet
    GridRobot counters (distance_m, travel_time_s, waiting_time_s,
    service_time_s) are per-robot cumulative totals. Per-order values
    are obtained by snapshotting them when the order starts and
    differencing at completion. This is sound because a robot runs one
    order at a time and never charges mid-order (a busy robot finishes
    its order first, by the frozen CHARGE_DUE rule). Traffic waiting
    and yields inside the order are correctly attributed to it;
    travel to a charger or a Stage Zone falls between orders and is
    correctly excluded.

decision-time route congestion is not computed
    OrderCandidate carries no congestion field, and nothing in the
    allocation path computes one. Phase 8 therefore logs the fleet and
    queue state that IS available at t -- free robots, window orders,
    pending orders, robots busy / available / charging -- and does not
    fabricate a route-congestion feature. A genuine decision-time
    congestion predictor is a scoped extension, not part of Phase 8,
    and it must be computed from state at t only.
    Approved for investigation 2026-10-27, together with route
    overlap ("Research Direction After the Audit"); not implemented.

estimated travel time is not recorded
    WorkloadExecutor accepts speed_mps but does not use it; the engine
    drives kinematics from each GridRobot's own speed. Recording an
    "estimated travel time" from the executor's unused parameter would
    be recording a number the allocator never saw. Travel time stays
    derivable downstream from the speed in the manifest.

predicted waiting is not recorded
    Dispatch.planned_wait_s is always 0.0: there is no pre-planned
    traffic wait any more, by design, since waiting is measured only
    from what actually occurs. A structurally-zero column in a research
    dataset only invites someone to read meaning into it.

per-order minimum SOC is not recorded
    The ledger exposes SOC at launch and at completion. A per-order
    minimum would need a running minimum maintained inside the ledger,
    which is a battery-infrastructure change, not a logging change.
    Closing this gap is a scoped extension.
```

Each of the four is absent because the value does not exist at decision
time, not because logging it was hard. Fabricating any of them would
put a number in a research dataset that no allocator ever saw.

## Dataset Storage Format Fallback

Decided 2026-10-22, during Phase 8 implementation, after the live ROS
node crashed on a completed run.

Parquet is the format. pyarrow is declared in `pixi.toml`, but the live
ROS node runs on the system interpreter under `/opt/ros/jazzy`, not in
the pixi environment, so pyarrow cannot be assumed there. The first
live run simulated all 100 orders, printed its full report, and then
died in `recorder.write` with `ModuleNotFoundError: No module named
'pyarrow'`, losing the entire run.

Frozen rule:

```text
RunRecorder.write tries Parquet and falls back to CSV on ImportError
the manifest records "format": "parquet" | "csv"
read_run reads either, and restores declared column types from CSV
a finished run is never lost to a missing optional dependency
```

The fallback is lossless, not a degraded mode. The exact columns are
already `"numerator/denominator"` strings, so the rational clock and the
SOC ledger survive text unchanged; the declared column types are what
restores `22` to an int and `true` to a bool on the way back in. A
Parquet run and a CSV run of the same workload read back equal row for
row, which `test_the_csv_fallback_loses_nothing` asserts.

Column order and type are therefore declared in plain Python
(`DECISION_COLUMNS`, `OUTCOME_COLUMNS`) and the pyarrow schema is built
from them. The CSV path needs the column order and must not need
pyarrow to get it, and one declaration cannot drift from itself the way
two would.

Parquet remains preferred where available: 56 KB against 530 KB for the
same 1973 rows.

## Dataset Export

Decided 2026-10-22, when Parquet was chosen over CSV as the stored
format.

Parquet is stored because the clock and the SOC ledger are exact
rationals and a run has to stay reproducible. It is not readable with
`less`, so `simulation_env/dataset_export.py` reads a run back out:

```text
decisions.csv / outcomes.csv   text, for grepping, diffing, plotting
bk.pl                          ground facts: decision-time state
exs.pl                         pos / neg examples
```

Frozen properties of the export:

```text
it derives nothing
    every exported value is a value read from the Parquet file. An
    export that computed a new quantity would be a second
    implementation of a feature that already exists in dataset.py, and
    the two would drift.

it does not discretize
    soc(d12, robot_003, 88.5), never battery_high(d12, robot_003).
    Threshold placement is the learner's experiment.

it writes no mode or bias declarations
    which predicates a learner may use, with what arity and direction,
    is the hypothesis space. That is the experiment, not the data, and
    it belongs to the ILP phase.

negative examples are ELIGIBLE pairs only
    a pair the infrastructure refused -- unreachable route,
    battery-ineligible -- is not a negative example. A learner asked to
    explain it would be learning to reproduce the feasibility mask,
    which is fixed infrastructure and not the research variable. Such
    pairs remain in the Parquet table, which is the record of what was
    offered.

no fact asserts anything ABOUT an identifier
    robot_003 and o0042 appear as the terms an example is about,
    because an example must name something, but no fact orders them or
    exposes the numeric part of an id. Identifiers are traceability.
    Asserted by test_prolog_asserts_nothing_about_an_identifier.

counts stay integers
    free_robots(d0, 22), not 22.0. A count compared as a float reads as
    though a measurement were involved.
```

## Run Identity

Approved 2026-10-22. A dataset without provenance is not evidence. Every
run directory carries a manifest recording:

```text
run_id            deterministic hash of everything below
git_commit        plus whether the tree was dirty
config path and sha256
map path and sha256
orders path and sha256
robot_count, speed_mps, resolution_m, service durations
strategy, seed
allocation_window, resolved window width, dispatch_quantum_s
the full battery configuration
schema_version
run totals, for cross-checking the tables against the report
strategy_identity and strategy_sha256 (M0, "Strategy Identity")
```

`schema_version` is explicit: a dataset whose schema changed is not
comparable with an older one, and silently mixing the two would
invalidate any learned rule. Phase 9 reads this manifest to assemble
experiment matrices.

## Research Direction After the Audit

Decided by the researcher 2026-10-27, accepting the read-only audit of
that day as the research engineering baseline:

```text
foundations        Phase 7, the traffic rotation rule, E1 and Phase 8
                   are implemented foundations. Their scientific
                   definitions change only with documented
                   justification.
congestion         decision-time congestion and route-overlap features
                   are APPROVED FOR INVESTIGATION. Computed from state
                   at t only (DECISIONS "Phase 8 Schema Gaps To Close");
                   not implemented in M0.
seeds              training 101-110, validation 111-115 ("Seed Split")
condition          dispatch quantum 3 s is the primary learning and
                   evaluation condition; quantum 0 is the secondary
                   comparison ("Dispatch quantum conditions")
statistics         primary metrics, paired comparisons, confidence
                   intervals, Holm correction and a Wilcoxon sensitivity
                   analysis are predefined before the final experiments
                   (EXPERIMENT_PROTOCOL.md, "Statistical Evaluation
                   Protocol")
deferred           G6 end-of-work partial charging and the held-out
                   transfer map remain requirements, deferred and not
                   cancelled
```

## Decision Linkage

M0, F5, 2026-10-27. An outcome row's `decision_id` is the decision that
assigned the order, recorded in the executor when the allocator decides
(`WorkloadExecutor._decision_of`) and read when the mission launches.

It used to be read as "the latest decision" at launch. A deferred
assignment (its plan refused, retried later) launches under a later
dispatch, so its outcome would have joined the wrong decision. No
recorded run was affected -- every run on record has 0 plan deferrals
-- but the label an outcome-aware learner reads would have been silently
wrong as soon as one occurred. The regression test forces deferrals that
span several decisions and fails on the old rule.

## Run Failure Classification

M0, F6, 2026-10-27. A run that does not finish is classified, never
lumped into "stalled":

```text
traffic_deadlock       fleet_engine.DeadlockError: a proven wait cycle,
                       livelock or no-progress timeout          STALLED
workload_stall         WorkloadStalledError: work outstanding, no
                       events, recovery freed nothing           STALLED
invalid_assignment     InvalidAssignmentError: robot not offered or
                       used twice, order outside the window or used
                       twice, pair without a route
eligibility_violation  IneligibleAssignmentError: battery-ineligible pair
solver_failure         AllocationSolverError: an optimiser failed
charging_saturation    ChargingSaturationError
execution_error        anything else; a defect, never a result
```

Only the first two are a STALLED run in the sense of
EXPERIMENT_PROTOCOL.md. A failed run writes `failure.json` (kind,
exception, message, simulated time, order counts) and no metrics, run
manifest or tables, so it can never be compared as a result.

The allocation contract (`_check_assignment_contract`) is now checked
for every strategy, with or without battery accounting. Before M0 a
contract breach was caught only by the battery check, and only when
battery accounting was on. It raises; it never repairs a decision.

## Strategy Identity

M0, F8, 2026-10-27. A strategy name is not an identity. Every allocator
reports `identity()`:

```text
name             the registered name
implementation   module and class
parameters       effective parameters that change its choices
                 (multi_criteria: the weights). Not the seed: the seed
                 is recorded per run, and it also selects the workload
execution        what its choices depend on beyond its inputs
                 (hungarian, milp: solver and scipy version)
artifacts        files it reads, by content hash (a learned strategy's
                 rule set), via artifact_record()
```

run.json records `strategy_identity` and its short hash
`strategy_sha256`; both are part of the run_id. The settings signature
leaves them out, because strategies differ by design; instead
`check_strategies` requires each strategy name to have exactly one
identity across an experiment, and run_experiment.py records every
strategy's hash in the spec, so `--resume` refuses a changed strategy.

Consequences, accepted: milp and hungarian runs from the ROS node
(system scipy 1.11.4) and from pixi (1.17.1) are different identities;
experiments made before M0 have no identity and are refused by
compare_paired.py.

## Experiment Conditions on the Command Line

M0, F7, 2026-10-27. One config stays the only config. An experiment may
override two of its values for every run:

```text
run_experiment.py --quantum T     dispatch quantum, 0 = continuous
run_experiment.py --window W      fleet_size | legacy | integer >= 1
```

They are passed to each run as run_workload.py's existing flags, run.json
records the effective values, the settings signature covers them, and
the experiment spec records them for `--resume`. A condition is
therefore an experiment, never a second config.

## Seed Split

Decided by the researcher 2026-10-27 (training, validation); frozen and refined 2026-10-29 (M1 Review):

```text
training      101-110    ILP training runs only
  exploratory 101-103    M1 outcome-signal diagnostics and development probes
  induction   104-110    primary ILP rule induction pool (M3-M6)
validation    111-115    learner settings (thresholds, bias, limits) chosen here
evaluation    1-30       the frozen conventional main study (pre-M0 exploratory)
final         201-230    sealed evaluation set; never run until final campaign
development   any other seed (42, the reference workload): debugging only
```

`run_experiment.py --purpose training|validation|evaluation|final`
refuses any seed outside that role; without a purpose, the reserved
final seeds are refused. The purpose is recorded in the experiment spec,
so the ILP pipeline (M2) can refuse to learn from an experiment that is
not a training experiment.

Adopted by the researcher 2026-10-27 (M1 brief): 201-230 are the
sealed final evaluation seeds; 101-103 are the M1 development seeds,
inside the training role. Whether seeds 1-30 keep a role is open.

Why a final set is proposed: seeds 1-30 have already been looked at
during development (E1 used 1-3, the paired-seed check 1-5, the fleet
sweep 1-3), so they are no longer strictly unseen. They are not yet
used for any learner, so they remain a valid evaluation set if the
researcher prefers to keep the frozen main study as the final one.
Decision needed before M8.

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

## M1 Acceptance Record and Outcome Signal Freeze (2026-10-29)

Accepted by the researcher on 2026-10-29 following review of the M1 Pilot v2 matrix (18 probes + 2 counterfactual rollout runs + 2 repeatability checks across training seeds 101-103):

```text
1. M1 Closure:
   - The completed 300-order congestion-signal study is accepted as the frozen M1 pilot.
   - The 1,000-order charging-active experiment is deferred to a separate research check before final Outcome-Aware ILP evaluation.
   - robot_soc_percent is rejected as a congestion predictor (established as a monotonic run-clock proxy on non-charging 300-order horizons; restricted to energy feasibility).

2. Outcome Supervision Parameters:
   - Primary evaluation settings: H = 120 s, lambda = 0.50, delta = 2.0 s.
   - Sensitivity settings: lambda = 0.25, delta = 5.0 s.
   - Composite causal objective: J(b) = pivot_mission_s(b) + lambda * waiting_in_h_s(b) over fixed horizon [t_d, min tick >= t_d + 120 s].
   - window_flow_s is rejected as a per-decision labeling objective due to 1/W dilution and unassigned-order queue wait confounding.
   - Treatment of unfinished & censored outcomes:
     * A counterfactual rollout branch where the pivot mission fails to complete inside H, or where a rollout terminates abnormally/stalls, cannot be scored as an improvement.
     * Censored/failed rollouts never generate positive or negative outcome labels; they are marked unverified/censored and excluded from supervision.

3. Seed Partition Freeze:
   - 104-110: Primary ILP rule induction pool (7 seeds).
   - 101-103: Development and exploratory probes (M1 signal verification).
   - 111-115: Validation pool (learner settings, bias constraints, clause selection).
   - 201-230: Sealed untouched final evaluation set.
   - 1-30: Baseline conventional strategy reference sweep.

4. M2 Matching Semantics:
   - Primary quantum-3 batch matching mechanism: M-Card (linear_sum_assignment min-cost max-cardinality over rule-covered pairs).
   - Ablation matching mechanism: M-Greedy (deterministic lexicographic greedy pairing).
   - Learned Rule Integrity: Learned rules must genuinely determine assignments; deterministic matching or fallback logic must not silently override learned preferences.
   - Tie-breaking: Queue-first order (tier, clause_rank, fifo_position, c_static_m, est_finish_s, robot_id) ensures an empty or tautological rule collapses to naive FIFO rather than secretly executing nearest_astar.

5. ILP Representation and Bias Budget:
   - Target predicate: unary assign(D, R, O).
   - Bias constraints: max_vars = 4, max_body = 4, K_max = 5 learned clauses.
   - Negative example sampling: k_neg = 3 sampled negatives per choice-bearing decision (half hard negatives, half uniform negatives) while retaining 100% of feasible pairs in bk.pl.
   - Inference candidate pool: Evaluated against 100% of feasible candidate pairs (eligible == True).

6. M5 Multi-Teacher Supervision Direction:
   - >= 4/6 consensus is NOT frozen as the sole primary supervision method.
   - First quantify teacher agreement coverage, decision diversity, and evaluate against a deterministic plurality comparator that retains decisions lacking a 4-teacher consensus.

7. Congestion Predicate Whitelist for ILP:
   - Tier 1 Survivors: occupied_now, st_conflict_robots, st_conflicts_30s, st_conflicts.
   - Tier 2 Survivors: local_density, overlap_ratio.
   - Within-decision relative delta predicates (f(r,o) - min_o' f(r,o')) preferred to eliminate t=0 simultaneous launch cold-start distortion.
   - Pruned from active ILP vocabulary: route_cells (collinear with c_static_m), overlap_cells (inverted/confounded), shelf_load (sparse/null), dropoff_load (null in 5/6 conditions).
```

## M3 Execution Semantics Amendment (2026-10-31)

Approved by the researcher on 2026-10-31 (decision D1), before any validation seed was used. This amends item 4 of the M1 Acceptance Record, whose text above stays as the record of the earlier decision. Items 1-3 and 5-7 are unchanged.

```text
1. Primary matching for greedy teachers (nearest_astar, earliest_finish, auction, multi_criteria): iterative.
   - Evaluate the learned rules on the remaining eligible pairs.
   - Make one pick: the smallest (tier, clause_rank, fifo_position, c_static_m, est_finish_s, robot_id).
   - Remove that robot and order, and repeat until no eligible pair remains.

2. Comparison and global-teacher matching (hungarian, milp): one-shot M-Card, retained as a pre-registered arm.
   M-Greedy remains an ablation.

3. Rule priority, both semantics:
   - A rule-covered pair is never given up to increase cardinality.
   - Fallback only fills robots and orders left over after rule-covered matching.
   - Every assignment records whether a rule or fallback made it.
   - This resolves the conflict between item 4 and M2 spec 4.4 step 3 in favour of item 4.
     The cost is that a matching may be maximal rather than maximum.

4. Unchanged: the queue-first tie-break, so an empty or tautological rule collapses to FIFO, not to nearest_astar.

5. Claims:
   - The greedy sequence comes from the fixed executor, not from learning.
   - A rule equivalent to nearest_astar under this executor is reported as an "ILP-recovered explanation of nearest_astar", not a new strategy, and is excluded from strategy rankings.
   - Fidelity to a greedy teacher is close to automatic once the conjunction is found, so it never counts alone as evidence of learning.
```

Reason: at quantum 3 on 2,623 Phase 0 decisions, one-shot M-Card reached 0.479 fidelity to nearest_astar and iterated execution 0.99. The risk is bias toward greedy teachers in M5, so per-teacher ceilings are reported.

## M4-M6 Decisions (2026-11-02)

Approved by the researcher after the exploratory M4 results (`.scratch/m4-single-teacher/exploratory-2026-11-01.md`):

```text
1. Vocabulary:  an extended set, off by default so Tier A stays as M3 froze it:
                near_best_cost_for_robot, best_finish_for_order,
                oldest_eligible_for_robot, aging_window_order, batch_contention,
                high_fleet_busy_ratio, high_queue_pressure (M2 spec 5.2).
                aging_window_order reads the decision's window_orders, not the
                configured width. cost_regret cutoff is its 25th percentile;
                busy_ratio and queue_pressure use the 75th. Choices are made on
                induction seeds only.
2. Validation:  seeds 116-120 validate M4, M5 and M6 as one pool, used with
                --purpose backup. 111-115 stay spent by M3.
3. M4 teacher:  multi_criteria, the strongest on induction seeds 104-110 at
                quantum 3. This replaces the spec's selection on 111-115.
4. M5 labels:   majority (policy C1) and plurality (policy C3), both reported.
5. Statistics:  Friedman primary, as the proposal states, then pairwise
                Wilcoxon with Holm; the paired t-interval is reported beside
                them. To be written into EXPERIMENT_PROTOCOL.md before M8.
```

M4, M5 and M6 share one state pool, `bk.pl`, `bias.pl`, thresholds and matcher, and differ in labels only. Labels come from the teacher panel on `nearest_astar` states.

## M6 Outcome Label Rules (2026-11-06)

Specified by the researcher in the M6-1 and M6-2 task briefs. The scoring contract (M1 record item 2) is unchanged.

```text
1. Branches:  the teacher's pivot pair and its tested alternatives are scored alike;
              which branch was the teacher's never enters the label.
2. Positive:  the unique best completed branch, at least delta = 2.0 s better in J than
              the second best completed branch.
3. Negative:  a completed branch at least delta worse than the best completed branch,
              also when there is no unique winner.
4. Abstain:   everything else; untested pairs are never examples.
5. Censoring: stalled branches (failed) and branches whose window or pivot mission is
              not done inside H (censored) are never examples.
6. Primary mode "conservative": no positive at a decision where any branch is censored
              or failed. Sensitivity mode "m6_1": the M6-1 rule, positive among the
              completed branches regardless.
7. States:    M6-A probes the M4/M5 corpus decisions, replayed and proven identical, so
              M4, M5 and M6-A share bk.pl, bias.pl and thresholds byte for byte.
8. Seeds:     rollouts and labels refuse any run outside 104-110.
```

Remaining bias (state distribution, pivot-only action sampling, nearest_astar continuation, censoring) and the M6-B / M6-C designs: `.scratch/m6-outcome-aware/design.md`.

## M6-MP Objective and Continuation Decisions (2026-11-06)

Decided by the researcher during M6-MP. M6-A and its frozen M1 J are unchanged.

```text
1. Multi-pair arm (mp1): candidates span robots and orders at one recorded state
   (behaviour pair, panel proposals, alternative robot and order, one seeded random
   control), each forced by rollout.ForcedPair; probed decisions come from a stratified
   sample (charging, low battery, congestion, waiting, normal, random control).
2. Objective jw1 (fixed evaluated order set) REJECTED: best branch disagreed with the
   whole-window sum in 4 of 6 pilot decisions; orders outside the set moved by up to
   2,706 s.
3. Objective jw2 ADOPTED as the proposed primary for mp1: whole-window total order flow
   time + 0.5 x fleet waiting over 120 s, the window fixed at the decision and identical
   in every branch; delta 2.0 s; conservative mode. Validated on 24 decisions (70 and
   100 robots): 0 censored, labels stable for lambda 0-1 (agreement 0.94-1.00).
4. Battery vocabulary (best_margin_for_order, low_energy_margin,
   near_charger_after_order, high_charging_pressure) available to M4, M5 and M6 alike
   (--battery); the vocabulary without it is the ablation.
5. P0, continuation-policy dependence: candidates are continued by nearest_astar,
   multi_criteria and auction; a label is given only when every policy agrees
   (cross_policy_labels). The HPC campaign waits for the cross-policy pilot.
6. Stage 2 (later): ILP self-refinement, outcomes collected with the learned policy
   continuing, then retraining.
```

Evidence: `.scratch/m6-outcome-aware/design.md`; pilot outputs under `runs/m6mp_pilot/`.

## Research Architecture: Four Groups (2026-11-07)

Decided by the researcher. Previous artifacts (M3, M4, M5, M6-A, M6-MP, the cross-policy
pilot) are kept as they are.

```text
A. Conventional baselines   all eight strategies run independently and are recorded
B. Single-teacher ILP       one Popper policy per strategy, from that strategy's own runs
                            and picks; compared with its own teacher
C. Multi-teacher ILP        pooled demonstrations (runs of several strategies, each
                            labelled by its own picks, no agreement required) and the
                            existing panel votes (M5)
D. Outcome-aware ILP        labels from measured counterfactual outcomes (jw2, mp1)
Workloads    W100; 70, 80, 90, 100 robots; 300 orders (normal), 2,000 (main, battery and
             charging), 10,000 and 20,000 later for long-duration validation
Seeds        104-110 training, 116-120 validation, 201-230 sealed final test
Cross-policy agreement is a sensitivity experiment, not a training requirement.
```

Matching per teacher (ilp_learning/teachers.py): iterative for nearest_astar,
earliest_finish, auction and multi_criteria and M-Card for hungarian and milp, as the M3
amendment states; iterative for random and nearest_manhattan by the same rule (one greedy
sweep), to be confirmed by the researcher.

## M6 Continuation and Per-Teacher Outcome Supervision (2026-11-07)

Decided by the researcher. Amends M6-MP item 5: cross-policy agreement is no longer
required for a label. M3-M6 artifacts and the cross-policy pilot are kept as they are.

```text
1. Baseline:     nearest_astar continuation for the initial M6 outcome-aware baseline
                 only (train_rules --continuation nearest_astar).
2. Per teacher:  each strategy generates its own states (its own corpus runs);
                 alternative assignments are forced from the identical replayed state;
                 the generating strategy continues every branch (m6_rollouts default;
                 train_rules --continuation behaviour).
3. Pooled:       outcome labels from those individually labelled datasets
                 (--pooled --continuation behaviour). No teacher agreement required;
                 every label is relative within one recorded state, so no raw score is
                 compared across unrelated states. The pool block keeps provenance.
4. Sensitivity:  cross-policy continuation (several policies, label only on agreement)
                 stays available: multi-policy rollouts without --continuation.
5. Seeds:        validation is 116-120 (--purpose backup); 111-115 stay spent by M3;
                 201-230 stay sealed.
```

## Campaign Decisions Before the 300-Order Run (2026-11-07)

Decided by the researcher before any campaign result exists.

```text
 1. Freeze:       review, keep GUI work out, test and commit a reproducible research
                  version before the campaign.
 2. Matching:     random and nearest_manhattan rules run iterative (confirmed).
 3. Primary D:    pooled per-teacher outcome learning (--pooled --continuation
                  behaviour). nearest_astar continuation stays a controlled baseline.
 4. Probing:      48 decisions per run (QUOTA=8 x 6 strata); measure the usable label
                  yield before deciding whether 2,000 orders need more.
 5. States:       matched training states (MATCHED=1, the probed decisions) primary;
                  all available decisions secondary.
 6. 2,000 orders: only after 300-order training gives usable rules.
 7. Metric:       order_makespan_s primary (mission-time hypothesis); mean order flow
                  time, waiting and charging reported separately.
 8. Fallback:     a policy with fallback_rate > 0.5 is flagged; its results are kept,
                  but its performance is not attributed mainly to learned rules.
 9. Experiments:  both. A (train 70-100) first, then B (train 70-90, test at 100).
10. Refinement:   ILP self-refinement secondary, only if time permits.
11. Workloads:    spatial hotspot workloads first (needed for grid-awareness claims);
                  10,000 orders optional robustness; 20,000 and held-out maps future.
12. Popper:       300 s limit kept; measure timeouts before choosing a larger budget,
                  which would then be identical for every compared method.
```

Gate before full evaluation (Problem A): an outcome-aware artifact counts as learned
only if it has a nonempty program, its rules fire in closed loop, and its coverage is
meaningful (fallback_rate <= 0.5).

Measurement intervals (Problem B). They are not the same interval:

```text
M6-A frozen J   pivot_mission_s + 0.5 x fleet waiting, waiting over (t_d, t_d + 120 s]
jw2 (mp1)       sum over the decision's window orders of (completed_at - t_d)
                + 0.5 x fleet waiting over (t_d, t_d + 120 s].
                A branch runs until 120 s have passed AND every window order is done
                (rollout.horizon_stop), so the flow term can extend well past 120 s
                and its end differs per branch; only the waiting term is fixed at 120 s.
                completion="horizon" (sensitivity) censors any window order done
                after t_d + 120 s.
```

Execution order: commit; 300-order teacher corpus; single- and multi-teacher training
(check clauses, fidelity, fallback); outcome labels (check positives and rule
learning); first closed-loop comparison. Cluster-scale training is decided after that.

## Long Workload, Workload Fact and Outcome-Corrected Labels (2026-11-07)

Decided by the researcher after the 300-order experiment A (no charging in any
300-order run; group D outcome rules empty or nearly so):

```text
long workload   5000 orders at 70/80/90/100 robots replaces 2000: about
                6.5-6.9% SOC per order gives 4-5 charge cycles per robot
                (2000 gives 1-2). Measured on one run (nearest_astar, 100
                robots, seed 104): 300 charging sessions, 21% of robot time
                waiting, 16.5 min wall, 2.6 GB peak
balance metric  spread of orders, distance and energy across robots,
                reporting only
workload fact   a decision-time "work done so far" predicate per robot
                (past only, never robot_id): least_driven_robot(D,R) over
                robot_distance_m, the robot's cumulative distance, switched
                on with --workload (built 2026-11-09)
group D labels  outcome-corrected demonstrations (train_rules
                --outcome-correct): the teacher's own picks of the probed
                decisions, corrected where its own continuation shows its
                pair lost by delta to a decisive best branch; pairwise
                preferences stay a later sensitivity option
cluster         training on the cluster; the researcher submits jobs
```

A corrected rule set runs under its teacher's matcher (cardinality for
hungarian and milp); a pooled one under the default. The plain mp1 rollouts
are not affordable at 5000 orders as designed (about 9 min per branch, about
10 h per shard); the rollout horizon is an open decision.

## Intermediate Workload and Gate 1 (2026-11-09)

Decided by the researcher:

```text
workload        1500 orders at 70-100 robots before 5000: about one charge per
                robot (benchmark: 70-90 charging sessions per run), steady
                congestion, about 4-5 min per run on one hpc1 core (AMD EPYC
                9554), under 1.3 GB
Gate 1          is "better than the teacher" possible at all? The oracle
                (ilp_learning.m6_oracle) for nearest_astar, multi_criteria and
                milp at 70-100 robots on validation seeds 116-120:
  option A      300 orders, --objective wholerun: every branch runs to the end;
                lowest order makespan wins, ties within delta broken by the sum
                of completion times (the exact rollout objective)
  option B      1500 orders, --objective horizon: every branch stops 120 s after
                the decision; a window order costs its completion time if done,
                else 120 s plus its best static finish estimate at the decision
                (optimistic remaining time), plus 0.5 x fleet waiting in 120 s
                (approved 2026-11-09); approximate, cost independent of the
                workload length
                Both run (A first). A branch beats the teacher's only by delta 2 s.
cluster         hpc1/hpc3 partitions; the cluster allows 5 tasks per job array,
                so tasks are packed into multi-core jobs (xargs inside one job)
```

The jw2 oracle at 1500 orders is not affordable (about 4.5 min per decision on
hpc1, about 450 decisions per run).

## Gate 2 Pass Criterion and Order (2026-11-10)

Decided by the researcher after Gate 1 (oracle beat its teacher in 60/60 runs at 300
orders and 56/60 at 1500; `runs/gate1/summary.txt`) and before any Gate 2 data exists.
Plan: `.scratch/gate2-oracle-distillation/plan.md`.

```text
question     does an ILP policy trained on oracle choices beat its teacher closed loop
             on validation seeds 116-120? Training data only from induction seeds 104-110.
pass         per teacher (nearest_astar, multi_criteria, milp), on the same 20 paired
             runs (70/80/90/100 robots x seeds 116-120):
               1. Problem A gate: non-empty program, rules fire, fallback_rate <= 0.5
               2. mean order makespan below the teacher's: paired Wilcoxon signed-rank,
                  Holm over the three teachers, alpha 0.05
reported     recovered share = (teacher - student) / (teacher - oracle), flow time,
             waiting, distance, charging, rule count and length, decision time
order        option A (300 orders, wholerun oracle) first; option B (1500 orders,
             horizon oracle) only if A gives non-empty rules with fallback <= 0.5
```

Group D labels (decided 2026-11-10, option P, before any Gate 2 data): oracle
demonstrations, the oracle's own picks along its own run (`m6_oracle`), replace the
outcome-corrected labels as the primary group-D label. Amends "Long Workload, Workload
Fact and Outcome-Corrected Labels" (2026-11-07), group D labels. Why: corrected labels
gave no closed-loop gain on validation seeds 116-120 (nearest_astar +8.3 s,
multi_criteria +17.3 s), and Gate 1 shows the oracle's choices do beat every teacher.
Selection used validation seeds only; evaluation seeds 201-230 stay sealed. The
corrected-label result is reported as a development-stage finding and is not re-run.

Gate 2 training and test details (decided 2026-11-12, after the 84 option-A oracle runs
finished and before any rule set was trained or evaluated):

```text
vocabulary   --extended --tier-b --battery --workload for every Gate 2 rule set
             (oracle-label and same-state imitation arms alike)
arms         per teacher: oracle labels (--oracle-labels) and imitation control
             (--label-teacher T on the same oracle runs); 6 rule sets in total.
             No pooled arm: it is not part of the pass criterion.
test         one-sided paired Wilcoxon signed-rank, alternative "student makespan lower
             than teacher" (gate2_verdict --alternative less), Holm over the three
             teachers, alpha 0.05. The oracle-vs-imitation contrast uses the same
             direction and is reported only. A student slower than its teacher is
             reported descriptively (mean difference, wins) and cannot pass.
```

Why one-sided: the research question and criterion 2 ("makespan below the teacher's")
are directional, and the direction is fixed before any Gate 2 result exists.

## Robust Oracle Labels and the Combined Campaign (2026-11-12)

Decided by the researcher after Gate 2 option A failed and before any robust oracle run.
Why: in option A the uniformly random control candidate won 49 % of the decisive overrides
and carried 54 % of the oracle's gain (`.scratch/gate2-oracle-distillation/results-option-a.md`),
so single-future oracle labels are largely luck of one deterministic, chaotic future.

```text
robust oracle   ilp_learning.m6_oracle --continuations nearest_astar multi_criteria milp:
                every candidate of a decision, the teacher's pair included, is rolled
                out (objective wholerun) once per continuation policy; the behaviour's
                own name continues with the behaviour policy itself, as before. The
                candidate with the best mean score is taken only if it beats the
                teacher's pair under EVERY continuation by the existing criterion
                (makespan lower by delta 2 s, or equal makespan and sum of completion
                times lower by delta); otherwise the teacher's pair is kept. One
                continuation is exactly the option-A oracle.
pilot           nearest_astar, 100 robots, 300 orders, induction seeds 104-110 (7 runs)
stop rule       fewer than 8 decisive robust overrides (makespan gain >= 2 s) in the
                7 pilot runs (the bar of 30 per teacher over 28 runs, scaled to 7 runs):
                the outcome-aware arm is reported as "learnable headroom about zero" and
                dropped; the rest of the campaign goes on without it. Reported either way:
                the random control's share of the robust winners and the robust oracle's
                makespan against the teacher's.
if it passes    robust oracle for nearest_astar, multi_criteria and milp at 70-100 robots
                on 104-110 (84 runs), then rule training and the closed-loop test exactly
                as Gate 2 (oracle labels and same-state imitation control, same
                vocabulary, one-sided Wilcoxon, Holm, alpha 0.05, fallback <= 0.5).
                116-120 has now been used for selection twice: a pass there is development
                evidence; the claim comes only from seeds never used before.
campaign        one evaluation of all arms on unseen seeds: the teachers, single-teacher
                ILP, multi-teacher ILP and outcome-aware ILP (if it survives), at 300, 1500
                and 5000 orders and 70-100 robots. Rules are trained on 104-110 only; the
                300- and 1500-order teacher corpora are reused (no simulator change since
                789a66c). 5000 orders is evaluation only (no oracle there). Seeds, lengths
                and statistics of the campaign are decided before it runs.
```

## M6 Closed (2026-11-13)

Decided by the researcher after the robust-label Gate 2 and the separability test.

```text
result      the oracle beats its teacher (plain -6 to -8 %; robust, a pick must win in
            three futures, -4 to -6 %, 27/28 runs per teacher, 300 orders), but no rule
            set trained on oracle or robust-oracle labels beats its teacher on 116-120
            (Gate 2 failed twice, all three teachers). Only multi_criteria's robust rules
            differ from the imitation control (-11.8 s vs imitation, not significant).
why         about half of the plain oracle's gain is luck (the random control candidate
            wins 49 % of decisive overrides); the robust improvements are about 3 per run
            among about 150 picks; and a flexible classifier on all decision-time
            features cannot tell which alternative is the improvement (within-decision
            top-1 39 % vs chance 35 %; pair-difference AUC 0.52-0.56; held out by seed).
            The limit is the information in the decision-time state, not the learner.
record      .scratch/gate2-oracle-distillation/results-option-a.md, results-robust.md,
            audit-2026-11-13.md
next        M8 final campaign (decided separately before it runs). No further M6 label
            schemes; the robust multi_criteria rule set may enter the campaign as the
            outcome-aware arm if the campaign decision includes it.
```

## M8 Final Campaign (2026-11-13)

Decided by the researcher before any campaign run. Everything runs on the cluster.

```text
arms (20)    A  the eight conventional strategies: random, nearest_manhattan,
                nearest_astar, earliest_finish, multi_criteria, auction, hungarian, milp
             B  single-teacher ILP for each of the eight (that teacher's runs and picks,
                its own matcher)
             C  multi-teacher ILP: pooled (all eight teachers' runs, each run labelled by
                its own picks), panel majority and panel plurality (votes of the panel on
                nearest_astar states, as M5)
             D  outcome-aware ILP: the robust multi_criteria rule set from Gate 2
                (runs/gate2/rules/o300_robust/oracle_multi_criteria, trained at 300 orders)
training     B and C on the 1500-order teacher corpus (runs/teachers/o1500), induction
             seeds 104-110, 70-100 robots; vocabulary --extended --tier-b --battery
             --workload (as Gate 2); Popper 300 s, decision cap 800, unchanged
lengths      1500 and 5000 orders (both with charging); 70, 80, 90, 100 robots;
             quantum 3, window fleet_size (the primary condition)
seeds        dry run on 1-3 (--purpose evaluation) to catch failures, nothing chosen from
             it; then the final on the sealed 201-230 (--purpose final), run once
statistics   per length and fleet size: Friedman over the 20 arms, blocks = seeds;
             post hoc two-sided paired Wilcoxon, Holm within each family per length:
               ILP family    each B arm vs its teacher; each C arm vs nearest_astar;
                             D vs multi_criteria and D vs B multi_criteria (13 x 4 fleets)
               conventional  each A arm vs nearest_astar (7 x 4 fleets)
             beside them: mean difference with its paired 95 % t-interval, d_z, wins/losses
primary      order_makespan_s
secondary    mean_completion_time_s, total_distance_m, total_waiting_s,
             energy_consumed_percent, charge_sessions, charger_wait_time_s; each its own
             Holm family, reported as secondary
failures     a failed or stalled run is a missing pair, never imputed; counted per arm
```

## Held-out Transfer Campaign (2026-11-13)

Decided by the researcher after the M8 final run and before any run on the held-out map.
Builds the map that "Warehouse Scalability" planned (deferred 2026-10-27, now step 3b).

```text
map          H100 (src/simulation_env/maps/H100.xlsx, scripts/make_heldout_map.py):
             76 x 28 cells, horizontal racking, drop-offs on the left and right walls,
             24 chargers + 12 holding in one top-right block, 100 parking (top-left and
             bottom walls), 512 shelves, 1412 road cells (W100: 1424). Never trained on.
             Gates passed 2026-11-13: map_tool check OK; check_map_topology OK (strongly
             connected, 0 two-way core edges, all 684 terminals enter/leave, 467,172 legs,
             0 unreachable, 0 through a terminal); 10 smoke runs (100 robots, 1500 orders,
             dev seed 42, 8 teachers + 2 rule sets) complete, 0 reservation violations
selection    run_experiment.py --map H100.xlsx (each seed's workload generated from H100;
             map name and sha256 in the spec; map_path/map_sha256 in every run.json and
             the settings signature, so H100 runs never pair with W100 runs). The one
             config stays on W100
arms         the same 20 as "M8 Final Campaign", the same rule files, unchanged (trained
             on W100 seeds 104-110 only); thresholds in metres are applied as trained
lengths      1500 and 5000 orders (option a, kept equal to M8 so the tables compare one to
             one). Measured: at 1500 orders on H100 the better policies charge 0-14 times
             per run (W100 about 81), because orders are shorter (108 vs 121 m); this is
             part of the transfer result, not corrected
fleets       70, 80, 90, 100 robots; quantum 3, window fleet_size
seeds        dry run 1-3 (--purpose evaluation, nothing chosen from it), then the sealed
             transfer set 301-330 (--purpose transfer, new role, refused otherwise), run once
statistics   exactly as "M8 Final Campaign": Friedman over the 20 arms per length and fleet;
             two-sided paired Wilcoxon, Holm per family x length x metric; same families,
             primary order_makespan_s, same secondary metrics
transfer     a W100 comparison transfers if H100 shows a significant difference in the same
             direction; reported comparison by comparison, never pooled across maps
failures     a failed or stalled run is a missing pair, never imputed
```

## M8 and Transfer Results (2026-11-14)

Recorded after both sealed runs and their statistics. Nothing below changes a frozen design;
it records the outcome and the claim the report may make.

```text
runs         M8 final: W100, seeds 201-230, jobs 479988-479995, code fb0a6bd
             transfer: H100, seeds 301-330, jobs 480047-480054, code da921a9
             each 4800/4800 runs, 0 missing pairs, 0 reservation violations; both
             seed sets are now spent and must not be re-run
statistics   simulation_env.campaign_stats on each campaign's summaries; stats/ in
             runs/campaign/{final,transfer}; reproduced exactly by
             runs/campaign/final/report/build_report.py
W100         D vs plain ILP copy of multi_criteria: -1.4 to -5.3 % makespan, significant
             in 8/8. D vs multi_criteria: worse at 1500 x 70-90 (+0.6/+1.2/+1.9 %,
             significant), tied in the other 5. multi_criteria has the best Friedman
             mean rank in all 8 settings
H100         D vs plain copy: -1.4 to -3.0 %, significant 8/8. D vs multi_criteria:
             -0.4 to -1.5 %, significant at 1500 x 90 and at all four 5000 settings.
             D has the best Friedman mean rank of the 20 arms in all 8 settings
transfer     of 96 primary comparisons significant on W100: 81 transfer, 4 reverse,
             11 do not hold. D's gain over plain imitation transfers 8/8. D's W100
             deficit against multi_criteria does not transfer (reverses at 1500 x 90,
             gone at 70 and 80); its H100 wins at 5000 orders are new on H100
reading      multi_criteria's lead over nearest_astar shrinks from -0.5..-1.2 % (W100) to
             -0.3..+0.9 % (H100), while D vs nearest_astar moves from -0.7..+0.8 % to
             -0.6..-1.3 % (descriptive, not a pre-registered test). Why the teacher
             weakens on H100 was not tested
claim        outcome labels beat plain imitation on both maps; the outcome-aware rules do
             not beat their teacher on the training map and do beat it on one held-out
             layout. No general claim that learned rules beat the teacher
caveats      D (300-order robust-oracle labels) and its B counterpart (1500-order teacher
             corpus) differ in training data as well as label type. auction = ILP auction
             = pooled = plurality and majority = ILP earliest_finish on both maps
correction   "Held-out Transfer Campaign" quotes 0-14 charge sessions per run at 1500
             orders on H100; that was measured with 100 robots only. In the final run the
             strong policies charge about once per robot at 70-90 robots and about 2 times
             per run at 100 robots (W100: about 81 per run)
```
