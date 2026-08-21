from __future__ import annotations

from pathlib import Path

from harness.loader.ingest import (
    drop_datasource,
    inspect_datasource,
    list_datasources,
    load_datasource,
    reload_datasource,
)
from harness.loader.spec import load_spec

SPECS = Path(__file__).resolve().parents[1] / "specs"


def test_load_inspect_list_five_column_spec(client):
    drop_datasource(client, "example_metrics")
    result = load_datasource(client, SPECS / "example_metrics.json")
    assert result["row_count"] == 20
    names = list_datasources(client)
    assert "example_metrics" in names
    columns = inspect_datasource(client, "example_metrics")
    column_names = {row.get("COLUMN_NAME") or row.get("column_name") for row in columns}
    spec = load_spec(SPECS / "example_metrics.json")
    for col in spec.columns:
        expected = "__time" if col.is_time else col.name
        assert expected in column_names


def test_reload_and_drop(client, loaded_metrics):
    result = reload_datasource(client, SPECS / "example_metrics.json")
    assert result["row_count"] == 20
    drop_datasource(client, loaded_metrics)
    assert loaded_metrics not in list_datasources(client)


def test_load_from_csv_and_json_files(client):
    drop_datasource(client, "example_metrics_csv")
    drop_datasource(client, "example_metrics_json")
    csv_result = load_datasource(client, SPECS / "example_metrics_csv.json")
    json_result = load_datasource(client, SPECS / "example_metrics_json.json")
    assert csv_result["row_count"] == 3
    assert json_result["row_count"] == 3
    drop_datasource(client, "example_metrics_csv")
    drop_datasource(client, "example_metrics_json")
