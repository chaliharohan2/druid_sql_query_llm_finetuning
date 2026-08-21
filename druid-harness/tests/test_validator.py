from __future__ import annotations

import json

import pytest
import requests

from harness.reporting.csv_report import write_csv_report
from harness.reporting.diff import diff_runs
from harness.validator.batch import run_batch, write_jsonl
from harness.validator.query import validate_query


def test_valid_group_by(client, loaded_metrics):
    result = validate_query(
        client,
        "SELECT country, COUNT(*) AS c FROM example_metrics GROUP BY country",
    )
    assert result.status == "VALID"
    assert result.row_count is not None and result.row_count >= 1
    assert result.sample


def test_invalid_order_by_nongrouped_column(client, loaded_metrics):
    result = validate_query(
        client,
        "SELECT country FROM example_metrics GROUP BY country ORDER BY clicks",
    )
    assert result.status == "INVALID"
    assert result.error_message
    assert result.row_count is None


def test_negative_mode_on_live_cluster(client, loaded_metrics):
    records = [
        {
            "id": "ok-trap",
            "query": "SELECT country FROM example_metrics GROUP BY country ORDER BY clicks",
            "expected_datasources": ["example_metrics"],
            "expected_status": "INVALID",
            "expected_error_substring": "ORDER BY",
        },
        {
            "id": "wrong-reason",
            "query": "SELECT country FROM example_metrics GROUP BY country ORDER BY clicks",
            "expected_status": "INVALID",
            "expected_error_substring": "this will not match the real error",
        },
        {
            "id": "should-have-failed",
            "query": "SELECT country, COUNT(*) FROM example_metrics GROUP BY country",
            "expected_status": "INVALID",
            "expected_error_pattern": "ORDER BY",
        },
    ]
    results = run_batch(client, records, workers=2)
    by_id = {row["id"]: row for row in results}
    assert by_id["ok-trap"]["status"] == "INVALID"
    assert by_id["ok-trap"]["assertion_passed"] is True
    assert by_id["wrong-reason"]["assertion_passed"] is False
    assert by_id["should-have-failed"]["status"] == "VALID"
    assert by_id["should-have-failed"]["assertion_passed"] is False
    assert by_id["ok-trap"]["expected_datasources"] == ["example_metrics"]


def test_batch_jsonl_csv_and_diff(client, loaded_metrics, tmp_path):
    records = [
        {
            "id": "good",
            "query": "SELECT COUNT(*) FROM example_metrics",
            "tags": ["smoke"],
        },
        {
            "id": "bad",
            "query": "SELECT country FROM example_metrics GROUP BY country ORDER BY clicks",
        },
    ]
    results = run_batch(client, records, workers=2)
    out_a = tmp_path / "run_a.jsonl"
    out_b = tmp_path / "run_b.jsonl"
    report = tmp_path / "report.csv"
    write_jsonl(out_a, results)
    write_csv_report(report, results)
    mutated = json.loads(json.dumps(results))
    mutated[0]["status"] = "INVALID"
    mutated[0]["error_message"] = "forced"
    write_jsonl(out_b, mutated)
    diffs = diff_runs(results, mutated)
    assert any(row["id"] == "good" for row in diffs)
    csv_text = report.read_text(encoding="utf-8")
    assert "status" in csv_text
    assert "good" in csv_text


def test_timeout_classification_on_client_timeout():
    class TimeoutClient:
        def sql(self, *args, **kwargs):
            raise requests.Timeout("simulated")

    result = validate_query(TimeoutClient(), "SELECT 1", timeout_seconds=1)
    assert result.status == "TIMEOUT"
    assert "timeout" in (result.error_message or "").lower()


def test_run_query_python_api_rejects_empty():
    from harness.api import run_query

    with pytest.raises(ValueError, match="non-empty"):
        run_query("   ")


def test_run_query_python_api_uses_supplied_client():
    from harness.api import run_query

    class TimeoutClient:
        session = type("S", (), {"close": staticmethod(lambda: None)})()

        def sql(self, *args, **kwargs):
            raise requests.Timeout("simulated")

    result = run_query("SELECT 1", client=TimeoutClient(), timeout_seconds=1)
    assert result.status == "TIMEOUT"
