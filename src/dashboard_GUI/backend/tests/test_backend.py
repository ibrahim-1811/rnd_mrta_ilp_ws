import csv
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from simulation_env.dataset import DECISION_COLUMNS, OUTCOME_COLUMNS


def fixture_run(root, name="sample", version=2, fmt="csv"):
    path = root / name
    path.mkdir(parents=True)
    manifest = dict(
        run_id="scientific-id",
        schema_version=1,
        format=fmt,
        decision_rows=3,
        outcome_rows=1,
        orders=1,
        strategy="nearest_astar",
        git={"commit": "abc", "dirty": True},
        config_sha256="c",
        map_sha256="m",
        orders_sha256="o",
    )
    (path / "run.json").write_text(json.dumps(manifest))
    metrics = dict(
        metrics_schema_version=version,
        orders=1,
        orders_completed=1,
        strategy="nearest_astar",
        makespan_s=12,
    )
    if version == 2:
        metrics.update(
            order_makespan_s=10, fleet_recovery_time_s=12, order_throughput_per_h=360
        )
    (path / "metrics.json").write_text(json.dumps(metrics))
    for name, columns, count in [
        ("decisions", DECISION_COLUMNS, 3),
        ("outcomes", OUTCOME_COLUMNS, 1),
    ]:
        rows = []
        for i in range(count):
            row = {k: ("" if t == "string" else None) for k, t in columns}
            row.update(
                run_id="scientific-id", decision_id=i, order_id="o1", robot_id="r1"
            )
            if name == "decisions":
                row["selected"] = i == 1
            rows.append(row)
        if fmt == "parquet":
            import pyarrow as pa
            import pyarrow.parquet as pq
            from simulation_env.dataset import _schema

            pq.write_table(
                pa.Table.from_pylist(rows, schema=_schema(columns)),
                path / f"{name}.parquet",
            )
        else:
            with (path / f"{name}.csv").open("w") as stream:
                writer = csv.DictWriter(stream, fieldnames=[k for k, _ in columns])
                writer.writeheader()
                writer.writerows(rows)
    return path


@pytest.fixture
def client(tmp_path):
    from control_center.app import create_app

    root = tmp_path / "runs"
    root.mkdir()
    fixture_run(root)
    with TestClient(
        create_app(history_root=root, data_dir=tmp_path / "data"),
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 51000),
    ) as c:
        yield c, root


def runs(c):
    return c.get("/api/v1/runs").json()["data"]


def test_startup_and_registry(client):
    c, _ = client
    assert c.get("/api/v1/health").json()["data"]["read_only"] is False
    from task_management.allocation_strategy import BATCH_STRATEGIES

    assert {x["id"] for x in c.get("/api/v1/strategies").json()["data"]} == set(
        BATCH_STRATEGIES
    )
    assert c.get("/openapi.json").status_code == 200
    assert c.post("/api/v1/runs").status_code == 403
    assert (
        c.get("/api/v1/capabilities").json()["data"]["simulation_launch"]["implemented"]
        is True
    )


def test_geometry(client):
    c, _ = client
    from control_center.adapters.catalogue import load_scenario

    config, warehouse = load_scenario()
    data = c.get("/api/v1/scenarios/W100/map").json()["data"]
    assert (data["width"], data["height"]) == (
        warehouse.grid.width,
        warehouse.grid.height,
    )
    assert data["origin"] == list(warehouse.grid.to_world((0, 0)))
    assert data["obstacles"] == [list(x) for x in sorted(warehouse.grid.obstacles)]
    from mrta_core.warehouse_routing import traversable_lane_edges, non_transit_cells

    assert data["directed_edges"] == [
        [list(a), list(b)] for a, b in sorted(traversable_lane_edges(warehouse))
    ]
    assert data["non_transit_cells"] == [
        list(x) for x in sorted(non_transit_cells(warehouse))
    ]
    assert c.get("/api/v1/scenarios").json()["data"][0]["id"] == "W100"
    assert c.get("/api/v1/scenarios/unknown/map").status_code == 404


