"""Regressions for the Stage 2 review; fixtures never run a simulation."""

from dataclasses import replace
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient
from httpx import Headers
import pytest
from starlette.websockets import WebSocketDisconnect

from control_center.adapters.catalogue import REPO, load_scenario
from control_center.adapters.commands import CommandPlanner
from control_center.schemas.jobs import ExperimentSpec, RunSpec
from control_center.services.jobs import JobManager
from test_backend import fixture_run


def synthetic_output(path, *, strategy="nearest_astar", robots=1, seed=7):
    """Use original manifest construction, with hand-built tiny recorded tables."""
    from mrta_core.orders import load_orders_csv, select_orders
    from simulation_env.dataset import build_manifest

    config, warehouse = load_scenario()
    available = load_orders_csv(config.orders_path, warehouse)
    from task_management.allocation_strategy import get_batch_strategy

    manifest = build_manifest(
        replace(config, robot_count=robots),
        strategy=strategy,
        strategy_identity=get_batch_strategy(strategy, seed=seed).identity(),
        window=config.allocation_window,
        window_width=robots,
        seed=seed,
        speed_mps=config.speed_mps,
        dispatch_quantum_s=config.dispatch_quantum_s,
        orders=select_orders(available, 1),
        order_limit=1,
        orders_available=len(available),
        repo=REPO,
    )
    fixture_run(path.parent, path.name)
    manifest.update(schema_version=1, format="csv", decision_rows=3, outcome_rows=1)
    (path / "run.json").write_text(json.dumps(manifest))
    for kind in ("decisions", "outcomes"):
        table = path / f"{kind}.csv"
        table.write_text(table.read_text().replace("scientific-id", manifest["run_id"]))
    return manifest


def owned_reference(root, *, old_source=False):
    planner = CommandPlanner(root)
    spec = ExperimentSpec(
        name="reference", workloads="reference", robots=[1], seeds=[7], orders=1
    )
    plan = planner.build(spec, "preview")
    output = Path(plan["output_dir"])
    output.mkdir(parents=True)
    (output / "experiment.json").write_text(
        json.dumps({"spec": plan["effective"]["spec"], "runs": plan["grid"]})
    )
    owner = root / "experiment_owners/reference.json"
    owner.parent.mkdir()
    owner.write_text(
        json.dumps(
            {
                "inputs_hash": "OLD-SOURCE" if old_source else plan["inputs_hash"],
                "job_id": "original",
            }
        )
    )
    return planner, spec, output, owner


@pytest.mark.parametrize(
    "field,value",
    [
        ("strategy", "random"),
        ("robot_count", 100),
        ("seed", 987),
        ("config_sha256", "wrong"),
        ("map_sha256", "wrong"),
        ("orders_sha256", "wrong"),
        ("speed_mps", 99),
    ],
)
def test_completed_artifacts_must_match_requested_inputs(tmp_path, field, value):
    with JobManager(tmp_path / "app") as manager:
        plan = manager.preview(RunSpec(robots=1, orders=1, seed=7))
        output = Path(plan["output_dir"])
        manifest = synthetic_output(output)
        manifest[field] = value
        (output / "run.json").write_text(json.dumps(manifest))
        job = dict(id=plan["job_id"], kind="run", output_dir=str(output), plan=plan)
        with pytest.raises(ValueError, match=field):
            manager._validate(job)


def test_matching_artifacts_still_validate_without_scientific_execution(tmp_path):
    with JobManager(tmp_path / "app") as manager:
        plan = manager.preview(RunSpec(robots=1, orders=1, seed=7))
        synthetic_output(Path(plan["output_dir"]))
        status, ids = manager._validate(
            dict(
                id=plan["job_id"], kind="run", output_dir=plan["output_dir"], plan=plan
            )
        )
        assert status == "complete" and len(ids) == 1


@pytest.mark.parametrize("corrupt", ["missing_table", "invalid_manifest"])
def test_resume_refuses_artifacts_that_original_runner_would_skip(tmp_path, corrupt):
    planner, spec, output, _ = owned_reference(tmp_path / "app")
    run = output / "nearest_astar/r1_s7"
    synthetic_output(run)
    if corrupt == "missing_table":
        (run / "outcomes.csv").unlink()
    else:
        (run / "run.json").write_text("{}")
    before = {p: p.read_bytes() for p in (tmp_path / "app").rglob("*") if p.is_file()}
    with pytest.raises(HTTPException) as error:
        planner.build(spec.model_copy(update={"mode": "resume"}), "resume")
    assert error.value.status_code == 409
    assert "artifact" in error.value.detail.lower()
    assert before == {p: p.read_bytes() for p in before}
    assert not list((tmp_path / "app").rglob("index.sqlite"))


