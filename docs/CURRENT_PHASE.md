# Current Development Phase

## Current Position

The deterministic warehouse simulator is complete and verified. The research learner is not yet implemented.

Current high-level state:

```text
Deterministic simulator       DONE
Directional traffic          DONE
Directed A* routing          DONE
Gazebo mirror                DONE
Nearest A* baseline          DONE
V2 warehouse map             DONE, not yet committed
Battery scheduler V1         IN PROGRESS
                             B0 charging bank DONE, visually approved
                             B1 SOC accounting DONE, approved
                             B2 eligibility + CHARGE_DUE/NEEDED DONE,
                                approved
                             B3 charger / holding travel DONE,
                                approved, verified in Gazebo
                                2026-10-06
                             B4 charging + leaving + rejoin COMPLETE,
                                VERIFIED, APPROVED 2026-10-06,
                                committed 5d61c84
                             B5 holding promotion + preemption
                                IMPLEMENTED 2026-10-11,
                                awaiting approval
                             B6 NOT STARTED
W20/W30/W50 map family       PLANNED, after Battery V1 (decided 2026-09-13)
Additional MRTA baselines    NOT IMPLEMENTED
Outcome logging              NOT IMPLEMENTED
Experiment runner            NOT IMPLEMENTED
ILP / Popper                 NOT IMPLEMENTED
Outcome-aware learning       NOT IMPLEMENTED
Explanations                 NOT IMPLEMENTED
```

## Current Map

The current V2 map is a 17 x 35 XLSX warehouse with:

```text
72 shelf service cells
36 dropoff cells
22 Stage Zone home cells
8 charging cells
14 charge-queue / holding cells
134 blocked cells
```

Chargers and holding cells form two one-cell-deep terminal banks,
y=2 and y=32, reading `C Q Q C Q Q C Q Q C Q` over x=4..14 (B0).

The current V2 map is the starting point for the W20-class warehouse.

For strict W20 experiments, use 20 active robots from the available home capacity unless a dedicated 20-home map is created later.

## Current Scientific Baseline

The currently implemented allocation strategy is:

```text
nearest_astar
```

The simulator uses directed A* over the one-way warehouse graph.

Gazebo replays the scientific execution rather than running an independent planner.

## Measured Baseline

Measured on 2026-09-13 after B0, branch `traffic/rmf-integration`,
working tree. Counts are read from the XLSX, not from documentation.

| Gate | Result |
|---|---|
| map load `CSV_To_Gazebo_v2.xlsx` | 17 x 35, 134 obstacles |
| shelves / dropoffs / Stage Zone homes | 72 / 36 / 22 |
| chargers / charge-queue holding | 8 / 14 |
| transit core cells | 309 |
| directed core edges | 485 |
| two-way (uncontrolled bidirectional) core edges | 0 |
| strong connectivity | 309/309 forward, 309/309 reverse |
| terminal access, enter from core and leave to core | shelf 72/72, dropoff 36/36, staging 22/22, charging 8/8, holding 14/14 |
| station legs / unreachable | 22952 / 0 |
| legs through a terminal / terminal->terminal moves / moves not in nav graph | 0 / 0 / 0 |
| nav graph vs lane policy | 973 lanes, 0 illegal, 0 missing, 0 terminal chain, 0 stranded |
| RMF `graph_check` | 461 waypoints, 973 lanes, OK |
| 100-order workload | 100/100, 22/22 parked, makespan 282.0000 s, 6430.0 m, waiting 264.6667 s, 0 violations |
| 500-order workload | 500/500, 22/22 parked, makespan 1123.3333 s, 29338.0 m, waiting 1242.6667 s, 0 violations |
| charger / holding cells ever occupied in those runs | 0 and 0; 0 terminal->terminal moves executed |
| Stage Zone occupancy | 22 distinct cells on both runs; 22/22 and 21/22 parked off spawn |
| unit tests | 155 passed |
| traffic behaviour suite | 8/8 PASS |
| lint | clean |

B0 changed the transit core (was 353 cells, 569 edges) and the nav
graph (was 483 waypoints, 1013 lanes). Every workload number is
bit-identical, because no route ever used the charging apron.

The superseded V1 map (`CSV_To_Gazebo.xlsx`, 18 homes, 40 dropoffs)
has the same two-deep apron and now fails the gate under the
battery-era terminal policy: 331 transit cells, not strongly connected,
14/16 chargers usable, 289 unreachable legs. V1 is not maintained.

