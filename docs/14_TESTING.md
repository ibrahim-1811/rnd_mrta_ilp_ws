# 14 — Testing

## Verified results

`colcon test` on a clean build, 2026-08-29:

```
Summary: 5 packages finished [1.73s]
Summary: 47 tests, 0 errors, 0 failures, 0 skipped
```

✅ **All tests pass.** Breakdown from the per-package result files:

| Package | Tests | Kind |
|---|---:|---|
| `mrta_core` | 27 | pytest — grid, world, kinematics, models, symbolic |
| `task_management` | 16 | pytest — allocation strategies |
| `robot_interfaces` | 2 | lint (`lint_cmake`, `xmllint`) |
| `shared_memory_node` | **0** | pytest ran, collected nothing |
| `simulation_env` | **0** | pytest ran, collected nothing |

`colcon` reports 47 rather than 45 because `robot_interfaces`' two lint tests are
counted both as ctest entries and as xunit results. **The real figure is 43 unit tests
plus 2 lint checks.**

## Running the tests

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash

# everything
colcon test && colcon test-result --verbose

# mrta_core only -> 27 passed in 0.02s
python3 -m pytest src/mrta_core/tests -q

# strategies only -> 16 passed in 0.01s
PYTHONPATH=src/task_management:$PYTHONPATH \
  python3 -m pytest src/task_management/test/test_allocators.py -q
```

All three verified. The direct-pytest commands work because `install/setup.bash` puts
`mrta_core` on `PYTHONPATH`; `task_management` needs the explicit prefix because its
tests import `task_management.features` from the source tree.

There is also `pixi run test`, which sets `PYTHONPATH=src/mrta_core` and runs pytest
from the repo root — it exercises `mrta_core` only and does not require ROS.

## Test inventory

### `mrta_core` — 27 tests

| File | Tests | Covers |
|---|---:|---|
| `test_grid.py` | ~8 | A\* correctness, obstacle avoidance, unreachable goals, congestion routing, path features |
| `test_world.py` | ~5 | metre↔cell round-trips, `from_bounds`/`square`, ASCII map loading |
| `test_kinematics.py` | ~6 | trapezoidal vs triangular time profiles, turn counting, energy monotonicity |
| `test_models.py` | ~4 | `RobotState.is_available`, dataclass invariants |
| `test_symbolic.py` | 12 | predicate generation for every threshold band, Prolog serialisation |

### `task_management` — 16 tests

`test/test_allocators.py` builds four synthetic `Candidate` objects and asserts each
strategy's selection, deferral behaviour, the registry contents, and the `greedy`
alias.

## Coverage gaps

| Component | Tests | Assessment |
|---|---|---|
| `mrta_core/grid.py` | ✅ good | A\*, obstacles, congestion all covered |
| `mrta_core/world.py` | ✅ good | **except** the silent out-of-bounds clamp is untested |
| `mrta_core/kinematics.py` | ✅ good | |
| `mrta_core/symbolic.py` | ✅ 12 tests | thoroughly tested — but the module has no production caller |
| `allocation_strategy.py` | ✅ 16 tests | selection logic covered |
| `features.py` | ❌ **none** | `build_candidates` and `task_cost` are **completely untested** |
| `task_manager.py` | ❌ **none** | the decision loop has no test |
| `mock_fleet_manager.py` | ❌ **none** | the simulator has no test |
| `shared_memory_node.py` | ❌ **none** | CSV loading, services, inventory untested |
| `order_generator.py` | ❌ **none** | seeding/reproducibility untested |
| `metrics_logger.py` | ❌ **none** | metric arithmetic untested |
| `ilp_dataset_logger.py` | ❌ **none** | CSV schema untested |
| Launch files | ❌ **none** | no launch testing |
| End-to-end integration | ❌ **none** | no automated pipeline test |

**All 43 unit tests exercise pure functions. Not one ROS node is tested**, and there is
no integration test. Roughly 60 % of the production code — every node, plus
`features.py` — has zero automated coverage.

## The most consequential gap

`features.py` is untested, and it is where the research-critical logic lives: the
two-leg A\* candidate construction, the battery feasibility rule, and `task_cost` — the
implementation of the proposal's cost equation.

Worse, the strategy tests use **hand-built synthetic candidates** with `workload` values
of 0, 1 and 5. That is why `test_auction_includes_load_and_differs_from_bid` passes
while `auction` and `bid` are provably identical on real data: the test asserts a
behaviour the real pipeline can never exhibit, because `build_candidates` always yields
`workload = 0`.

**The test suite passes, and simultaneously fails to detect that three strategies have
collapsed into one.** That is a coverage problem, not a correctness problem in the
tests themselves — but it is exactly the kind of gap that lets a research artefact
produce confident, wrong comparisons.

## Recommended additions

Priority order:

1. **A `build_candidates` test** against a known small grid — asserting distance,
   energy, feasibility, and that `workload` is whatever the caller passes.
2. **A property test that the collapse is visible**: assert on *pipeline-realistic*
   candidates (workload 0, priority 1) that `score`, `bid` and `auction` agree — turning
   today's silent degeneracy into a documented, intentional fact.
3. **A launch smoke test** (`launch_testing`) that brings up the stack, publishes three
   orders, and asserts the metrics CSV appears with `n_completed == 3`.
4. **A `metrics_logger` unit test** for makespan and throughput arithmetic.
5. **A `to_cell` clamping test** documenting the silent-clamp behaviour.
