# 09 — Build and install

Every command in this document was executed during the audit on 2026-08-29 and is
reported with its actual result.

## Verified environment

| Component | Version present | Required? |
|---|---|---|
| OS | Linux (Ubuntu 24.04 class), kernel 7.0.0-30-generic | – |
| ROS 2 | **Jazzy**, at `/opt/ros/jazzy` | ✅ required |
| Python | 3.12.3 (system) | ✅ required |
| colcon | `/usr/bin/colcon` | ✅ required |
| `swipl` | **not installed** | not needed today |

## Dependencies

### System / ROS

Declared across the `package.xml` files: `rclpy`, `geometry_msgs`, `std_msgs`,
`std_srvs`, `ament_cmake`, `rosidl_default_generators`, `rosidl_default_runtime`,
`ament_lint_auto`, `ament_lint_common`, `python3-pytest`.

All of these ship with a standard **`ros-jazzy-desktop`** installation. There is no
third-party ROS dependency.

### Python

**None beyond the standard library.** `mrta_core` imports only `dataclasses`, `enum`,
`heapq`, `math`, `typing`. The nodes add only `rclpy`, `json`, `csv`, `os`, `threading`,
`time`, `random`, `datetime`.

No `requirements.txt`, `pyproject.toml`, or `rosdep.yaml` exists in the repository —
and none is currently needed, because nothing outside the ROS 2 distribution is used.

### `pixi.toml` — not used by the build

`pixi.toml` declares a conda environment with numpy, pandas, scipy, networkx,
matplotlib, pyyaml, tqdm, pytest, ruff, mypy and swi-prolog.

**The ROS 2 build and runtime do not use it.** None of those packages is imported by any
source file. It is scaffolding for future analysis and ILP work. You do **not** need
pixi to build or run this repository.

If you do want its lint/format tooling:

```bash
pixi run lint      # ruff check .
pixi run format    # ruff format .
```

## Build

### Verified clean build

```bash
cd ~/rnd_mrta_ilp_ws
source /opt/ros/jazzy/setup.bash
colcon build
source install/setup.bash          # source the overlay after EVERY build
```

**Result — 5/5 packages, zero errors, zero warnings:**

```
Starting >>> robot_interfaces
Starting >>> mrta_core
Finished <<< mrta_core [0.94s]
Finished <<< robot_interfaces [10.1s]
Starting >>> shared_memory_node
Starting >>> simulation_env
Starting >>> task_management
Finished <<< shared_memory_node [1.07s]
Finished <<< task_management [1.12s]
Finished <<< simulation_env [1.18s]

Summary: 5 packages finished [11.5s]
```

A from-scratch build takes about 12 seconds; an incremental rebuild about 5.

The build logs were searched for `warning`, `error` and `deprecat` — **no matches**.
This is a genuinely clean build.

### Build order

`robot_interfaces` and `mrta_core` have no interdependency and build in parallel; the
three node packages depend on them and build afterwards.

```
robot_interfaces ──┐
                   ├──> shared_memory_node, simulation_env, task_management
mrta_core ─────────┘
```

### Single-package rebuild

```bash
colcon build --packages-select task_management && source install/setup.bash
```

Since all Python packages are `ament_python` installed by copy, **editing a `.py` file
requires a rebuild** before `ros2 run` sees the change. There is no symlink install
configured; add `--symlink-install` if you want live editing.

## Note on `build/`, `install/`, `log/`

All three are git-ignored and each contains a `COLCON_IGNORE` marker so colcon does not
descend into them. They are fully regenerable — deleting them and rebuilding is safe.

> The root `README.md` states that *"the old workspace in `ilp_ws/` carries a
> `COLCON_IGNORE` marker so colcon skips it."* There is **no `ilp_ws/` directory** in
> the repository — only the git-ignored archive `ilp_ws.zip`, which colcon never sees.
> That sentence is stale.

## Verifying the install

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash

ros2 pkg list | grep -E 'mrta_core|robot_interfaces|shared_memory_node|simulation_env|task_management'
ros2 pkg executables task_management
ros2 pkg executables simulation_env
ros2 pkg executables shared_memory_node
```

Expected executables:

| Package | Executables |
|---|---|
| `task_management` | `task_manager_node`, `order_generator`, `metrics_logger`, `ilp_dataset_logger` |
| `simulation_env` | `mock_fleet_manager` |
| `shared_memory_node` | `shared_memory_node` |
| `mrta_core` | *(none — library only)* |
| `robot_interfaces` | *(none — interfaces only)* |

## Fresh-machine checklist

```bash
# 1. ROS 2 Jazzy (see https://docs.ros.org/en/jazzy/Installation.html)
sudo apt install ros-jazzy-desktop python3-colcon-common-extensions

# 2. Clone
git clone <repo-url> ~/rnd_mrta_ilp_ws
cd ~/rnd_mrta_ilp_ws

# 3. Build
source /opt/ros/jazzy/setup.bash
colcon build
source install/setup.bash

# 4. Verify
colcon test && colcon test-result --verbose     # expect 47 tests, 0 failures

# 5. Run
scripts/run_experiment.sh nearest 4 10 42 5.0 20.0
```

`rosdep install --from-paths src --ignore-src -y` will also succeed but installs
nothing beyond what `ros-jazzy-desktop` already provides.

> ⚠ **Step 2 caveat:** the repository's main `README.md` is currently **untracked**, so
> a fresh clone will not contain it. See
> [03_REPOSITORY_STRUCTURE.md](03_REPOSITORY_STRUCTURE.md).