## Known Loose Ends Before New Feature Work

The current V2 changes are not committed.

Resolved during the Phase A audit:

```text
practice-map tests repaired   test_nearest_astar.py now uses a
                              policy-legal one-band miniature of the
                              production map; 5/5 pass
openpyxl declared             added to pixi.toml; the
                              /usr/lib/python3/dist-packages
                              PYTHONPATH workaround is no longer needed
pixi run test fixed           it only put mrta_core on the path, so the
                              task_management tests never ran at all
final parking policy          the Stage Zone bank is now a shared
                              fleet resource. A robot claims the
                              nearest FREE cell by directed A* when
                              the fleet runs out of work, reserved at
                              assignment time so two robots cannot
                              pick the same cell. Spawn position
                              confers no title. See DECISIONS.md.
nav graph terminal fidelity   the generated graph carried 232
                              terminal -> terminal lanes, letting a
                              static RMF route walk the home bank or a
                              shelf column. Both halves of the lane
                              policy now resolve in one place,
                              traversable_lane_edges(); the graph is
                              1013 lanes and mirrors the scientific
                              planner cell for cell.
```

Still open:

```text
lane policy is hard-coded to V2 coordinates
    EASTBOUND_ROWS, WESTBOUND_ROWS, NORTHBOUND_COLS, SOUTHBOUND_COLS,
    SHELF_COLS, OPEN_BANDS and the frontage lane columns in
    warehouse_routing.py are absolute 17 x 35 indices. A map of any
    other shape cannot currently be legal. This must be parameterised
    before W30 and W50 exist; it is a Phase B prerequisite, not a
    cosmetic cleanup.

docs/images/ holds 19 MB of generated PNGs referenced by nothing.
    Keep them out of the research code commit.

docs/16_CURRENT_STATUS.md still carries its 2026-08-29 component
    table, which lists "Battery / charging COMPLETE". That describes
    the pre-freeze simulator deleted in 7a07208; battery is not
    implemented. The file is marked superseded but the row misleads.
```

Resolved in Battery V1 increment B0 (2026-09-13):

```text
charging apron migration      With chargers and holding cells as
                              terminals, 16 of the 32 apron cells
                              could not be both entered and left, not
                              the 4 "stranded chargers" previously
                              reported: that count came from the nav
                              graph generator's local lane check, and
                              two of the four were holding cells. The
                              4 corners had no aisle neighbour; the
                              rest of each outer row hung off one-way
                              stubs. The 12-cell relabel formerly in
                              runbook 2.7a sealed 22 terminals and was
                              wrong. Fix: one-cell-deep banks (outer
                              rows y=1, y=33 walled), and the lane
                              policy forbids terminal->terminal edges.
                              See 00_RUNBOOK.md 2.7a.
```

## Immediate Phase

### Phase A: Freeze the verified baseline

Acceptance, as measured on 2026-09-13:

```text
V2 map loads normally                          MET
current topology checks pass                   MET
current routing checks pass                    MET
current traffic checks pass                    MET
100-order workload passes                      MET
working tree changes are understood            MET
practice-map tests are repaired                MET
openpyxl is declared in project environment    MET
baseline commit created only when explicitly requested
                                               PENDING APPROVAL
```

### Phase B: Create the warehouse scalability family

Create:

```text
W20
W30
W50
```

Each warehouse must use the same semantic rules while scaling capacity.

Each map must independently pass:

```text
map load
strong connectivity
0 uncontrolled bidirectional transit edges
all terminals reachable
all chargers reachable
all holding cells reachable
nav graph verification
traffic stress test
```

Do not begin battery implementation on W30/W50 until the maps are structurally valid.

### Phase C: Battery Scheduler V1

Status: CURRENT, started 2026-09-13 on the V2 map, ahead of Phase B.

Implement only the approved V1 policy from `docs/DECISIONS.md`,
including the decisions approved on 2026-09-13 (CHARGE_NEEDED,
preemption rights and guard, queue discipline, end of workload).

Do not redesign charging while implementing it.

One increment at a time. After each: focused tests, full regression
gate, report, stop for approval.

