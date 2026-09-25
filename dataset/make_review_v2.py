#!/usr/bin/env python3
"""Human-review packs for the v2 dataset (plan Section 9 and G9).

  v2/review_prodlike.html   every test_prodlike row -- the plan requires these to be hand-verified
  v2/review_sample.html     a random 2% of train / val / test_heldout_shape (plan G9: the owner
                            reviews a further 2% by hand), stratified across the splits

Each entry shows the question, the gold SQL, the target table (columns, descriptions, nullability),
any overview / rules / glossary the prompt showed, and a LIVE sample of the gold query's result
against the primary seed, so a reviewer can judge plausibility without a Druid console.

The review is a judgement about the QUESTION and the SQL together: does this SQL answer exactly what
was asked (grain, filters, window, direction)? Record the ids you accept in
v2/prodlike_verified_ids.txt (one per line); assemble.py then sets meta.verified for them.
"""
from __future__ import annotations

import html
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "druid-harness"))
OUT = ROOT / "v2"

import validators as V  # noqa: E402
from harness.client import DruidClient  # noqa: E402

CSS = """
body{font:15px/1.5 system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#1c1c1c;background:#fafafa}
h1{font-size:22px} .meta{color:#555;font-size:12px}
details{background:#fff;border:1px solid #ddd;border-radius:8px;margin:10px 0;padding:8px 14px}
summary{cursor:pointer;font-weight:600}
.q{font-size:16px;background:#eef4ff;padding:8px 12px;border-radius:6px;margin:8px 0}
pre{background:#101820;color:#e8f1ff;padding:10px 12px;border-radius:6px;overflow:auto;font-size:12.5px}
table{border-collapse:collapse;font-size:12px;margin:6px 0} td,th{border:1px solid #ddd;padding:2px 8px;text-align:left}
.note{background:#fff8e1;border-left:4px solid #f0b400;padding:6px 10px;margin:6px 0;font-size:13px;white-space:pre-wrap}
.tag{display:inline-block;background:#e3e8ef;border-radius:10px;padding:0 8px;margin-right:4px;font-size:11px}
@media (prefers-color-scheme:dark){body{background:#15181d;color:#e6e6e6}details{background:#1d2128;border-color:#333}
.q{background:#1f2b40}.note{background:#3a3210}td,th{border-color:#333}.meta{color:#9aa}}
"""


def esc(x) -> str:
    return html.escape(str(x))


def result_sample(client, sql: str, tables: list[str], index: dict) -> str:
    try:
        r = V.run_query(sql, client=client, timeout_seconds=30, max_rows=6)
    except Exception as exc:  # noqa: BLE001
        return f"(not run: {type(exc).__name__})"
    if r.status != "VALID":
        return f"(query status {r.status}: {(r.error_message or '')[:120]})"
    if not r.sample:
        return f"{r.row_count} rows"
    cols = list(r.sample[0].keys())
    head = "".join(f"<th>{esc(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{esc(row.get(c))}</td>" for c in cols) + "</tr>" for row in r.sample[:5])
    return f"<div class='meta'>{r.row_count} rows; first {min(5, len(r.sample))}:</div><table><tr>{head}</tr>{body}</table>"


def entry(rec: dict, index: dict, client) -> str:
    m = rec["meta"]
    sql = m.get("gold_sql") or rec["messages"][-1]["content"]
    t = index[m["target_tables"][0]]
    nullable = set(t.get("nullable_columns") or [])
    cols = "".join(f"<tr><td>{esc(c[0])}</td><td>{esc(c[1])}</td><td>{'NULLABLE' if c[0] in nullable else ''}</td>"
                   f"<td>{esc(c[2])}</td></tr>" for c in t["columns"][:60])
    more = f"<div class='meta'>... and {len(t['columns']) - 60} more columns</div>" if len(t["columns"]) > 60 else ""
    notes = ""
    if m.get("notes_shown"):
        # what the prompt actually printed for the target table: the text that follows its overview
        prompt = "\n".join(x["content"] for x in rec["messages"][:-1])
        at = prompt.find(t.get("overview") or "\0")
        shown = prompt[at: at + 1500] if at >= 0 else "(overview not found in the prompt)"
        extra = ""
        if m.get("defn_term"):
            extra = f"\nASSIGNED TERM ({m['defn_shape']}): {m['defn_term']} -- the SQL must apply exactly this definition"
        if m.get("decoy"):
            extra = f"\nDECOY ({m['decoy']['mode']}): the word '{m['decoy']['word']}' defines nothing; the SQL must not add a glossary filter"
        notes = "<div class='note'><b>Rules / glossary as printed in the prompt:</b>\n" + esc(shown + extra) + "</div>"
    elif m.get("decoy") or m.get("defn_term"):
        notes = "<div class='note'>" + esc(f"DECOY ({m['decoy']['mode']}): the word '{m['decoy']['word']}' defines nothing") + "</div>" if m.get("decoy") else ""
    tags = " ".join(f"<span class='tag'>{esc(x)}</span>" for x in
                    [m["question_style"], m["format"], m.get("time_scope"), m.get("complexity"),
                     f"{m['prompt_tokens_est']} tok", f"{len(m['prompt_tables'])} tables"] + m.get("feature_families", []) if x)
    other = ", ".join(index[x]["datasource"] for x in m["prompt_tables"] if x != m["target_tables"][0])
    body = ""
    if m.get("unanswerable"):
        body = f"<div class='note'>Expected output (the model must decline): <b>{esc(sql)}</b></div>"
    else:
        body = f"<pre>{esc(sql)}</pre>{result_sample(client, sql, m['prompt_tables'], index)}"
    return (f"<details><summary>{esc(m['id'])} - {esc(m['question'][:110])}</summary>"
            f"<div class='meta'>{tags}</div><div class='q'>{esc(m['question'])}</div>{body}{notes}"
            f"<div class='meta'>target: <b>{esc(t['datasource'])}</b> ({esc(t['domain'])}); other tables in the prompt: {esc(other) or 'none'}</div>"
            f"<details><summary>target table columns</summary><table><tr><th>column</th><th>type</th><th>null</th><th>description</th></tr>{cols}</table>{more}</details>"
            f"</details>")


def page(title: str, recs: list[dict], index: dict, client) -> str:
    items = "\n".join(entry(r, index, client) for r in recs)
    return (f"<!doctype html><meta charset=utf-8><title>{esc(title)}</title><style>{CSS}</style>"
            f"<h1>{esc(title)}</h1><p class='meta'>{len(recs)} examples. Judge the question and the SQL together.</p>{items}")


def main() -> int:
    index = json.loads((ROOT / "schema_index.json").read_text())
    client = DruidClient()
    load = lambda n: [json.loads(l) for l in (OUT / f"{n}.jsonl").read_text().splitlines() if l.strip()]  # noqa: E731
    prod = load("test_prodlike")
    (OUT / "review_prodlike.html").write_text(page("test_prodlike: hand verification", prod, index, client), encoding="utf-8")
    rng = random.Random(20260924)
    picked = []
    for split, share in (("train", 0.02), ("val_heldout_domain", 0.05), ("test_heldout_shape", 0.05)):
        recs = [r for r in load(split) if r["meta"].get("source") != "handwritten"]
        picked += rng.sample(recs, max(3, round(len(recs) * share)))
    rng.shuffle(picked)
    (OUT / "review_sample.html").write_text(page("Random review sample (train 2%, val/test 5%)", picked, index, client), encoding="utf-8")
    print(f"review_prodlike.html: {len(prod)} rows; review_sample.html: {len(picked)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
