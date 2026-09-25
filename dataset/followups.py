#!/usr/bin/env python3
"""Two-turn follow-up examples (plan P2-1, ~5% of train).

Takes answerable rows from a generated pool, asks the teacher for a short
follow-up ("now split that by channel", "same but last quarter") plus the new
SQL, and clears the new SQL through the same gates as any other example:
G0/G2/G3/G5/G8, G1 execution, G9 judge, and G4 agreement with an independent
second model that sees the same conversation. The final assistant turn is the
only training target; the earlier assistant turn is the prior SQL.

  followups.py --base train_pool --n 300 --out followup_pool
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "druid-harness"))

import generate_v2 as G  # noqa: E402
import teacher  # noqa: E402
import validators as V  # noqa: E402

_QUOTED = re.compile(r"'([^']{1,60})'")


def attempt(base: dict, i: int, run: "G.Run", index: dict, seed: int):
    rng = random.Random(f"fu:{seed}:{i}")
    m = base["meta"]
    prev_q, prev_sql = m["question"], m.get("gold_sql") or base["messages"][-1]["content"]
    # Everything the model saw for the first turn. The schema is in the system message for some
    # formats and in the user message for others, so include every message but the gold answer.
    context = "\n\n".join(msg["content"] for msg in base["messages"][:-1])
    kind = rng.choice(teacher.FOLLOWUP_KINDS)
    try:
        w = teacher.write_followup(context, prev_q, prev_sql, kind)
    except teacher.QuotaError:
        raise
    except teacher.TeacherError as exc:
        run.add_reject(i, ["TEACHER"], None, [str(exc)[:200]])
        return
    fu, sql = (w.get("followup") or "").strip(), (w.get("sql") or "").strip()
    if not fu or not sql or sql.strip() == prev_sql.strip():
        run.add_reject(i, ["TEACHER"], None, ["empty or unchanged follow-up"])
        return
    tables = m["prompt_tables"]
    sample = {"table_ids": tables, "target_table": m["target_tables"][0], "question_style": "terse",
              "format": m["format"], "notes_for": set(), "feature_families": [], "sql_features": []}
    cand = {"sample": sample, "question": fu, "sql": sql}
    fails = []
    fails += V.g0_style(sql)[1]
    fails += V.g2_grounded(sql, tables, index)[1]
    # literals carried over from the earlier turn are legitimate in the new SQL
    carried = set(_QUOTED.findall(prev_sql)) | set(_QUOTED.findall(prev_q))
    fails += V.g3_literal_fidelity(fu, sql, allowed_extra=carried)[1]
    fails += V.g5_role_compliance(sql, tables, index, allow_sum_total=True)[1]
    fails += V.g8_time_phrase(fu, sql)[1]
    shown = [index[t] for t in tables] if m.get("notes_shown") else []
    ok6, r6, req, forb, changing = V.g6_instructions(sql, prev_q + " " + fu, shown)
    fails += r6
    if fails:
        run.add_reject(i, sorted({f.split(" ", 1)[0] for f in fails}), cand, fails)
        return
    ok, reasons, _ = V.g1_execute(sql, G.client())
    if not ok:
        run.add_reject(i, ["G1"], cand, reasons)
        return
    convo = f"{context}\n\nEarlier question: {prev_q}\nEarlier SQL:\n{prev_sql}"
    ok, reasons = V.g9_semantic_judge(convo, fu, sql)
    if not ok:
        run.add_reject(i, ["G9"], cand, reasons)
        return
    try:
        second = teacher.independent_followup_sql(context, prev_q, prev_sql, fu)
    except teacher.QuotaError:
        raise
    except teacher.TeacherError as exc:
        run.add_reject(i, ["G4_UNAVAILABLE"], cand, [str(exc)[:200]])
        return
    ok, reasons, fps = V.g4_result_agreement(sql, second, tables, index, G.client())
    if not ok:
        cand["sql_second"] = second
        run.add_reject(i, ["G4"], cand, reasons)
        return
    msgs = [dict(x) for x in base["messages"][:-1]]
    msgs.append({"role": "assistant", "content": prev_sql})
    msgs.append({"role": "user", "content": fu})
    msgs.append({"role": "assistant", "content": sql})
    meta = dict(m)
    meta.update({"id": "", "attempt": i, "turns": 2, "question": fu, "prior_question": prev_q,
                 "followup_kind": kind, "skeleton_hash": G.skeleton_hash(sql),
                 "feature_families": sorted(V.detect_features(sql, [index[t]["roles"] for t in tables])
                                            & G.sampler.DRUID_COUNTED),
                 "sql_features": sorted(V.detect_features(sql) & set(G.sampler.FEATURE_FAMILIES_GENERAL)),
                 "g4_agreement": True, "result_fingerprints": fps, "wrapper": None, "gold_sql": None,
                 "instructions_changing": changing, "exact_column_in_question": bool(G.leaky_columns(fu, index, tables[:1])),
                 "source": "teacher"})
    meta.pop("wrapper", None); meta.pop("gold_sql", None)
    run.try_keep({"messages": msgs, "meta": meta})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="train_pool")
    ap.add_argument("--out", default="followup_pool")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--seed", type=int, default=20260924)
    args = ap.parse_args()
    index = json.loads((ROOT / "schema_index.json").read_text())
    base = [json.loads(l) for l in (ROOT / f"{args.base}.jsonl").read_text().splitlines() if l.strip()]
    base = [b for b in base if not b["meta"].get("unanswerable") and b["meta"].get("question")
            and b["meta"].get("source") == "teacher" and len(b["meta"]["prompt_tables"]) <= 4]
    random.Random(args.seed).shuffle(base)
    run = G.Run(args.out, skeleton_cap=10 ** 6)
    todo = [(i, b) for i, b in enumerate(base[: int(args.n * 2.5)]) if i not in run.done_attempts]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = []
        for i, b in todo:
            if len(run.kept) + len(futs) >= args.n * 2:
                break
            futs.append(pool.submit(attempt, b, i, run, index, args.seed))
        for f in futs:
            if f.exception():
                print("crash:", f.exception(), file=sys.stderr)
            if len(run.kept) >= args.n:
                break
    print(f"follow-ups kept {len(run.kept)}, rejected {len(run.rejects)} -> {run.kept_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
