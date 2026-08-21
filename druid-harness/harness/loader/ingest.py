"""SQL-based ingestion: spec → EXTERN INSERT/REPLACE, poll until queryable."""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any

from harness.client import DruidClient, DruidError, _task_status_code, task_error_message
from harness.loader.generator import generate_rows
from harness.loader.spec import ColumnSpec, DatasourceSpec, load_spec

HARNESS_ROOT = Path(__file__).resolve().parents[2]
HOST_DATA_DIR = HARNESS_ROOT / "data"
CONTAINER_DATA_DIR = "/opt/shared/harness"
MSQ_CONTEXT = {"maxNumTasks": 2}


class LoaderError(DruidError):
    pass


def load_datasource(client: DruidClient, spec_path: str | Path, *, replace: bool = False) -> dict[str, Any]:
    spec = load_spec(spec_path)
    existing = datasource_exists(client, spec.name)
    if existing and not replace:
        raise LoaderError(
            f"Datasource {spec.name!r} already exists. Use reload to replace it, or drop it first."
        )
    seed_file = materialize_seed(spec)
    use_replace = replace or existing
    local_sql = build_ingest_sql(spec, seed_file, replace=use_replace)
    try:
        task_id = _run_ingest_task(client, local_sql)
        source = "local"
    except (DruidError, LoaderError) as local_error:
        rows = _read_jsonl_file(seed_file)
        inline_sql = build_inline_ingest_sql(spec, rows, replace=use_replace)
        try:
            task_id = _run_ingest_task(client, inline_sql)
            source = "inline"
        except (DruidError, LoaderError) as inline_error:
            raise LoaderError(
                f"Ingestion of {spec.name!r} failed via local EXTERN ({local_error}) "
                f"and inline EXTERN ({inline_error})"
            ) from inline_error
    wait_until_queryable(client, spec.name)
    row_count = count_rows(client, spec.name)
    return {
        "name": spec.name,
        "task_id": task_id,
        "row_count": row_count,
        "replaced": bool(use_replace),
        "seed_file": str(seed_file),
        "input_source": source,
    }


def _run_ingest_task(client: DruidClient, sql: str) -> str:
    task_id = client.submit_sql_task(sql, context=MSQ_CONTEXT)
    status = client.wait_for_task(task_id)
    if _task_status_code(status) != "SUCCESS":
        reports = client.task_reports(task_id)
        raise LoaderError(f"task {task_id}: {task_error_message(status, reports)}")
    return task_id


def reload_datasource(client: DruidClient, spec_path: str | Path) -> dict[str, Any]:
    return load_datasource(client, spec_path, replace=True)


def drop_datasource(client: DruidClient, name: str, timeout_sec: float = 60.0) -> None:
    if not datasource_exists(client, name):
        return
    try:
        _run_ingest_task(client, f"DROP TABLE IF EXISTS {sql_ident(name)}")
    except (DruidError, LoaderError):
        try:
            client.mark_unused_overlord(name)
        except DruidError:
            pass
        client.disable_datasource(name)
        try:
            client.kill_datasource(name)
        except DruidError:
            pass
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not datasource_exists(client, name):
            return
        time.sleep(1.0)
    raise LoaderError(f"Datasource {name!r} still present in INFORMATION_SCHEMA after drop")


def list_datasources(client: DruidClient) -> list[str]:
    rows = client.sql_rows(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = 'druid' ORDER BY TABLE_NAME"
    )
    names: list[str] = []
    for row in rows:
        value = row.get("TABLE_NAME") or row.get("table_name")
        if value:
            names.append(str(value))
    return names


def inspect_datasource(client: DruidClient, name: str) -> list[dict[str, Any]]:
    if not datasource_exists(client, name):
        raise LoaderError(f"Datasource {name!r} is not loaded")
    rows = client.sql_rows(
        "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
        f"WHERE TABLE_SCHEMA = 'druid' AND TABLE_NAME = {sql_string(name)} "
        "ORDER BY ORDINAL_POSITION"
    )
    return rows


def datasource_exists(client: DruidClient, name: str) -> bool:
    rows = client.sql_rows(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
        f"WHERE TABLE_SCHEMA = 'druid' AND TABLE_NAME = {sql_string(name)}"
    )
    return bool(rows)


def count_rows(client: DruidClient, name: str) -> int:
    rows = client.sql_rows(f"SELECT COUNT(*) AS c FROM {sql_ident(name)}")
    if not rows:
        return 0
    value = rows[0].get("c") or rows[0].get("EXPR$0") or 0
    return int(value)


