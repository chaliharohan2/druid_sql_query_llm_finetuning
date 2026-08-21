"""Python API for running Druid SQL (same path as `druid-harness validate`)."""

from __future__ import annotations

import os
from typing import Any

from harness import DEFAULT_MAX_ROWS, DEFAULT_ROUTER_URL, DEFAULT_TIMEOUT_SECONDS
from harness.client import DruidClient
from harness.validator.query import QueryResult, validate_query


def run_query(
    query: str,
    *,
    router_url: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_rows: int = DEFAULT_MAX_ROWS,
    client: DruidClient | None = None,
) -> QueryResult:
    """Execute one SQL string against the running Druid cluster.

    Returns a QueryResult with status VALID / INVALID / TIMEOUT, plus
    error_message, latency_ms, row_count, and a truncated sample of rows.
    Does not rewrite the SQL. Requires `make up` first.
    """
    sql = query.strip()
    if not sql:
        raise ValueError("query must be a non-empty SQL string")
    owned_client = client is None
    if client is None:
        url = router_url or os.environ.get("DRUID_ROUTER_URL") or DEFAULT_ROUTER_URL
        client = DruidClient(router_url=url)
    try:
        return validate_query(
            client,
            sql,
            timeout_seconds=timeout_seconds,
            max_rows=max_rows,
        )
    finally:
        if owned_client:
            client.session.close()


def run_queries(
    queries: list[str],
    *,
    router_url: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> list[QueryResult]:
    """Run several SQL strings on one shared client connection."""
    url = router_url or os.environ.get("DRUID_ROUTER_URL") or DEFAULT_ROUTER_URL
    client = DruidClient(router_url=url)
    try:
        return [
            run_query(
                query,
                timeout_seconds=timeout_seconds,
                max_rows=max_rows,
                client=client,
            )
            for query in queries
        ]
    finally:
        client.session.close()


def result_as_dict(result: QueryResult) -> dict[str, Any]:
    payload = result.to_record()
    if result.sample:
        payload["sample"] = result.sample
    return payload
