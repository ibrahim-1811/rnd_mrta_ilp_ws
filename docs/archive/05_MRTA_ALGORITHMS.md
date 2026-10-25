# 05 — MRTA algorithms

Source: `src/task_management/task_management/allocation_strategy.py` (265 lines) and
`src/task_management/task_management/features.py` (117 lines).

## Registry

12 classes are registered via the `@register` decorator, plus one alias
(`greedy` → `HeuristicAllocator`), giving **13 usable names**. The registry is the
authoritative list and the node prints it on an unknown name.

All strategies share one interface (`allocation_strategy.py:42-59`):

```python
select(candidates: list[Candidate]) -> int | None   # robot id, or None to defer
explain(candidates, chosen) -> str                  # human-readable reason
```

## What every strategy receives

A `Candidate` per **currently idle, not-yet-assigned-this-tick** robot
(`features.py:29-44`), built by `build_candidates` (`features.py:71`):

| Field | Symbol | Definition |
|---|---|---|
| `distance` | `D_ij` | A\* path length robot→pickup, × resolution (metres) |
| `travel_time` | `T_ij` | physics trapezoidal time for the robot→pickup leg (s) |
| `round_trip` | — | metres, robot→pickup→drop (**computed but used by no strategy**) |
| `proc_time` | — | physics time for the full robot→pickup→drop path (s) |
| `congestion` | `G_ij` | sum of congestion values along both legs |
| `energy_pct` | `E_ij` | physics energy for the full path, % of battery |
| `battery` | `b_i` | robot charge (%) |
| `workload` | `W_i` | in-flight task count |
| `cumulative_load` | — | manager-tracked cumulative `proc_time` (s) |
| `battery_feasible` | — | `b_i − E_ij ≥ b_min` |
| `priority` | `q_j` | task priority |

Every quantity comes from the same `WorldGrid` + `RobotModel` the simulator uses, so
predicted cost and executed cost agree. This is a genuine strength of the design.

## The composite cost

`task_cost` (`features.py:59-68`) implements exactly:

```
C_ij = λ1·T_ij + λ2·D_ij + λ3·G_ij + λ4·E_ij + λ5·W_i − λ6·q_j
```

Default weights (`CostWeights`, `features.py:46-56`), hard-coded — **not exposed as ROS
parameters and not settable from any launch file**:

| Weight | Symbol | Default | Unit |
|---|---|---:|---|
| `time` | λ1 | 0.05 | per second |
| `distance` | λ2 | 0.03 | per metre |
| `congestion` | λ3 | 1.00 | per congestion unit |
| `energy` | λ4 | 0.10 | per percent |
| `workload` | λ5 | 1.00 | per in-flight task |
| `priority` | λ6 | 1.00 | per priority level (subtracted) |

## Strategy reference

| Name | Family | Selection rule (verified from source) | Tie-break |
|---|---|---|---|
| `random` | baseline | `Random(seed).choice` over reachable | RNG |
| `round_robin` | baseline | next reachable id `>` last chosen, else lowest | by id |
| `nearest` | distance | `argmin (D, id)` | robot id |
| `least_busy` | load | `argmin (W, D, id)` | distance, id |
| `highest_battery` | energy | `argmin (−b, D, id)` | distance, id |
| `spt` | time | `argmin (proc_time, id)` | robot id |
| `lpt` | load | `argmin (cumulative_load, D, id)` | distance, id |
| `edf` | deadline | queue sorted by deadline in the manager; within a task `argmin (D, id)` | robot id |
| `heuristic` / `greedy` | lexicographic | filter `battery_feasible`, then `argmin (W, D, −b, id)` | id |
| `score` | cost | `argmin (C_ij, id)` | robot id |
| `bid` | market | `argmax 1/(1+C_ij)` with λ5=λ6=0; infeasible bids 0 | `−id` |
| `auction` | market | `argmax 1/(1+C_ij)` with full weights; infeasible bids 0 | `−id` |

Feature usage per strategy:

| Strategy | Grid dist | Time | Energy | Congestion | Deadline | Battery | Workload |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| `random` | – | – | – | – | – | – | – |
| `round_robin` | – | – | – | – | – | – | – |
| `nearest` | ✅ | – | – | indirect¹ | – | – | – |
| `least_busy` | ✅ | – | – | indirect¹ | – | – | ✅ |
| `highest_battery` | ✅ | – | – | indirect¹ | – | ✅ | – |
| `spt` | ✅ | ✅ | – | indirect¹ | – | – | – |
| `lpt` | ✅ | ✅² | – | indirect¹ | – | – | ✅² |
| `edf` | ✅ | – | – | indirect¹ | ✅³ | – | – |
| `heuristic`/`greedy` | ✅ | – | ✅⁴ | indirect¹ | – | ✅ | ✅ |
| `score` | ✅ | ✅ | ✅ | ✅ | – | – | ✅ |
| `bid` | ✅ | ✅ | ✅ | ✅ | – | ✅⁴ | – |
| `auction` | ✅ | ✅ | ✅ | ✅ | – | ✅⁴ | ✅ |

¹ Congestion changes the A\* path, so it perturbs `distance` even where the strategy
does not read `congestion` directly.
² via `cumulative_load`, which accumulates `proc_time`.
³ via queue ordering only — see the deadline finding below.
⁴ through the `battery_feasible` gate, not as a continuous term.

**All 12 are genuinely implemented, unit-tested (16 tests), and invoked by the live
pipeline.** Each was executed end to end during the audit.

## ⚠ Three hard-coded values silently disable four cost terms

This is the most important algorithmic finding in the audit. It was verified both by
reading the source and by measuring 1,396 logged candidate rows across 25 runs.

### 1. `workload` is *always* 0 — structurally, not incidentally

A robot becomes a candidate only if `r.is_available` is true (`task_manager.py:182`).
`_assign` immediately sets `workload = 1` **and** the simulator sets
`is_available = False`. The counter returns to 0 in `_on_travel_done` at the same moment
the robot becomes available again.

So a robot is never simultaneously *available* and *carrying work*: **`W_i ≡ 0` for
every candidate, always.**

Measured: `workload = 0` in **1,396 of 1,396** logged rows, across every strategy and
fleet size tested.

Consequences:

- The `λ5·W_i` term contributes **nothing** to `C_ij`.
- `least_busy` degenerates to `nearest` — its key `(W, D, id)` becomes `(0, D, id)`.
- `heuristic`'s "least workload" stage is a no-op; it reduces to
  battery-feasible → nearest → most battery.
- `auction` and `bid` differ *only* by the λ5 and λ6 terms — see below.

### 2. `priority` is hard-coded to 1

`task_manager.py:155` sets `'priority': 1` for every task. `q_j` is therefore a
constant, so `−λ6·q_j` is a **constant offset** applied equally to all candidates and
cannot change any `argmin`.

Measured: `priority = 1` in every logged row.

### 3. `deadline` is hard-coded to `now + 300.0`

`task_manager.py:156`. Since every task is released with the same 300 s horizon,
sorting the queue by deadline (`task_manager.py:186-187`) is equivalent to sorting by
release time — and Python's sort is stable, so **`edf` produces exactly FIFO order**.

`edf` is currently `nearest` with a FIFO queue. There is no deadline pressure, no
deadline variance, and no deadline-violation metric anywhere in the system.

### Consequence: `auction ≡ bid`, provably

With `W_i ≡ 0` and `q_j ≡ 1`:

```
C_auction = λ1T + λ2D + λ3G + λ4E + 1.0·0 − 1.0·1 = C_base − 1
C_bid     = λ1T + λ2D + λ3G + λ4E + 0.0·0 − 0.0·1 = C_base
```

Both strategies maximise `1/(1+C)`, which is monotonically decreasing in `C`. Since the
two costs differ by a constant, **the argmax is the same robot**. And because
`C_auction = C_score` exactly (same weights), `score`, `bid` and `auction` all select
identically whenever every candidate is battery-feasible.

