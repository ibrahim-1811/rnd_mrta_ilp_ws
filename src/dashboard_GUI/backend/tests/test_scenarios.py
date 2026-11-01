import hashlib
from pathlib import Path

import pytest
from fastapi import HTTPException


REPO = Path(__file__).resolve().parents[4]
W100_CONFIG = REPO / "src/simulation_env/config/warehouse.yaml"
W100_MAP = REPO / "src/simulation_env/maps/W100.xlsx"
W100_ORDERS = REPO / "src/simulation_env/config/orders.csv"


def write_registry(path, entries):
    import yaml

    path.write_text(yaml.safe_dump({"version": 1, "scenarios": entries}, sort_keys=False))
    return path


def select_registry(monkeypatch, tmp_path, entries):
    from control_center.adapters import scenarios as registry

    path = write_registry(tmp_path / "scenarios.yaml", entries)
    monkeypatch.setattr(registry, "REGISTRY", path)
    return registry


def copied_config(tmp_path, name="scenario.yaml", *, map_path=W100_MAP, orders_path=W100_ORDERS):
    text = W100_CONFIG.read_text()
    text = text.replace("map: W100.xlsx", f"map: {map_path}", 1)
    text = text.replace("file: orders.csv", f"file: {orders_path}", 1)
    path = tmp_path / name
    path.write_text(text)
    return path


def entry(scenario_id, config, *, reference=False, label=None):
    return {
        "id": scenario_id,
        "label": label or scenario_id,
        "config": str(config),
        "reference": reference,
        "provenance": "test-fixture",
    }


def test_registry_lists_two_valid_entries_with_independent_provenance(monkeypatch, tmp_path):
    registry = select_registry(
        monkeypatch,
        tmp_path,
        [entry("W100", W100_CONFIG, reference=True, label="Scientific W100"), entry("second", W100_CONFIG)],
    )

    records = registry.scenarios()

    assert [record["id"] for record in records] == ["W100", "second"]
    assert records[0]["label"] == "Scientific W100"
    assert records[0]["reference"] is True
    assert records[1]["reference"] is False
    for record in records:
        assert record["valid"] is True
        assert record["validation"] == {"errors": [], "warnings": []}
        assert (record["width"], record["height"], record["capacity"]) == (40, 54, 100)
        assert record["hashes"] == {
            "config": hashlib.sha256(W100_CONFIG.read_bytes()).hexdigest(),
            "map": hashlib.sha256(W100_MAP.read_bytes()).hexdigest(),
            "orders": hashlib.sha256(W100_ORDERS.read_bytes()).hexdigest(),
        }
        assert all(len(value) == 64 for value in record["hashes"].values())
        assert len(record["identity"]) == 64
        assert record["settings"]["config_path"] == str(W100_CONFIG)
    assert records[0]["identity"] != records[1]["identity"]


def test_invalid_entry_stays_visible_with_map_checker_diagnostics(monkeypatch, tmp_path):
    from mrta_core.map_builder import blank_layout, write_layout_xlsx

    bad_map = write_layout_xlsx(blank_layout(5, 5), tmp_path / "invalid.xlsx")
    bad_config = copied_config(tmp_path, "invalid.yaml", map_path=bad_map)
    registry = select_registry(
        monkeypatch,
        tmp_path,
        [entry("W100", W100_CONFIG, reference=True), entry("invalid", bad_config)],
    )

    records = registry.scenarios()

    assert records[0]["valid"] is True
    invalid = records[1]
    assert invalid["valid"] is False
    assert invalid["width"] == 5 and invalid["height"] == 5
    assert invalid["capacity"] == 0
    assert any("missing station" in message and "parking" in message for message in invalid["validation"]["errors"])
    with pytest.raises(registry.InvalidScenario):
        registry.load_scenario("invalid")


def test_malformed_yaml_entry_does_not_hide_valid_w100(monkeypatch, tmp_path):
    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("warehouse: [\n")
    registry = select_registry(
        monkeypatch,
        tmp_path,
        [entry("W100", W100_CONFIG, reference=True), entry("malformed", malformed)],
    )

    records = registry.scenarios()

    assert [record["id"] for record in records] == ["W100", "malformed"]
    assert records[0]["valid"] is True
    assert records[1]["valid"] is False
    assert any("ParserError" in message for message in records[1]["validation"]["errors"])
    assert registry.load_scenario("W100")[0].config_path == W100_CONFIG
    with pytest.raises(registry.InvalidScenario, match="ParserError"):
        registry.load_scenario("malformed")


