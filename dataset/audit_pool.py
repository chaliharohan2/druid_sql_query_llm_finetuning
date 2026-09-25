#!/usr/bin/env python3
"""Second-opinion audit of EVERY answerable row in a pool by a model that is neither the row's writer
family's judge nor the pipeline's G9 judge (default gemini-2.5-pro). Resumable.

Writes <pool>_audit.jsonl, one line per audited row: {"id", "attempt", "ok": bool, "reason"}.
assemble.py --audit-drop removes rows the auditor flagged (a fixed price for label quality: the auditor
can be wrong, but noisy labels do more damage than a few dropped rows).

  audit_pool.py --pool val_pool --workers 4
"""
import argparse, json, sys, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import teacher  # noqa: E402

SYSTEM = teacher.AUDITOR_SYSTEM
ap = argparse.ArgumentParser()
ap.add_argument("--pool", required=True)
ap.add_argument("--model", default="gemini-2.5-pro")
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--redo-flagged", action="store_true", help="re-audit only rows a previous audit flagged (after a prompt change)")
ap.add_argument("--fraction", type=float, default=1.0,
                help="audit a deterministic subset (by attempt id); rerunning later extends it as the pool grows")
a = ap.parse_args()

out_path = ROOT / f"{a.pool}_audit.jsonl"
done = set()
if out_path.exists():
    prev = [json.loads(l) for l in out_path.read_text().splitlines() if l.strip()]
    if a.redo_flagged:
        prev = [x for x in prev if x["ok"]]  # forget the flagged ones so they are audited again
        out_path.write_text("".join(json.dumps(x) + "\n" for x in prev))
    done = {x["attempt"] for x in prev}
rows = [json.loads(l) for l in (ROOT / f"{a.pool}.jsonl").read_text().splitlines() if l.strip()]
rows = [r for r in rows if not r["meta"]["unanswerable"] and r["meta"]["attempt"] not in done and r["meta"].get("turns", 1) == 1
        and (a.fraction >= 1.0 or (r["meta"]["attempt"] * 2654435761 % 1000) / 1000 < a.fraction)]
lock = threading.Lock()


def audit(r):
    m = r["meta"]
    prompt = "\n\n".join(x["content"] for x in r["messages"][:-1])[:400000]
    try:
        out = teacher.generate(a.model, SYSTEM,
                               f"{prompt}\n\n---\nQuestion (the last user message above): {m['question']}\n\nCandidate SQL:\n{r['messages'][-1]['content']}",
                               temperature=0.0, max_output_tokens=12000, max_quota_waits=30)
        j = teacher._extract_json(out)
        rec = {"id": m["id"], "attempt": m["attempt"], "ok": bool(j.get("answers_question")), "reason": j.get("reason", "")}
    except teacher.TeacherError as exc:
        return  # left unaudited; a rerun retries it
    with lock:
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")


with ThreadPoolExecutor(a.workers) as pool:
    list(pool.map(audit, rows))
res = [json.loads(l) for l in out_path.read_text().splitlines() if l.strip()]
print(f"{a.pool}: audited {len(res)}, flagged {sum(1 for x in res if not x['ok'])}")
