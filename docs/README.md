# Documentation index

Technical documentation for **rnd_mrta_ilp_ws** — a ROS 2 Jazzy workspace for
grid-aware multi-robot task allocation (MRTA) in a warehouse setting.

All statements in these documents were verified against the source tree at commit
`ec6ecd6` on branch `main` (audit date 2026-08-29). Where a claim rests on an
executed command, the command and its output are recorded in
[AUDIT_2026_08_29.md](AUDIT_2026_08_29.md).

> **Start here if you just want to run something:** the repository root
> [`../README.md`](../README.md) is accurate and is the fastest path to a working run.
> These documents go deeper and are written to be *auditable* — they cite files and
> line numbers, and they state explicitly what is **not** implemented.

## Contents

| Document | What it covers |
|---|---|
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