def test_unsupported_workbook_entry_does_not_hide_valid_w100(monkeypatch, tmp_path):
    workbook = tmp_path / "not-a-workbook.txt"
    workbook.write_text("not an XLSX workbook")
    config = copied_config(tmp_path, "bad-workbook.yaml", map_path=workbook)
    registry = select_registry(
        monkeypatch,
        tmp_path,
        [entry("W100", W100_CONFIG, reference=True), entry("bad-workbook", config)],
    )

    records = registry.scenarios()

    assert records[0]["valid"] is True
    assert records[1]["valid"] is False
    assert any("InvalidFileException" in message for message in records[1]["validation"]["errors"])
    with pytest.raises(registry.InvalidScenario, match="InvalidFileException"):
        registry.load_scenario("bad-workbook")


@pytest.mark.parametrize("scenario_id", ["unknown", "../W100", "/tmp/map", "W100/config", "", "space id"])
def test_unknown_or_path_like_ids_are_refused(monkeypatch, tmp_path, scenario_id):
    registry = select_registry(monkeypatch, tmp_path, [entry("W100", W100_CONFIG, reference=True)])
    error = registry.UnknownScenario if scenario_id == "unknown" else registry.UnsafeScenarioId
    with pytest.raises(error):
        registry.load_scenario(scenario_id)


def test_planner_reports_an_unknown_safe_id_as_invalid_input(tmp_path):
    from control_center.adapters.commands import CommandPlanner
    from control_center.schemas.jobs import RunSpec

    with pytest.raises(HTTPException) as caught:
        CommandPlanner(tmp_path).build(RunSpec(scenario_id="unregistered"), "job")

    assert caught.value.status_code == 422
    assert "Unknown scenario" in caught.value.detail


def test_selected_config_capacity_and_identity_enter_single_run_plan(monkeypatch, tmp_path):
    from mrta_core.map_builder import example_layout, write_layout_xlsx
    from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
    from mrta_core.warehouse_map import ZoneType

    small_map = write_layout_xlsx(example_layout(2), tmp_path / "small.xlsx")
    warehouse = load_warehouse_xlsx(small_map)
    shelf = next(zone.zone_id for zone in warehouse.zones if zone.zone_type == ZoneType.SHELF)
    dropoff = next(zone.zone_id for zone in warehouse.zones if zone.zone_type == ZoneType.DROPOFF)
    orders = tmp_path / "orders.csv"
    orders.write_text(
        "order_id,item_id,shelf_id,quantity,dropoff_id\n"
        f"o1,item1,{shelf},1,{dropoff}\n"
    )
    small_config = copied_config(tmp_path, "small.yaml", map_path=small_map, orders_path=orders)
    registry = select_registry(
        monkeypatch,
        tmp_path,
        [entry("W100", W100_CONFIG, reference=True), entry("small", small_config)],
    )
    from control_center.adapters.commands import CommandPlanner
    from control_center.schemas.jobs import RunSpec

    plan = CommandPlanner(tmp_path / "app").build(RunSpec(scenario_id="small", robots=2, orders=1), "job")

    assert plan["argv"][plan["argv"].index("--config") + 1] == str(small_config)
    assert plan["effective"]["scenario_id"] == "small"
    assert plan["effective"]["capacity"] == 2
    assert plan["effective"]["scenario_identity"] == registry.scenario("small")["identity"]
    assert plan["effective"]["scenario_hashes"] == registry.scenario("small")["hashes"]
    with pytest.raises(HTTPException, match="capacity 2"):
        CommandPlanner(tmp_path / "other").build(RunSpec(scenario_id="small", robots=3), "job")


