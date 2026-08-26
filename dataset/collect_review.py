#!/usr/bin/env python3
"""Collect everything the review page needs, including each trap's real Druid error."""
from __future__ import annotations
import importlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "examples"))
from harness.api import run_query
from harness.client import DruidClient
from prompt import load_index, system_prompt

ROOT = Path(__file__).resolve().parent
E = importlib.import_module("batch01").E
index = load_index()
c = DruidClient()
out = []
for e in E:
    r = run_query(e["sql"], client=c, timeout_seconds=45)
    rec = {**e, "row_count": r.row_count, "sample": r.sample[:3]}
    if e.get("trap"):
        n = run_query(e["trap"]["naive_sql"], client=c, timeout_seconds=45)
        rec["trap"] = {**e["trap"], "status": n.status,
                       "error": (n.error_message or "")[:400],
                       "sample": n.sample[:3]}
    out.append(rec)
payload = {"examples": out, "index": index,
           "system_prompt_example": system_prompt(index, ["sec_alerts"]),
           "system_prompt_join": system_prompt(index, ["orders", "products"])}
(ROOT / "review_data.json").write_text(json.dumps(payload, indent=1, default=str))
print(f"collected {len(out)} examples, {sum(1 for e in out if e.get('trap'))} traps")
c.session.close()
