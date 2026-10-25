# The map

```text
W100.xlsx    the current map: 40 x 54 cells, 100 parking, 552 shelves,
             36 drop-offs, 24 chargers, 12 holding, no rotaries
```

The config (`config/warehouse.yaml`) names the current map, and the one
Gazebo world (`worlds/warehouse.world`) is built from it.

## A new map

Draw it in Excel with [docs/MAKE_A_MAP.md](../../../docs/MAKE_A_MAP.md)
and save it here. Start from a valid one instead of a blank sheet:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/map_tool.py example \
    --robots 100 --out src/simulation_env/maps/my_map.xlsx
```

Check it after every edit:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/map_tool.py check \
    --map src/simulation_env/maps/my_map.xlsx
```

Then make it the current map, which rewrites the config and orders and
rebuilds the world:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/build_scenario.py \
    --map src/simulation_env/maps/my_map.xlsx
```

Switching back is the same command with `W100.xlsx`.

## Where the other maps went

The V2 map (`CSV_To_Gazebo_v2.xlsx`) moved to
`src/simulation_env/test/fixtures/`: the tests and frozen V2 baselines
use it. It has 33 rotaries, one of which caused the deadlock seen under
the random strategy. V1, the demo CSV and the `templates/` folder were
removed on 2026-10-23; `map_tool.py new` and `map_tool.py example`
make any template on demand.
