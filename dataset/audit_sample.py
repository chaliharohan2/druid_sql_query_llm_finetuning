#!/usr/bin/env python3
"""Independent label-noise estimate: a random sample of KEPT answerable rows is re-judged by the
stronger (writer-tier) model, which the pipeline's own G9 judge is not. Prints the flag rate and
each flagged row so a human can decide whether the label or the auditor is wrong.

  audit_sample.py --pool train_pool --n 80
"""
import argparse, json, random, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import teacher  # noqa: E402

SYSTEM = teacher.JUDGE_SEMANTIC_SYSTEM + "\n\nDialect conventions that are CORRECT:\n" + teacher.CONVENTIONS

ap = argparse.ArgumentParser()
ap.add_argument("--pool", default="train_pool")
ap.add_argument("--n", type=int, default=80)
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--model", default="gemini-2.5-pro", help="auditor; use a model different from the row's writer and the G9 judge")
a = ap.parse_args()
rows = [json.loads(l) for l in (ROOT / f"{a.pool}.jsonl").read_text().splitlines() if l.strip()]
rows = [r for r in rows if not r["meta"]["unanswerable"] and r["meta"].get("turns", 1) == 1]
random.Random(a.seed).shuffle(rows)
rows = rows[: a.n]


def audit(r):
    m = r["meta"]
    # Everything the model saw: the schema can live in the system OR the user message depending on the
    # format, so pass every message up to (not including) the gold answer.
    prompt = "\n\n".join(x["content"] for x in r["messages"][:-1])[:400000]
    out = teacher.generate(a.model, SYSTEM,
                           f"{prompt}\n\n---\nQuestion (the last user message above): {m['question']}\n\nCandidate SQL:\n{r['messages'][-1]['content']}",
                           temperature=0.0, max_output_tokens=12000, max_quota_waits=20)
    return r, teacher._extract_json(out)


with ThreadPoolExecutor(2) as pool:
    results = list(pool.map(audit, rows))
flagged = [(r, j) for r, j in results if not j.get("answers_question")]
print(f"audited {len(results)}; flagged {len(flagged)} ({100*len(flagged)/max(1,len(results)):.0f}%)")
for r, j in flagged:
    print(f"\n[{r['meta']['id']}] Q: {r['meta']['question']}\nSQL: {' '.join(r['messages'][-1]['content'].split())}\nWHY: {j.get('reason')}")
