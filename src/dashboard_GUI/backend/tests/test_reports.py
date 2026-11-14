import json
import pytest
from test_research import experiment


def test_report_is_categorical_and_keeps_original_sources(tmp_path):
    from control_center.adapters.experiments import ExperimentReader
    from control_center.reports import render

    source = tmp_path / "source"
    experiment(source)
    reader = ExperimentReader(source)
    eid = reader.list()[0]["id"]
    before = {str(p): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    out = tmp_path / "report"
    data = render(reader, eid, out)
    assert data["expected_runs"] == 4 and data["present_runs"] == 4
    assert (out / "index.html").is_file() and (
        out / "r2/order_makespan_s.png"
    ).is_file()
    assert "min–max" in (out / "index.html").read_text()
    assert before == {str(p): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    with pytest.raises(FileExistsError):
        render(reader, eid, out)


def test_incomplete_report_refuses_without_creating_output(tmp_path):
    from control_center.adapters.experiments import ExperimentReader
    from control_center.reports import render
    from fastapi import HTTPException

    source = tmp_path / "source"
    base = experiment(source)
    (base / "random/r2_s1/metrics.json").unlink()
    reader = ExperimentReader(source)
    eid = reader.list()[0]["id"]
    out = tmp_path / "report"
    with pytest.raises(HTTPException):
        render(reader, eid, out)
    assert not out.exists()


def test_report_discloses_metric_specific_sample_counts(tmp_path):
    from control_center.adapters.experiments import ExperimentReader
    from control_center.reports import render

    source = tmp_path / "source"
    base = experiment(source)
    path = base / "nearest_astar/r2_s1/metrics.json"
    values = json.loads(path.read_text())
    values["energy_consumed_percent"] = 8
    path.write_text(json.dumps(values))
    reader = ExperimentReader(source)
    out = tmp_path / "report"
    render(reader, reader.list()[0]["id"], out)
    data = json.loads((out / "data.json").read_text())
    assert data["metric_samples"]["2"]["energy_consumed_percent"]["nearest_astar"] == {
        "actual": 1,
        "expected": 2,
    }
    assert "n=1/2" in (out / "index.html").read_text()
