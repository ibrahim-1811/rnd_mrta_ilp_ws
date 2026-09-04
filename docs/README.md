# Documentation index

Technical documentation for **rnd_mrta_ilp_ws** — a ROS 2 Jazzy workspace for
grid-aware multi-robot task allocation (MRTA) in a warehouse setting.

Documents **00** and **18** describe the current frozen simulator
(branch `traffic/rmf-integration`, 2026-09-03) and were verified by
executing every command in them.

Documents **01–17** were verified against commit `ec6ecd6` on `main`
(audit date 2026-08-29) and have **not** been re-audited since. Several
claims in them are now stale — see *Known stale claims* below. Where a
claim rests on an executed command, the command and its output are
recorded in [AUDIT_2026_08_29.md](AUDIT_2026_08_29.md).

> **Start here if you just want to run something:**
> [00_RUNBOOK.md](00_RUNBOOK.md) — every command, from a fresh clone to
> robots moving in Gazebo, all of it executed and verified.
> These documents go deeper and are written to be *auditable* — they cite files and
> line numbers, and they state explicitly what is **not** implemented.

## Contents

| Document | What it covers |
|---|---|
| [00_RUNBOOK.md](00_RUNBOOK.md) | **Every command**: build, regenerate artefacts, headless runs, Gazebo, verification gates |
| [01_PROJECT_OVERVIEW.md](01_PROJECT_OVERVIEW.md) | Research goal, scope, what this repo is and is not |
| [02_SYSTEM_ARCHITECTURE.md](02_SYSTEM_ARCHITECTURE.md) | Nodes, topics, services, runtime data flow |
| [03_REPOSITORY_STRUCTURE.md](03_REPOSITORY_STRUCTURE.md) | Directory map, tracked vs generated files |
| [04_ROS2_PACKAGES.md](04_ROS2_PACKAGES.md) | Per-package reference: executables, parameters, interfaces |
| [05_MRTA_ALGORITHMS.md](05_MRTA_ALGORITHMS.md) | All 12 allocation strategies, with measured behaviour |
| [06_OUTCOME_AWARE_ILP.md](06_OUTCOME_AWARE_ILP.md) | Status of the central research contribution |
| [07_GRID_AND_PATH_COST.md](07_GRID_AND_PATH_COST.md) | Grid model, A*, congestion, energy, path cost |
| [08_EXPLAINABILITY.md](08_EXPLAINABILITY.md) | What the system can and cannot explain today |
| [09_BUILD_AND_INSTALL.md](09_BUILD_AND_INSTALL.md) | Dependencies and verified build procedure |
| [10_LAUNCH_GUIDE.md](10_LAUNCH_GUIDE.md) | Copy-pasteable commands from a fresh terminal |
| [11_SIMULATION_GUIDE.md](11_SIMULATION_GUIDE.md) | How the headless simulator behaves |
| [12_EXPERIMENTS.md](12_EXPERIMENTS.md) | What can be varied; what batch support exists |
| [13_METRICS_AND_OUTPUTS.md](13_METRICS_AND_OUTPUTS.md) | Output files, CSV schemas, metric definitions |
| [14_TESTING.md](14_TESTING.md) | Test inventory and coverage gaps |
| [15_TROUBLESHOOTING.md](15_TROUBLESHOOTING.md) | Known failure modes and traps |
| [16_CURRENT_STATUS.md](16_CURRENT_STATUS.md) | Component status table |
| [17_RESEARCH_GAPS.md](17_RESEARCH_GAPS.md) | Prioritised P0/P1/P2 research gaps |
| [18_TRAFFIC_TOPOLOGY.md](18_TRAFFIC_TOPOLOGY.md) | **One-way lane policy**: why, invariants, what it costs |
| [AUDIT_2026_08_29.md](AUDIT_2026_08_29.md) | Full forensic audit report with evidence |

## Non-technical material in this directory

These files predate the audit and are **left untouched**:

- `ICAPS 26 PlanRob Presentation/` — LaTeX sources, theme, and figures for the
  PlanRob @ ICAPS 2026 talk.
- `ICAPS_26_PlanRob_Presentation.pdf`, `ICAPS_26_PlanRob_Presentation_need chanmges.pdf`
- `Learning Interpretable Scheduling Policies for Online Multi-Robot Task Allocation
  via Inductive Logic Programming.pdf` — the accompanying paper.

> **Important:** the results presented in that talk (ILP policy, makespan tables for
> fleet sizes 5/10/15/20) were produced by an **earlier, different workspace**,
> archived here as the git-ignored `ilp_ws.zip`. They are **not reproducible from this
> repository**, which does not yet contain an ILP implementation. See
> [06_OUTCOME_AWARE_ILP.md](06_OUTCOME_AWARE_ILP.md).


## Known stale claims in 01–17

Recorded rather than silently rewritten, so the audit trail stays
honest. These are wrong as of 2026-09-03:

- **"12 allocation strategies."** Only `nearest_astar` is registered in
  `BATCH_STRATEGIES`. The others were removed in commit `7a07208`
  ("cleanup: remove legacy simulator and ROS interfaces"). Affects
  05_MRTA_ALGORITHMS.md and the index line above.
- **MAPF / space-time planning.** Removed at the freeze. `mapf.py`,
  `mapf_planner.py`, `execution_buffer.py`, `fleet_playback.py` and the
  trajectory execution path in `fleet_engine.py` are gone; every run
  reported 0 conflicts, 0 replans, 0 expansions. See
  [00_RUNBOOK.md §8.3](00_RUNBOOK.md).
- **Bidirectional grid movement.** The warehouse core is now fully
  one-way. Anything in 07_GRID_AND_PATH_COST.md that treats travel cost
  as symmetric, or Manhattan distance as a proxy for path cost, no
  longer holds — 90.9% of shelf pairs have `d(a,b) ≠ d(b,a)`. See
  [18_TRAFFIC_TOPOLOGY.md](18_TRAFFIC_TOPOLOGY.md).
- **Metric named "MAPF conflicts."** It was never MAPF; it is
  `reservations.audit()`. Now reported as `reservation violations`.
  Affects 13_METRICS_AND_OUTPUTS.md.
- **Test counts.** 69 tests, not the audited figure: the two test files
  covering the removed MAPF execution path were deleted.
