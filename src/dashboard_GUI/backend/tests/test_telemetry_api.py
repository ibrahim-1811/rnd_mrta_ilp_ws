"""Recorded-state transport uses genuine copied frames; no runner launches."""

import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from test_backend import fixture_run
from test_observation import executor_fixture


@pytest.fixture
def observed(tmp_path):
    from control_center.app import create_app
    from control_center.schemas.jobs import RunSpec
    from control_center.services.observation import TraceRecorder

    app = create_app(history_root=tmp_path / "history", data_dir=tmp_path / "app")
    with TestClient(
        app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 51000)
    ) as client:
        manager = app.state.jobs
        plan = manager.preview(RunSpec(execution_mode="observed", robots=2, orders=3))
        folder = tmp_path / "app/jobs" / plan["job_id"]
        folder.mkdir(parents=True)
        path = folder / "telemetry.ndjson"
        executor, _ = executor_fixture()
        recorder = TraceRecorder(
            path, plan["job_id"], plan["inputs_hash"], executor=executor
        )
        recorder.observer.capture(force=True)
        recorder.close(complete=True)
        job = dict(
            id=plan["job_id"],
            kind="run",
            status="interrupted",
            sequence=0,
            created_at="now",
            started_at=None,
            process_identity=None,
            plan=plan,
            log_path=str(folder / "process.log"),
            output_dir=plan["output_dir"],
            artifact_status="unknown",
            exit_code=None,
        )
        manager.store.save(job)
        yield client, plan["job_id"], path, tmp_path


def test_latest_snapshot_and_exact_time_are_persisted_in_global_events(observed):
    client, jid, _, _ = observed
    data = client.get(f"/api/v1/jobs/{jid}/snapshot").json()["data"]
    assert data["telemetry"]["available"] is True
    assert data["snapshot"]["source"] == "executor_observer"
    assert data["snapshot"]["sim_time_exact"] == "0"
    assert len(data["snapshot"]["payload"]["robots"]) == 2
    assert data["snapshot"]["payload"]["orders"]["total"] == 3
    events = client.app.state.jobs.events(jid, 0)["events"]
    samples = [e for e in events if e["type"] == "fleet_snapshot"]
    assert len(samples) == 2 and samples[0]["sequence"] < samples[1]["sequence"]
    assert samples[0]["sample_sequence"] < samples[1]["sample_sequence"]
    assert samples[0]["sim_time_exact"] == samples[1]["sim_time_exact"] == "0"
    assert samples[-1]["sequence"] == data["snapshot"]["sequence"]
    assert (
        client.get(f"/api/v1/jobs/{jid}/snapshot").json()["data"]["sequence"]
        == data["sequence"]
    )


def test_recorded_pages_are_bounded_and_preserve_stream_generation(observed):
    client, jid, _, _ = observed
    prefix = f"/api/v1/jobs/{jid}/telemetry"
    page = client.get(prefix, params={"limit": 1}).json()
    assert len(page["data"]["records"]) == 1
    assert page["data"]["records"][0]["type"] == "telemetry_header"
    next_page = client.get(
        prefix, params={"limit": 1, "cursor": page["data"]["next_cursor"]}
    ).json()
    assert next_page["data"]["records"][0]["type"] == "fleet_snapshot"
    assert (
        next_page["data"]["records"][0]["stream_generation"]
        == page["data"]["records"][0]["stream_generation"]
    )
    assert client.get(prefix + "?limit=101").status_code == 422
    assert client.get(prefix + "?cursor=-1").status_code == 422
    assert client.get(prefix + "?cursor=99999999").status_code == 409


def test_trailing_partial_record_is_pending_then_readable(observed):
    client, jid, path, _ = observed
    lines = path.read_bytes().splitlines(keepends=True)
    cut = len(lines[1]) // 2
    path.write_bytes(lines[0] + lines[1][:cut])
    first = client.get(f"/api/v1/jobs/{jid}/snapshot").json()["data"]
    assert first["telemetry"]["available"] is False
    assert first.get("snapshot") is None
    with path.open("ab") as handle:
        handle.write(lines[1][cut:])
    second = client.get(f"/api/v1/jobs/{jid}/snapshot").json()["data"]
    assert second["telemetry"]["available"] is True
    assert second["telemetry"]["recording_complete"] is False


