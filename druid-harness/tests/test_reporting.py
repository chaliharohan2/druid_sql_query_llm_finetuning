from __future__ import annotations

from harness.reporting.csv_report import write_csv_report
from harness.reporting.diff import diff_runs
from harness.reporting.summary import summarize
from harness.validator.negative import apply_negative_assertion
from harness.validator.query import QueryResult


def test_summary_counts_and_error_patterns():
    records = [
        {"status": "VALID", "error_message": None, "query": "q1"},
        {"status": "INVALID", "error_message": "Unknown column 'clicks'\nmore", "query": "q2"},
        {"status": "INVALID", "error_message": "Unknown column 'clicks'", "query": "q3"},
        {"status": "TIMEOUT", "error_message": "Query exceeded timeout of 30s", "query": "q4"},
    ]
    stats = summarize(records)
    assert stats["total"] == 4
    assert stats["valid"] == 1
    assert stats["invalid"] == 2
    assert stats["timeout"] == 1
    patterns = dict(stats["error_patterns"])
    assert patterns["Unknown column 'clicks'"] == 2
    assert patterns["(timeout)"] == 1


def test_csv_report_groups_by_error(tmp_path):
    records = [
        {"id": "b", "status": "VALID", "query": "SELECT 1", "latency_ms": 1, "row_count": 1},
        {
            "id": "a",
            "status": "INVALID",
            "error_message": "bad ORDER BY",
            "query": "SELECT x",
            "latency_ms": 2,
            "row_count": None,
        },
    ]
    path = tmp_path / "report.csv"
    write_csv_report(path, records)
    text = path.read_text(encoding="utf-8")
    lines = text.strip().splitlines()
    assert lines[0].startswith("error_pattern")
    # INVALID error pattern sorts before "(valid)"
    assert "bad ORDER BY" in lines[1]
    assert "(valid)" in lines[2]


def test_diff_detects_status_change():
    a = [{"id": "1", "query": "SELECT 1", "status": "VALID", "error_message": None}]
    b = [{"id": "1", "query": "SELECT 1", "status": "INVALID", "error_message": "nope"}]
    diffs = diff_runs(a, b)
    assert len(diffs) == 1
    assert diffs[0]["status_a"] == "VALID"
    assert diffs[0]["status_b"] == "INVALID"


def test_negative_assertion_match_and_mismatch():
    result = QueryResult(
        status="INVALID",
        error_message="Column [clicks] must be aggregated or in GROUP BY",
        latency_ms=5,
        row_count=None,
    )
    ok = apply_negative_assertion(
        {
            "expected_status": "INVALID",
            "expected_error_substring": "GROUP BY",
        },
        result,
    )
    assert ok["assertion_passed"] is True

    miss = apply_negative_assertion(
        {
            "expected_status": "INVALID",
            "expected_error_substring": "syntax error",
        },
        result,
    )
    assert miss["assertion_passed"] is False

    valid = apply_negative_assertion(
        {"expected_status": "INVALID", "expected_error_pattern": "GROUP"},
        QueryResult(status="VALID", error_message=None, latency_ms=1, row_count=1),
    )
    assert valid["assertion_passed"] is False
    assert "VALID" in valid["assertion_detail"]
