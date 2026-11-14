
import pytest
from fastapi import HTTPException
from pydantic import ValidationError


def fake_setup(tmp_path):
    ros = tmp_path / "opt/ros/jazzy/setup.bash"
    workspace = tmp_path / "install/setup.bash"
    ros.parent.mkdir(parents=True)
    workspace.parent.mkdir(parents=True)
    ros.write_text("# test ros setup")
    workspace.write_text("# test workspace setup")
    return ros, workspace


def test_gazebo_preview_is_fixed_to_the_existing_w100_ros_launch(tmp_path):
    from control_center.adapters.catalogue import CONFIG, REPO
    from control_center.adapters.gazebo import GazeboPlanner
    from control_center.schemas.jobs import GazeboSpec

    ros, workspace = fake_setup(tmp_path)
    plan = GazeboPlanner(
        tmp_path / "app",
        ros_setup=ros,
        workspace_setup=workspace,
    ).build(
        GazeboSpec(
            robots=2,
            orders=3,
            strategy="nearest_astar",
            quantum_s=3,
            window="fleet_size",
            speed_mps=1.5,
            headless=True,
            verbose=True,
            progress_interval_s=5,
        ),
        "gazebo_job",
    )

    assert plan["kind"] == "gazebo"
    assert plan["output_dir"] == str(tmp_path / "app/runs/gazebo/gazebo_job")
    assert plan["argv"][:2] == [
        str(REPO / ".pixi/envs/default/bin/python"),
        str(REPO / "src/dashboard_GUI/scripts/run_gazebo.py"),
    ]
    ros_argv = plan["ros_argv"]
    assert ros_argv[:4] == [
        "ros2",
        "launch",
        "simulation_env",
        "warehouse_sim.launch.py",
    ]
    assert f"config_file:={CONFIG}" in ros_argv
    assert "orders:=true" in ros_argv
    assert "robots:=2" in ros_argv and "order_limit:=3" in ros_argv
    assert "strategy:=nearest_astar" in ros_argv
    assert "quantum:=3.0" in ros_argv and "window:=fleet_size" in ros_argv
    assert "speed:=1.5" in ros_argv and "headless:=true" in ros_argv
    assert "verbose:=true" in ros_argv and "progress:=5.0" in ros_argv
    assert f"dataset:={plan['output_dir']}" in ros_argv
    assert plan["effective"]["scientific_authority"] == "simulation_env.fleet_node"


def test_gazebo_preview_refuses_missing_ros_or_workspace_setup(tmp_path):
    from control_center.adapters.gazebo import GazeboPlanner
    from control_center.schemas.jobs import GazeboSpec

    ros, workspace = fake_setup(tmp_path)
    with pytest.raises(HTTPException, match="ROS 2 Jazzy setup"):
        GazeboPlanner(
            tmp_path / "app", ros_setup=tmp_path / "missing", workspace_setup=workspace
        ).build(GazeboSpec(), "job")
    with pytest.raises(HTTPException, match="workspace is not built"):
        GazeboPlanner(
            tmp_path / "app", ros_setup=ros, workspace_setup=tmp_path / "missing"
        ).build(GazeboSpec(), "job")


@pytest.mark.parametrize(
    "values",
    [
        {"robots": 0},
        {"robots": True},
        {"orders": -1},
        {"quantum_s": -1},
        {"window": 0},
        {"speed_mps": 0},
        {"headless": "yes"},
        {"scenario_id": "../../etc"},
        {"strategy": ""},
    ],
)
def test_gazebo_spec_refuses_unsafe_or_impossible_values(values):
    from control_center.schemas.jobs import GazeboSpec

    with pytest.raises(ValidationError):
        GazeboSpec(**values)


def test_gazebo_wrapper_builds_ros_environment_without_browser_shell_text(
    tmp_path, monkeypatch
):
    import importlib.util
    from control_center.adapters.catalogue import REPO

    path = REPO / "src/dashboard_GUI/scripts/run_gazebo.py"
    spec = importlib.util.spec_from_file_location("dashboard_run_gazebo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ros, workspace = fake_setup(tmp_path)
    seen = {}

    class Done:
        stdout = b"PATH=/opt/ros/jazzy/bin\0SAFE=value\0"

    def run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return Done()

    monkeypatch.setattr(module.subprocess, "run", run)
    env = module.sourced_environment(ros, workspace)
    assert seen["argv"][0:3] == ["/bin/bash", "--noprofile", "--norc"]
    assert str(ros) in seen["argv"] and str(workspace) in seen["argv"]
    assert seen["kwargs"]["shell"] is False
    assert env["PATH"] == "/opt/ros/jazzy/bin" and env["SAFE"] == "value"


def test_gazebo_job_streams_logs_and_cancels_only_its_owned_group(tmp_path):
    from control_center.schemas.jobs import GazeboSpec
    from control_center.services.jobs import JobManager
    from test_jobs import FixturePlanner, wait_for

    planner = FixturePlanner(
        tmp_path, 'import time;print("gazebo-ready",flush=True);time.sleep(30)'
    )
    with JobManager(tmp_path, planner=planner, grace_s=0.1) as manager:
        preview = manager.preview(GazeboSpec())
        job = manager.launch(preview["preview_id"], "gazebo")
        deadline = __import__("time").monotonic() + 3
        while "gazebo-ready" not in manager.logs(job["id"], 0, 65536)["text"]:
            assert __import__("time").monotonic() < deadline
            __import__("time").sleep(0.02)
        assert job["kind"] == "gazebo"
        assert manager.get(job["id"])["owned_process_alive"] is True
        manager.cancel(job["id"])
        final = wait_for(manager, job["id"], {"cancelled"})
        assert final["owned_process_alive"] is False
        assert "gazebo-ready" in manager.logs(job["id"], 0, 65536)["text"]


def test_gazebo_zero_exit_without_a_dataset_is_failed(tmp_path):
    from control_center.schemas.jobs import GazeboSpec
    from control_center.services.jobs import JobManager
    from test_jobs import FixturePlanner, wait_for

    with JobManager(
        tmp_path, planner=FixturePlanner(tmp_path, 'print("gazebo-ended")')
    ) as manager:
        preview = manager.preview(GazeboSpec())
        job = manager.launch(preview["preview_id"], "gazebo")
        final = wait_for(manager, job["id"], {"failed"})
        assert final["exit_code"] == 0
        assert final["artifact_status"] == "incomplete"