```text
B0  charging-apron topology              DONE, visually approved
B1  SOC accounting                       DONE, approved
B2  eligibility + CHARGE_DUE / NEEDED    DONE, approved
B3  charger reservation + travel         DONE, approved 2026-10-06
B4  charging + leaving + fleet rejoin    COMPLETE, VERIFIED, APPROVED 2026-10-06
                                         committed 5d61c84
B5  preemption + holding promotion       IMPLEMENTED 2026-10-11,
                                         AWAITING APPROVAL
B6  decision / outcome battery metrics   NOT STARTED
```

B0 acceptance, measured 2026-09-13:

```text
every charger enterable and leavable              MET  8/8
every holding cell enterable and leavable         MET  14/14
no terminal-to-terminal move                      MET  0 of 22952 legs
no terminal used as intermediate transit          MET  0 of 22952 legs
directed topology strongly connected              MET  309/309
nav graph matches production routing              MET  973/973 lanes
100-order baseline unchanged                      MET
500-order baseline unchanged                      MET
traffic suite                                     MET  8/8
Gazebo worlds regenerated                         MET  warehouse_grid, warehouse_10
visual inspection in Gazebo                       MET  (user, 2026-09-13)
```

B1 acceptance, measured 2026-09-13. Observation only: one
`BatteryLedger` in the fleet engine, booked at the engine's existing
travel / service / wait call sites with the clock's own Fraction
durations; idle is the remainder of elapsed time. Nothing reads SOC.

```text
20 m travel / 60 s service / 300 s wait / 300 s idle = 1%   MET  exact
combined hand calculation, yield + rejoin as travel         MET
service not double-counted as waiting                        MET
SOC identity, cross-check vs engine counters                 MET  <= 6.4e-14 %
deterministic repeat, per-robot initial-SOC override         MET
battery on vs off: dispatches, completions, totals, parking  MET  identical
100-order output vs B0 (RESULT + Stage Zone table)           MET  identical
500-order output vs B0 (RESULT + Stage Zone table)           MET  identical
100 orders  final SOC min 80.7222  mean 84.7322  max 86.6256   fleet 335.8911 %
500 orders  final SOC min 27.8989  mean 30.7151  max 33.6000   fleet 1524.2689 %
```

B2 acceptance, measured 2026-09-13. Eligibility is a hard mask for every
strategy: `required = travel(route incl. post-dropoff retreat) + service
+ directed-A* leg from the retreat cell to the nearest charger + 10%
reserve`, eligible when SOC >= required; no predicted waiting. The
executor re-derives every returned pair and raises
`IneligibleAssignmentError` rather than execute or re-assign one.

```text
zero margin eligible, negative margin refused                MET
closer but ineligible robot skipped                          MET
charger leg is directed A* (28 m), not Manhattan (4 m)       MET
no predicted waiting term; realized waiting drains SOC        MET
busy robot crossing 10% finishes whole order, then REQUEST    MET
CHARGE_NEEDED judged over all pending orders, not the batch  MET
no pending work -> no CHARGE_NEEDED                          MET
allocator returning an ineligible pair -> explicit error     MET
deterministic eligibility fields                             MET
100 and 500 orders vs B1, incl. every SOC row                MET  identical
  ineligible candidate pairs      0 / 0
  CHARGE_DUE / CHARGE_NEEDED      0 / 0 and 0 / 0
  lowest margin over an assigned pair  71.2722 % / 19.1922 %
controlled 50/20/14/12/10/9 % fleet
  t=0: 12% CHARGE_NEEDED, 10% and 9% CHARGE_DUE, 14% fits 36/100
  40-order run: 40/40, 156 ineligible pairs, 0 ineligible assignments
```

B3 acceptance, measured 2026-09-13. On entering REQUEST a robot
reserves, before it moves, the FREE charger with the cheapest
directed-A* route, else the cheapest FREE holding cell, else it records
SATURATION and takes the nearest free Stage Zone; it then drives the
costed route by the production router. Simultaneous requesters are
paired greedily on (route cost, zone_id, robot_id). Slots are FREE /
RESERVED / OCCUPIED / VACATING (`simulation_env/charging.py`) and are
offered only while FREE. Charger trips are recovery moves, like
parking: order missions keep right of way over them. No charging: the
robot stays on its terminal, never becomes AVAILABLE again in B3, and
its SOC keeps draining at the idle rate.

