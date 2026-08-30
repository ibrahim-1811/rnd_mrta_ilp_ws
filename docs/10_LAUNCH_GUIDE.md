# 10 — Launch guide

**Every command below was executed during the audit.** Commands that do not work are
marked as such rather than omitted.

## Start from a fresh terminal

```bash
cd ~/rnd_mrta_ilp_ws
source /opt/ros/jazzy/setup.bash

colcon build

source install/setup.bash
```

You must re-source `install/setup.bash` in **every** new terminal, and after **every**
build.

---

## A. Basic simulation (simulator only, no allocation)

Brings up `fleet_manager` + `shared_memory_node`. Robots spawn but nothing dispatches
tasks — useful for checking the grid and spawn logic.

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ros2 launch simulation_env sim_only.launch.py num_robots:=3 time_scale:=20.0
```

✅ Verified. Expected first line:

```
[mock_fleet_manager-1] [INFO] [fleet_manager]: MockFleetManager (grid): 3 robots on
31x31 grid (res=1.0m), speed=2.0m/s, time_scale=20.0, battery=10.0Wh (physics energy model)
```

Arguments: `num_robots` (4), `world_size_m` (30.0), `resolution_m` (1.0),
`robot_speed_ms` (2.0), `time_scale` (1.0), `shelves_csv`.

Inspect it from a second terminal:

```bash
ros2 topic echo /fleet_status --once
ros2 topic echo /simulation/congestion --once
ros2 service call /get_drop_off_pose robot_interfaces/srv/GetPose
```

---

## B. Complete MRTA pipeline (two terminals)

**Terminal A — bring up the stack** (5 nodes: simulator, database, task manager, both
loggers):

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ros2 launch task_management headless_warehouse.launch.py \
  strategy:=score num_robots:=6 time_scale:=20.0 \
  out_dir:=/home/ibrahim/rnd_mrta_ilp_ws/Mrta_experiments/score_run
```

**Terminal B — drive orders:**

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ros2 run task_management order_generator --ros-args \
  -p num_orders:=20 -p seed:=42 -p rate_hz:=5.0
```

✅ Verified. Terminal A prints `[assign] …` lines and a 2 s `status:` heartbeat.

> **Use an absolute `out_dir`.** ROS launch does not expand `~`; passing
> `out_dir:=~/Documents` creates a directory literally named `~`. The loggers call
> `expanduser`, but the launch substitution that builds `<out_dir>/ilp_logs` does not.

### `headless_warehouse.launch.py` arguments

| Argument | Default | Notes |
|---|---|---|
| `num_robots` | `6` | |
| `strategy` | `auction` | one of the 13 names |
| `seed` | `0` | allocator seed — only `random` uses it |
| `world_size_m` | `30.0` | given to simulator **and** task manager |
| `resolution_m` | `1.0` | given to both |
| `robot_speed_ms` | `2.0` | given to both |
| `time_scale` | `20.0` | simulator only |
| `out_dir` | `/tmp/mrta_run` | metrics here, traces in `<out_dir>/ilp_logs` |
| `shelves_csv` | `headless_shelves_100.csv` (installed share path) | |

> **Not exposed by this launch file**, though the nodes accept them:
> `min_battery_after`, `congestion_weight`, `grid_map_file`, `battery_*` parameters,
> and `initial_battery`. To vary them you must run the nodes individually (section D2)
> or edit the launch file.

---

## C. Headless experiment (one command)

```bash
scripts/run_experiment.sh [strategy] [num_robots] [num_orders] [seed] [rate_hz] [time_scale]
```

Defaults: `auction 6 20 42 5.0 20.0`.

✅ Verified — this exact command was run 25 times during the audit:

```bash
scripts/run_experiment.sh nearest 4 10 42 5.0 20.0
```

Actual output:

```
== launching headless stack: strategy=nearest robots=4 ==
== sending 10 orders (seed=42, rate=5.0Hz) ==
  progress: completed=0 / 10
  progress: completed=6 / 10
  progress: completed=10 / 10

==================== RESULT ====================
strategy  n_assign  n_defer  n_completed  makespan_s  throughput_ops  mean_travel_m  mean_energy_pct  mean_alloc_runtime_ms
nearest   10        0        10           3.756       2.6626          38.7           11.661           0.0034
ILP dataset rows: 18
saved to: /home/ibrahim/rnd_mrta_ilp_ws/Mrta_experiments/nearest_2026-08-29_17-03-48
================================================
```

The script sources ROS itself, so a bare terminal is fine. It `pkill`s any leftover
nodes before starting and on exit, waits 8 s for bring-up, then polls the metrics CSV
for up to 90 s.

> ⚠ **The `seed` argument is passed only to `order_generator`, not to the allocator.**
> `run_experiment.sh:36-38` does not forward `seed:=` to the launch file, so
> `allocator_seed` is always `0`. The order *stream* varies with the seed; the
> allocator's RNG does not. This affects `random` only.

> ⚠ **The script does not fail on timeout.** If the run stalls, the loop exits after
> 90 s and the script still prints `RESULT` with partial numbers and exits 0. Always
> check that `n_completed` equals the order count.

---

## D. Selecting an allocator

### D1 — via the launch file

```bash
ros2 launch task_management headless_warehouse.launch.py strategy:=heuristic
```

### D2 — running the task manager alone (full parameter access)

```bash
ros2 run task_management task_manager_node --ros-args \
  -p allocation_strategy:=score \
  -p allocator_seed:=7 \
  -p min_battery_after:=25.0 \
  -p world_size_m:=30.0 -p resolution_m:=1.0 -p robot_speed_ms:=2.0
