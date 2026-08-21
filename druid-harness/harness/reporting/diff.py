"""Diff two batch-run JSONL files."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from harness.validator.batch import read_jsonl

DIFF_COLUMNS = [
    "key",
    "id",
    "query",
    "status_a",
    "status_b",
    "error_a",
    "error_b",
]


def record_key(record: dict[str, Any], *, prefer_id: bool) -> str:
    if prefer_id and record.get("id") is not None and str(record.get("id")) != "":
        return f"id:{record['id']}"
    return f"query:{record.get('query') or ''}"


def diff_runs(records_a: list[dict[str, Any]], records_b: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prefer_id = _both_have_ids(records_a) and _both_have_ids(records_b)
    map_a = {record_key(record, prefer_id=prefer_id): record for record in records_a}
    map_b = {record_key(record, prefer_id=prefer_id): record for record in records_b}
    keys = sorted(set(map_a) | set(map_b))
    diffs: list[dict[str, Any]] = []
    for key in keys:
        left = map_a.get(key)
        right = map_b.get(key)
        if left is None:
            diffs.append(_row(key, None, right, missing="a"))
            continue
        if right is None:
            diffs.append(_row(key, left, None, missing="b"))
            continue
        if (left.get("status") != right.get("status")) or (
            (left.get("error_message") or "") != (right.get("error_message") or "")
        ):
            diffs.append(_row(key, left, right, missing=None))
    return diffs


def write_diff_csv(path: str | Path, diffs: list[dict[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DIFF_COLUMNS)
        writer.writeheader()
        for row in diffs:
            writer.writerow(row)


def load_and_diff(path_a: str | Path, path_b: str | Path) -> list[dict[str, Any]]:
    return diff_runs(read_jsonl(path_a), read_jsonl(path_b))


def _both_have_ids(records: list[dict[str, Any]]) -> bool:
    return bool(records) and all(
        record.get("id") is not None and str(record.get("id")) != "" for record in records
    )


def _row(
    key: str,
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
    *,
    missing: str | None,
) -> dict[str, Any]:
    sample = left or right or {}
    status_a = left.get("status") if left else f"(missing in {missing})"
    status_b = right.get("status") if right else f"(missing in {missing})"
    if missing == "a":
        status_a = "(missing in a)"
    if missing == "b":
        status_b = "(missing in b)"
    return {
        "key": key,
        "id": sample.get("id") or "",
        "query": sample.get("query") or "",
        "status_a": status_a or "",
        "status_b": status_b or "",
        "error_a": (left.get("error_message") if left else "") or "",
        "error_b": (right.get("error_message") if right else "") or "",
    }