```text
nearest charger chosen by directed A*; Manhattan disagrees,   MET
  does not decide
simultaneous requests -> distinct chargers; reserved twice    MET  ResourceError
robot drives the costed route onto its reserved charger        MET  realized == planned
no terminal as intermediate transit, no terminal->terminal    MET
arrival: RESERVED -> OCCUPIED at the arrival instant           MET
all chargers OCCUPIED -> holding; holding reservations unique  MET
no REQUEST robot ever left standing on a transit cell          MET  REQUEST -> departure 0 s
no opposite-edge swap on a charger / holding access edge      MET  0 exits, 1 entry each, by holder
occupied slot stays unavailable to the end of the run          MET
saturation -> nearest free Stage Zone (holding cut to 2)       MET  1 SATURATION event
order ownership untouched; deterministic repeat               MET
SOC never rises; on a charger it drains exactly 1 % / 300 s    MET
controlled runs (check_charger_evacuation.py), 100 orders each
  four   (4 at 9%)     100/100, 4 on chargers,          314.6667 s, 0 yields
  twelve (12 at 9%)    100/100, 8 chargers + 4 holding, 496.6667 s, 0 yields
  late   (8 at 9%, one 14% robot turns CHARGE_NEEDED at (14,5) at
         t=67.3333 with every charger OCCUPIED -> holding (14,2))
                       100/100, 390.0000 s, 0 yields
  saturated (10 at 9%, holding cut to 2; a 14% robot asks at (14,4)
         -> Stage Zone staging_015)   100/100, 460.6667 s, 0 yields
  b2_block (50/20/12/10/9/14 %, deadlocked at t=391 in B2)
                       100/100, 5 on chargers, 304.0000 s, 0 yields
100 and 500 orders vs B2, incl. every SOC row                  MET  identical
  0 requests, 0 reservations, 0 yields
```

B3 visual acceptance in Gazebo, 2026-10-06 (user), with
`warehouse_sim_b3_twelve_low.yaml` and `orders:=true`. The Gazebo run
reproduced the headless run of the same config cell for cell: the same
charger and holding cell per robot, the same reservation and arrival
instants, the same arrival SOC.

```text
12 CHARGE_DUE at t=0, all while idle; 0 CHARGE_NEEDED
reserved and departed at t=0.0000, max REQUEST -> departure 0.0000 s
0 REQUEST robots left standing on a transit cell
8 chargers OCCUPIED, 4 holding OCCUPIED, 0 Stage Zone fallback
100/100 orders, 10/22 parked, 496.6667 s, 0 reservation violations
0 saturation events, 0 yield events
final SOC min 5.0989 mean 34.5432 max 71.5589; below 0 % none
SOC identity exact; cross-check vs engine counters <= 2.842e-14 %
allocator pairs 196 evaluated, 0 ineligible; lowest margin 56.6378 %
```

The lowest SOC is the expected B3 consequence, not a fault:
`robot_005` reached `charging_008` at 6.6367 % at t=35.3333 and then
idled on it to the end of the run, 461.3334 s at 1 % / 300 s =
1.5378 %, leaving 5.0989 %. Nothing recharges it until B4.

B3 traffic-safety argument (the two B2 blockers):

```text
1. equal-right-of-way yield livelock -- not triggered by charging paths
   A yield needs a head-on: two robots requesting opposite directions
   on one edge (a swap). Ordinary transit edges are one-way, so a swap
   can only happen on a two-way terminal access edge, with one robot
   entering terminal T while another leaves T.
   - A charging-workflow robot's leg ends at its own reserved terminal,
     and every other terminal is blocked in its navigation grid. So
     each of its moves is a one-way transit edge or its final entry
     into its own terminal. It never enters a shelf, dropoff or Stage
     Zone (other than a claimed fallback), so it can never be the
     entering half of a swap anywhere else. Its only terminal exit is
     its first move off the Stage Zone it spawned on, and nobody can be
     entering that cell: a Stage Zone somebody stands on is never
     claimed.
   - Only the reservation holder may enter a charger or holding cell,
     and the slot is not offered again until the robot leaving it has
     cleared the access edge (VACATING -> FREE on clearance). In B3
     nothing ever leaves a charger or holding cell at all.
   - A fallback Stage Zone is claimed only while nobody stands on it,
     and no robot enters a Stage Zone without a claim.
   Hence no swap on any charging path; measured 0 yields in all five
   controlled runs and both standard runs. The generic traffic / yield
   controller is unchanged. The livelock itself remains a known issue
   for head-ons elsewhere (not reachable on the production map today);
   B4 must keep VACATING until the access edge is cleared.

2. a REQUEST robot holding still in an aisle -- resolved
   REQUEST now reserves and departs at the same instant; the B2
   deadlock fleet completes 100/100.
```

