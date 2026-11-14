"""Launcher lifecycle fixtures; no scientific runs in these tests."""

import importlib.util
from pathlib import Path
import socket
import subprocess
import sys
import time
from unittest.mock import patch
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/launch_control_center.py"


def module():
    spec = importlib.util.spec_from_file_location("dashboard_launcher", SCRIPT)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_busy_port_is_rejected_without_touching_listener():
    app = module()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        with pytest.raises(RuntimeError, match="occupied"):
            app.check_ports([port])
        assert sock.getsockname()[1] == port


def test_missing_dependencies_report_actionable_errors(tmp_path):
    app = module()
    with (
        patch.object(app, "ROOT", tmp_path),
        patch.object(app.shutil, "which", return_value=None),
    ):
        with pytest.raises(RuntimeError, match="Node|backend|dependency"):
            app.paths()


def test_gazebo_preflight_requires_ros_and_built_workspace_setups(tmp_path):
    app = module()
    ros = tmp_path / "opt/ros/jazzy/setup.bash"
    workspace = tmp_path / "install/setup.bash"
    with pytest.raises(RuntimeError, match="ROS 2 Jazzy"):
        app.gazebo_readiness(ros_setup=ros, workspace_setup=workspace)
    ros.parent.mkdir(parents=True)
    ros.write_text("# ros")
    with pytest.raises(RuntimeError, match="colcon build"):
        app.gazebo_readiness(ros_setup=ros, workspace_setup=workspace)
    workspace.parent.mkdir(parents=True)
    workspace.write_text("# workspace")
    result = app.gazebo_readiness(ros_setup=ros, workspace_setup=workspace)
    assert result["ros_setup"] == ros and result["workspace_setup"] == workspace


def test_wait_ready_times_out_and_detects_early_exit():
    app = module()
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    with pytest.raises(RuntimeError, match="exited"):
        app.wait_ready([("fixture", child)], "http://127.0.0.1:1/", timeout=0.2)
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(10)"])
    try:
        with pytest.raises(RuntimeError, match="Timed out"):
            app.wait_ready([("fixture", child)], "http://127.0.0.1:1/", timeout=0.1)
    finally:
        child.terminate()
        child.wait()


def test_failure_or_shutdown_cleans_only_owned_children():
    app = module()
    unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(10)"])
    owned = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(10)"])
    exited = subprocess.Popen([sys.executable, "-c", "pass"])
    exited.wait()
    try:
        app.shutdown([("owned", owned), ("exited", exited)], timeout=0.2)
        assert owned.poll() is not None
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait()


def test_partial_startup_failure_stops_both_children(tmp_path):
    app = module()
    created = []
    original_spawn = app.spawn

    def fixture_spawn(argv, cwd, log):
        process = original_spawn(
            [sys.executable, "-c", "import time;time.sleep(20)"], tmp_path, log
        )
        created.append(process)
        return process

    runtime = dict(
        front=tmp_path, python=sys.executable, node=sys.executable, npm="unused"
    )
    with (
        patch.object(app, "check_ports"),
        patch.object(app, "logs_directory", return_value=tmp_path),
        patch.object(app, "spawn", side_effect=fixture_spawn),
        patch.object(
            app, "wait_ready", side_effect=RuntimeError("fixture readiness failed")
        ),
    ):
        with pytest.raises(RuntimeError, match="readiness failed"):
            app.start(runtime, dev=True)
    assert len(created) == 2 and all(p.poll() is not None for p in created)


def test_exit_after_readiness_stops_remaining_service(tmp_path):
    app = module()
    created = []
    original_spawn = app.spawn

    def fixture_spawn(argv, cwd, log):
        code = "pass" if not created else "import time;time.sleep(20)"
        process = original_spawn([sys.executable, "-c", code], tmp_path, log)
        created.append(process)
        return process

    runtime = dict(
        front=tmp_path, python=sys.executable, node=sys.executable, npm="unused"
    )
    with (
        patch.object(app, "check_ports"),
        patch.object(app, "logs_directory", return_value=tmp_path),
        patch.object(app, "spawn", side_effect=fixture_spawn),
        patch.object(app, "wait_ready"),
    ):
        with pytest.raises(RuntimeError, match="exited"):
            app.start(runtime, dev=True)
    assert len(created) == 2 and all(p.poll() is not None for p in created)


def test_owned_descendant_is_stopped_after_leader_exit(tmp_path):
    app = module()
    pid_file = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)']);open("
        + repr(str(pid_file))
        + ",'w').write(str(p.pid));time.sleep(.1)"
    )
    with (tmp_path / "fixture.log").open("wb") as log:
        leader = app.spawn([sys.executable, "-c", code], tmp_path, log)
        try:
            leader.wait(timeout=3)
            pid = int(pid_file.read_text())
            app.shutdown([("fixture", leader)], timeout=0.2)
            entry = Path(f"/proc/{pid}/stat")
            assert not entry.exists() or entry.read_text().split(") ")[1][0] == "Z"
        finally:
            app.shutdown([("fixture", leader)], timeout=0.1)


def test_repeated_interrupt_cannot_abandon_cleanup(tmp_path):
    import os
    import signal
    import threading

    app = module()
    ready = tmp_path / "ready"
    code = (
        "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);open("
        + repr(str(ready))
        + ",'w').write('ready');time.sleep(20)"
    )
    with (tmp_path / "slow.log").open("wb") as log:
        process = app.spawn([sys.executable, "-c", code], tmp_path, log)
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        before = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        timer = threading.Timer(0.05, lambda: os.kill(os.getpid(), signal.SIGINT))
        timer.start()
        try:
            try:
                app.shutdown([("slow", process)], timeout=0.2)
            except KeyboardInterrupt:
                pytest.fail("Repeated interrupt abandoned cleanup")
            assert process.poll() is not None
            assert signal.getsignal(signal.SIGINT) == signal.default_int_handler
        finally:
            timer.cancel()
            timer.join()
            signal.signal(signal.SIGINT, before)
            app.shutdown([("slow", process)], timeout=0.1)


def test_verify_includes_real_local_http_startup_gate(tmp_path):
    app = module()
    steps = []
    runtime = dict(
        front=tmp_path, python=sys.executable, node=sys.executable, npm="npm"
    )
    with (
        patch.object(app, "logs_directory", return_value=tmp_path),
        patch.object(app, "check_ports"),
        patch.object(
            app, "run_step", side_effect=lambda name, *a, **k: steps.append(name)
        ),
    ):
        assert app.verify(runtime) == 0
    assert "Local HTTP integration" in steps