```

> `world_size_m`, `resolution_m` and `robot_speed_ms` **must match the simulator** or
> the allocator scores a different world than the one the robots drive in.

### The 13 valid names

```
random  round_robin  nearest  least_busy  highest_battery  spt  lpt  edf
heuristic  greedy(=heuristic)  score  bid  auction
```

An invalid name kills `task_manager_node` with `KeyError` at startup while the other
four nodes keep running — you get a stack with no allocator. ✅ Verified.

See [05_MRTA_ALGORITHMS.md](05_MRTA_ALGORITHMS.md) before comparing strategies: several
of these names produce **identical** behaviour in the current pipeline.

---

## E. Running Outcome-Aware ILP

❌ **Not possible. This is not implemented.**

There is no ILP allocator, no solver, no Prolog integration, and no learned rule file
in this repository. Any command of the form `strategy:=outcome_aware_ilp` fails with:

```
KeyError: "unknown strategy 'outcome_aware_ilp'; known: ['auction', 'bid', 'edf',
'greedy', 'heuristic', 'highest_battery', 'least_busy', 'lpt', 'nearest', 'random',
'round_robin', 'score', 'spt']"
```

See [06_OUTCOME_AWARE_ILP.md](06_OUTCOME_AWARE_ILP.md) for the full evidence and for
what the closest available substitute is (`heuristic` — the teacher policy the previous
workspace's ILP was trained to imitate).

---

## F. Running tests

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash

# All packages via colcon  -> 47 tests, 0 failures
colcon test
colcon test-result --verbose

# mrta_core only           -> 27 passed
python3 -m pytest src/mrta_core/tests -q

# allocation strategies    -> 16 passed
PYTHONPATH=src/task_management:$PYTHONPATH \
  python3 -m pytest src/task_management/test/test_allocators.py -q
```

✅ All three verified. See [14_TESTING.md](14_TESTING.md).

---

## G. Finding output metrics

```bash
# newest run directory
ls -td Mrta_experiments/*/ | head -1

# summary metrics of the newest run
cat "$(ls -td Mrta_experiments/*/ | head -1)"/metrics_*.csv

# readable columns
column -s, -t "$(ls -td Mrta_experiments/*/ | head -1)"/metrics_*.csv

# per-candidate decision trace
column -s, -t "$(ls -td Mrta_experiments/*/ | head -1)"/ilp_logs/ilp_dataset_*.csv | less -S

# trace row count
wc -l "$(ls -td Mrta_experiments/*/ | head -1)"/ilp_logs/ilp_dataset_*.csv

# assignments made during the run
grep '\[assign\]' "$(ls -td Mrta_experiments/*/ | head -1)"/launch.log
```

Layout per run directory:

```
Mrta_experiments/<strategy>_<timestamp>/
├── metrics_<ts>.csv              # one-row summary
├── ilp_logs/ilp_dataset_<ts>.csv # one row per candidate per decision
├── launch.log                    # all five nodes' stdout
└── order_gen.log                 # order generator stdout
```

Launching directly (not via the script) writes to `out_dir`, defaulting to
`/tmp/mrta_run`, and produces no `launch.log`/`order_gen.log`.

See [13_METRICS_AND_OUTPUTS.md](13_METRICS_AND_OUTPUTS.md) for column semantics.

---

## H. Stopping processes cleanly

`run_experiment.sh` installs an `EXIT` trap that kills everything it started, so a
normal or interrupted run cleans up after itself.

For manual launches, `Ctrl-C` in each terminal is the clean path. To be certain nothing
is left behind:

```bash
pkill -f "mock_fleet_manager|shared_memory_node|task_manager_node|ilp_dataset_logger|metrics_logger|order_generator"
```

Confirm the workspace is quiet:

```bash
pgrep -af "mock_fleet_manager|task_manager_node|order_generator"   # expect no output
ros2 node list                                                      # expect no output
```

> **Always check for stragglers before a new run.** The simulator runs each task in a
> daemon thread and the loggers hold open file handles; a surviving `fleet_manager`
> from a previous run will answer `/task_list` calls from the new one and corrupt both
> runs' results. `run_experiment.sh` guards against this with a `pkill` at startup;
> manual launches do not.