B4 acceptance, measured 2026-10-06. A robot that reaches its reserved
charger now charges to exactly 100%, reverses into the aisle cell it
came from, and rejoins the fleet. The charge is a timed dwell on the
engine's own Fraction clock, booked as its own ledger category:

```text
duration = (100 - SOC_arrival) / charging_percent_per_second
SOC(t+dt) = min(100, SOC(t) + charging_percent_per_second * dt)
```

The configured rate is the NET gain -- no idle drain is subtracted over
a charge interval (docs/DECISIONS.md, "Charging Rate"). The slot goes
OCCUPIED -> VACATING before the robot moves and becomes FREE only once
it physically stands on the access aisle cell, so an entering and a
leaving robot can never meet on that edge. Holding cells are unchanged:
promotion is B5.

```text
71% at 1%/s charges for exactly 29 simulated s          MET  exact Fraction
every session lands on exactly 100%                     MET  all scenarios
60 s of charging adds exactly 60%, no idle subtracted   MET
charging time excluded from the idle remainder          MET
conservation: final = initial - travel - service
  - waiting - idle + charge                             MET  exact
SOC clamped at 100%, never above                        MET
live mid-charge SOC read rises and is exact             MET  ledger-read tests
minimum SOC latched before the charge raises it         MET
cross-check vs the engine's float counters              MET  <= 1.4e-14 %
no order dispatched between request and rejoin          MET
slot OCCUPIED for the whole charge, VACATING while
  reversing out, FREE only after clearance              MET  sampled per instant
two robots never overlap on one charger cell            MET
a freed charger is reused only after full clearance     MET  recycle scenario
a charged robot rejoins and completes further orders    MET  single scenario
holding robots are NOT promoted (B5 scope guard)        MET
deterministic repeat                                    MET

unit + integration tests                                189 passed (179 before correction)
ruff                                                    clean
map topology / highways / nav graph                     OK
traffic behaviour suite                                 7/7
check_battery_eligibility.py                            OK
check_charger_evacuation.py                             5/5
check_b4_charging.py                                    6/6
  single / four / twelve / recycle / contested / slow

100-order scientific run vs the B0-B3 checkpoint        identical
  100/100, 22/22 parked, 282.0000 s, 6430.0 m,
  waiting 264.6667 s, 0 violations,
  final SOC min 80.7222 mean 84.7322 max 86.6256,
  0 charge sessions
500-order original B4 result (not rerun for correction)  identical
  500/500, 22/22 parked, 1123.3333 s, 29338.0 m,
  waiting 1242.6667 s, 0 violations,
  final SOC min 27.8989 mean 30.7151 max 33.6000,
  0 charge sessions
```

Controlled B4 runs on the V2 map, 22 robots unless stated:

```text
single    1 robot at 9%, 6 orders
          6/6, 1 session, 1 rejoin, 441.7667 s, 0 violations
four      4 at 9%, 40 orders
          40/40, 4 sessions, 4 rejoins, 167.3333 s, 0 violations
twelve    12 at 9%, 100 orders
          100/100, 8 sessions, 8 rejoins, 4 still in holding,
          375.2633 s, 0 violations, 0 yields, 0 saturation
          (the same fleet settled at 496.6667 s in B3, where nothing
           could charge or come back)
recycle   A at 9%, B at 18%, 4 robots, 12 orders, 1 usable charger
          12/12, 2 sessions, 2 rejoins, 272.3367 s, 0 violations
          both charge on charging_001; B requests after A clears
contested 12 at 9%, 100 orders, departures merging into order traffic
          100/100, 8 sessions, 8 rejoins, 375.2633 s, 0 violations
slow      1 robot at 9%, 6 orders, charge rate 0.1%/s
          arrival t=28, SOC 6.9%; 931 s charge, full t=959
          rejoin t=959.6667, SOC 99.95%; all 6 orders complete
          fleet-end makespan 1279.6667 s, 0 violations
```

