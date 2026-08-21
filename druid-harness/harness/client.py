"""HTTP client for Druid Router (SQL, MSQ tasks, metadata)."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import requests

from harness import DEFAULT_ROUTER_URL

DEFAULT_HTTP_TIMEOUT = 60


class DruidError(RuntimeError):
    """A Druid API call failed in a way the harness cannot recover from."""


class DruidClient:
    def __init__(self, router_url: str = DEFAULT_ROUTER_URL, http_timeout: float = DEFAULT_HTTP_TIMEOUT):
        self.router_url = router_url.rstrip("/")
        self.http_timeout = http_timeout
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.router_url}{path}"

    def health(self) -> bool:
        try:
            resp = self.session.get(self._url("/status/health"), timeout=5)
            return resp.ok
        except requests.RequestException:
            return False

    def sql(
        self,
        query: str,
        *,
        timeout_ms: int | None = None,
        http_timeout: float | None = None,
        result_format: str = "object",
        context: dict[str, Any] | None = None,
    ) -> requests.Response:
        payload: dict[str, Any] = {
            "query": query,
            "resultFormat": result_format,
        }
        ctx: dict[str, Any] = dict(context or {})
        if timeout_ms is not None:
            ctx["timeout"] = timeout_ms
        if ctx:
            payload["context"] = ctx
        return self.session.post(
            self._url("/druid/v2/sql"),
            json=payload,
            timeout=http_timeout if http_timeout is not None else self.http_timeout,
        )

    def sql_rows(self, query: str, timeout_ms: int | None = 30_000) -> list[dict[str, Any]]:
        resp = self.sql(query, timeout_ms=timeout_ms)
        if not resp.ok:
            raise DruidError(_error_from_response(resp))
        data = resp.json()
        if not isinstance(data, list):
            raise DruidError(f"Unexpected SQL response: {data!r}")
        return data

    def submit_sql_task(self, query: str, context: dict[str, Any] | None = None) -> str:
        payload: dict[str, Any] = {"query": query}
        if context:
            payload["context"] = context
        resp = self.session.post(
            self._url("/druid/v2/sql/task"),
            json=payload,
            timeout=self.http_timeout,
        )
        if not resp.ok:
            raise DruidError(_error_from_response(resp))
        body = resp.json()
        task_id = body.get("taskId") or body.get("taskid")
        if not task_id:
            raise DruidError(f"SQL task submit did not return taskId: {body!r}")
        return str(task_id)

    def task_status(self, task_id: str) -> dict[str, Any]:
        encoded = quote(task_id, safe="")
        resp = self.session.get(
            self._url(f"/druid/indexer/v1/task/{encoded}/status"),
            timeout=self.http_timeout,
        )
        if not resp.ok:
            raise DruidError(_error_from_response(resp))
        return resp.json()

    def task_reports(self, task_id: str) -> Any:
        encoded = quote(task_id, safe="")
        resp = self.session.get(
            self._url(f"/druid/indexer/v1/task/{encoded}/reports"),
            timeout=self.http_timeout,
        )
        if not resp.ok:
            return {"http_status": resp.status_code, "body": resp.text}
        try:
            return resp.json()
        except ValueError:
            return {"body": resp.text}

    def wait_for_task(self, task_id: str, poll_sec: float = 1.0, timeout_sec: float = 180.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_sec
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.task_status(task_id)
            status = _task_status_code(last)
            if status in {"SUCCESS", "FAILED"}:
                return last
            time.sleep(poll_sec)
        raise DruidError(f"Timed out waiting for task {task_id}; last status={last!r}")

    def disable_datasource(self, name: str) -> Any:
        encoded = quote(name, safe="")
        resp = self.session.delete(
            self._url(f"/druid/coordinator/v1/datasources/{encoded}"),
            timeout=self.http_timeout,
        )
        if resp.status_code not in {200, 202, 204} and not resp.ok:
            raise DruidError(_error_from_response(resp))
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def kill_datasource(self, name: str, interval: str = "1000-01-01_3000-01-01") -> Any:
        encoded = quote(name, safe="")
        encoded_interval = quote(interval, safe="")
        resp = self.session.delete(
            self._url(f"/druid/coordinator/v1/datasources/{encoded}/intervals/{encoded_interval}"),
            timeout=self.http_timeout,
        )
        if resp.status_code not in {200, 202, 204} and not resp.ok:
            raise DruidError(_error_from_response(resp))
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def mark_unused_overlord(self, name: str) -> Any:
        encoded = quote(name, safe="")
        resp = self.session.delete(
            self._url(f"/druid/indexer/v1/datasources/{encoded}"),
            timeout=self.http_timeout,
        )
        if resp.status_code in {404, 405}:
            return None
        if resp.status_code not in {200, 202, 204} and not resp.ok:
            raise DruidError(_error_from_response(resp))
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text


def _task_status_code(payload: dict[str, Any]) -> str:
    status = payload.get("status")
    if isinstance(status, dict):
        return str(status.get("statusCode") or status.get("status") or "").upper()
    if isinstance(status, str):
        return status.upper()
    return str(payload.get("statusCode") or "").upper()


def _error_from_response(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        text = (resp.text or "").strip()
        return f"HTTP {resp.status_code}: {text or resp.reason}"
    return extract_druid_error(body, fallback=f"HTTP {resp.status_code}: {resp.text}")


def extract_druid_error(body: Any, fallback: str = "") -> str:
    if isinstance(body, dict):
        for key in ("errorMessage", "errorMsg", "error"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value
        if "status" in body and isinstance(body["status"], dict):
            nested = extract_druid_error(body["status"], fallback="")
            if nested:
                return nested
    if fallback:
        return fallback
    return str(body)


def task_error_message(status_payload: dict[str, Any], reports: Any) -> str:
    msg = extract_druid_error(status_payload, fallback="")
    if msg:
        return msg
    if isinstance(reports, dict):
        ingestion = reports.get("ingestionStatsAndErrors") or reports.get("multiStageQuery")
        if isinstance(ingestion, dict):
            payload = ingestion.get("payload") or ingestion
            error_msg = payload.get("errorMsg") if isinstance(payload, dict) else None
            if error_msg:
                return str(error_msg)
            error = payload.get("error") if isinstance(payload, dict) else None
            if error:
                return str(error)
    return f"Task failed: {status_payload!r}"
