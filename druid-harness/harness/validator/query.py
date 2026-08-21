"""Execute a single SQL query against the Druid Broker and classify the result."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import requests

from harness import DEFAULT_MAX_ROWS, DEFAULT_TIMEOUT_SECONDS
from harness.client import DruidClient, extract_druid_error

STATUS_VALID = "VALID"
STATUS_INVALID = "INVALID"
STATUS_TIMEOUT = "TIMEOUT"


@dataclass
class QueryResult:
    status: str
    error_message: str | None
    latency_ms: int
    row_count: int | None
    sample: list[dict[str, Any]] = field(default_factory=list)

    def to_record(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "error_message": self.error_message,
            "latency_ms": self.latency_ms,
            "row_count": self.row_count,
        }


def validate_query(
    client: DruidClient,
    query: str,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> QueryResult:
    timeout_ms = max(1, int(timeout_seconds * 1000))
    http_timeout = timeout_seconds + 5
    started = time.perf_counter()
    try:
        resp = client.sql(
            query,
            timeout_ms=timeout_ms,
            http_timeout=http_timeout,
            result_format="object",
        )
    except requests.Timeout:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return QueryResult(
            status=STATUS_TIMEOUT,
            error_message=f"Query exceeded timeout of {timeout_seconds}s",
            latency_ms=latency_ms,
            row_count=None,
        )
    except requests.RequestException as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return QueryResult(
            status=STATUS_INVALID,
            error_message=str(exc),
            latency_ms=latency_ms,
            row_count=None,
        )
    latency_ms = int((time.perf_counter() - started) * 1000)
    if _is_timeout_response(resp):
        return QueryResult(
            status=STATUS_TIMEOUT,
            error_message=_response_error(resp),
            latency_ms=latency_ms,
            row_count=None,
        )
    if not resp.ok:
        return QueryResult(
            status=STATUS_INVALID,
            error_message=_response_error(resp),
            latency_ms=latency_ms,
            row_count=None,
        )
    try:
        rows = resp.json()
    except ValueError:
        return QueryResult(
            status=STATUS_INVALID,
            error_message=resp.text or "Druid returned a non-JSON success body",
            latency_ms=latency_ms,
            row_count=None,
        )
    if not isinstance(rows, list):
        return QueryResult(
            status=STATUS_INVALID,
            error_message=f"Unexpected SQL result payload: {rows!r}",
            latency_ms=latency_ms,
            row_count=None,
        )
    sample = rows[: max(0, max_rows)]
    return QueryResult(
        status=STATUS_VALID,
        error_message=None,
        latency_ms=latency_ms,
        row_count=len(rows),
        sample=sample,
    )


def _response_error(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        text = (resp.text or "").strip()
        return text or f"HTTP {resp.status_code}: {resp.reason}"
    return extract_druid_error(body, fallback=resp.text or f"HTTP {resp.status_code}")


def _is_timeout_response(resp: requests.Response) -> bool:
    message = _response_error(resp).lower()
    if resp.status_code == 504:
        return True
    timeout_tokens = ("timeout", "timed out", "querycanceled", "query cancelled")
    return any(token in message for token in timeout_tokens) and resp.status_code >= 400
