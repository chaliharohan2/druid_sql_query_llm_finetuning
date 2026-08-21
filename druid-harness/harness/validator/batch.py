"""Batch JSONL validation with bounded parallelism."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from harness import DEFAULT_MAX_ROWS, DEFAULT_TIMEOUT_SECONDS, DEFAULT_WORKERS
from harness.client import DruidClient
from harness.validator.negative import apply_negative_assertion
from harness.validator.query import QueryResult, validate_query


class BatchError(ValueError):
    pass


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BatchError(f"{path}:{line_no}: invalid JSON ({exc})") from exc
            if not isinstance(item, dict):
                raise BatchError(f"{path}:{line_no}: each line must be a JSON object")
            if "query" not in item or not isinstance(item["query"], str) or not item["query"].strip():
                raise BatchError(f"{path}:{line_no}: field 'query' is required")
            records.append(item)
    return records


def write_jsonl(path: str | Path, records: list[dict[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_batch(
    client: DruidClient,
    records: list[dict[str, Any]],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_rows: int = DEFAULT_MAX_ROWS,
    workers: int = DEFAULT_WORKERS,
    validate: Callable[..., QueryResult] = validate_query,
) -> list[dict[str, Any]]:
    if workers < 1:
        raise BatchError("workers must be >= 1")
    results: list[dict[str, Any] | None] = [None] * len(records)

    def _run(index: int, record: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            query_result = validate(
                client,
                record["query"],
                timeout_seconds=timeout_seconds,
                max_rows=max_rows,
            )
        except Exception as exc:  # noqa: BLE001 — one bad query must not kill the batch
            query_result = QueryResult(
                status="INVALID",
                error_message=f"harness error: {exc}",
                latency_ms=0,
                row_count=None,
            )
        merged = dict(record)
        merged.update(query_result.to_record())
        merged.update(apply_negative_assertion(record, query_result))
        return index, merged

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run, index, record) for index, record in enumerate(records)]
        for future in as_completed(futures):
            index, merged = future.result()
            results[index] = merged
    return [item for item in results if item is not None]
