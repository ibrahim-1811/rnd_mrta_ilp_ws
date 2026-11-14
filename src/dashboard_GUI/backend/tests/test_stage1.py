"""Stage 1 contract: real readers, no job manager or scientific execution."""

import hashlib
import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from test_backend import fixture_run


def factory():
    from control_center.stage1 import create_app

    return create_app


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "history"
    fixture_run(root)
    app = factory()(history_root=root, data_dir=tmp_path / "metadata")
    with TestClient(
        app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 51000)
    ) as c:
        yield c, root


def test_readonly_health_and_no_mutation_routes(client):
    c, _ = client
    assert c.get("/api/v1/health").json()["data"]["read_only"] is True
    schema = c.get("/openapi.json").json()
    assert all(set(methods) == {"get"} for methods in schema["paths"].values())
    assert not any(
        "jobs" in p or "experiments" in p or "session" in p for p in schema["paths"]
    )
    for path in (
        "/api/v1/runs",
        "/api/v1/jobs/preview",
        "/api/v1/experiments",
        "/api/v1/jobs/x/cancel",
    ):
        assert c.post(path, json={}).status_code == 405
    assert c.get("/api/v1/jobs").status_code == 404


def test_no_job_lifecycle_side_effects(client, tmp_path):
    c, _ = client
    assert not hasattr(c.app.state, "jobs")
    files = {
        str(p.relative_to(tmp_path / "metadata"))
        for p in (tmp_path / "metadata").rglob("*")
    }
    assert files == {"index.sqlite"}


def test_capabilities_report_source_options_without_enabling_launch(client):
    c, _ = client
    data = c.get("/api/v1/capabilities").json()["data"]
    assert data["simulation_launch"]["implemented"] is False
    assert data["experiment_control"]["implemented"] is False
    assert data["telemetry"]["readable"] is False
    assert "--quantum" in data["cli"]["single"]["options"]
    assert "--quantum" in data["cli"]["experiment"]["options"]
    assert "--purpose" in data["cli"]["experiment"]["options"]
    assert "--teacher-panel" in data["cli"]["experiment"]["options"]
    assert "--orders-file" in data["cli"]["single"]["options"]
    assert len(data["cli"]["single"]["sha256"]) == 64


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "localhost.evil.example",
        "localhost@evil.example",
        "127.0.0.1:0",
        "127.0.0.1:99999",
    ],
)
def test_reject_nonlocal_host(client, host):
    c, _ = client
    assert c.get("/api/v1/health", headers={"host": host}).status_code == 400


def test_origin_scope_and_duplicate_authority_validation(client):
    c, _ = client
    assert (
        c.get("/api/v1/health", headers={"origin": "http://127.0.0.1:8000"}).status_code
        == 200
    )
    assert (
        c.get("/api/v1/health", headers={"origin": "http://evil.example"}).status_code
        == 403
    )
    assert (
        c.get(
            "/api/v1/health",
            headers=[("host", "127.0.0.1:8000"), ("host", "evil.example")],
        ).status_code
        == 400
    )
    assert c.get("/api/v1/runs?scope=app").status_code == 422
    assert c.get("/api/v1/runs?scope=history&scope=app").status_code == 422
    assert c.get("/api/v1/runs?scope=history").status_code == 200


def test_reject_nonloopback_peer_even_with_loopback_host(tmp_path):
    app = factory()(history_root=tmp_path / "history", data_dir=tmp_path / "metadata")
    with TestClient(
        app, base_url="http://127.0.0.1:8000", client=("192.0.2.1", 51000)
    ) as c:
        assert c.get("/api/v1/health").status_code == 403


@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_history_readers_preserve_original_bytes_and_types(client, fmt):
    c, root = client
    fixture_run(root, "second", version=1, fmt=fmt)
    before = {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }
    items = c.get("/api/v1/runs").json()["data"]
    item = next(r for r in items if r["directory"] == "second")
    prefix = f"/api/v1/runs/{item['id']}"
    assert c.get(prefix).json()["data"]["manifest"]["format"] == fmt
    metrics = c.get(prefix + "/metrics").json()
    assert metrics["availability"] == "legacy"
    assert metrics["data"]["makespan_s"] == 12
    assert "order_makespan_s" not in metrics["data"]
    page = c.get(prefix + "/decisions?limit=1").json()
    assert page["data"][0]["decision_id"] == 0
    assert page["next_cursor"]
    assert (
        c.get(
            prefix + "/decisions", params={"cursor": page["next_cursor"], "limit": 1}
        ).json()["data"][0]["decision_id"]
        == 1
    )
    assert (
        c.get(prefix + "/decisions?selected=true").json()["data"][0]["selected"] is True
    )
    outcome = c.get(prefix + "/outcomes").json()["data"][0]
    assert outcome["order_id"] == "o1" and "selected" not in outcome
    assert (
        c.get(prefix + "/artifacts/run.json").content
        == (root / "second/run.json").read_bytes()
    )
    assert c.get(prefix + "/artifacts/../../secret").status_code == 404
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}