@pytest.mark.parametrize(
    "corruption", ["schema", "identity", "time", "nan", "timestamp", "footer"]
)
def test_bad_trace_is_unavailable_without_changing_scientific_job_status(
    observed, corruption
):
    client, jid, path, _ = observed
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if corruption == "schema":
        rows[0]["schema_version"] = 999
    elif corruption == "identity":
        rows[0]["job_id"] = "other-job"
    elif corruption == "time":
        rows[1]["sim_time_exact"] = "1/0"
    elif corruption == "nan":
        rows[1]["payload"]["robots"][0]["distance_m"] = float("nan")
    elif corruption == "timestamp":
        rows[1]["observed_at_utc"] = "not-a-date"
    else:
        rows[-1]["payload"]["scientific_run_returned"] = False
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    response = client.get(f"/api/v1/jobs/{jid}/snapshot")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "interrupted"
    assert data["telemetry"]["available"] is False
    assert data["telemetry"]["availability"] == "invalid"


def test_sample_loss_is_a_gap_and_never_invented_motion(observed):
    client, jid, path, _ = observed
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[2]["sample_sequence"] = 3
    rows[2]["payload"]["dropped_samples_total"] = 1
    rows[-1]["payload"]["dropped_samples_total"] = 1
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    data = client.get(f"/api/v1/jobs/{jid}/snapshot").json()["data"]
    assert data["telemetry"]["availability"] == "partial"
    assert any(
        e["type"] == "telemetry_gap"
        for e in client.app.state.jobs.events(jid, 0)["events"]
    )
    assert data["snapshot"]["sample_sequence"] == 3


def test_websocket_carries_real_snapshot_envelope(observed):
    client, jid, _, _ = observed
    client.get("/api/v1/session")
    with client.websocket_connect(
        f"ws://127.0.0.1:8000/api/v1/ws/jobs/{jid}",
        headers={"Origin": "http://127.0.0.1:8000"},
    ) as ws:
        event = ws.receive_json()
        assert event["type"] == "fleet_snapshot"
        assert event["source"] == "executor_observer" and event["sim_time_exact"] == "0"


def test_legacy_history_has_no_fabricated_recording(observed):
    client, _, _, root = observed
    fixture_run(root / "history")
    rid = client.get("/api/v1/runs").json()["data"][0]["id"]
    response = client.get(f"/api/v1/runs/{rid}/telemetry").json()
    assert response["availability"] == "unavailable" and response["data"] is None


def test_persisted_snapshot_and_event_cursor_survive_restart(observed):
    client, jid, _, root = observed
    data = client.get(f"/api/v1/jobs/{jid}/snapshot").json()["data"]
    client.app.state.jobs.close()
    from control_center.services.jobs import JobManager

    with JobManager(root / "app") as restarted:
        loaded = restarted.get(jid)
        assert loaded["snapshot"] == data["snapshot"]
        assert loaded["sequence"] == data["sequence"]
        assert restarted.events(jid, loaded["sequence"])["events"] == []


def test_truncation_after_ingestion_makes_previous_snapshot_unverified(observed):
    client, jid, path, _ = observed
    assert (
        client.get(f"/api/v1/jobs/{jid}/snapshot").json()["data"]["telemetry"][
            "available"
        ]
        is True
    )
    path.write_text("")
    data = client.get(f"/api/v1/jobs/{jid}/snapshot").json()["data"]
    assert data["telemetry"]["available"] is False
    assert data["telemetry"]["availability"] == "invalid"
    assert data["status"] == "interrupted"


def test_missing_decoder_dependency_cannot_fail_job_or_snapshot_endpoint(
    observed, monkeypatch
):
    client, jid, _, _ = observed

    def broken(*args, **kwargs):
        raise ImportError("injected observation dependency failure")

    monkeypatch.setattr("control_center.services.telemetry.validate", broken)
    response = client.get(f"/api/v1/jobs/{jid}/snapshot")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "interrupted"
    assert response.json()["data"]["telemetry"]["available"] is False


