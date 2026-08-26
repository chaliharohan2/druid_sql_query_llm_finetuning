#!/usr/bin/env python3
"""Validate authored examples against the live Druid 35.0.0 cluster.

Four gates, all must pass before an example is allowed into the SFT file:
  1. EXEC   the completion returns VALID
  2. ROWS   it returns at least one row (when expect_rows)
  3. LINT   it contains the construct the cluster is meant to teach
  4. TRAP   the naive standard-SQL version fails (INVALID) or differs (DIFFERENT)

Usage: validate.py <module>   e.g. validate.py batch01
"""
from __future__ import annotations
import importlib, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "examples"))
from harness.api import run_query
from harness.client import DruidClient
from prompt import load_index, system_prompt

ROOT = Path(__file__).resolve().parent


def check(e: dict, c: DruidClient) -> list[str]:
    fails: list[str] = []
    r = run_query(e["sql"], client=c, timeout_seconds=45)
    if r.status != "VALID":
        return [f"EXEC {r.status}: {(r.error_message or '')[:200]}"]
    if e["gates"]["expect_rows"] and not r.row_count:
        fails.append("ROWS returned 0 rows")
    for token in e["gates"]["must_contain"]:
        if token not in e["sql"]:
            fails.append(f"LINT missing {token!r}")
    trap = e.get("trap")
    if trap:
        n = run_query(trap["naive_sql"], client=c, timeout_seconds=45)
        if trap["expect"] == "INVALID":
            if n.status != "INVALID":
                fails.append(f"TRAP naive SQL was {n.status}, expected INVALID")
        elif trap["expect"] == "DIFFERENT":
            if n.status != "VALID":
                fails.append(f"TRAP naive SQL was {n.status}, expected VALID-but-wrong")
            elif n.sample == r.sample:
                fails.append("TRAP naive SQL produced identical results")
    return fails


def main() -> int:
    module = sys.argv[1] if len(sys.argv) > 1 else "batch01"
    examples = importlib.import_module(module).E
    index = load_index()
    c = DruidClient()
    if not c.health():
        print("Druid is not up.", file=sys.stderr)
        return 1
    ok, bad = [], []
    for e in examples:
        missing = [s for s in e["schemas"] if s not in index]
        fails = [f"SCHEMA unknown {missing}"] if missing else check(e, c)
        (bad if fails else ok).append((e, fails))
        print(f"{'PASS' if not fails else 'FAIL'}  {e['id']:8} {e['cluster']:22} "
              + ("" if not fails else " | ".join(fails)))
    print(f"\n{len(ok)} passed, {len(bad)} failed, {len(examples)} total")

    out = ROOT / f"{module}.sft.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for e, _ in ok:
            fh.write(json.dumps({"messages": [
                {"role": "system", "content": system_prompt(index, e["schemas"])},
                {"role": "user", "content": e["question"]},
                {"role": "assistant", "content": e["sql"]},
            ], "meta": {"id": e["id"], "cluster": e["cluster"], "schemas": e["schemas"]}},
                ensure_ascii=False) + "\n")
    print(f"wrote {out} ({len(ok)} records)")
    c.session.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
