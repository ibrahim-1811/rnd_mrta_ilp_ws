# 16 — Current status

Status as of 2026-08-29, branch `main`, commit `ec6ecd6`.

## Status definitions

| Status | Meaning |
|---|---|
| **COMPLETE** | Implemented, verified working, adequately tested |
| **FUNCTIONAL BUT INCOMPLETE** | Works and is verified, but missing capability the research needs |
| **PARTIAL** | Some of the intended capability exists |
| **PLACEHOLDER** | Code or scaffolding exists but does nothing useful |
| **BROKEN** | Present and does not work |
| **NOT IMPLEMENTED** | Does not exist |
| **NOT VERIFIED** | Exists; could not be confirmed |

## Component status

| Component | Status | Evidence | Notes |
|---|---|---|---|
| ROS 2 build | **COMPLETE** | `colcon build` → 5/5 packages, 0 errors, 0 warnings | clean from scratch in 11.5 s |
| Unit tests | **COMPLETE** | `colcon test` → 47 tests, 0 failures | 43 unit + 2 lint |
| Node/integration tests | **NOT IMPLEMENTED** | 0 tests in `simulation_env`, `shared_memory_node`; none for `features.py` or any node | ~60 % of production code untested |
| Interfaces (`robot_interfaces`) | **COMPLETE** | builds; used by all nodes | 2 actions + 2 msgs unused |
| Grid model (`WorldGrid`) | **COMPLETE** | 27 `mrta_core` tests; 31×31 grid confirmed at runtime | silent out-of-bounds clamp is untested |
| A\* planner | **COMPLETE** | unit tests + live use in both simulator and allocator | 4-connected, admissible heuristic, optimal |
| Obstacles | **PARTIAL** | `load_ascii_obstacles` implemented and tested | **no map files ship; not exposed in either launch file; task manager never loads them** — unusable end to end |
| Kinematics / energy model | **COMPLETE** | unit tested; simulator and allocator share it | strongest property in the repo |
| Shared memory node | **FUNCTIONAL BUT INCOMPLETE** | loads 100 shelves; serves all clients in live runs | **0 tests**; duplicate `/get_robot_fleet_status` |
| Headless simulator | **FUNCTIONAL BUT INCOMPLETE** | 25 end-to-end runs; 8 concurrent robots verified | **0 tests**; wall-clock timing; no collisions; no service times |
| Task manager / decision loop | **FUNCTIONAL BUT INCOMPLETE** | verified: assigns, defers, completes, frees robots | **0 tests**; priority and deadline hard-coded |
| Baseline allocators (12) | **FUNCTIONAL BUT INCOMPLETE** | 16 unit tests; all 13 names run end to end | **collapse to 5 distinct behaviours**; λ weights unreachable at runtime |
| Batch allocators (Hungarian, GA) | **NOT IMPLEMENTED** | no solver library anywhere | named in docstrings only |
| ILP | **NOT IMPLEMENTED** | no ILP code, no solver, no Prolog runtime, no rule file | — |
| **Outcome-Aware ILP** | **NOT IMPLEMENTED** | no outcome is ever fed back into any decision | the central research contribution |
| Symbolic layer (`symbolic.py`) | **PLACEHOLDER** | 12 tests pass; **no production caller** | plus a 0–1 vs 0–100 battery unit mismatch |
| Congestion | **PARTIAL** | occupancy published, consumed by A\*, values 0–3 observed | instantaneous occupancy only — no reservations, no time-indexing, no collision avoidance |
| Path cost `α·d + β·obs + γ·cong` | **PARTIAL** | A\* step cost = `1.0 + w·congestion` | no `β` obstacle term (obstacles are a hard constraint); `α` fixed at 1.0 |
| Composite cost `C_ij` | **FUNCTIONAL BUT INCOMPLETE** | `task_cost` implements the 6-term equation | λ5 and λ6 inert (`W ≡ 0`, `q ≡ 1`); all weights hard-coded |
| Explainability | **PARTIAL** | `explain()` strings + 21-column decision trace | no cost decomposition; no contrastive generation; 7 of 12 strategies give no reason |
| Deadlines | **PLACEHOLDER** | `TaskState.deadline` exists; `edf` sorts by it | every deadline is `now + 300.0`, so EDF ≡ FIFO |
| Task priority | **PLACEHOLDER** | `q_j` in the cost equation | hard-coded to 1 in every task |
| Battery / charging | **COMPLETE** | drain, low threshold, charging, resume all observed live | recharge unaffected by `time_scale` |
| End-to-end simulation | **COMPLETE** | 25 runs; 10/10 and 40/40 tasks completed | verified with 3–8 robots |
| Headless execution | **COMPLETE** | no Gazebo, no Nav2, no display | |
| Metrics | **FUNCTIONAL BUT INCOMPLETE** | 9-column CSV written every run | 7 of 13 standard metrics missing; makespan is wall-clock |
| Dataset export | **FUNCTIONAL BUT INCOMPLETE** | 21-column trace, verified | no outcome column; constant `priority`/`workload`; half of decisions single-candidate |
| Single-run automation | **COMPLETE** | `run_experiment.sh`, run 25 times | does not fail on timeout; does not forward the allocator seed |
| Batch experiments | **NOT IMPLEMENTED** | no runner, no sweep, no repetition, no aggregation | `/reset_*` services exist but nothing calls them |
| Statistical analysis | **NOT IMPLEMENTED** | nothing computes a mean over trials | |
| Visualization | **NOT IMPLEMENTED** | no RViz config, no plotting script | figures in `docs/` came from the previous workspace |
| Reproducibility | **PARTIAL** | assignment sequences reproduce exactly | logged **features** do not; makespan is wall-clock |
| Documentation | **FUNCTIONAL** | root `README.md` accurate; `docs/` created by this audit | **`README.md` is untracked** |

## Headline summary

**What works well.** The engineering substrate is genuinely solid. A clean five-package
workspace builds without a single warning, 43 unit tests pass, and the full pipeline —
orders → allocation → A\* → execution → pickup → delivery → CSV — was run 25 times
without a failure. The shared `WorldGrid` + `RobotModel` between simulator and allocator
is a real design achievement: predicted cost and executed cost agree by construction.
Twelve baseline strategies exist behind one clean, switchable interface.

**What is missing.** The research contribution itself. There is no ILP, nothing is
outcome-aware, and the explainability layer stops at numeric traces. Beneath that, three
hard-coded literals (`priority = 1`, `deadline = now + 300`, and the structural
`workload ≡ 0`) silently disable four of the six cost terms and collapse thirteen
strategy names into five behaviours — so the baseline comparison the research depends on
does not currently discriminate. Timing is wall-clock, so makespan is not yet a
scientific quantity, and there is no batch runner, no repetition, and no statistics.

**The gap between these two paragraphs is the project's current position:** a
well-built testbed, with the experiment and the contribution still ahead of it.

See [17_RESEARCH_GAPS.md](17_RESEARCH_GAPS.md) for prioritisation.