def test_unknown_dataset_version_is_not_decoded(client):
    c, root = client
    path = root / "sample/run.json"
    data = json.loads(path.read_text())
    data["schema_version"] = 999
    path.write_text(json.dumps(data))
    rid = c.get("/api/v1/runs").json()["data"][0]["id"]
    for kind in ("decisions", "outcomes"):
        response = c.get(f"/api/v1/runs/{rid}/{kind}").json()
        assert response["availability"] == "unsupported" and response["data"] is None


def test_unknown_version_skips_native_decoder_and_keeps_raw_downloads(
    client, monkeypatch
):
    c, root = client
    path = root / "sample/run.json"
    data = json.loads(path.read_text())
    data["schema_version"] = 999
    path.write_text(json.dumps(data))

    def unavailable(*args):
        raise ModuleNotFoundError("Decoder must not run for unknown schema")

    with monkeypatch.context() as patch:
        patch.setattr("control_center.storage.index.rows", unavailable)
        response = c.get("/api/v1/runs")
        assert response.status_code == 200
        rid = response.json()["data"][0]["id"]
        assert (
            c.get(f"/api/v1/runs/{rid}/artifacts/decisions.csv").content
            == (root / "sample/decisions.csv").read_bytes()
        )
    # A corrected manifest must invalidate the unsupported-schema cache even
    # when the table itself has not changed.
    data["schema_version"] = 1
    path.write_text(json.dumps(data))
    assert len(c.get(f"/api/v1/runs/{rid}/decisions").json()["data"]) == 3


def test_validation_and_partial_history(client, tmp_path):
    c, root = client
    rid = c.get("/api/v1/runs").json()["data"][0]["id"]
    prefix = f"/api/v1/runs/{rid}"
    for query in ("limit=0", "limit=1001", "cursor=bad", "decision_id=-1"):
        assert c.get(prefix + "/decisions?" + query).status_code == 422
    page = c.get(prefix + "/decisions?limit=1").json()
    (root / "sample/decisions.csv").touch()
    assert (
        c.get(prefix + "/decisions", params={"cursor": page["next_cursor"]}).status_code
        == 409
    )
    (root / "sample/metrics.json").unlink()
    assert c.get(prefix + "/metrics").json()["availability"] == "missing"
    secret = tmp_path / "secret"
    secret.write_text("private")
    (root / "sample/metrics.json").symlink_to(secret)
    assert c.get(prefix + "/artifacts/metrics.json").status_code == 404
    assert c.get("/api/v1/runs").json()["data"][0]["integrity"] == "incomplete"


def test_stage1_import_does_not_load_job_or_runner_modules(tmp_path):
    module = "control_center.stage1"
    code = """import importlib,sys
from pathlib import Path
from fastapi.testclient import TestClient
module=importlib.import_module(sys.argv[1])
with TestClient(module.create_app(history_root=Path(sys.argv[2])/'history',data_dir=Path(sys.argv[2])/'data'),base_url='http://127.0.0.1:8000',client=('127.0.0.1',50000)) as c:
    assert c.get('/api/v1/health').status_code == 200
    assert not any(k.startswith('control_center.services.jobs') or k.startswith('control_center.api.jobs') or k.endswith('run_workload') or k.endswith('run_experiment') for k in sys.modules)
"""
    done = subprocess.run(
        [sys.executable, "-c", code, module, str(tmp_path)],
        env=dict(os.environ),
        capture_output=True,
        timeout=10,
    )
    assert done.returncode == 0, done.stderr.decode()


def test_missing_table_dependency_returns_unavailable(client, monkeypatch):
    def unavailable(*args):
        raise ModuleNotFoundError("Reader dependency unavailable")

    monkeypatch.setattr("control_center.storage.index.rows", unavailable)
    c, _ = client
    response = c.get("/api/v1/runs")
    assert response.status_code == 503
    assert "dependency" in response.json()["detail"].lower()


def test_parquet_requests_survive_successive_app_lifetimes(tmp_path):
    fixture_run(tmp_path / "history", fmt="parquet")
    code = """import sys
from pathlib import Path
from fastapi.testclient import TestClient
from control_center.stage1 import create_app
root=Path(sys.argv[1])
for i in range(12):
    with TestClient(create_app(history_root=root/'history',data_dir=root/str(i)),base_url='http://127.0.0.1:8000',client=('127.0.0.1',50000)) as c:
        assert c.get('/api/v1/capabilities').status_code == 200
        rid=c.get('/api/v1/runs').json()['data'][0]['id']
        assert len(c.get('/api/v1/runs/'+rid+'/decisions').json()['data']) == 3
        assert len(c.get('/api/v1/runs/'+rid+'/outcomes').json()['data']) == 1
"""
    done = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", code, str(tmp_path)],
        capture_output=True,
        timeout=20,
    )
    assert done.returncode == 0, done.stderr.decode()[-4000:]


def test_launcher_fixes_loopback_and_disables_proxy_headers(monkeypatch):
    import uvicorn
    from control_center.stage1 import main

    called = []
    monkeypatch.setattr(sys, "argv", ["stage1", "--port", "8123"])
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: called.append(options))
    main()
    assert called == [dict(host="127.0.0.1", port=8123, proxy_headers=False, ws="none")]
