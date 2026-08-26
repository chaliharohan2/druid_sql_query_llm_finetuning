#!/usr/bin/env python3
"""Execute every generated example against the live Druid 35.0.0 cluster.

Four gates, all of which must pass before an example reaches the SFT file:

  EXEC  the completion returns VALID
  ROWS  it returns at least one row (unless the example is marked otherwise)
  LINT  it contains the construct its cluster exists to teach
  TRAP  the naive standard-SQL version fails, or returns something different

VALID alone is not enough, which is the whole reason ROWS and TRAP exist: a
query like JSON_VALUE(json_string_col, '$.k') is accepted by Druid and returns
NULL for every row.

Usage:
  validate_bulk.py            run every gate, write validation_report.json
  validate_bulk.py --split train
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "druid-harness"))

from harness.api import run_query  # noqa: E402
from harness.client import DruidClient  # noqa: E402

WORKERS = 6
_local = threading.local()


def client() -> DruidClient:
    if not hasattr(_local, "c"):
        _local.c = DruidClient()
    return _local.c


def check(e: dict) -> dict:
    c = client()
    fails: list[str] = []
    r = run_query(e["sql"], client=c, timeout_seconds=60)
    if r.status != "VALID":
        return {"id": e["id"], "fails": [f"EXEC {r.status}: {(r.error_message or '')[:220]}"]}
    if e["gates"]["expect_rows"] and not r.row_count:
        fails.append("ROWS returned 0 rows")
    for token in e["gates"]["must_contain"]:
        if token not in e["sql"]:
            fails.append(f"LINT missing {token!r}")
    trap = e.get("trap")
    trap_error = None
    if trap:
        n = run_query(trap["naive_sql"], client=c, timeout_seconds=60)
        trap_error = (n.error_message or "")[:220]
        if trap["expect"] == "INVALID" and n.status != "INVALID":
            fails.append(f"TRAP naive SQL was {n.status}, expected INVALID")
        elif trap["expect"] == "DIFFERENT":
            if n.status != "VALID":
                fails.append(f"TRAP naive SQL was {n.status}, expected VALID-but-wrong")
            elif n.sample == r.sample:
                fails.append("TRAP naive SQL produced identical results")
    return {"id": e["id"], "fails": fails, "row_count": r.row_count,
            "trap_error": trap_error}


def run(examples: list[dict], label: str) -> dict:
    t0 = time.time()
    done = 0
    results = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for res in pool.map(check, examples):
            results[res["id"]] = res
            done += 1
            if done % 100 == 0:
                print(f"  {label}: {done}/{len(examples)}  {time.time() - t0:.0f}s", flush=True)
    bad = {k: v for k, v in results.items() if v["fails"]}
    print(f"{label}: {len(examples) - len(bad)}/{len(examples)} passed "
          f"in {time.time() - t0:.0f}s")
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["train", "val", "both"], default="both")
    args = ap.parse_args()

    data = json.loads((ROOT / "examples.json").read_text())
    if not DruidClient().health():
        print("Druid is not up. Run `make up` in druid-harness/.", file=sys.stderr)
        return 1

    report = {}
    for split in (["train", "val"] if args.split == "both" else [args.split]):
        report[split] = run(data[split], split)

    (ROOT / "validation_report.json").write_text(json.dumps(report, indent=1) + "\n")

    # Failure summary, grouped so a broken template shows up as one line.
    by_template = {}
    for split, res in report.items():
        for e in data[split]:
            r = res.get(e["id"])
            if r and r["fails"]:
                by_template.setdefault(e["template"], []).append((e["id"], r["fails"][0]))
    if by_template:
        print(f"\n{sum(len(v) for v in by_template.values())} failures "
              f"across {len(by_template)} templates:")
        for t, rows in sorted(by_template.items(), key=lambda kv: -len(kv[1])):
            print(f"  {t:24} {len(rows):4d}  e.g. {rows[0][0]}: {rows[0][1][:150]}")
    else:
        print("\nall gates passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
