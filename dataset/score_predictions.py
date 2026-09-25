#!/usr/bin/env python3
"""Score model predictions against a v2 eval split (plan Section 9). Metrics only:
this script never calls a model, so it is safe to run on outputs from any model.

  python3 score_predictions.py --eval v2/test_prodlike.jsonl --pred base_preds.jsonl [--out scores.json]

`--pred` is JSONL with {"id": "<meta.id>", "prediction": "<raw model output>"}.
Reported, per split and overall:
  valid_pct        prediction executes on Druid 35.0.0
  grounded_pct     every column reference exists in the tables the query reads (G2)
  result_match_pct same result as the gold SQL on all 3 seeds (G4-style, order-insensitive)
  instruction_pct  obeys every rule/glossary term that binds it (G6), on rows that showed notes
  decline_ok_pct   for unanswerable rows: output starts with `-- CANNOT_ANSWER`;
                   for answerable rows: does NOT decline (over_decline_pct)
  unscorable       rows whose GOLD result is empty on all but at most one seed. Both gold and prediction return
                   nothing there, which would score as a match, so they are left out of result_match_pct and
                   counted here instead (the seed data ends at its anchor date, so relative windows such as
                   "last 7 days" go empty as the data ages). A warning is printed when the anchor is more
                   than 14 days old.
A model that grounds columns worse than the base model has failed, whatever its valid_pct.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "druid-harness"))

import validators as V  # noqa: E402
from harness.client import DruidClient  # noqa: E402

STALE_ANCHOR_DAYS = 14


def anchor_age_days() -> int:
    from datetime import datetime, timezone
    meta = json.loads((ROOT / "dataset_meta.json").read_text())
    return (datetime.now(timezone.utc) - datetime.fromisoformat(meta["anchor"])).days


def unwrap(text: str) -> str:
    """Pull the SQL out of the wrapper variants (json / fenced / <sql> tags)."""
    t = text.strip()
    m = re.search(r"```(?:sql)?\s*(.*?)```", t, re.S | re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"<sql>(.*?)</sql>", t, re.S | re.I)
    if m:
        return m.group(1).strip()
    if t.startswith("{"):
        try:
            obj = json.loads(t)
            for k in ("sql", "query"):
                if isinstance(obj.get(k), str):
                    return obj[k].strip()
        except json.JSONDecodeError:
            pass
    return t.rstrip(";").strip() if t.count(";") == 1 and t.endswith(";") else t


def per_seed(client, sql, tables, index):
    n = min(len(index[s].get("g4_variants") or [1]) for s in tables)
    res = []
    for i in range(n):
        mapping = {index[s]["datasource"]: (index[s].get("g4_variants") or [index[s]["datasource"]])[i] for s in tables}
        res.append(V.run_query(V._swap_tables(sql, mapping), client=client, timeout_seconds=45, max_rows=V.G4_MAX_ROWS))
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", required=True)
    ap.add_argument("--pred", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    index = json.loads((ROOT / "schema_index.json").read_text())
    evals = [json.loads(l) for l in Path(args.eval).read_text().splitlines() if l.strip()]
    preds = {}
    for l in Path(args.pred).read_text().splitlines():
        if l.strip():
            p = json.loads(l)
            preds[p["id"]] = p["prediction"]
    client = DruidClient()
    age = anchor_age_days()
    if age > STALE_ANCHOR_DAYS:
        print(f"WARNING: the seed data is anchored {age} days ago (limit {STALE_ANCHOR_DAYS}). Relative windows now "
              f"return nothing, so many gold results are empty and unscorable; regenerate and reload the seed data "
              f"(gen_schemas.py, load_all.py) before trusting result_match_pct.", file=sys.stderr)
    tally = {k: [0, 0] for k in ("valid", "grounded", "result_match", "instruction", "decline_ok", "over_decline")}
    per_row = []
    unscorable = 0

    def bump(key, ok):
        tally[key][0] += bool(ok)
        tally[key][1] += 1

    for r in evals:
        m = r["meta"]
        if m["id"] not in preds:
            continue
        pred = unwrap(preds[m["id"]])
        declined = pred.lstrip().startswith("-- CANNOT_ANSWER")
        if m.get("unanswerable"):
            bump("decline_ok", declined)
            per_row.append({"id": m["id"], "declined": declined})
            continue
        bump("over_decline", declined)
        gold = m.get("gold_sql") or r["messages"][-1]["content"]
        tables = m["prompt_tables"]
        row = {"id": m["id"]}
        if declined:
            for k in ("valid", "grounded", "result_match"):
                bump(k, False)
            row["declined"] = True
            per_row.append(row)
            continue
        got = per_seed(client, pred, tables, index)
        valid = all(x.status == "VALID" for x in got)
        bump("valid", valid)
        grounded = V.g2_grounded(pred, tables, index)[0]
        bump("grounded", grounded)
        match, row_unscorable = False, False
        if valid:
            want = per_seed(client, gold, tables, index)
            non_empty = sum(1 for w in want if w.status == "VALID" and (w.row_count or 0) > 0)
            if non_empty < 2:  # the standard G4 applied at generation: the gold must return rows on >= 2 seeds
                row_unscorable = True
            else:
                match = all(w.status == "VALID" and V._rows_match(w.sample or [], g.sample or [])
                            for w, g in zip(want, got))
        else:
            want = per_seed(client, gold, tables, index)
            if sum(1 for w in want if w.status == "VALID" and (w.row_count or 0) > 0) < 2:
                row_unscorable = True
        if row_unscorable:
            unscorable += 1
            row["unscorable"] = True
        else:
            bump("result_match", match)
        if m.get("notes_shown"):
            shown = []
            terms = set(m.get("glossary_terms_shown") or [])
            for t in tables:
                dd = dict(index[t])
                if terms:
                    dd["glossary"] = [e for e in dd.get("glossary") or [] if e["term"] in terms]
                shown.append(dd)
            question = (m.get("prior_question", "") + " " + m.get("question", "")).strip()
            bump("instruction", V.g6_instructions(pred, question, shown)[0])
        row.update(valid=valid, grounded=grounded, result_match=match, unscorable=row_unscorable)
        per_row.append(row)

    out = {k.replace("valid", "valid_pct") if k == "valid" else f"{k}_pct":
           (round(100 * a / b, 1) if b else None) for k, (a, b) in tally.items()}
    out["scored_rows"] = len(per_row)
    out["unscorable_rows"] = unscorable
    out["anchor_age_days"] = age
    out["stale_anchor_warning"] = age > STALE_ANCHOR_DAYS
    print(json.dumps(out, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": out, "rows": per_row}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