@pytest.mark.parametrize("extra", ["snapshot", "partial"])
def test_bytes_after_end_marker_invalidate_recording(observed, extra):
    client, jid, path, _ = observed
    assert client.get(f"/api/v1/jobs/{jid}/snapshot").json()["data"]["telemetry"][
        "recording_complete"
    ]
    if extra == "snapshot":
        record = json.loads(path.read_text().splitlines()[1])
        record["sample_sequence"] = 3
        addition = json.dumps(record) + "\n"
    else:
        addition = '{"unfinished":'
    with path.open("a") as handle:
        handle.write(addition)
    data = client.get(f"/api/v1/jobs/{jid}/snapshot").json()["data"]
    assert data["telemetry"]["recording_complete"] is False
    assert data["telemetry"]["availability"] == "invalid"
    assert data["telemetry"]["available"] is False


def test_transient_event_failure_retries_uncommitted_sample(observed, monkeypatch):
    client, jid, _, _ = observed
    store = client.app.state.jobs.store
    original = store.event
    failed = False

    def once(job, kind, *args, **kwargs):
        nonlocal failed
        if kind == "fleet_snapshot" and not failed:
            failed = True
            raise OSError("transient telemetry event failure")
        return original(job, kind, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "event", once)
        assert client.get(f"/api/v1/jobs/{jid}/snapshot").status_code == 200
    data = client.get(f"/api/v1/jobs/{jid}/snapshot").json()["data"]
    samples = [
        e
        for e in client.app.state.jobs.events(jid, 0)["events"]
        if e["type"] == "fleet_snapshot"
    ]
    assert [e["sample_sequence"] for e in samples] == [1, 2]
    assert data["telemetry"]["recording_complete"] is True


def test_history_telemetry_lookup_is_not_limited_to_first_100_jobs(observed):
    client, jid, _, root = observed
    manager = client.app.state.jobs
    output = Path(manager.store.get(jid)["output_dir"])
    fixture_run(output.parent, output.name)
    for i in range(101):
        manager.store.save(
            dict(
                id=f"newer-{i}",
                status="failed",
                sequence=0,
                created_at="now",
                plan={"execution_mode": "cli"},
                output_dir=str(root / "other" / str(i)),
            )
        )
    rid = client.get("/api/v1/runs?scope=app").json()["data"][0]["id"]
    response = client.get(f"/api/v1/runs/{rid}/telemetry?scope=app").json()
    assert response["availability"] == "available"
    assert response["data"]["records"][0]["job_id"] == jid


@pytest.mark.parametrize("failure", ["event_store", "relay"])
def test_persistent_optional_relay_failure_never_stops_owned_child(
    tmp_path, monkeypatch, failure
):
    from control_center.services.jobs import JobManager
    from control_center.schemas.jobs import RunSpec
    from control_center.services.observation import TraceRecorder
    from control_center.services.processes import signal_group
    from test_jobs import FixturePlanner

    planner = FixturePlanner(
        tmp_path, 'import time;print("alive",flush=True);time.sleep(30)'
    )
    build = planner.build
    planner.build = lambda *args: dict(build(*args), execution_mode="observed")
    with JobManager(tmp_path, planner=planner, grace_s=0.1) as manager:
        job = manager.launch(manager.preview(RunSpec())["preview_id"], "run")
        try:
            with monkeypatch.context() as patch:
                original = manager.store.event

                def broken_event(job, kind, *args, **kwargs):
                    if kind in ("fleet_snapshot", "telemetry_gap"):
                        raise OSError("persistent telemetry write failure")
                    return original(job, kind, *args, **kwargs)

                if failure == "event_store":
                    patch.setattr(manager.store, "event", broken_event)
                else:

                    def broken_relay(*args):
                        raise RuntimeError("optional relay crashed")

                    patch.setattr(
                        "control_center.services.telemetry.ingest", broken_relay
                    )
                executor, _ = executor_fixture()
                trace = TraceRecorder(
                    tmp_path / "jobs" / job["id"] / "telemetry.ndjson",
                    job["id"],
                    "one",
                    executor=executor,
                )
                trace.observer.capture(force=True)
                trace.close(complete=True)
                time.sleep(0.15)
                current = manager.get(job["id"])
                assert current["status"] == "running"
                assert current["owned_process_alive"] is True
                assert current["telemetry"]["available"] is False
        finally:
            signal_group(job["process_identity"], force=True)