def test_valid_and_legacy_metrics(client):
    c, root = client
    fixture_run(root, "legacy", version=1)
    items = runs(c)
    assert len(items) == 2
    for item in items:
        assert item["integrity"] == "complete"
        assert item["process_status"] == "unknown"
        result = c.get(f"/api/v1/runs/{item['id']}/metrics").json()
        assert result["data"]["makespan_s"] == 12
        assert result["availability"] in ("current", "legacy")
        if result["availability"] == "legacy":
            assert "order_makespan_s" not in result["data"]


def test_missing_and_partial(client):
    c, root = client
    (root / "sample" / "metrics.json").unlink()
    item = runs(c)[0]
    assert item["integrity"] == "incomplete"
    result = c.get(f"/api/v1/runs/{item['id']}/metrics").json()
    assert result["data"] is None and result["availability"] == "missing"
    (root / "sample" / "run.json").write_text("{")
    assert runs(c)[0]["integrity"] == "incomplete"


@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_pagination_filters_and_drift(client, fmt):
    c, root = client
    fixture_run(root, "paged", fmt=fmt)
    item = next(x for x in runs(c) if x["directory"] == "paged")
    url = f"/api/v1/runs/{item['id']}/decisions"
    page = c.get(url, params={"limit": 1}).json()
    assert page["data"][0]["decision_id"] == 0
    second = c.get(url, params={"limit": 1, "cursor": page["next_cursor"]}).json()
    assert second["data"][0]["decision_id"] == 1
    selected = c.get(url, params={"selected": True}).json()["data"]
    assert len(selected) == 1 and selected[0]["selected"] is True
    assert c.get(url, params={"order_id": "absent"}).json()["data"] == []
    assert c.get(url, params={"cursor": "bad"}).status_code == 422
    assert (
        c.get(url, params={"cursor": page["next_cursor"], "selected": True}).status_code
        == 409
    )
    path = root / "paged" / f"decisions.{fmt}"
    path.touch()
    assert c.get(url, params={"cursor": page["next_cursor"]}).status_code == 409


def test_artifact_safety(client, tmp_path):
    c, root = client
    item = runs(c)[0]
    url = f"/api/v1/runs/{item['id']}/artifacts"
    assert (
        c.get(url + "/run.json").content == (root / "sample" / "run.json").read_bytes()
    )
    assert c.get(url + "/secret.txt").status_code == 404
    assert c.get(url + "/%2e%2e%2fsecret.txt").status_code == 404
    outside = tmp_path / "secret"
    outside.write_text("SECRET")
    (root / "sample" / "metrics.json").unlink()
    (root / "sample" / "metrics.json").symlink_to(outside)
    assert c.get(url + "/metrics.json").status_code == 404
    assert runs(c)[0]["integrity"] == "incomplete"
    (root / "escape").symlink_to(root / "sample", target_is_directory=True)
    assert len(runs(c)) == 1


