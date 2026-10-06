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
                             B4..B6 NOT STARTED
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
B4  charging + leaving + fleet rejoin    NOT STARTED
B5  preemption + holding                 NOT STARTED
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
