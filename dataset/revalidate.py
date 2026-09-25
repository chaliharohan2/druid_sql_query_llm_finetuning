#!/usr/bin/env python3
"""Final pass over the assembled v2 files: re-execute every answerable gold SQL on all three seed copies.

A row fails if any seed rejects the query (add --require-nonempty to also demand rows on two seeds; that check is
clock-dependent, see the flag). Failures are
written to v2/revalidate_failures.json (and, with --drop, removed from the files). This exists because
relative windows depend on the clock and the data can drift between generation and delivery.

  revalidate.py [--drop] [--workers 8]
"""
import argparse, json, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT.parent / "druid-harness"))
import validators as V  # noqa: E402
from harness.client import DruidClient  # noqa: E402

OUT = ROOT / "v2"
SPLITS = ["train", "val_heldout_domain", "test_heldout_shape", "test_prodlike"]

ap = argparse.ArgumentParser()
ap.add_argument("--drop", action="store_true")
ap.add_argument("--workers", type=int, default=8)
ap.add_argument("--require-nonempty", action="store_true",
                help="also require rows on 2+ seeds (clock-dependent: the seed data ends at its anchor, so short windows go empty)")
a = ap.parse_args()
index = json.loads((ROOT / "schema_index.json").read_text())


def check(rec):
    m = rec["meta"]
    if m.get("unanswerable"):
        return None
    sql = m.get("gold_sql") or rec["messages"][-1]["content"]
    client = DruidClient()
    tables = m["prompt_tables"]
    n = min(len(index[t].get("g4_variants") or [1]) for t in tables)
    nonempty = 0
    for i in range(n):
        mapping = {index[t]["datasource"]: (index[t].get("g4_variants") or [index[t]["datasource"]])[i] for t in tables}
        r = V.run_query(V._swap_tables(sql, mapping), client=client, timeout_seconds=60, max_rows=10)
        if r.status != "VALID":
            return f"seed {i} {r.status}: {(r.error_message or '')[:100]}"
        nonempty += bool(r.row_count)
    if nonempty < min(2, n) and a.require_nonempty:
        return f"empty on {n - nonempty}/{n} seeds"
    return None


failures = {}
kept_by_split = {}
for split in SPLITS:
    path = OUT / f"{split}.jsonl"
    if not path.exists():
        continue
    recs = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    with ThreadPoolExecutor(a.workers) as pool:
        results = list(pool.map(check, recs))
    bad = [(r, why) for r, why in zip(recs, results) if why]
    for r, why in bad:
        failures[r["meta"]["id"]] = why
    kept_by_split[split] = [r for r, why in zip(recs, results) if not why]
    print(f"{split:20} {len(recs):6d} rows, {len(bad)} failed revalidation")
(OUT / "revalidate_failures.json").write_text(json.dumps(failures, indent=1) + "\n")
if a.drop and failures:
    for split, recs in kept_by_split.items():
        (OUT / f"{split}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
    print(f"dropped {len(failures)} rows")
sys.exit(1 if failures and not a.drop else 0)