B4 correction: the 600-second mission watchdog previously rejected
that valid slow charge at t=959. It now excludes only elapsed intervals
where all in-flight work consists of CHARGING robots whose exact finite
completion events remain queued. Existing waiting/unfinished traffic
disables that exemption; elapsed no-progress debt before and after a
charge is preserved, and the event budget still detects zero-time loops.
Missing completion events cannot exempt a session, and a drained queue
with an open charge session raises an error.

The reuse regression observes A clear charging_001 at t=123.2000, then
B reserve it at t=140.6667 and physically occupy it at t=165.3333. Clearance tests
inspect both the physical move and zero-delay mission-completion batches;
`_rejoin()` verifies the expected access cell and completed idle exit
before freeing the slot. Post-rejoin assignment is non-empty and the
later order completes. A controlled six-order run also completes two
charge/rejoin sessions for the same robot without stale reservations.

Live charging SOC tests validate rational ledger reads, not live 2 Hz
ROS publication. During synchronous execution FleetNode publishes via
engine ticks; its periodic timer is created after the workload returns.
Gazebo visual acceptance PASSED on 2026-10-06, verified and approved by
the user with `warehouse_sim_b3_twelve_low.yaml` and `orders:=true`:

```text
12 low-SOC robots entered the charging workflow
8 reached chargers, charged, physically reversed out and rejoined
rejoined robots resumed warehouse work
4 remained in holding until the end (promotion is B5)
100/100 orders completed; 8 charge sessions; 8 rejoins
all 8 charger slots FREE at the end
0 reservation violations; 0 yield events
SOC identity exact; gain matched the configured 1%/s net rate
no visible collision or opposite-edge swap
no completed charging robot remained on a charger
```

B4 is COMPLETE, VERIFIED, APPROVED, and committed as `5d61c84`
("feat: complete Battery V1 B4 charging and fleet rejoin", 2026-10-06).
B5 is implemented below and awaits approval; B6 remains NOT STARTED.

Worked example from the `twelve` run, exactly as reported:

```text
robot_011 charging_005 start t=21.3333 SOC 7.4000%  92.6000 s
          full t=113.9333  +92.6000%  rejoined t=114.6000 SOC 99.9500%
```

7.4000 + 92.6000 = 100 exactly. It rejoins at 99.9500% because
reversing one cell off the charger is real driving and costs one move's
travel energy (1 m at 1% / 20 m = 0.05%). Fleet total: 743.6344% gained
over 743.6344 simulated seconds, i.e. exactly 1%/s net.

Deliberately NOT in B4, and verified absent: holding-cell promotion and
preemption (B5), the `order_makespan` / `fleet_recovery_time` split and
battery metrics (B6), G6 end-of-work partial charging (B6). The
eligibility equation, order schema, strategy set, FIFO window, traffic
and lane policy are untouched.

B3/B4 test supersession, for the record. Nine tests written in B3
asserted that charging does not exist. They were retargeted, not
deleted, because B4 makes the asserted behaviour wrong:

```text
test_no_soc_increase_exists_yet        -> test_soc_rises_only_while_charging
                                          plus two new B4 invariants
test_robot_physically_reaches_the_reserved_charger
                                       -> asserts arrival from the record
                                          and the sampled history, since
                                          the robot no longer ends there
test_charger_arrival_makes_the_resource_occupied
                                       -> OCCUPIED during the charge,
                                          then VACATING, then FREE
test_holding_cells_receive_unique_reservations
                                       -> counts charger trips, not slots
                                          still occupied at the end
test_no_request_robot_remains_stopped_on_a_transit_cell
                                       -> a REJOINED robot in an aisle is
                                          legal; CHARGING must be on a
                                          terminal
test_terminal_access_cannot_create_an_opposite_edge_swap
                                       -> an exit is legal only by the
                                          slot's own holder, and never at
                                          the same instant as an entry
test_resource_remains_unavailable_while_occupied
                                       -> unavailable through the charge
                                          AND the vacating move
test_charging_scheduler_never_modifies_order_ownership
                                       -> no dispatch between request and
                                          rejoin, rather than none ever
test_busy_robot_crossing_ten_percent_finishes_its_order
                                       -> scoped to the charge request;
                                          the robot may work again after
```

B5 acceptance, measured 2026-10-11. A holding cell is now a waiting
room, not a destination. Two policies decide who gets a charger, both
frozen in docs/DECISIONS.md:

```text
promotion   when a charger is FREE, the best waiting robot is promoted
            onto it: lowest SOC, then earliest charge request, then
            robot_id as the final engineering tie-break. One idempotent
            pass per dispatch, so it fires both when a charger is
            released and when a waiting robot arrives in holding.
preemption  when every charger is occupied and a CHARGE_DUE robot needs
            one, the highest-SOC charging robot gives it up, and only
            if its SOC is strictly above the requester's. A
            CHARGE_NEEDED robot never preempts. victim_soc_ge_80 is
            recorded, never branched on.
```

A promoted robot leaves holding exactly as a full robot leaves a
charger: the slot goes OCCUPIED -> VACATING before it moves and is
released only once the robot physically stands on its access aisle
cell. It then drives to the charger by the production router, from that
aisle cell -- a holding cell is non-transit, so no route starts on one.
The holding trip and the charger trip are separate `ChargeRecord`s
carrying the same original request, so every record still describes
exactly one terminal.

```text
12 requesters, 8 chargers, 14 holding: all 4 waiters promoted  MET
  12 charge sessions, 12 rejoins, every session exactly 100%
charger occupancy never above 8; one robot per cell             MET
no robot ever holds two charger or two holding reservations     MET
no robot waits in holding beside a free unpromised charger      MET
promotion goes to the lowest-SOC waiter of that instant         MET
promotion in the same instant the charger frees, and in the
  same instant a waiter arrives next to a free one              MET
CHARGE_NEEDED robot (14%) waits and is promoted, never preempts MET
preempted session books only the interval it charged            MET  exact
  gained = elapsed * rate; charge_completed_at stays None
preempted robot fit for work rejoins the fleet and works again  MET
preempted robot at or below CHARGE_DUE waits in holding         MET
the charger freed by a preemption is held for its requester     MET
no promise outstanding at the end of a run                      MET
100-order default baseline vs B4                                MET  identical
  100/100, 22/22 parked, makespan 282.0000 s, 6430.0 m,
  waiting 264.6667 s, 0 violations
unit tests                                                      MET  207 passed
check_charger_evacuation.py                                     MET  5/5
check_b4_charging.py                                            MET  6/6
check_b5_queue.py                                               MET  5/5
lint                                                            MET  clean
visual inspection in Gazebo                                     MET  (user, 2026-10-11)
```

Gazebo visual acceptance PASSED 2026-10-11, verified by the user with
`warehouse_sim_b3_twelve_low.yaml` and `orders:=true`. The ROS run
reproduces the scientific engine exactly -- same makespan, same
distance, same promotion instants:

```text
12 low-SOC robots entered the charging workflow
8 reached chargers, charged, reversed out and rejoined
4 waited in holding and were PROMOTED onto the chargers the first
  eight vacated, each in the instant that charger became FREE:
    t=114.6000  robot_001  charge_queue_002 -> charging_005
    t=116.7500  robot_002  charge_queue_001 -> charging_001
    t=118.1833  robot_003  charge_queue_010 -> charging_006
    t=120.3333  robot_004  charge_queue_008 -> charging_002
all 12 charged to exactly 100% and rejoined; 12 sessions, 12 rejoins
100/100 orders completed; 22/22 parked off spawn (B4 run: 18/22)
holding bank 14/14 FREE at the end (B4 run: 4 OCCUPIED for good)
makespan 357.1378 s, 6850.0 m, 0 reservation violations, 0 yields
0 preemptions: with 8 chargers free at t=0 nothing had to be taken
```

Measured side effect, in the fleet's favour: the makespan fell from
375.2633 s to 357.1378 s against the same scenario under B4, because
four robots that used to sit in the queue bank for the whole run
rejoined the fleet and took orders. Total distance rose 6732 -> 6850 m
for the same reason.

The B3 invariant line in the `CHARGING` report is now measured over
un-promoted trips only. A promoted robot departs when its holding cell
is clear, not when it first requested, and it waited on a terminal
rather than in an aisle, so counting it there read as a 121 s aisle
wait that never happened. Its wait is reported separately as
`request -> charger`, which is a B5 outcome, not an invariant.

Controlled B5 runs, 22 robots unless stated:

```text
twelve        12 at 9%, 40 orders   4 promotions, 0 preemptions
late          8 at 9% + 1 at 14%    1 promotion,  0 preemptions
preempt       2 robots, 1 charger   1 preemption, victim rejoined
              robot_002 at 11.5%, reserve 0, charge 1%/s
preempt_wait  the same at 0.1%/s    1 preemption, victim to holding
contested     12 at 9%, 100 orders  4 promotions, 0 preemptions
```

The `preempt` worked example, exactly as reported:

```text
t=29.3333  robot_001 starts charging on charging_001 at 6.8000%
t=56.0000  robot_002 requests CHARGE_DUE at 7.7667% after its order;
           every charger OCCUPIED -> holding charge_queue_006, and it
           preempts robot_001 at 33.4667% (> 7.7667%)
           robot_001 gained 26.6667% in 26.6667 s, i.e. exactly 1%/s
t=56.6667  robot_001 clear of the charger at 33.4167%, fit for work,
           rejoins the fleet
t=74.0000  robot_002 reaches holding and is promoted onto charging_001
           in that same instant
t=179.0500 robot_002 full; rejoins at t=179.7167
```

Two defects were found and fixed while measuring B5, both in the
interaction between preemption and the rules it leans on:

```text
1. the victim re-took the charger it had just given up
   It ends its retreat one cell from the charger with the lowest SOC in
   the bank, so the queue discipline handed it straight back and the
   requester waited out a whole further charge. Fixed by assigning the
   charger to its requester at the instant of preemption; see
   docs/DECISIONS.md, "Preemption Hands the Charger Over".
2. a victim at or below CHARGE_DUE was declared fit for work
   It rejoined the fleet and the B2 gate pulled it out again on the
   same tick. Fixed: the rejoin test now requires SOC above CHARGE_DUE
   as well as an eligible order.
```

Deliberately NOT in B5, and verified absent: the `order_makespan` /
`fleet_recovery_time` split and battery metrics (B6), G6 end-of-work
partial charging (B6), promotion out of a Stage Zone saturation
fallback, and retargeting a robot already in flight to a holding cell.
The eligibility equation, order schema, strategy set, FIFO window,
routing, traffic and lane policy are untouched, and the default
100-order baseline is bit-identical.

Known interaction, recorded rather than fixed: a robot requesting in
the few seconds between a preemption and the requester's arrival in
holding can still be offered that charger by the B3 rule ("a FREE
charger exists -> reserve the cheapest") if it is not the promised
robot. The promise prevents exactly this, so the window is closed for
the preemption case; for an ordinary release the frozen B3 rule governs
and a fresh requester may take a charger ahead of a waiting robot only
when promotion has already run in that same dispatch and declined it.

B5/B4 test supersession, for the record. Six B4 assertions asserted
that holding robots stay put. They were retargeted, not deleted,
because B5 makes the asserted behaviour wrong:

```text
test_holding_robots_are_not_promoted_in_b4
                                       -> test_a_holding_robot_is_promoted
                                          _onto_a_freed_charger, plus
                                          test_promotion_takes_the_lowest
                                          _soc_waiting_robot
test_all_chargers_occupied_sends_the_robot_to_holding
                                       -> asserts the holding trip as it
                                          was taken, which then ends in
                                          promotion
test_holding_cells_receive_unique_reservations
                                       -> holding cells stay one trip
                                          each; a charger may serve more
                                          than one robot per run
test_no_request_robot_remains_stopped_on_a_transit_cell
                                       -> a promoted robot departs when
                                          its holding cell is clear, not
                                          when it first requested
test_terminal_access_cannot_create_an_opposite_edge_swap
                                       -> a holding cell may be left, by
                                          its own holder only
test_charging_scheduler_never_modifies_order_ownership
                                       -> the no-work window closes at
                                          the end of each trip, since a
                                          holding trip hands the robot to
                                          a charger trip
check_charger_evacuation.py            -> the same three assumptions in
                                          the validator
check_b4_charging.py                   -> a freed charger no longer stays
                                          FREE at the clearance instant;
                                          it must only stop being the
                                          leaving robot's
```

## Definition of Done for Phase B

The current phase is complete when:

```text
1. baseline simulator state is clean and reproducible
2. W20, W30, and W50 maps exist
3. all three maps pass topology and reachability gates
4. their active robot capacities are validated
5. docs reflect final map names and paths
```

After that, move to Battery Scheduler V1.