def test_w100_single_run_argv_and_matrix_scope_remain_compatible(tmp_path):
    from control_center.adapters.commands import CommandPlanner
    from control_center.schemas.jobs import ExperimentSpec, RunSpec

    planner = CommandPlanner(tmp_path)
    run = planner.build(RunSpec(scenario_id="W100", robots=2, orders=3), "run")
    matrix = planner.build(ExperimentSpec(name="matrix", robots=[2], seeds=[1]), "matrix")

    assert run["argv"][run["argv"].index("--config") + 1] == str(W100_CONFIG)
    assert run["effective"]["scenario_id"] == "W100"
    assert "--config" not in matrix["argv"]
    assert matrix["effective"]["scenario_id"] == "W100"
    assert "scenario_id" not in matrix["request"]


def test_csv_validation_uses_the_selected_scenario(monkeypatch, tmp_path):
    from mrta_core.map_builder import example_layout, write_layout_xlsx
    from mrta_core.warehouse_map_xlsx import load_warehouse_xlsx
    from mrta_core.warehouse_map import ZoneType
    from control_center.adapters.inputs import InputStore

    small_map = write_layout_xlsx(example_layout(2, units=50), tmp_path / "small.xlsx")
    warehouse = load_warehouse_xlsx(small_map)
    shelf = [zone.zone_id for zone in warehouse.zones if zone.zone_type == ZoneType.SHELF][-1]
    dropoff = next(zone.zone_id for zone in warehouse.zones if zone.zone_type == ZoneType.DROPOFF)
    small_config = copied_config(tmp_path, "small.yaml", map_path=small_map)
    select_registry(monkeypatch, tmp_path, [entry("W100", W100_CONFIG, reference=True), entry("small", small_config)])
    csv_text = (
        "order_id,item_id,shelf_id,quantity,dropoff_id\n"
        f"o1,item1,{shelf},1,{dropoff}\n"
    )

    accepted = InputStore(tmp_path / "store").add(csv_text, scenario_id="small")

    assert accepted["scenario_id"] == "small"
    with pytest.raises(HTTPException):
        InputStore(tmp_path / "other").add(csv_text, scenario_id="W100")


def test_config_byte_drift_after_preview_is_rejected(monkeypatch, tmp_path):
    from control_center.schemas.jobs import RunSpec
    from control_center.services.jobs import JobManager

    config = copied_config(tmp_path)
    select_registry(monkeypatch, tmp_path, [entry("mutable", config, reference=True)])
    with JobManager(tmp_path / "app") as manager:
        preview = manager.preview(RunSpec(scenario_id="mutable", robots=1, orders=1))
        config.write_text(config.read_text() + "\n# source drift after preview\n")
        with pytest.raises(HTTPException, match="changed after preview"):
            manager.launch(preview["preview_id"], "run")


def test_shipped_registry_serves_w100_reference_and_my_map_at_100_robots():
    """The tracked registry: W100 stays the reference, my_map sits beside it."""

    from control_center.adapters import scenarios as registry

    records = {record["id"]: record for record in registry.scenarios()}

    assert list(records)[:2] == ["W100", "my_map"]
    assert [record["id"] for record in records.values() if record["reference"]] == ["W100"]

    for record in records.values():
        assert record["valid"], record["validation"]["errors"]

    assert records["W100"]["capacity"] >= 100
    assert records["my_map"]["capacity"] >= 100

    assert (records["W100"]["width"], records["W100"]["height"]) == (40, 54)
    assert (records["my_map"]["width"], records["my_map"]["height"]) == (79, 104)
    assert records["my_map"]["settings"]["gazebo_world_name"] == "my_map"

    geometry = registry.geometry("my_map")

    assert (geometry["width"], geometry["height"]) == (79, 104)
    assert geometry["directed_edges"]


def test_my_map_single_run_plan_uses_its_own_config_at_100_robots(tmp_path):
    from control_center.adapters.commands import CommandPlanner
    from control_center.schemas.jobs import RunSpec

    plan = CommandPlanner(tmp_path).build(
        RunSpec(scenario_id="my_map", robots=100, orders=10, seed=7), "job"
    )
    config = plan["argv"][plan["argv"].index("--config") + 1]

    assert config == str(REPO / "src/simulation_env/config/my_map.yaml")
    assert plan["argv"][plan["argv"].index("--robots") + 1] == "100"
    assert plan["effective"]["capacity"] == 200
