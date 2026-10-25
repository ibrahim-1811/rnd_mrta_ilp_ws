# 17 — Research gaps

Gaps are **documented, not implemented** — per the terms of the audit. Each entry states
the gap, the evidence, and why it matters.

## Engineering completeness vs scientific validation completeness

These two dimensions are at very different stages, and conflating them is the main risk
to the project's timeline.

**Engineering completeness — strong.** The workspace builds cleanly, tests pass, and the
full pipeline runs reliably end to end. The simulator is fast, headless, and shares its
world and physics model with the allocator. Twelve strategies sit behind one clean
interface. As a *software artefact* this is in good shape.

**Scientific validation completeness — not yet started.** No claim in the project title
is currently supported by the code: there is no ILP, nothing outcome-aware, and the
"explainable" layer produces traces rather than explanations. The baseline comparison
that would anchor any result does not discriminate between strategies. Timing is
wall-clock. There are no repetitions and no statistics.

The engineering is roughly ready to *host* the research. The research has not begun.

---

## P0 — blocking research validity

Nothing publishable can be produced until these are resolved.

### P0-1. The Outcome-Aware ILP does not exist

**Evidence:** no ILP code, no solver library, no Prolog runtime, no learned rules, no
consumer of the trace CSV. The strategy registry contains 13 classical heuristics.

**Why P0:** it is the thesis contribution. Everything else is infrastructure for it.

**Depends on:** P0-2, P0-3, P0-4 — the ILP cannot be trained on the data currently
produced.

### P0-2. No outcome is ever recorded or fed back

**Evidence:** `/fleet_travel_stats` carries the only realised results
(`travel_dist`, `battery_consumed`); `task_manager._on_travel_done` uses it solely to
decrement `workload` and publish `/end_order`, discarding both values. The trace CSV has
no outcome column — its only label is `is_chosen`, i.e. what the teacher *did*, not how
it *turned out*.

**Why P0:** "outcome-aware" is definitionally unachievable without outcomes. A dataset
with no outcome column cannot train an outcome-aware policy.

### P0-3. Three hard-coded literals disable four cost terms

**Evidence:** `priority = 1` and `deadline = now + 300.0`
(`task_manager.py:155-156`); `workload ≡ 0` structurally, because a robot is a candidate
only while idle. Measured: `workload = 0` and `priority = 1` in **1,396 of 1,396**
logged rows.

**Consequences:** λ5·W and λ6·q are inert; `least_busy ≡ nearest`; `auction ≡ bid ≡
score`; `edf ≡ nearest` with a FIFO queue; the `heuristic`'s load-balancing stage is a
no-op.

**Why P0:** the cost model the proposal specifies is only two-thirds active, and the
baselines are not independent.

### P0-4. Baselines do not discriminate

**Evidence:** 13 strategy names → **5 distinct behaviours** at 4 robots / 10 orders.
Under contention the full spread across all strategies is **0.7 % in travel distance**
and **1.9 % in energy** — with zero repetitions to establish whether that is even
signal.

**Why P0:** a comparison table built on this would report duplicated rows as independent
results and differences smaller than the measurement noise.

**Note:** partly caused by P0-3 (structural) and partly by the scenarios being too easy
(see P1-2).

### P0-5. Timing is wall-clock, not simulated

**Evidence:** the simulator advances via `time.sleep(step_time)` with one thread per
task; `makespan_s` is the wall-clock delta between the first decision and the last
completion. Identical runs gave 3.751 s vs 3.756 s, and logged `congestion`,
`travel_time` and `energy_pct` differ between identical-seed runs.

**Why P0:** `makespan_s` and `throughput_ops` measure machine speed and scheduler
jitter, not warehouse performance. They are not valid scientific quantities, and results
would not reproduce on different hardware. It also means the ILP training data is not
reproducible.

**Fix direction:** a deterministic simulation clock (event-stepped rather than
sleep-based).

### P0-6. No experiment automation, repetition, or statistics

**Evidence:** `run_experiment.sh` runs exactly one configuration and restarts the whole
stack each time. No sweep, no repetition, no aggregation, no significance testing. The
`/reset_simulation` and `/reset_shared_memory` services that would enable fast
in-process repetition exist but are called by nothing.

**Why P0:** single-run numbers with no variance estimate cannot support a claim.

---

## P1 — important

Needed for a credible, defensible result.

### P1-1. The congestion model is not a congestion model

**Evidence:** congestion is the instantaneous occupancy count of robots' *current* cells
only, republished at 1 Hz. No path reservations, no time-indexed occupancy, no collision
avoidance (robots pass through each other), no re-planning during motion, no waiting
cost. Observed values 0–3, dominated by the drop-off spawn cluster.

**Why P1:** "grid-aware" is in the project title, and congestion is the main thing a
grid buys you over Euclidean distance. As implemented, it mostly measures where robots
started.

### P1-2. Obstacles are unusable end to end

**Evidence:** no map files ship (`setup.py` globs a non-existent `maps/`); neither launch
file exposes `grid_map_file`; and `task_manager.py:61` builds its `WorldGrid` with no
obstacles at all — so the allocator would plan through walls the simulator routes
around.

