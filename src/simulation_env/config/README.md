# The one config

```text
warehouse.yaml   THE config: the current map, robots, battery, speed, ...
orders.csv       the orders for the current map (2000, seed 42)
```

That is all. Every run uses `warehouse.yaml` by default: headless runs,
Gazebo, recording. Every setting in it is explained in the file itself
and marked FREE (yours to vary) or FIXED (the same for every strategy,
on purpose).

## Switching to another map

Do not edit the map line by hand. Run:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/build_scenario.py \
    --map src/simulation_env/maps/<your map>.xlsx
```

It checks the map, then rewrites `warehouse.map` and `fleet.robot_count`
in this config (keeping every comment), regenerates `orders.csv` from
the map's own stations, and rebuilds the one Gazebo world. Running it
again for the same map gives byte-identical files.

## Changing something for one run

No file needs editing:

```text
--robots 10           fleet size, up to the map's parking cells
--orders 300          the first 300 orders of orders.csv
--orders-file X.csv   a different order list (looked for in this folder)
--strategy random     nearest_astar | nearest_manhattan | random
--seed 7              seed for the random strategy
--quantum 3.0         batch freed robots for 3 s before assigning
--speed 1.0           robot speed
--dataset runs/X      record decisions and outcomes
```

In Gazebo the robot count is `robots:=N` on the `ros2 launch` line.

## Where the old configs went

`warehouse_sim.yaml` (the V2 map, 22 robots), its order lists and the
two battery test configs now live in `src/simulation_env/test/fixtures/`
as `v2_reference.yaml` and friends. The test suite and every frozen V2
baseline are measured on them; they are not for experiments.

Removed on 2026-10-23: `warehouse_W100.yaml`, `warehouse_W100_20.yaml`,
`orders_W100.csv`, `orders_W100_5000.csv`, `warehouse_sim_10.yaml`,
`warehouse_sim_500.yaml`, `warehouse_sim_batched.yaml`. Each is covered
by the one config plus a command-line option. To make 5000 orders again:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/generate_orders.py \
    --orders 5000 --output src/simulation_env/config/orders_5000.csv
```

and use it with `--orders-file orders_5000.csv`.