def test_failed_force_does_not_replace_old_ownership(tmp_path, monkeypatch):
    planner, spec, output, owner = owned_reference(tmp_path / "app", old_source=True)
    previous = owner.read_bytes()
    marker = output / "old-evidence.txt"
    marker.write_text("original evidence")
    with JobManager(tmp_path / "app", planner=planner) as manager:
        plan = manager.preview(spec.model_copy(update={"mode": "force"}))

        def cannot_spawn(*args, **kwargs):
            raise OSError("injected spawn failure")

        monkeypatch.setattr(
            "control_center.services.jobs.subprocess",
            SimpleNamespace(Popen=cannot_spawn, STDOUT=subprocess.STDOUT),
        )
        job = manager.launch(plan["preview_id"], "experiment")
        assert job["status"] == "failed"
        assert owner.read_bytes() == previous
        assert marker.read_text() == "original evidence"
        assert (
            Path(job["log_path"]).parent / "previous_attempt/old-evidence.txt"
        ).read_text() == "original evidence"
        with pytest.raises(HTTPException) as error:
            planner.build(spec.model_copy(update={"mode": "resume"}), "resume")
        assert error.value.status_code == 409


def test_resume_refuses_replaced_output_generation(tmp_path):
    planner, spec, output, owner = owned_reference(tmp_path / "app")
    record = json.loads(owner.read_text())
    stat = output.stat()
    record["output_identity"] = [stat.st_dev, stat.st_ino]
    owner.write_text(json.dumps(record))
    saved = output.with_name("original-kept")
    output.rename(saved)
    output.mkdir()
    (output / "experiment.json").write_bytes((saved / "experiment.json").read_bytes())
    with pytest.raises(HTTPException) as error:
        planner.build(spec.model_copy(update={"mode": "resume"}), "resume")
    assert error.value.status_code == 409
    assert "generation" in error.value.detail.lower()


@pytest.fixture
def api(tmp_path):
    from control_center.app import create_app

    with TestClient(
        create_app(history_root=tmp_path / "history", data_dir=tmp_path / "app"),
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 51000),
    ) as client:
        yield client


def test_duplicate_host_or_origin_cannot_reach_session(api):
    assert (
        api.get(
            "/api/v1/session",
            headers=[("host", "127.0.0.1:8000"), ("host", "evil.example")],
        ).status_code
        == 400
    )
    assert (
        api.get(
            "/api/v1/session",
            headers=[
                ("origin", "http://127.0.0.1:8000"),
                ("origin", "http://evil.example"),
            ],
        ).status_code
        == 403
    )


def test_non_ascii_token_is_forbidden_without_server_error(api):
    response = api.post(
        "/api/v1/jobs/preview",
        content='{"kind":"run"}',
        headers=[
            (b"content-type", b"application/json"),
            (b"X-Control-Center-Token", b"\xff"),
        ],
    )
    assert response.status_code == 403


def test_remote_peer_cannot_obtain_session_with_spoofed_local_host(tmp_path):
    from control_center.app import create_app

    with TestClient(
        create_app(history_root=tmp_path / "history", data_dir=tmp_path / "app"),
        base_url="http://127.0.0.1:8000",
        client=("192.0.2.1", 51000),
    ) as client:
        assert client.get("/api/v1/session").status_code == 403


def test_websocket_rejects_duplicate_host_even_with_valid_cookie(api):
    api.get("/api/v1/session")
    api.app.state.jobs.store.save(
        dict(id="recorded", status="failed", sequence=0, created_at="now")
    )
    with pytest.raises(WebSocketDisconnect) as error:
        with api.websocket_connect(
            "ws://127.0.0.1:8000/api/v1/ws/jobs/recorded",
            headers=Headers(
                [
                    ("host", "127.0.0.1:8000"),
                    ("host", "evil.example"),
                    ("origin", "http://127.0.0.1:8000"),
                ]
            ),
        ) as ws:
            ws.receive_json()
    assert error.value.code == 1008
