# Scripts

Every script runs from the repository root with the project on the
path (see [docs/START_HERE.md](../../../docs/START_HERE.md), step 0):

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/<script>.py --help
```

## The ones you use

| Script | What for |
|---|---|
| `map_tool.py` | make, check and view maps (`new`, `example`, `check`, `show`, `export`) |
| `generate_orders.py` | a seeded order list from the current map (`build_scenario.py --map` runs it for you) |
| `run_workload.py` | run a simulation headless; `--strategy`, `--robots`, `--dataset` |
| `check_strategy_distinctness.py` | prove strategies make different choices |
| `check_map_topology.py` | full acceptance check for a map (`--map`) |
| `build_scenario.py` | `--map X.xlsx`: make a map the current one; rewrites the one config, regenerates the orders, rebuilds the one Gazebo world |
| `plot_dataset.py` | figures from recorded datasets |
| `generate_gazebo_grid_world.py` | the Gazebo world alone (`build_scenario.py` calls it) |

## Regression checks for the simulator itself

These prove the fixed infrastructure still behaves. Each prints its
evidence and ends with a status line; run them all after changing
traffic, routing or battery code:

```bash
for f in src/simulation_env/scripts/check_*.py; do
    b=$(basename $f); A=""
    case "$b" in check_map_topology.py|check_strategy_distinctness.py) A="--config src/simulation_env/test/fixtures/v2_reference.yaml";; esac
    printf "%-40s " "$b"
    PYTHONPATH=$PP $PY "$f" $A > /dev/null 2>&1 && echo PASS || echo FAIL
done
```

| Script | Checks |
|---|---|
| `check_batch_reservations.py` | the reservation table never double-books a cell |
| `check_engine_batch_traffic.py` | robots moving in one batch resolve conflicts correctly |
| `check_engine_headon_yield.py` | the engine prevents head-on meetings |
| `check_local_headon_resolution.py` | head-on detection, yielding and rejoining on the real map |
| `check_local_traffic_controller.py` | the traffic controller's decisions on hand-built cases |
| `check_local_yield_selector.py` | a yielding robot picks a legal side cell |
| `check_18_robot_traffic_stress.py` | 18 robots on deliberately difficult routes |
| `check_raw_mission_cycle.py` | one robot's full mission cycle, home to home |
| `check_battery_eligibility.py` | Battery B2: robots are refused orders they cannot finish |
| `check_charger_evacuation.py` | Battery B3/B4: robots leave the roads to charge |
| `check_b4_charging.py` | Battery B4: charge to 100%, leave, rejoin the fleet |
| `check_b5_queue.py` | Battery B5: holding promotion and charger preemption |

## V2-only analysis

These read the built-in V2 lane rules directly, so they describe the
V2 map and nothing else. They are kept for the record of how V2's
one-way layout was designed.

| Script | What it does |
|---|---|
| `discover_directional_highways.py` | lists V2's straight road runs |
| `validate_directional_highways.py` | reports V2's lane rules against its map |
