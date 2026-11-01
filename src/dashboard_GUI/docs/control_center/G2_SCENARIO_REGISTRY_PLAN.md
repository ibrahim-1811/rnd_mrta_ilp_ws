# G2 Scenario Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:test-driven-development`. This task is deliberately one integrated
> implementation owned by one agent, followed by one independent QA reviewer.
> Do not commit, stage, push, merge, or modify either existing worktree.

**Goal:** Add a validated, immutable, multi-entry-ready warehouse scenario
registry and pre-run single-simulation map selection to the existing dashboard.

**Architecture:** A GUI-owned YAML registry points to existing scenario config
files. A backend adapter resolves each entry through production config/map
loaders and `check_map`, returning per-entry validation/provenance. Run previews
carry the selected scenario and hashes; the React UI selects valid scenarios and
loads their geometry without mutable global backend state.

**Tech Stack:** Python 3.12, FastAPI/Pydantic, React/TypeScript/Vite, pytest,
Vitest/Testing Library, existing `mrta_core` loaders and validator.

**Spec:** `src/dashboard_GUI/docs/control_center/G2_SCENARIO_REGISTRY_DESIGN.md`

## Global constraints

- Work only in the isolated `feature/gui-scenario-registry` worktree.
- Do not modify scientific allocation, execution, traffic, routing, battery,
  experiment protocol, `run_workload.py`, or `run_experiment.py`.
- W100 config/map/orders bytes and original W100 CLI semantics remain unchanged.
- Do not read, copy, edit, regenerate, or register the owner's dirty
  `my_map.xlsx`; use committed W100 and temporary test fixtures only.
- Do not commit, stage, push, merge, reset, or delete historical work.
- Preserve existing API envelope and security behavior.
- Matrix experiments remain on the original W100 runner/default config.
- New behavior follows strict red-green-refactor TDD; record the red and green
  commands/results in the implementation report.

## Review focus

- One invalid registry entry must not hide valid W100; it is listed with useful
  diagnostics and is refused for geometry/preview.
- Unknown/path-like scenario IDs are rejected without resolving arbitrary paths.
- A map/config/orders byte change after preview must cause launch-time drift
  rejection, not a run with different inputs.
- Selecting another scenario must clear stale geometry, previews, and uploaded
  order selection, and selection stays disabled while a job is active.
- W100 single-run argv remains the original runner with the same config path;
  experiments do not acquire unsupported scenario/config fields.

---

### Task 1: Implement and verify the integrated G2 registry

**Files:**

- Create: `src/dashboard_GUI/config/scenarios.yaml`
- Create: `src/dashboard_GUI/backend/control_center/adapters/scenarios.py`
- Modify: `src/dashboard_GUI/backend/control_center/adapters/catalogue.py`
- Modify: `src/dashboard_GUI/backend/control_center/adapters/commands.py`
- Modify: `src/dashboard_GUI/backend/control_center/adapters/inputs.py`
- Modify: `src/dashboard_GUI/backend/control_center/api/routes.py`
- Modify: `src/dashboard_GUI/backend/control_center/api/research.py`
- Modify: `src/dashboard_GUI/backend/control_center/schemas/jobs.py`
- Modify: `src/dashboard_GUI/backend/control_center/services/jobs.py` only if
  selected-scenario final validation requires it
- Test: `src/dashboard_GUI/backend/tests/test_scenarios.py`
- Modify tests only where an existing W100-only assertion becomes multi-scenario
- Modify: `src/dashboard_GUI/frontend/src/types.ts`
- Modify: `src/dashboard_GUI/frontend/src/model.ts`
- Modify: `src/dashboard_GUI/frontend/src/App.tsx`
- Modify: `src/dashboard_GUI/frontend/src/RunSetup.tsx`
- Modify: `src/dashboard_GUI/frontend/src/InputPicker.tsx`
- Test: `src/dashboard_GUI/frontend/src/RunSetup.test.tsx`
- Add or modify a focused App/scenario test if selection/geometry ownership is
  not observable in `RunSetup.test.tsx`
- Modify: `src/dashboard_GUI/frontend/src/styles.css` only for the existing visual
  language's selector/diagnostic states
- Modify: `src/dashboard_GUI/README.md`
- Create: `src/dashboard_GUI/docs/control_center/G2_SCENARIO_REGISTRY_REPORT.md`

**Interfaces:**

- `load_scenario(scenario_id: str = "W100") -> tuple[SimConfig, WarehouseMap]`
  remains the compatibility entry point.
- `scenarios() -> list[dict]` returns every declared scenario independently with
  `id`, `label`, `reference`, nullable dimensions/capacity, `valid`,
  `validation.errors`, `validation.warnings`, `hashes`, `identity`, and settings.
- `geometry(scenario_id: str = "W100") -> dict` returns production geometry for
  one valid entry.
- `RunSpec.scenario_id` accepts a safe registry ID, not a path; planner lookup
  establishes existence and validity.
- Frontend `buildSpec(form, capacity, scenarioId)` emits the selected stable ID.
- CSV upload includes `scenario_id` so immediate map-aware validation matches the
  chosen map; preview revalidates against the chosen map.

- [ ] **Step 1: Write backend failing tests**

  Cover two valid temporary entries, W100 reference metadata, production
  loader/`check_map` validation, invalid-map diagnostics, unknown/path-like ID
  refusal, selected config in single-run argv/effective plan, capacity, W100 argv
  compatibility, and preview/launch drift. Derive literal expectations from
  controlled fixtures; do not assert mocked calls.

- [ ] **Step 2: Run focused backend tests and capture the expected RED failures**

  Run the new scenario tests plus affected command/API tests with the established
  dashboard Python/PYTHONPATH. Failures must be due to missing registry behavior.

- [ ] **Step 3: Implement the minimal backend registry and integration**

  Keep validation in the existing production loader/checker. Registry/API code
  only adapts results and error status. Do not modify scientific packages.

- [ ] **Step 4: Run focused backend tests GREEN**

  Run the exact focused command from Step 2 and record pass counts.

- [ ] **Step 5: Write frontend failing tests**

  Prove scenario selection changes the preview `scenario_id`, shows dimensions
  and capacity, clears custom input/preview, exposes invalid diagnostics, and is
  disabled during an active job. Test rendered behavior and request payloads.

- [ ] **Step 6: Run focused frontend tests and capture the expected RED failures**

  Run the relevant Vitest files; failures must identify missing selection behavior.

- [ ] **Step 7: Implement minimal frontend selection and geometry flow**

  Default to the valid reference entry, URL-encode geometry requests, keep old
  job observations independent of the current setup selection, and leave matrix
  experiments bound to W100/reference capacity.

- [ ] **Step 8: Run focused frontend tests GREEN**

  Run the exact focused command from Step 6 and record pass counts.

- [ ] **Step 9: Update dashboard documentation and implementation report**

  Document identity/immutability, W100/reference behavior, invalid-map messages,
  single-run scope, runtime-storage separation, custom-map proposal, files changed,
  red/green evidence, and known integration dependencies. Do not update research
  phase/protocol documents.

- [ ] **Step 10: Run full verification**

  Run backend tests, frontend tests, TypeScript/production build, and focused
  W100 scenario/map checks. Record exact counts/output. Do not run a large study.

- [ ] **Step 11: Self-review and report without committing**

  Inspect `git diff --check`, `git status`, the diff from `ae832f5`, and hashes of
  W100 plus the owner worktree's `my_map.xlsx`. Report status, files, tests, and
  concerns in the requested implementation report; leave all feature changes
  uncommitted for independent QA.

