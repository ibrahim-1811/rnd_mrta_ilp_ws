# G2 scenario registry implementation report

## Status

Task 1 is implemented in the isolated `feature/gui-scenario-registry` worktree.
The changes are uncommitted. W100 remains the only tracked registry entry and
the reference/default; the registry is multi-entry-ready and single-run only.
Matrix experiments remain bound to the original W100 runner configuration.

No scientific package, runner, W100 input, research phase document, or owner
custom map was changed. Per the controller's explicit protection instruction,
the owner's dirty `my_map.xlsx` was not read or hashed.

## Implementation

- Added a GUI-owned YAML scenario registry and a backend adapter which resolves
  each declaration independently with `load_sim_config`,
  `load_warehouse_xlsx`, and `check_map`.
- Added stable safe-ID lookup, useful per-entry errors/warnings, provenance,
  dimensions, home capacity, input SHA-256 hashes, and a declaration/content
  identity. Invalid entries remain listed but are refused for map and preview.
- Made single-run plans use the selected config in the existing
  `run_workload.py --config` argument, record the scenario identity/hashes, and
  re-resolve at launch/final validation. Existing preview fingerprint comparison
  rejects byte drift.
- Kept experiment planning explicitly on W100 with no new experiment request or
  CLI field.
- Made CSV upload and preview validation use the selected scenario.
- Added a reference-defaulting frontend selector, invalid diagnostics,
  dimensions/capacity, scenario-specific geometry loading, and clearing of stale
  upload/preview state. The selector is locked during an active job; old job map
  ownership remains independent of current setup selection.
- Updated the user guide with identity/immutability, invalid-map behavior,
  runtime-storage separation, and the future custom-registration proposal.

## Files

Created:

- `src/dashboard_GUI/config/scenarios.yaml`
- `src/dashboard_GUI/backend/control_center/adapters/scenarios.py`
- `src/dashboard_GUI/backend/tests/test_scenarios.py`
- `src/dashboard_GUI/frontend/src/App.scenario.test.tsx`
- `src/dashboard_GUI/docs/control_center/G2_SCENARIO_REGISTRY_REPORT.md`

Modified:

- `src/dashboard_GUI/README.md`
- `src/dashboard_GUI/backend/control_center/adapters/catalogue.py`
- `src/dashboard_GUI/backend/control_center/adapters/commands.py`
- `src/dashboard_GUI/backend/control_center/adapters/inputs.py`
- `src/dashboard_GUI/backend/control_center/api/research.py`
- `src/dashboard_GUI/backend/control_center/api/routes.py`
- `src/dashboard_GUI/backend/control_center/schemas/jobs.py`
- `src/dashboard_GUI/backend/control_center/services/jobs.py`
- `src/dashboard_GUI/frontend/src/App.tsx`
- `src/dashboard_GUI/frontend/src/InputPicker.tsx`
- `src/dashboard_GUI/frontend/src/RunSetup.tsx`
- `src/dashboard_GUI/frontend/src/model.ts`
- `src/dashboard_GUI/frontend/src/types.ts`
- `src/dashboard_GUI/frontend/src/InputPicker.test.tsx`
- `src/dashboard_GUI/frontend/src/RunSetup.test.tsx`
- `src/dashboard_GUI/frontend/src/model.test.ts`

The pre-existing untracked `.pixi` symlink and the provided untracked G2 design
and plan documents were not added or modified by this implementation.

## TDD evidence

The isolated worktree has no dashboard `.venv`, and its locked `.pixi`
environment does not include FastAPI. An isolated temporary dependency target
under `/tmp/g2-dashboard-deps.*` supplied the dashboard-only Python packages;
the repository and locked scientific environment were not mutated. Backend
commands used:

```bash
g2_deps=$(cat /tmp/g2-dashboard-deps.path)
PYTHONPATH="$g2_deps:$PWD/src/dashboard_GUI/backend:$PWD/src/mrta_core:$PWD/src/simulation_env:$PWD/src/task_management" PYTHONDONTWRITEBYTECODE=1 .pixi/envs/default/bin/python -m pytest src/dashboard_GUI/backend/tests/test_scenarios.py src/dashboard_GUI/backend/tests/test_commands.py src/dashboard_GUI/backend/tests/test_backend.py src/dashboard_GUI/backend/tests/test_research.py -q
```

Backend RED, before implementation: `11 failed, 55 passed, 1 warning in 6.41s`.
The failures were missing scenario-registry behavior. Backend GREEN after the
implementation and boundary-test cycle: `67 passed, 1 warning in 8.97s`.

The safe-but-unknown planner boundary had its own red/green cycle:

