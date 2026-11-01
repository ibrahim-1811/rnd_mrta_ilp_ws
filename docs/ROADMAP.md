# Phasewise Development Roadmap

> **Updated 31 October 2026.** Current measured status: [CURRENT_PHASE.md](CURRENT_PHASE.md). Demo commands: [PROJECT_HANDOVER.md](PROJECT_HANDOVER.md). M0 and M1 are closed; M2 is corrected; M3 validation ran once from `m3-evidence-1`, with mixed gates recorded in CURRENT_PHASE.md; online congestion predicates come next, then M4, M5 and M6. Earlier phase text below is a dated development record, not a new completion claim.

## Milestone plan to 20 December 2026

Set 2026-10-27 after the read-only audit, on the research backend branch
`research/outcome-aware-ilp`. Phases below are the long-standing
structure; these milestones are the working schedule through them.

```text
M0  Oct 27-30   integrity and reproducibility: F5 decision linkage,
                F6 failure classes, F7 quantum/window per experiment,
                F8 strategy identity, seed split       DONE, 163c6a4
M1  Oct 30-Nov 2  measure the outcome signal at 70/100 robots;
                decision-time congestion / route-overlap proposal;
                F9 statistics, F10 outcome attribution, F12 workload
                variant scoped                         diagnostics DONE
                                                       2026-10-28; matrix
                                                       pending
M2  Nov 2-7     ILP data layer: learner-side predicates, run-scoped
                atoms, sampling, bias, offline teacher relabelling
M3  Nov 7-12    Phase 10: reproduce nearest_astar with Popper; rules
                compiled to Python; rule-executing strategy
M4  Nov 12-17   Phase 11: Single-Teacher ILP
M5  Nov 17-22   Phase 12: Multi-Teacher ILP
M6  Nov 22-Dec 2  Phase 13: Outcome-Aware ILP, supervision frozen first
M7  Dec 1-4     Phase 14: explanations
M8  Dec 4-10    Phase 15: final campaign, quantum 3 primary, quantum 0
                secondary
M9  Dec 8-20    Phase 16: report
```

Deferred, not cancelled (2026-10-27): G6 end-of-work partial charging
and the held-out transfer map.

## Phase 0: Research Scope

Status: DONE

Outputs:

```text
research title
scientific boundary
research objectives
grid-aware motivation
outcome-aware target
explainability target
```

## Phase 1: Deterministic Warehouse Simulator

Status: DONE

Outputs:

```text
XLSX map ingestion
grid representation
robot fleet
order lifecycle
simulated clock
shelf service
dropoff service
return-home behavior
workload execution
```

## Phase 2: Directional Grid Traffic

Status: DONE

Outputs:

```text
one-way transit
production directed A*
strong-connectivity validation
traffic arbitration
service retreat protection
structural head-on elimination
```

## Phase 3: Gazebo and RMF Mirror

Status: DONE

Outputs:

```text
Gazebo warehouse world
robot visualization
nav graph generated from production lane rules
nav graph verifier
headless and Gazebo execution consistency
```

## Phase 4: V2 Baseline Freeze

