#!/usr/bin/env python3
"""Independent check that Druid holds exactly what schema_index.json says it should.

Does not trust the loader's own row count (it can be read before a REPLACE
publishes). For every datasource and every seed copy it checks: present, exact
row count, time range reaches back >= 500 days and forward to the anchor, rows
exist in the last 36h / 45d, and (where the schema says so) NULLs, empty
multi-value cells and malformed JSON are actually in the data.

Exit code 0 only if everything passes.  Usage: verify_load.py [--quick]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "druid-harness"))
from harness.client import DruidClient  # noqa: E402


def q(c, sql):
    return c.sql_rows(sql)


def main() -> int:
    quick = "--quick" in sys.argv
    c = DruidClient()
    index = json.loads((ROOT / "schema_index.json").read_text())
    anchor = datetime.fromisoformat(json.loads((ROOT / "dataset_meta.json").read_text())["anchor"])
    loaded = {r["TABLE_NAME"] for r in q(c, "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                                             "WHERE TABLE_SCHEMA='druid'")}
    problems: list[str] = []
    expected = {}
    for d in index.values():
        for v in d.get("g4_variants") or [d["datasource"]]:
            expected[v] = d
    for name in sorted(set(expected) - loaded):
        problems.append(f"missing datasource {name}")
    checked = 0
    for name, d in sorted(expected.items()):
        if name not in loaded:
            continue
        checked += 1
        is_dim = d["id"].startswith("dim_") or d["id"] == "products"
        row = q(c, f'SELECT COUNT(*) n, MIN(__time) mn, MAX(__time) mx FROM "{name}"')[0]
        if row["n"] != d["rows"]:
            problems.append(f"{name}: {row['n']} rows, expected {d['rows']}")
            continue
        if is_dim:
            continue
        mn = datetime.fromisoformat(row["mn"].replace("Z", "+00:00"))
        mx = datetime.fromisoformat(row["mx"].replace("Z", "+00:00"))
        if anchor - mn < timedelta(days=500):
            problems.append(f"{name}: earliest row {mn:%Y-%m-%d} is under 500 days before the anchor")
        if anchor - mx > timedelta(days=2):
            problems.append(f"{name}: latest row {mx:%Y-%m-%d %H:%M} is well before the anchor")
        if quick:
            continue
        recent = q(c, f'SELECT COUNT(*) FILTER (WHERE __time >= TIMESTAMP \'{(anchor - timedelta(hours=36)):%Y-%m-%d %H:%M:%S}\') h36, '
                      f'COUNT(*) FILTER (WHERE __time >= TIMESTAMP \'{(anchor - timedelta(days=45)):%Y-%m-%d %H:%M:%S}\') d45 FROM "{name}"')[0]
        if recent["h36"] < 20 or recent["d45"] < 400:
            problems.append(f"{name}: too few recent rows (36h={recent['h36']}, 45d={recent['d45']})")
        r = d["roles"]
        for col in d.get("nullable_columns") or []:
            n = q(c, f'SELECT COUNT(*) n FROM "{name}" WHERE "{col}" IS NULL')[0]["n"]
            if n == 0:
                problems.append(f"{name}: column {col} is marked nullable but holds no NULLs")
                break
        if r.get("mvd"):
            n = q(c, f'SELECT COUNT(*) n FROM "{name}" WHERE MV_LENGTH("{r["mvd"]}") IS NULL OR MV_LENGTH("{r["mvd"]}") = 0')[0]["n"]
            if n == 0:
                problems.append(f"{name}: no empty multi-value cells")
        if r.get("json_dirty") and d.get("generated"):
            n = q(c, f'SELECT COUNT(*) n FROM "{name}" WHERE TRY_PARSE_JSON("{r["json"]}") IS NULL')[0]["n"]
            if n == 0:
                problems.append(f"{name}: schema says malformed JSON exists but none found")
    print(f"checked {checked} of {len(expected)} expected datasources; {len(loaded - set(expected))} extra in Druid")
    for p in problems[:60]:
        print("  PROBLEM:", p)
    print("OK" if not problems else f"{len(problems)} problems")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
