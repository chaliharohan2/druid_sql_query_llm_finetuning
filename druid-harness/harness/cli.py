"""CLI entry point: druid-harness."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from harness import (
    DEFAULT_MAX_ROWS,
    DEFAULT_ROUTER_URL,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_WORKERS,
)
from harness.client import DruidClient, DruidError
from harness.loader.ingest import (
    drop_datasource,
    inspect_datasource,
    list_datasources,
    load_datasource,
    reload_datasource,
)
from harness.reporting.csv_report import write_csv_report
from harness.reporting.diff import load_and_diff, write_diff_csv
from harness.reporting.summary import print_summary
from harness.validator.batch import read_jsonl, run_batch, write_jsonl
from harness.validator.query import validate_query


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    router_url = args.router_url or os.environ.get("DRUID_ROUTER_URL") or DEFAULT_ROUTER_URL
    client = DruidClient(router_url=router_url)
    try:
        return args.func(client, args)
    except (DruidError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="druid-harness",
        description="Load datasources into Druid 35.0.0 and validate SQL queries.",
    )
    parser.add_argument(
        "--router-url",
        default=None,
        help=f"Druid Router base URL (default: {DEFAULT_ROUTER_URL} or DRUID_ROUTER_URL)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    load_p = sub.add_parser("load", help="Ingest a datasource spec (fails if it already exists)")
    load_p.add_argument("spec", help="Path to a JSON datasource spec")
    load_p.set_defaults(func=_cmd_load)

    reload_p = sub.add_parser("reload", help="REPLACE OVERWRITE ALL from a datasource spec")
    reload_p.add_argument("spec", help="Path to a JSON datasource spec")
    reload_p.set_defaults(func=_cmd_reload)

    drop_p = sub.add_parser("drop", help="Drop a single datasource")
    drop_p.add_argument("name", help="Datasource name")
    drop_p.set_defaults(func=_cmd_drop)

    list_p = sub.add_parser("list", help="List loaded datasources")
    list_p.set_defaults(func=_cmd_list)

    inspect_p = sub.add_parser("inspect", help="Show INFORMATION_SCHEMA columns for a datasource")
    inspect_p.add_argument("name", help="Datasource name")
    inspect_p.set_defaults(func=_cmd_inspect)

    validate_p = sub.add_parser("validate", help="Run a single SQL query")
    src = validate_p.add_mutually_exclusive_group(required=True)
    src.add_argument("--query", help="SQL string")
    src.add_argument("--file", dest="query_file", help="File containing a SQL string")
    _add_query_flags(validate_p)
    validate_p.set_defaults(func=_cmd_validate)

    batch_p = sub.add_parser("validate-batch", help="Validate a JSONL file of queries")
    batch_p.add_argument("input", help="Input JSONL")
    batch_p.add_argument("--output", required=True, help="Output JSONL")
    batch_p.add_argument("--report", help="Optional CSV report path")
    batch_p.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Parallel Broker workers (default {DEFAULT_WORKERS})",
    )
    _add_query_flags(batch_p)
    batch_p.set_defaults(func=_cmd_validate_batch)

    diff_p = sub.add_parser("diff", help="Diff two batch-run JSONL files")
    diff_p.add_argument("run_a", help="First JSONL")
    diff_p.add_argument("run_b", help="Second JSONL")
    diff_p.add_argument("--output", required=True, help="CSV of differences")
    diff_p.set_defaults(func=_cmd_diff)

    return parser


def _add_query_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-query timeout (default {DEFAULT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=DEFAULT_MAX_ROWS,
        help=f"Max result sample rows (default {DEFAULT_MAX_ROWS})",
    )


def _cmd_load(client: DruidClient, args: argparse.Namespace) -> int:
    result = load_datasource(client, args.spec, replace=False)
    print(
        f"Loaded {result['name']} ({result['row_count']} rows, task {result['task_id']})"
    )
    return 0


def _cmd_reload(client: DruidClient, args: argparse.Namespace) -> int:
    result = reload_datasource(client, args.spec)
    print(
        f"Reloaded {result['name']} ({result['row_count']} rows, task {result['task_id']})"
    )
    return 0


def _cmd_drop(client: DruidClient, args: argparse.Namespace) -> int:
    drop_datasource(client, args.name)
    print(f"Dropped {args.name}")
    return 0


def _cmd_list(client: DruidClient, args: argparse.Namespace) -> int:
    names = list_datasources(client)
    if not names:
        print("(no datasources)")
        return 0
    for name in names:
        print(name)
    return 0


def _cmd_inspect(client: DruidClient, args: argparse.Namespace) -> int:
    rows = inspect_datasource(client, args.name)
    print(json.dumps(rows, indent=2))
    return 0


def _cmd_validate(client: DruidClient, args: argparse.Namespace) -> int:
    query = args.query
    if args.query_file:
        query = Path(args.query_file).read_text(encoding="utf-8")
    result = validate_query(
        client,
        query,
        timeout_seconds=args.timeout_seconds,
        max_rows=args.max_rows,
    )
    payload: dict[str, Any] = result.to_record()
    if result.sample:
        payload["sample"] = result.sample
    print(json.dumps(payload, indent=2, default=str))
    return 0 if result.status == "VALID" else 1


def _cmd_validate_batch(client: DruidClient, args: argparse.Namespace) -> int:
    records = read_jsonl(args.input)
    results = run_batch(
        client,
        records,
        timeout_seconds=args.timeout_seconds,
        max_rows=args.max_rows,
        workers=args.workers,
    )
    write_jsonl(args.output, results)
    if args.report:
        write_csv_report(args.report, results)
        print(f"Wrote CSV report {args.report}")
    print_summary(results)
    print(f"Wrote {args.output} ({len(results)} records)")
    return 0


def _cmd_diff(client: DruidClient, args: argparse.Namespace) -> int:
    diffs = load_and_diff(args.run_a, args.run_b)
    write_diff_csv(args.output, diffs)
    print(f"Wrote {args.output} ({len(diffs)} differing records)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