**Why P1:** every measurement in this repository was taken on an empty 31×31 grid. Obstacle
density is a primary experimental axis for a grid-aware claim, and it is currently
unavailable.

### P1-3. Cost weights are unreachable at runtime

**Evidence:** `CostWeights` defaults are hard-coded in `features.py`; `get_strategy`
accepts a `cost_weights` argument but `task_manager.py:63` never passes one.

**Why P1:** a weight-sensitivity study or ablation requires editing source and
rebuilding for every configuration. This blocks the ablation capability the research
needs.

### P1-4. Seven of thirteen standard metrics are missing

**Missing:** per-task completion time, waiting/queue time, deadline violations, robot
utilisation, workload balance, congestion aggregate, path-planning time. Also absent:
per-robot breakdowns, cost decomposition, and any run-configuration manifest.

**Why P1:** makespan and mean distance alone cannot support claims about balance,
fairness, timeliness, or congestion.

### P1-5. Explainability is traceability

**Evidence:** no cost decomposition anywhere (`task_cost` returns a scalar; the six terms
are never stored); no generated contrastive statement; 7 of 12 strategies emit only
`"<name>: selected robot N"`; `symbolic.py` has no caller; busy robots never appear in
the trace, so "why not R1?" is unanswerable when the answer is "R1 was busy".

**Why P1:** "explainable" is in the title. The trace is a good substrate, but the system
does not currently produce explanations.

### P1-6. No node or integration tests

**Evidence:** 0 tests in `simulation_env` and `shared_memory_node`; none for
`features.py`, `task_manager.py`, or any logger. All 43 tests exercise pure functions.

**Why P1:** the test suite passes while three strategies have silently collapsed into
one — because the strategy tests use synthetic candidates with `workload > 0`, a state
the real pipeline cannot produce. Tests that cannot detect that are not protecting the
research.

### P1-7. Latent division defect in the auction bid

**Evidence:** `_bid` computes `1.0 / (1.0 + C_ij)` where `C_ij` includes `− λ6·q_j`.
With `q_j ≡ 1` the denominator stays non-negative, so it is currently unreachable — but
the moment priorities vary (which P0-3 requires), `q_j ≥ 2` drives the denominator to
zero or negative, raising `ZeroDivisionError` or producing negative bids that the
`> 0.0` guard silently discards as infeasible.

**Why P1:** it will fire on the first experiment that varies priority — i.e. immediately
after P0-3 is addressed.

---

## P2 — desirable

### P2-1. `README.md` is untracked
The project's most accurate documentation is not under version control and would be
absent from a fresh clone. One `git add` fixes it.

### P2-2. Duplicate `/get_robot_fleet_status` service
Advertised by both `shared_memory_node` and `fleet_manager`. Harmless today (no client),
a trap tomorrow.

### P2-3. Dead code and unused declarations
`battery_drain_per_m` (read, never used); `Candidate.round_trip` (computed, never read);
`extract_path_features` (a second, inferior feature path used only by tests);
`ProcessOrder.action`, `TaskList.action`, `GoalStartMsg`, `GoalReachMsg` (no
implementer); empty `configs/` and `data/*` directories.

### P2-4. `symbolic.py` unit mismatch
`PredicateThresholds` uses battery as a 0–1 fraction while the live system uses 0–100
percent. Wiring it to real data unconverted would classify every robot as
`battery_high`. Tests pass because their fixtures are 0–1 throughout.

### P2-5. No visualisation
No RViz config, no plotting script, no trajectory rendering. The figures in the ICAPS
presentation came from the previous workspace.

### P2-6. Simulation fidelity gaps
Zero pickup/drop service time (task duration is pure travel, which makes distance
minimisation trivially optimal for makespan); constant 20 kg payload on the empty
outbound leg; `RobotStatus.FAILED` never set; shelf capacity never constrains anything;
robot start positions not randomisable.

### P2-7. `run_experiment.sh` robustness
Does not forward the allocator seed; exits 0 after a 90 s timeout so a partial run looks
successful; writes no run manifest.

---

## Suggested sequencing

The dependency structure matters more than the raw priority order:

1. **Make the features real** (P0-3) — variable priority, variable deadlines, and a
   design decision on workload (per-robot task queueing). Everything downstream depends
   on this, and it is the smallest change with the largest effect: it revives four dead
   cost terms and un-collapses the baselines. Fix P1-7 at the same time, since it fires
   the moment priorities vary.
2. **Make time deterministic** (P0-5) — a simulation clock. Without it no timing result
   and no training set is reproducible.
3. **Record outcomes** (P0-2) — join realised results back onto decision rows. This is
   the prerequisite for anything "outcome-aware".
4. **Build the experiment harness** (P0-6) + metrics (P1-4) + weight exposure (P1-3) +
   obstacles (P1-2). Now the baselines can be compared properly (P0-4).
5. **Then build the ILP layer** (P0-1) on data that is reproducible, discriminating, and
   outcome-labelled.
6. **Then the explanation layer** (P1-5), wiring `symbolic.py` in and adding cost
   decomposition.

Attempting step 5 before steps 1–3 would train a learner on a non-reproducible dataset
with two constant feature columns, half of whose decisions have no alternative to
contrast against.