Status: DONE. Baseline committed 578c668; superseded as the frozen
reference by the S0 re-freeze (docs/DECISIONS.md, "Baseline Re-Freeze
Policy"). window=legacy still reproduces these numbers exactly.

Goals:

```text
clean V2 working tree
repair tiny nearest_astar practice-map tests
add openpyxl to project environment
review generated documentation media
re-run full baseline acceptance gate
commit only when explicitly requested
```

Exit gate:

```text
map validation PASS
routing validation PASS
traffic validation PASS
100-order regression PASS
Gazebo/nav graph consistency PASS
repository docs synchronized
```

## Phase 5: Warehouse Scalability Family

Status: W100 DONE, committed 503ca90; the held-out transfer map is a
DEFERRED requirement (2026-10-27). UNBLOCKED 2026-10-23. The blocker was that
the lane policy was hard-coded to the 17 x 35 V2 indices, so no other
map shape could be legal (ARCHITECTURE.md section 12, P0-5). A map can
now carry its own lanes as arrows drawn in Excel (DECISIONS.md, "Drawn
Lane Maps"; docs/MAKE_A_MAP.md), checked by `map_tool.py check` and
`check_map_topology.py`.

Measured on a generated 40 x 54 map with 100 parking cells and no
rotaries: 100 robots complete 1000/1000 orders with 0 reservation
violations, and 10 and 50 robots complete under all three strategies.

**Decided 2026-10-23 by the researcher**, superseding the family below:
one main map, **W100** (100 parking cells), swept from 10 to 100 robots,
plus one held-out map of a different layout for testing whether learned
rules transfer (planned). See DECISIONS.md, "Warehouse Scalability". The
W20 / W30 / W50 plan below is kept for the record only.

Create:

```text
W20
W30
W50
```

Design principle:

```text
preserve scientific semantics
scale physical capacity
avoid artificial overcrowding
```

For each map define and document:

```text
grid dimensions
home capacity
shelf count
dropoff count
charger count
holding count
lane topology
expected robot density
recommended order volumes
```

Each map must pass:

```text
load validation
strong connectivity
0 uncontrolled bidirectional transit edges
terminal reachability
charger reachability
holding reachability
nav graph verification
traffic stress test
```

## Phase 6: Battery Scheduler V1

Status: B0-B5 DONE, approved and committed (578c668, 5d61c84, d6d67c1).
B6a (the order_makespan / fleet_recovery_time split: run metrics
separate order work from end-of-work recovery) DONE 2026-10-26,
approved, committed 5abf5c2. The rest of B6 (decision/outcome battery
metrics) NOT STARTED; G6 end-of-work partial charging is a DEFERRED
requirement (2026-10-27).

Implement:

```text
live SOC
travel drain
service drain
waiting / idle drain
CHARGE_DUE
energy eligibility
charger selection
charger reservation
travel to charger
charging action
charge completion
preemption
holding behavior
availability gating
battery metrics
```

Acceptance scenarios:

```text
idle robot reaches threshold
busy robot reaches threshold
robot finishes order before charging
free charger selected and reserved
reserved charger cannot be double-assigned
robot charges to 100%
robot becomes available after leaving charger
all chargers occupied
highest-SOC charger preemption
preempted robot returns to fleet when eligible
preempted robot uses holding when not eligible
no transit through charging or holding cells
```

Run at least one long workload where charging actually occurs.

## Phase 6b: Candidate Window and Dispatch (S0)

Status: IMPLEMENTED 2026-10-22, committed 503ca90. Decisions in
docs/DECISIONS.md, acceptance in docs/CURRENT_PHASE.md.

A prerequisite for Phase 7, not an optimisation. The FIFO window sizes
the allocator's candidate set at `min(available_robots, pending)`, which
collapses 62 of 71 decisions on the 100-order run to 1 robot x 1 order.
Every strategy agrees on a 1x1 decision, so no strategy comparison is
defensible until this is fixed.

Deliver:

```text
candidate window W = fleet_size, configurable and recorded       DONE
dispatch_quantum, default 0, T = 3.0 s controlled scenario       DONE
FIFO liveness extension when the prefix holds no assignable pair DONE
decision-shape instrumentation: robots x orders per decision     DONE
re-frozen 100 / 500 baselines, plus a window=legacy test         DONE
```

Measured: forced 1x1 decisions fell from 62 of 71 to 1 of 64 on the
100-order workload, and the batched scenario has 0 forced of 33.

Acceptance: the stall case reproduced as a test first, the decision
shape measured rather than assumed, `window=legacy` bit-identical to
the pre-S0 baselines, the full regression gate green, and a Gazebo run
matching the headless numbers.

Nothing in routing, traffic, battery, eligibility or the order schema
changes.

## Phase 7: MRTA Baselines

Status: DONE. Slice 1 committed 503ca90, slice 2 committed aa77ee0;
accepted by the researcher as an implemented foundation 2026-10-27.
Primary condition quantum 3 (DECISIONS.md, "Dispatch quantum
conditions"); measurements in CURRENT_PHASE.md, "Phase 7 slice 2".

Implemented in two slices, so the window, the dataset and the
distinctness gate are exercised end to end before any effort goes into
the optimising strategies.

### Slice 1 — done

```text
random              uniformly random among eligible pairs; the lower bound
nearest_manhattan   the deliberately naive baseline
nearest_astar       the grid-aware baseline (already implemented)
```

All three are the same greedy sweep (`greedy_pairing`) over a different
ordering, so two strategies differ by their ranking and by nothing
else -- not by how they resolve a conflict, not by how many pairs they
emit, not by what they do when one side runs out.

### Slice 2 — done

```text
Earliest Finish
Multi-Criteria Grid-Aware
Auction / Marginal Cost
Hungarian
MILP
```

### Measured, 22 robots / 100 orders / seed 42

| Strategy | Makespan | Distance | Waiting |
|---|---|---|---|
| `nearest_astar` | 268.6667 s | **5798.0 m** | 305.3333 s |
| `nearest_manhattan` | 265.3333 s | 5974.0 m | 325.3333 s |
| `random` | 266.6667 s | 6488.0 m | 258.0000 s |

Distance orders exactly as theory predicts: grid-aware beats naive
beats none, +3.0% and +11.9% against the A* baseline.

**Makespan does not order the same way**, and that is a finding rather
than a defect. Makespan here is the engine's end time, which includes
end-of-run recovery (P0-4), and it is dominated by whichever robot
finishes last after traffic. Distance is where the allocation policy
shows up cleanly; makespan is confounded. This is exactly why the
distinctness gate states that outcome delta alone is not sufficient
evidence.

### Distinctness gate

`scripts/check_strategy_distinctness.py`. Comparing two independent
runs would not measure distinctness: after the first decision where
they differ the two fleets are in different states, so their decision N
is not the same decision. The gate therefore measures a
**counterfactual** -- one strategy drives the run and every rival is
asked the same question at every decision, with the same free robots,
window, positions, SOC and route cache.

Measured, 100 orders, seed 42, against a 5% threshold:

```text
driving with nearest_astar      vs nearest_manhattan   76.2 %
                                vs random              90.5 %
driving with nearest_manhattan  vs nearest_astar       60.3 %
                                vs random              97.1 %
driving with random             vs nearest_astar       93.8 %
                                vs nearest_manhattan   95.3 %
```

Every pair is distinct in both directions, by an order of magnitude
over the threshold.

### Contract every strategy obeys

```text
same candidate window        sized by infrastructure, not by the strategy
same routing                 one score(); C_static is never recomputed
same battery mask            an ineligible pair is not a candidate
same traffic and execution   the strategy never touches the engine
same order workload and seed
deterministic                ties break on (cost, fifo_position, robot_id)
```

The executor re-checks every returned pair independently and raises
rather than execute an ineligible one, so the mask is enforced and not
merely requested.

Stochastic strategies take the seed from `simulation.seed`, overridable
with `--seed`. Candidates are sorted into a canonical order before they
are shuffled, so a seeded run never depends on dict iteration order.

## Phase 8: Decision and Outcome Logging

Status: DONE, committed 503ca90; accepted as an implemented foundation
2026-10-27. The deferred-launch decision_id defect was fixed in M0
(DECISIONS.md, "Decision Linkage").

Two Parquet tables and a manifest per run, written by
`simulation_env/dataset.py`:

```text
decisions.parquet   one row per scored (robot, order) candidate pair
outcomes.parquet    one row per order
run.json            provenance; without it the tables are not evidence
```

Enabled per run, off by default, because recording is observation only:

```bash
python3 src/simulation_env/scripts/run_workload.py \
    --dataset runs/nearest_astar --export
```

Measured on the shipped 22-robot / 100-order workload: 1973 decision
rows over 39 columns, 100 outcome rows over 22 columns, makespan
unchanged at 268.6667 s / 5798.0 m with recording on.

### No-leakage separation

Enforced structurally, not by convention. A decision row is built
before any mission is planned and is never revisited, and the
truncation test proves it: a run aborted immediately after a decision
produces byte-identical decision rows to the same run carried through
to completion (`test/test_dataset.py`).

### Deliberately raw

Every feature is a number in its natural unit. No discretized
predicate (`battery_high` and the like) is stored: where a threshold
goes is a learner-side choice that will be tuned repeatedly, and
freezing one into the dataset would mean re-running every experiment
whenever it moved.

The clock and the SOC ledger are exact rationals and Parquet has no
rational type, so where exactness matters the value is stored twice: a
`float64` column for analysis and an exact `"numerator/denominator"`
string beside it. That is what makes a run reproducible rather than
merely plottable.

### Attribution boundary

`C_static` includes the post-drop-off retreat because the energy
equation requires it, so the outcome row is written at retirement,
after the retreat is driven -- not at the drop-off service. Written at
the drop-off, every order read exactly one cell short of its
prediction and every predicted-vs-realized comparison carried a
constant bias. `completed_at` remains the drop-off instant, which is
when the order is fulfilled; `retreat_finished_at` records the rest.

Per-order realized distance sums to less than the fleet total (5269.0 m
of 5798.0 m). The difference is end-of-run travel to a Stage Zone,
which belongs to no order and is correctly excluded.

### Storage format

Parquet where pyarrow is available, CSV where it is not, with the
manifest recording which. The live ROS node runs on the system
interpreter where pyarrow cannot be assumed, and a finished run must not
be lost to a missing optional dependency. The fallback is lossless: the
exact columns are already strings, and a Parquet run and a CSV run of
the same workload read back equal row for row.

### Both execution paths record the same dataset

The headless runner and the live ROS node produce byte-identical
decision and outcome tables for the same workload, verified with `cmp`.
One scientific engine, one dataset: there is no Gazebo-side recording
path.

### Export

`simulation_env/dataset_export.py` reads a run back out: CSV for
grepping and diffing, and ground Prolog facts (`bk.pl`, `exs.pl`) as
ILP input. It derives nothing -- every exported value is a recorded
value -- and it writes no mode or bias declarations, because the
hypothesis space is the experiment, not the data.

Examples are the selected pair against the *eligible* pairs it was
chosen over. A pair the infrastructure refused is not a negative
example: a learner asked to explain it would be learning to reproduce
the feasibility mask, which is fixed infrastructure.

### Fields in the original plan that are not recorded

Not dropped silently; each is absent for a reason:

```text
estimated travel time        the executor accepts speed_mps but does
                             not use it; derivable downstream from the
                             speed in the manifest
predicted congestion         OrderCandidate carries no congestion
                             feature, and inventing one would be a
                             strategy-visible change to fixed
                             infrastructure
predicted waiting            Dispatch.planned_wait_s is always 0.0; a
                             structurally-zero column only invites
                             someone to read meaning into it
minimum SOC during order     the ledger exposes SOC at launch and at
                             completion, not a per-order minimum
```

See docs/DECISIONS.md, "Decision and Outcome Dataset" through "Run
Identity", for the frozen schema decisions and "Phase 8 Schema Gaps To
Close" for the above.

## Phase 9: Experiment Runner

Status: DONE for paired seeds and E1 integrity (committed 5abf5c2,
aa77ee0); M0 adds per-experiment quantum and window, strategy identity,
classified failures and the seed split. Holm correction and Wilcoxon
sensitivity (F9) are not implemented; spatial workload scenarios (F12)
do not exist yet.

Build reproducible batch execution over:

```text
warehouse map
fleet size
order count
workload scenario
strategy
seed
```

Outputs should be machine-readable and ready for statistical analysis.

## Phase 10: ILP Baseline Integration

Status: PLANNED

Tasks:

```text
integrate Popper environment
bring over required symbolic-learning utilities
repair battery unit consistency
reproduce simple teacher rule on grid-aware warehouse
```

Do not copy old ILP code blindly. Adapt it to the current data model and units.

## Phase 11: Single-Teacher ILP

Status: PLANNED

Learn symbolic rules from one strong teacher strategy.

Evaluate:

```text
allocation quality
rule complexity
coverage
fidelity
runtime
```

## Phase 12: Multi-Teacher ILP

Status: PLANNED

Train from decisions generated by multiple reference strategies.

Evaluate whether rule robustness improves across workloads and maps.

## Phase 13: Outcome-Aware ILP

Status: CORE RESEARCH PHASE

Define outcome-derived supervision using realized execution quality.

Candidate outcome dimensions:

```text
completion time
waiting
distance
energy
charging impact
traffic contention
future fleet availability
```

The final label or weighting design must be frozen before the main experiment campaign.

Compare against:

```text
Nearest Manhattan
Nearest Directed A*
optimization baselines
Single-Teacher ILP
Multi-Teacher ILP
```

## Phase 14: Explanations

Status: PLANNED

For every ILP decision expose:

```text
selected robot
rule used
predicates satisfied
predicates rejected
relevant grid features
battery state
```

Evaluate explanation complexity and usefulness.

## Phase 15: Full Evaluation

Status: PLANNED

Run controlled experiments on W100 across the fleet sweep 10, 30, 50,
70, 100 (the W20 / W30 / W50 family was replaced 2026-10-23), quantum 3
primary and quantum 0 secondary, on the evaluation seeds
(EXPERIMENT_PROTOCOL.md, "Statistical Evaluation Protocol" and "Data
Split Protocol"). The held-out transfer map is deferred.

Vary:

```text
fleet size
order count
spatial demand
traffic intensity
charging pressure
random seed
```

Measure:

```text
performance
scalability
energy
congestion
runtime
robustness
rule complexity
explainability
```

## Phase 16: R&D Report

Status: PLANNED

Final report should include:

```text
problem statement
related work
methodology
system architecture
experimental design
hypotheses
results
statistical analysis
learned rules
explanation examples
limitations
future work
```