```bash
g2_deps=$(cat /tmp/g2-dashboard-deps.path)
PYTHONPATH="$g2_deps:$PWD/src/dashboard_GUI/backend:$PWD/src/mrta_core:$PWD/src/simulation_env:$PWD/src/task_management" PYTHONDONTWRITEBYTECODE=1 .pixi/envs/default/bin/python -m pytest src/dashboard_GUI/backend/tests/test_scenarios.py::test_planner_reports_an_unknown_safe_id_as_invalid_input -q
```

RED: `1 failed in 0.17s` with an uncaught `UnknownScenario`. GREEN:
`1 passed in 0.16s`, returning the intended HTTP 422 boundary error.

Frontend RED/GREEN command:

```bash
cd src/dashboard_GUI/frontend
npm test -- --run src/RunSetup.test.tsx src/InputPicker.test.tsx src/model.test.ts src/App.scenario.test.tsx
```

RED, before frontend implementation: `4 failed` files, `5 failed | 8 passed`
tests, duration `1.77s`. Failures identified the missing scenario payload,
selector, state clearing, and geometry ownership. GREEN: `4 passed` files,
`13 passed` tests, duration `975ms`.

## Independent QA findings resolved

QA identified two important boundary defects. Both were reproduced before their
production fixes.

### Per-entry parser/workbook isolation

Malformed YAML regression command:

```bash
g2_deps=$(cat /tmp/g2-dashboard-deps.path)
PYTHONPATH="$g2_deps:$PWD/src/dashboard_GUI/backend:$PWD/src/mrta_core:$PWD/src/simulation_env:$PWD/src/task_management" PYTHONDONTWRITEBYTECODE=1 .pixi/envs/default/bin/python -m pytest src/dashboard_GUI/backend/tests/test_scenarios.py::test_malformed_yaml_entry_does_not_hide_valid_w100 -q
```

RED: `1 failed in 0.35s`; `yaml.parser.ParserError` escaped `scenarios()` and
hid W100. GREEN: `1 passed in 0.35s`; W100 remains valid/usable, the malformed
entry remains listed invalid with the parser diagnostic, and direct loading is
refused as `InvalidScenario`.

Workbook-format boundary regression command:

```bash
g2_deps=$(cat /tmp/g2-dashboard-deps.path)
PYTHONPATH="$g2_deps:$PWD/src/dashboard_GUI/backend:$PWD/src/mrta_core:$PWD/src/simulation_env:$PWD/src/task_management" PYTHONDONTWRITEBYTECODE=1 .pixi/envs/default/bin/python -m pytest src/dashboard_GUI/backend/tests/test_scenarios.py::test_unsupported_workbook_entry_does_not_hide_valid_w100 -q
```

RED: `1 failed in 0.35s`; OpenPyXL `InvalidFileException` escaped the entry.
The combined GREEN command for both isolation regressions was:

```bash
g2_deps=$(cat /tmp/g2-dashboard-deps.path)
PYTHONPATH="$g2_deps:$PWD/src/dashboard_GUI/backend:$PWD/src/mrta_core:$PWD/src/simulation_env:$PWD/src/task_management" PYTHONDONTWRITEBYTECODE=1 .pixi/envs/default/bin/python -m pytest src/dashboard_GUI/backend/tests/test_scenarios.py::test_malformed_yaml_entry_does_not_hide_valid_w100 src/dashboard_GUI/backend/tests/test_scenarios.py::test_unsupported_workbook_entry_does_not_hide_valid_w100 -q
```

Result: `2 passed in 0.40s`. `_evaluate` now catches the explicit data/parser/workbook
families (`yaml.YAMLError`, ZIP corruption, XML parsing, and OpenPyXL invalid
workbooks) in addition to its existing loader errors. It does not catch
`BaseException` or process-control exceptions.

### Selected-job map/provenance coherence

```bash
cd src/dashboard_GUI/frontend
npm test -- --run src/App.scenario.test.tsx
```

RED: `1 failed | 1 passed`; selected second-scenario geometry was fetched while
the sidebar still rendered W100. GREEN after deriving a separate displayed
scenario from the selected job: `2 passed` tests, duration `924ms`. The
regression proves sidebar and Operations labels, System hashes/settings, and
geometry all belong to `second_map`, while Run Setup remains on W100 and remains
editable for a completed job. Run Setup continues to lock only for active jobs.

Post-fix focused backend result: `69 passed, 1 warning in 8.51s`. Post-fix
focused frontend result: `4 passed` files, `14 passed` tests, duration `958ms`.

## Full verification

Full backend:

```bash
g2_deps=$(cat /tmp/g2-dashboard-deps.path)
PYTHONPATH="$g2_deps:$PWD/src/dashboard_GUI/backend:$PWD/src/mrta_core:$PWD/src/simulation_env:$PWD/src/task_management" PYTHONDONTWRITEBYTECODE=1 .pixi/envs/default/bin/python -m pytest src/dashboard_GUI/backend/tests -q
```

Final result after QA fixes: `167 passed, 1 warning in 40.73s`. The warning is Starlette's existing
AnyIO `BlockingPortal` deprecation warning from the temporary dashboard
dependency layer.

Full frontend:

```bash
cd src/dashboard_GUI/frontend
npm test -- --run
```

Final result after QA fixes: `7 passed` files, `26 passed` tests, duration `1.06s`.

Production build:

```bash
cd src/dashboard_GUI/frontend
npm run build
```

The first type-check exposed two test-fixture typing gaps (missing fetch options
and missing scenario provenance). After correcting those test fixtures, the same
command passed: TypeScript clean, Vite transformed `1949` modules, and the build
completed in `139ms` on the final QA-fix run.

Direct production W100 map validation:

```bash
PYTHONDONTWRITEBYTECODE=1 .pixi/envs/default/bin/python src/simulation_env/scripts/map_tool.py check --map src/simulation_env/maps/W100.xlsx --robots 100
```

Result: `40 x 54`, `100` max robots, `No problems found`, `status: OK`.

Full W100 topology gate:

```bash
PYTHONPATH="$PWD/src/mrta_core:$PWD/src/simulation_env:$PWD/src/task_management" PYTHONDONTWRITEBYTECODE=1 .pixi/envs/default/bin/python src/simulation_env/scripts/check_map_topology.py --config src/simulation_env/config/warehouse.yaml
```

Result: `1424/1424` forward/reverse reachable, strong connectivity `PASS`, `0`
two-way core edges, all `724` terminal cells enter/leave the core, `523452`
station legs with `0` unreachable/through-terminal/off-nav-graph legs, and
`status: OK`. An earlier invocation without the required `PYTHONPATH` stopped
before validation with `ModuleNotFoundError: mrta_core`; the command above is the
corrected successful gate.

W100 byte hashes before and after implementation are identical:

```text
508c3ab470df7de45de22c9f7f690c77cd09c3de54d89c9a2810742710c27fbc  src/simulation_env/maps/W100.xlsx
fbab9056ece331128758d894513dd11bbe7596d9149f28c17e4eb65ab5c7ca9d  src/simulation_env/config/warehouse.yaml
c38a93e492b0e38afecbc3118c70fb4ab8a6a1a35b156b42986bed48fce20ea6  src/simulation_env/config/orders.csv
```

## Self-review

`git diff --check` is clean. The diff from `ae832f5` was inspected, including
all newly created feature files. Final `git status --short` shows only the
uncommitted G2 implementation plus the pre-existing `.pixi`, G2 design, and G2
plan untracked paths; nothing is staged.

Both important QA findings are resolved. The review's future-hardening notes
about unrestricted registry paths and enforcing exactly one reference entry were
left unchanged as explicitly out of scope for these acceptance fixes. The
temporary dashboard dependency target is outside the repository and is not part
of the deliverable.

---

# Addendum 2026-10-31: both maps at 100 robots (owner-approved)

Owner approval of 2026-10-31 overrides two earlier G2 statements: the non-goal
"no registration of `my_map.xlsx`" and the instruction not to read it. The
researcher's `my_map.xlsx` (SHA-256 `3a0977c7...cec5dc2`) was copied byte-for-byte
from the main workspace into `src/simulation_env/maps/my_map.xlsx`. The tracked
branch copy of that file was an older version (`ee3c21d0...`); the copy now shows
as modified. The main workspace was only read.

## Deliberate exception to "one config, one world"

W100 remains the reference with its own `config/warehouse.yaml`,
`worlds/warehouse.world` and `warehouse_nav_graph.yaml`, all byte-identical to
before. Serving a second map needs a second config and world. One of each was added
for `my_map` and nothing per fleet size:

| File | Role |
|---|---|
| `src/simulation_env/config/my_map.yaml` | the reference config text with the map, `robot_count` (200), `orders.file` and `gazebo.world_name` switched |
| `src/simulation_env/config/orders_my_map.csv` | 2000 orders from the map (byte-identical to the owner's `orders.csv`) |
| `src/simulation_env/worlds/my_map.world` | Gazebo world, from the existing generator |
| `src/rmf_grid_traffic/config/my_map_nav_graph.yaml` | RMF graph, from the existing generator, verified lane-for-lane |
| `src/dashboard_GUI/config/scenarios.yaml` | second registry entry `my_map` (not the reference) |

`test_one_config.py` pinned "the config folder holds exactly `warehouse.yaml`". It now
requires `warehouse.yaml` plus, for every other config, its own world and a matching
`gazebo.world_name`.

## Any-map design (no map name is hardcoded)

- `scripts/build_scenario_bundle.py --id <id> --map <xlsx>` is the one repeatable
  step that writes a scenario's bundle (config, orders, world, nav graph) by calling
  the existing `build_scenario.py` tools. `--id my_map` took 6.8 s. It never writes
  `warehouse.yaml`. A third map needs this command plus one registry entry.
- `simulation_env/scenario_registry.py` resolves `scenario:=<id>` to the registered
  config path (safe ids only, unknown ids rejected, `config_file` and `scenario`
  mutually exclusive). Both launch files take `scenario:=<id>`; neither names a map.
  With no argument the reference config is used, as before.
- Gazebo remains a mirror: the launch only chooses which config the same engine
  reads; no planner, allocator or traffic code was touched.

## Verification

Reproduce from the worktree root with `PP="$PWD/src/mrta_core:$PWD/src/simulation_env:$PWD/src/task_management"`
and `PY=$PWD/.pixi/envs/default/bin/python`.

W100 hashes before and after (`sha256sum` identical, `-c` OK): `maps/W100.xlsx`
`508c3ab4...`, `config/warehouse.yaml` `fbab9056...`, `config/orders.csv` `c38a93e4...`,
`worlds/warehouse.world` `98a55eaf...`, `warehouse_nav_graph.yaml` `6c19c9e6...`. The tracked world and nav
graph were confirmed to be W100's by regenerating them (identical bytes).

Tests: backend 169 passed (167 plus 2 new), frontend 26 passed (no frontend code
change was needed: the selector is registry-driven), production build passes,
scientific suite 575 passed (includes 13 new tests in `test_scenario_bundle.py`),
`ruff` clean on touched files.

Headless, 100 robots, 200 orders, `nearest_astar`:

| | W100 | my_map |
|---|---|---|
| map | 40 x 54, 100 parking | 79 x 104, 200 parking |
| completed | 200/200 | 200/200 |
| makespan | 302.67 s | 418.33 s |
| total distance | 26052 m | 41145 m |
| total waiting | 1253.3 s | 188.0 s |
| reservation violations | 0 | 0 |
| wall clock | 10.8 s | 57.2 s |

`map_tool.py check --robots 100` on `my_map.xlsx`: OK.

`check_map_topology.py --exhaustive` on my_map: 6,757,400 legs, 0 unreachable, 0 through a terminal, 0 terminal hops, 0 off the nav graph, status OK, in about 1.5 hours. The default gate now audits legs in one pass over the road graph: same result on my_map in 5.2 s, and on W100 in 0.5 s against 92 s exhaustive.

Gazebo smoke (ROS Jazzy, `gz sim -s` headless, isolated `GZ_PARTITION`/`ROS_DOMAIN_ID`,
100 robots, 150 orders): both maps start, the fleet node reports "Gazebo already
shows these 100 robot(s)" and "100 robot poses pushed to Gazebo world '<id>'", and
sampling `/world/<id>/pose/info` twice shows 100 of 100 robots changed position (W100
world `warehouse`, my_map world `my_map`). All processes were stopped afterwards.

Dashboard: `launch_control_center.py start` from the worktree built the UI and served
it. `GET /api/v1/scenarios` listed W100 (reference, valid, 40x54, capacity 100) and
my_map (valid, 79x104, capacity 200). `GET /api/v1/scenarios/my_map/map` returned 79x104
geometry (8204 traversable cells, 16464 directed edges, 2600 zones). A preview then
launch of a my_map run at 100 robots, 10 orders completed with exit code 0 and used
`--config .../my_map.yaml`. Unknown and path-like ids were refused (404).

## Backend environment

`src/dashboard_GUI/README.md` now has "Start from a fresh checkout or worktree":
create `backend/.venv` from the scientific interpreter with
`--system-site-packages`, `pip install -e 'src/dashboard_GUI/backend[test]'`, `npm ci`,
then `launch_control_center.py check` and `start`. The `.venv`, egg-info and
`var/` are git-ignored. This replaced the `/tmp` dependency directory.

## Not built (next increment)

Upload of a map, a Gazebo start/stop control, and experiment-matrix map selection.
Findings for them are in the hand-off message.