## Measured behavioural collapse

All 13 names were run end to end (4 robots, 10 orders, seed 42) and clustered by their
task→robot assignment signature:

| Class | Names that behaved **identically** |
|---|---|
| 1 | `nearest` · `least_busy` · `spt` · `edf` · `heuristic` · `greedy` |
| 2 | `score` · `bid` · `auction` |
| 3 | `highest_battery` · `lpt` |
| 4 | `round_robin` |
| 5 | `random` |

**13 names → 5 distinct behaviours.**

Under contention (8 robots, 40 orders) the classes separate somewhat — `heuristic`,
`score`, `auction`, `lpt` and `highest_battery` diverge — but `nearest ≡ least_busy ≡
spt` still collapse, and the *outcome* spread stays tiny:

| Strategy | mean travel (m) | mean energy (%) | deferrals |
|---|---:|---:|---:|
| nearest / least_busy / spt / lpt | 45.375 | 13.719 | 0 |
| heuristic | 45.375 | 13.844 | 1 |
| score | 45.475 | 13.772 | 0 |
| auction | 45.475 | 13.786 | 1 |
| random | 45.525 | 13.835 | 0 |
| round_robin | 45.675 | 13.976 | 0 |
| highest_battery | 45.375 | 13.873 | 0 |

The full spread across *all* strategies is **0.7 % in travel distance and 1.9 % in
energy** — smaller than the run-to-run noise documented in
[12_EXPERIMENTS.md](12_EXPERIMENTS.md). At present the benchmark cannot distinguish
these policies.

Some collapse is scale-dependent and would ease with harder scenarios; the parts caused
by `W_i ≡ 0`, `q_j ≡ 1` and constant deadlines are **structural and will not ease** until
those three literals become real variables.

## Latent defect: division risk in the auction bid

`_AuctionBase._bid` (`allocation_strategy.py:224-227`) computes `1.0 / (1.0 + C_ij)`.
`C_ij` includes `− λ6·q_j`, which is unbounded below as priority grows.

With today's constants (`q_j ≡ 1`, λ6 = 1.0) the denominator is `C_base ≥ 0`, so the
only failure needs `C_base == 0` exactly — currently unreachable. **But the moment task
priorities become variable** (the obvious next step, and required for the research), a
task with `q_j ≥ 2` drives `1 + C_ij` to zero or negative, producing a
`ZeroDivisionError` or silently negative bids that are then discarded by the
`> 0.0` guard as if the robot were infeasible.

This is not currently triggered. It is flagged because it will fire on the first
experiment that varies priority.

## Not implemented

Named in the project proposal and in `allocation_strategy.py`'s own docstring, but
absent from the code:

| Algorithm | Status |
|---|---|
| Hungarian (batch optimal assignment) | **NOT IMPLEMENTED** |
| Genetic Algorithm | **NOT IMPLEMENTED** |
| Manual Prolog policy | **NOT IMPLEMENTED** |
| Single-Teacher ILP | **NOT IMPLEMENTED** |
| Multi-Teacher ILP | **NOT IMPLEMENTED** |
| Outcome-Aware ILP | **NOT IMPLEMENTED** |

No optimisation or ILP solver library (`pulp`, `mip`, `cvxpy`, `ortools`,
`scipy.optimize`, `linear_sum_assignment`) appears anywhere in the tree, and none is
declared in any `package.xml`. See [06_OUTCOME_AWARE_ILP.md](06_OUTCOME_AWARE_ILP.md).

## Also note

- **All strategies are single-task greedy.** Each decision considers one task against
  the currently free robots; there is no batch assignment, no lookahead, and no
  reconsideration of earlier choices.
- **`round_trip` is dead.** `features.py` computes it for every candidate; no strategy
  reads it.
- **Cost weights are unreachable at runtime.** `CostWeights` defaults are baked in;
  `get_strategy` accepts a `cost_weights` argument but `task_manager.py:63` never passes
  one. Tuning λ requires editing `features.py` and rebuilding — a blocker for the
  weight-sensitivity study the research needs.