def wait_until_queryable(client: DruidClient, name: str, timeout_sec: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_sec
    last_error = "unknown"
    while time.monotonic() < deadline:
        try:
            if datasource_exists(client, name):
                client.sql_rows(f"SELECT 1 FROM {sql_ident(name)} LIMIT 1")
                return
            last_error = "table not yet in INFORMATION_SCHEMA"
        except DruidError as exc:
            last_error = str(exc)
        time.sleep(1.0)
    raise LoaderError(
        f"Datasource {name!r} ingestion succeeded but the table was not queryable: {last_error}"
    )


def materialize_seed(spec: DatasourceSpec) -> Path:
    generated_dir = HOST_DATA_DIR / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    dest = generated_dir / f"{spec.name}.json"
    if spec.seed.mode == "generate":
        rows = generate_rows(spec)
        _write_jsonl(dest, rows)
        return dest
    source = _resolve_seed_path(spec)
    if spec.seed.format == "csv":
        rows = _read_csv(source, spec)
        _write_jsonl(dest, rows)
        return dest
    if spec.seed.format == "json":
        rows = _read_json(source, spec)
        _write_jsonl(dest, rows)
        return dest
    raise LoaderError(f"Unsupported seed format {spec.seed.format!r}")


def _resolve_seed_path(spec: DatasourceSpec) -> Path:
    assert spec.seed.path is not None
    candidate = Path(spec.seed.path)
    if candidate.is_file():
        return candidate.resolve()
    if spec.spec_path is not None:
        relative = (spec.spec_path.parent / spec.seed.path).resolve()
        if relative.is_file():
            return relative
    raise LoaderError(f"Seed file not found: {spec.seed.path}")


def _read_csv(path: Path, spec: DatasourceSpec) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise LoaderError(f"CSV {path} has no header row")
        rows = [dict(row) for row in reader]
    return [_coerce_row(row, spec, origin=str(path)) for row in rows]


def _read_json(path: Path, spec: DatasourceSpec) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise LoaderError(f"JSON seed file {path} is empty")
    if text.startswith("["):
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise LoaderError(f"JSON seed file {path} must be an array or JSONL")
        rows = parsed
    else:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise LoaderError(f"JSON seed file {path} must contain objects")
    return [_coerce_row(row, spec, origin=str(path)) for row in rows]


def _coerce_row(row: dict[str, Any], spec: DatasourceSpec, origin: str) -> dict[str, Any]:
    coerced: dict[str, Any] = {}
    for column in spec.columns:
        if column.name not in row:
            raise LoaderError(f"Seed {origin} is missing column {column.name!r}")
        coerced[column.name] = _coerce_value(row[column.name], column)
    return coerced


def _coerce_value(value: Any, column: ColumnSpec) -> Any:
    if value is None or value == "":
        raise LoaderError(f"Null/empty value for column {column.name!r}")
    if column.type == "string":
        return str(value)
    if column.type == "long":
        return int(value)
    if column.type in {"float", "double"}:
        return float(value)
    return value


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def _read_jsonl_file(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_ingest_sql(spec: DatasourceSpec, seed_file: Path, *, replace: bool) -> str:
    rel = seed_file.resolve().relative_to(HOST_DATA_DIR.resolve())
    container_path = f"{CONTAINER_DATA_DIR}/{rel.as_posix()}"
    input_source = json.dumps(
        {"type": "local", "files": [container_path]},
        separators=(",", ":"),
    )
    return _build_extern_sql(spec, input_source, replace=replace)


def build_inline_ingest_sql(spec: DatasourceSpec, rows: list[dict[str, Any]], *, replace: bool) -> str:
    data = "\n".join(json.dumps(row, separators=(",", ":")) for row in rows)
    input_source = json.dumps({"type": "inline", "data": data}, separators=(",", ":"))
    return _build_extern_sql(spec, input_source, replace=replace)


def _build_extern_sql(spec: DatasourceSpec, input_source: str, *, replace: bool) -> str:
    input_format = json.dumps({"type": "json"}, separators=(",", ":"))
    signature = json.dumps(
        [{"name": col.name, "type": col.type} for col in spec.columns],
        separators=(",", ":"),
    )
    select_list = ",\n  ".join(_select_expr(col) for col in spec.columns)
    verb = (
        f"REPLACE INTO {sql_ident(spec.name)} OVERWRITE ALL"
        if replace
        else f"INSERT INTO {sql_ident(spec.name)}"
    )
    return (
        f"{verb}\n"
        f"SELECT\n  {select_list}\n"
        "FROM TABLE(\n"
        "  EXTERN(\n"
        f"    {sql_string(input_source)},\n"
        f"    {sql_string(input_format)},\n"
        f"    {sql_string(signature)}\n"
        "  )\n"
        ")\n"
        "PARTITIONED BY ALL TIME"
    )


def _select_expr(column: ColumnSpec) -> str:
    ident = sql_ident(column.name)
    if column.is_time:
        if column.type == "string":
            return f"TIME_PARSE({ident}) AS __time"
        return f"MILLIS_TO_TIMESTAMP({ident}) AS __time"
    return ident


def sql_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
