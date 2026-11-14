"""Validated Gazebo/ROS launch planning; scientific behavior stays in fleet_node."""

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from control_center.adapters.catalogue import CONFIG, REPO, load_scenario, strategies


def _fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _bool(value):
    return "true" if value else "false"


class GazeboPlanner:
    def __init__(self, data_dir, *, ros_setup=None, workspace_setup=None):
        self.data_dir = Path(data_dir).absolute()
        self.python = REPO / ".pixi/envs/default/bin/python"
        self.wrapper = REPO / "src/dashboard_GUI/scripts/run_gazebo.py"
        self.ros_setup = Path(ros_setup or "/opt/ros/jazzy/setup.bash").absolute()
        self.workspace_setup = Path(
            workspace_setup or (REPO / "install/setup.bash")
        ).absolute()

    def _require_environment(self):
        from control_center.adapters.commands import fail

        if not self.ros_setup.is_file() or self.ros_setup.is_symlink():
            fail("ROS 2 Jazzy setup is unavailable", 503)
        if not self.workspace_setup.is_file() or self.workspace_setup.is_symlink():
            fail(
                "ROS workspace is not built; run the documented colcon build first", 503
            )
        if not self.python.is_file() or not self.wrapper.is_file():
            fail("Dashboard or scientific interpreter is unavailable", 503)

    def inputs(self):
        config, _ = load_scenario()
        files = {}
        for path in (
            CONFIG,
            config.map_path,
            config.orders_path,
            self.wrapper,
            REPO / "src/simulation_env/launch/warehouse_sim.launch.py",
            REPO / "src/simulation_env/launch/gazebo.launch.py",
        ):
            files[str(path)] = sha256(path.read_bytes()).hexdigest()
        return files

    def build(self, spec, job_id):
        from control_center.adapters.commands import fail
        from simulation_env.fleet import home_bank

        self._require_environment()
        config, warehouse = load_scenario()
        known = {item["id"] for item in strategies()}
        if spec.strategy not in known:
            fail("Unknown registered strategy", 422)
        if spec.strategy == "ilp":
            fail(
                "Gazebo ILP execution requires a reviewed rule-artifact contract; use CLI mode",
                422,
            )
        capacity = len(home_bank(warehouse))
        if spec.robots > capacity:
            fail(f"Fleet exceeds map capacity {capacity}", 422)

        output = self.data_dir / "runs" / "gazebo" / job_id
        if output.exists() or output.is_symlink():
            fail("Gazebo destination already exists")
        for parent in (output, *output.parents):
            if parent.is_symlink():
                fail("Output path contains a symlink")

        ros_argv = [
            "ros2",
            "launch",
            "simulation_env",
            "warehouse_sim.launch.py",
            f"config_file:={CONFIG}",
            "orders:=true",
            "start_gazebo:=true",
            f"robots:={spec.robots}",
            f"order_limit:={spec.orders}",
            f"strategy:={spec.strategy}",
            f"dataset:={output}",
            f"headless:={_bool(spec.headless)}",
            f"verbose:={_bool(spec.verbose)}",
            f"progress:={float(spec.progress_interval_s)}",
        ]
        if spec.quantum_s is not None:
            ros_argv.append(f"quantum:={float(spec.quantum_s)}")
        if spec.window is not None:
            ros_argv.append(f"window:={spec.window}")
        if spec.speed_mps is not None:
            ros_argv.append(f"speed:={float(spec.speed_mps)}")

        argv = [
            str(self.python),
            str(self.wrapper),
            "--ros-setup",
            str(self.ros_setup),
            "--workspace-setup",
            str(self.workspace_setup),
            "--config",
            str(CONFIG),
            "--dataset",
            str(output),
            "--robots",
            str(spec.robots),
            "--orders",
            str(spec.orders),
            "--strategy",
            spec.strategy,
            "--progress",
            str(float(spec.progress_interval_s)),
        ]
        if spec.headless:
            argv.append("--headless")
        if spec.verbose:
            argv.append("--verbose")
        if spec.quantum_s is not None:
            argv.extend(["--quantum", str(float(spec.quantum_s))])
        if spec.window is not None:
            argv.extend(["--window", str(spec.window)])
        if spec.speed_mps is not None:
            argv.extend(["--speed", str(float(spec.speed_mps))])

        files = self.inputs()
        payload = dict(
            job_id=job_id,
            kind="gazebo",
            request=spec.model_dump(),
            argv=argv,
            ros_argv=ros_argv,
            cwd=str(REPO),
            output_dir=str(output),
            inputs_hash=_fingerprint(files),
            output_before=None,
            grid=[],
            warnings=[
                "Gazebo mirrors the scientific fleet node; it is not a second planner or allocator.",
                "This is a long-running ROS session. Stop it with the owned dashboard cancel control.",
            ],
            effective=dict(
                scenario_id="W100",
                config=asdict(config),
                capacity=capacity,
                robots=spec.robots,
                orders=spec.orders,
                strategy=spec.strategy,
                quantum_s=config.dispatch_quantum_s
                if spec.quantum_s is None
                else spec.quantum_s,
                window=config.allocation_window if spec.window is None else spec.window,
                speed_mps=config.speed_mps
                if spec.speed_mps is None
                else spec.speed_mps,
                seed=config.seed,
                headless=spec.headless,
                scientific_authority="simulation_env.fleet_node",
            ),
        )
        payload = json.loads(json.dumps(payload, default=str))
        payload["fingerprint"] = _fingerprint(payload)
        return payload
