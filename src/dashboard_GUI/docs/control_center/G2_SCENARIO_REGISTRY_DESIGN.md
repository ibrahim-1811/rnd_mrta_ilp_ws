# G2 validated scenario registry design

## Intent

Add pre-run warehouse selection to the existing Control Center without changing
the simulator, its allocation policies, routing, traffic, battery behavior, or
tracked scientific inputs. W100 remains the scientific reference. A selected
scenario is an input to a single-run preview; it is never mutable global state.

## Goals and acceptance

- List registered scenarios and select a valid one before previewing a single run.
- Load maps through `load_sim_config` and `load_warehouse_xlsx`, then validate
  their production graph through `mrta_core.map_check.check_map`.
- Show dimensions, initial robot capacity, provenance hashes, warnings, and
  useful validation failures.
- Reject unknown or invalid scenario IDs and fleets above the selected capacity.
- Capture scenario identity and input hashes in preview plans, then rebuild and
  compare the plan at launch so source drift cannot change the launched map.
- Keep W100's existing CLI argv and scientific files unchanged.
- Disable scenario changes while any owned simulation is active.
- Keep generated/interactive scenarios under ignored dashboard runtime storage;
  never rewrite `src/simulation_env/config/warehouse.yaml`, W100, or orders.

## Non-goals

- No map editor, upload/register mutation API, runtime robot admission, Open-RMF,
  experiment-matrix map selection, or scientific runner changes.
- No registration of `my_map.xlsx` in this task. It remains owner work and is
  only proposed for registration after the full map acceptance gate.
- No fallback validation logic in the GUI; production loaders and `check_map`
  remain authoritative.

## Approaches considered

1. Mutate the repository's one current config when the user selects a map.
   Rejected: it overwrites scientific configuration and lets UI state affect
   unrelated runs.
2. Implement map upload, generation, validation, and registration together.
   Rejected for G2: it expands scope and could endanger the existing custom map.
3. Use a read-only GUI registry and per-run scenario identity. Chosen: it is the
   smallest independently testable change and builds on the original `--config`
   single-run API.

## Design

The tracked GUI registry declares stable scenario IDs, labels, config paths, and
provenance class. Initially it contains only W100. The backend registry adapter
validates every declaration independently. A bad entry remains visible with
diagnostics but cannot provide geometry or enter a preview.

Scenario metadata contains a stable ID plus a content identity derived from the
registry declaration and SHA-256 hashes of config, map, and orders. The selected
ID is part of `RunSpec`; `CommandPlanner` resolves it to its immutable input set,
uses that scenario's config path in the existing `run_workload.py --config`
argument, and includes identity/hashes in the plan. The existing launch-time plan
rebuild/fingerprint comparison enforces immutability between preview and launch.
There is no backend "selected scenario" value and no mutation route.

The React app loads the list first, defaults to the scientific reference, and
loads geometry by encoded scenario ID. The selector is part of Run Setup and is
disabled while an owned job is active. Invalid entries show their diagnostics and
cannot be previewed. Changing selection clears the current preview and custom
order selection; uploaded CSV validation names the selected scenario and planning
validates the CSV against that map again.

Matrix experiments remain explicitly W100-only. The existing experiment CLI has
no `--config` option, and changing it is scientific runner work outside this task.

## Interactive scenario storage and future custom registration

Future validated interactive registrations should be copied into an ignored,
app-owned directory such as `src/dashboard_GUI/var/scenarios/<content-id>/`, with
read-only config/map/orders bytes and an app-owned registry record. They must not
replace tracked W100 configuration. To register `my_map.xlsx`, first preserve its
bytes, run the full topology/routing/traffic/terminal/Gazebo consistency gate,
produce compatible config and orders in a separate staging directory, then copy
the validated immutable bundle into app storage. That registration workflow
requires project-owner approval because it creates persistent app-owned scenario
data and decides whether warnings are acceptable at the custom map's scale.