def test_repeat_index_and_validation(client):
    c, root = client
    before = {
        p: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }
    first = runs(c)
    assert runs(c) == first
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}
    assert after == before
    assert c.get("/api/v1/runs/invalid").status_code == 404
    for params in ({"limit": 0}, {"limit": 1001}, {"cursor": "nonsense"}):
        assert c.get("/api/v1/runs", params=params).status_code == 422
    assert c.get("/api/v1/health", headers={"Host": "evil.example"}).status_code == 400
    assert (
        c.get("/api/v1/health", headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
    assert (
        c.get("/api/v1/health", headers={"Origin": "http://localhost:8000"}).status_code
        == 403
    )


@pytest.mark.parametrize(
    "field,value", [("schema_version", 999), ("decision_rows", True), ("orders", "1")]
)
def test_invalid_manifest_not_complete(client, field, value):
    c, root = client
    path = root / "sample" / "run.json"
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    assert runs(c)[0]["integrity"] == "incomplete"


@pytest.mark.parametrize("value", ["not-a-number", [], True])
def test_invalid_metric_types(client, value):
    c, root = client
    path = root / "sample" / "metrics.json"
    data = json.loads(path.read_text())
    data["order_makespan_s"] = value
    path.write_text(json.dumps(data))
    assert runs(c)[0]["integrity"] == "incomplete"


def test_nonfinite_json(client):
    c, root = client
    (root / "sample" / "metrics.json").write_text('{"makespan_s":1e999}')
    item = runs(c)[0]
    assert item["integrity"] == "incomplete"
    response = c.get(f"/api/v1/runs/{item['id']}/metrics")
    assert response.status_code == 200
    assert response.json()["availability"] == "invalid"


def test_truncated_schema_and_wrong_identity(client):
    c, root = client
    path = root / "sample" / "decisions.csv"
    path.write_text(
        "run_id,decision_id,order_id,robot_id\nwrong,0,o1,r1\nwrong,1,o1,r1\nwrong,2,o1,r1\n"
    )
    assert runs(c)[0]["integrity"] == "incomplete"


def test_failure_log_evidence(client):
    c, root = client
    (root / "sample" / "run.log").write_text("STALLED: runtime error\n")
    item = runs(c)[0]
    assert item["process_status"] == "stalled"
    assert item["scientific_status"] != "completed"


def test_outcomes_and_history_cursor(client):
    c, root = client
    fixture_run(root, "second")
    response = c.get("/api/v1/runs", params={"limit": 1}).json()
    page2 = c.get(
        "/api/v1/runs", params={"limit": 1, "cursor": response["next_cursor"]}
    ).json()
    assert page2["data"][0]["id"] != response["data"][0]["id"]
    rid = response["data"][0]["id"]
    data = c.get(f"/api/v1/runs/{rid}/outcomes").json()["data"]
    assert len(data) == 1 and "selected" not in data[0]
    assert (
        c.get(f"/api/v1/runs/{rid}/outcomes", params={"decision_id": 100}).json()[
            "data"
        ]
        == []
    )


def test_same_scientific_id_distinct_directories(client):
    c, root = client
    fixture_run(root, "copy")
    assert len({r["id"] for r in runs(c)}) == 2


def test_oversized_csv_field_is_artifact_error(client):
    c, root = client
    path = root / "sample" / "decisions.csv"
    lines = path.read_text().splitlines()
    lines[1] = "x" * 140000 + lines[1][len("scientific-id") :]
    path.write_text("\n".join(lines) + "\n")
    item = runs(c)[0]
    assert item["integrity"] == "incomplete"
    response = c.get(f"/api/v1/runs/{item['id']}/decisions")
    assert response.json()["availability"] == "invalid"


def test_unversioned_legacy_metrics(client):
    c, root = client
    path = root / "sample" / "metrics.json"
    path.write_text(json.dumps(dict(orders=1, orders_completed=1, makespan_s=12)))
    item = runs(c)[0]
    assert item["metrics_schema"] == "legacy"
    values = c.get(f"/api/v1/runs/{item['id']}/metrics").json()
    assert "metrics_schema_version" not in values["data"]


def test_changed_read_is_conflict(tmp_path):
    from fastapi import HTTPException
    from control_center.storage.files import open_source

    path = tmp_path / "run.json"
    path.write_text("{}")
    with pytest.raises(HTTPException) as error:
        with open_source(tmp_path, "run.json") as (handle, _):
            handle.read()
            path.write_text('{"changed":true}')
    assert error.value.status_code == 409


def test_outcome_count_mismatch(client):
    c, root = client
    path = root / "sample" / "run.json"
    manifest = json.loads(path.read_text())
    manifest["outcome_rows"] = 0
    path.write_text(json.dumps(manifest))
    outcome = root / "sample" / "outcomes.csv"
    outcome.write_text(outcome.read_text().splitlines()[0] + "\n")
    assert runs(c)[0]["integrity"] == "incomplete"


def test_legacy_manifest_without_orders_stays_readable(client):
    c, root = client
    path = root / "sample" / "run.json"
    manifest = json.loads(path.read_text())
    del manifest["orders"]
    path.write_text(json.dumps(manifest))
    item = runs(c)[0]
    assert item["integrity"] == "incomplete"
    assert any("expected order count" in w for w in item["warnings"])
    assert len(c.get(f"/api/v1/runs/{item['id']}/decisions").json()["data"]) == 3
