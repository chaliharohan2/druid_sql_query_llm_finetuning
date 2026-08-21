"""CSV export of batch results, grouped by error type."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from harness.reporting.summary import normalize_error

CSV_COLUMNS = [
    "error_pattern",
    "id",
    "status",
    "error_message",
    "latency_ms",
    "row_count",
    "assertion_passed",
    "expected_datasources",
    "tags",
    "query",
]


def write_csv_report(path: str | Path, records: list[dict[str, Any]]) -> None:
    def sort_key(record: dict[str, Any]) -> tuple:
        status = str(record.get("status") or "")
        # Errors first so a curator can scan invalid queries without scrolling.
        valid_rank = 1 if status == "VALID" else 0
        return (
            valid_rank,
            normalize_error(record.get("error_message"), status),
            str(record.get("id") or ""),
            str(record.get("query") or ""),
        )

    ordered = sorted(records, key=sort_key)
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for record in ordered:
            writer.writerow(
                {
                    "error_pattern": normalize_error(
                        record.get("error_message"), str(record.get("status") or "")
                    ),
                    "id": record.get("id") or "",
                    "status": record.get("status") or "",
                    "error_message": record.get("error_message") or "",
                    "latency_ms": record.get("latency_ms") if record.get("latency_ms") is not None else "",
                    "row_count": record.get("row_count") if record.get("row_count") is not None else "",
                    "assertion_passed": (
                        record.get("assertion_passed") if "assertion_passed" in record else ""
                    ),
                    "expected_datasources": _jsonish(record.get("expected_datasources")),
                    "tags": _jsonish(record.get("tags")),
                    "query": record.get("query") or "",
                }
            )


def _jsonish(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
