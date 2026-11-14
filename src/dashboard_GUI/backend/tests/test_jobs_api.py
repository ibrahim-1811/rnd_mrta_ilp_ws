import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from test_jobs import FixturePlanner, wait_for


@pytest.fixture
def client(tmp_path):
    from control_center.app import create_app

    root = tmp_path / "history"
    root.mkdir()
    app = create_app(history_root=root, data_dir=tmp_path / "app")
    with TestClient(
        app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 51000)
    ) as c:
        c.app.state.jobs.planner = FixturePlanner(
            tmp_path / "app", 'import time;print("started",flush=True);time.sleep(30)'
        )
        yield c


def auth(c):
    response = c.get("/api/v1/session")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    return {"X-Control-Center-Token": response.json()["data"]["csrf_token"]}


def test_token_origin_and_host_protect_launch_and_preview(client):
    c = client
    body = dict(kind="run", orders=1, robots=1)
    assert c.post("/api/v1/jobs/preview", json=body).status_code == 403
    headers = auth(c)
    assert (
        c.post(
            "/api/v1/jobs/preview",
            json=body,
            headers=headers | {"Origin": "http://evil.example"},
        ).status_code
        == 403
    )
    assert (
        c.post(
            "/api/v1/jobs/preview",
            json=body,
            headers=headers | {"Host": "evil.example"},
        ).status_code
        == 400
    )
    assert (
        c.post(
            "/api/v1/jobs/preview", json=body | {"command": "whoami"}, headers=headers
        ).status_code
        == 422
    )
    assert (
        c.get("/api/v1/capabilities").json()["data"]["simulation_launch"]["implemented"]
        is True
    )


def test_preview_launch_cancel_and_reconnect(client):
    c = client
    headers = auth(c)
    preview = c.post(
        "/api/v1/jobs/preview",
        json=dict(kind="run", robots=1, orders=1),
        headers=headers,
    )
    assert preview.status_code == 200
    pid = preview.json()["data"]["preview_id"]
    assert (
        c.post(
            "/api/v1/experiments", json={"preview_id": pid}, headers=headers
        ).status_code
        == 409
    )
    job = c.post("/api/v1/runs", json={"preview_id": pid}, headers=headers)
    assert job.status_code == 202
    jid = job.json()["data"]["id"]
    assert c.get(f"/api/v1/jobs/{jid}").json()["data"]["status"] == "running"
    snapshot = c.get(f"/api/v1/jobs/{jid}/snapshot").json()["data"]
    assert snapshot["telemetry"]["available"] is False
    assert c.get("/api/v1/jobs").json()["data"][0]["id"] == jid
    with c.websocket_connect(
        f"ws://127.0.0.1:8000/api/v1/ws/jobs/{jid}",
        headers={"Origin": "http://127.0.0.1:8000"},
    ) as ws:
        event = ws.receive_json()
        assert event["type"] == "job_status" and event["sequence"] > 0
    assert c.post(f"/api/v1/jobs/{jid}/cancel", json={}).status_code == 403
    assert (
        c.post(f"/api/v1/jobs/{jid}/cancel", json={}, headers=headers).status_code
        == 200
    )
    final = wait_for(c.app.state.jobs, jid, {"cancelled"})
    assert final["artifact_status"] == "partial"
    assert c.get(f"/api/v1/jobs/{jid}/logs?limit=4").status_code == 200
    assert c.get(f"/api/v1/jobs/{jid}/logs?cursor=-1").status_code == 422
    assert (
        c.post("/api/v1/runs", json={"preview_id": pid}, headers=headers).status_code
        == 409
    )


def test_gazebo_has_an_explicit_launch_route_and_reuses_job_logs(client):
    c = client
    headers = auth(c)
    preview = c.post(
        "/api/v1/jobs/preview",
        json={
            "kind": "gazebo",
            "robots": 2,
            "orders": 3,
            "strategy": "nearest_astar",
            "headless": True,
        },
        headers=headers,
    )
    assert preview.status_code == 200
    pid = preview.json()["data"]["preview_id"]
    assert (
        c.post("/api/v1/runs", json={"preview_id": pid}, headers=headers).status_code
        == 409
    )
    launched = c.post("/api/v1/gazebo", json={"preview_id": pid}, headers=headers)
    assert launched.status_code == 202
    job = launched.json()["data"]
    assert job["kind"] == "gazebo"
    assert c.get(f"/api/v1/jobs/{job['id']}/logs").status_code == 200
    assert (
        c.post(f"/api/v1/jobs/{job['id']}/cancel", json={}, headers=headers).status_code
        == 200
    )
    assert (
        wait_for(c.app.state.jobs, job["id"], {"cancelled"})["owned_process_alive"]
        is False
    )


def test_gazebo_has_an_explicit_launch_route_and_reuses_job_logs(client):
    c = client
    headers = auth(c)
    preview = c.post(
        "/api/v1/jobs/preview",
        json={
            "kind": "gazebo",
            "robots": 2,
            "orders": 3,
            "strategy": "nearest_astar",
            "headless": True,
        },
        headers=headers,
    )
    assert preview.status_code == 200
    pid = preview.json()["data"]["preview_id"]
    assert (
        c.post("/api/v1/runs", json={"preview_id": pid}, headers=headers).status_code
        == 409
    )
    launched = c.post("/api/v1/gazebo", json={"preview_id": pid}, headers=headers)
    assert launched.status_code == 202
    job = launched.json()["data"]
    assert job["kind"] == "gazebo"
    assert c.get(f"/api/v1/jobs/{job['id']}/logs").status_code == 200
    assert (
        c.post(f"/api/v1/jobs/{job['id']}/cancel", json={}, headers=headers).status_code
        == 200
    )
    assert (
        wait_for(c.app.state.jobs, job["id"], {"cancelled"})["owned_process_alive"]
        is False
    )


def test_ws_rejects_missing_session_or_bad_origin(client):
    c = client
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(
            "ws://127.0.0.1:8000/api/v1/ws/jobs/nope",
            headers={"Origin": "http://127.0.0.1:8000"},
        ):
            pass
    auth(c)
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(
            "ws://127.0.0.1:8000/api/v1/ws/jobs/nope",
            headers={"Origin": "http://evil.example"},
        ):
            pass


def test_app_artifacts_remain_separate_from_existing_history(client, tmp_path):
    from test_backend import fixture_run

    c = client
    fixture_run(tmp_path / "app/runs", "tiny")
    assert c.get("/api/v1/runs").json()["data"] == []
    runs = c.get("/api/v1/runs?scope=app").json()["data"]
    assert len(runs) == 1
    rid = runs[0]["id"]
    assert (
        c.get(f"/api/v1/runs/{rid}/metrics?scope=app").json()["data"]["makespan_s"]
        == 12
    )
    assert c.get(f"/api/v1/runs/{rid}/metrics").status_code == 404
    assert c.get("/api/v1/runs?scope=../../etc").status_code == 422


def test_nonfinite_json_input_is_a_validation_error_not_server_error(client):
    c = client
    headers = auth(c) | {"Content-Type": "application/json"}
    response = c.post(
        "/api/v1/jobs/preview",
        content='{"kind":"run","speed_mps":NaN}',
        headers=headers,
    )
    assert response.status_code == 422
