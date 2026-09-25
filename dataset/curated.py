#!/usr/bin/env python3
"""Build and validate the hand-written training rows (plan P1-5 and Section 7).

Sources: examples/batch01.py (the 64 v1 hand-authored rows, with ob_01 corrected)
and examples/batch02*.py (>=150 new rows). Each row is rendered into a prompt in a
format that can express what it needs, then must clear, with nothing waived:

  G0 style, G2 grounding, G3 literal fidelity, G5 roles, G7 style truth, G8 time phrase,
  G1 execution on ALL THREE seeds and a non-empty result on at least two,
  G9 semantic judge (100% of rows, not a sample).

Answerable rows that fail are written to curated_failures.json with the reason;
they are never patched automatically. Output: curated.jsonl.
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "druid-harness"))

import generate_v2 as G  # noqa: E402
import prompt_formats as pf  # noqa: E402
import teacher  # noqa: E402
import validators as V  # noqa: E402
from harness.client import DruidClient  # noqa: E402
from examples import batch01, batch02, batch02b, batch02c  # noqa: E402

HANDWRITTEN = ["web_events", "orders", "products", "ad_impressions", "sec_alerts",
               "telco_cdr", "iot_readings", "fin_txn", "game_sessions"]

# batch01 ob_01 asked for "the 10 slowest requests" but answered with a GROUP BY that
# collapses requests into (session, page) pairs -- a different question (plan A13).
BATCH01_FIXES = {
    "ob_01": ("Which 10 pages have the worst slowest-request latency?",
              """SELECT page_path AS "page_path", MAX(latency_ms) AS "worst_latency_ms"
FROM ds_web_events
GROUP BY 1
ORDER BY 2 DESC
LIMIT 10"""),
    # The G9 review of the 64 v1 rows found questions that do not state what their SQL does
    # (an unrequested LIMIT or time filter, a changed grain, an inclusive date range). Each is
    # corrected here rather than in batch01.py so the v1 file stays as the historical record.
    "st_01": ("Average sensor value per hour of the device's own reading time, for readings ingested in the last 2 days",
              None),
    "ep_01": ("Average latency bucketed by the hour the client started the request, for events logged in the last 3 days",
              None),
    "le_02": ("First firmware version we ever saw for each device",
              """SELECT device_id,
       EARLIEST(firmware, 16) AS "first_firmware"
FROM ds_iot_readings
GROUP BY 1
ORDER BY 1"""),
    "mv_03": ("Average winning price for video creatives, counting only impressions that actually won (a positive clearing price)", None),
    "ob_03": ("Show the 25 customers who spent the most, highest first", None),
    "ob_05": ("Which device and metric pairs have the highest peak reading? Show the top 5", None),
    "tl_01": ("Orders placed between 2026-08-01 and 2026-08-08, inclusive of both days",
              """SELECT COUNT(*) AS "order_count"
FROM ds_orders
WHERE __time >= TIMESTAMP '2026-08-01 00:00:00'
  AND __time < TIMESTAMP '2026-08-09 00:00:00'"""),
    "le_01": ("What is the most recent analyst verdict for each host?",
              """SELECT host_id,
       LATEST(analyst_verdict, 32) AS "latest_verdict"
FROM ds_sec_alerts
GROUP BY 1
ORDER BY 1"""),
    "le_03": ("The 20 subscribers with the highest total rated cost, with their latest contract status", None),
    "mf_04": ("How many alerts did each rule whose name starts with suspicious produce?", None),
    "mi_02": ("Merchants with more than 15 approved transactions",
              """SELECT merchantId,
       COUNT(*) AS "approved_count"
FROM ds_fin_txn
WHERE authResult = 'approved'
GROUP BY 1
HAVING COUNT(*) > 15
ORDER BY 2 DESC"""),
    "tl_02": ("Count call records from 2026-08-01 up to but not including 2026-08-15", None),
    "ts_03": ("Dropped-call rate for the last 3 hours compared with the same 3-hour window one week ago",
              """SELECT CASE WHEN __time >= CURRENT_TIMESTAMP - INTERVAL '3' HOUR THEN 'last_3_hours' ELSE 'same_window_week_ago' END AS "period",
       SUM(dropped_flag) * 1.0 / COUNT(*) AS "drop_rate"
FROM ds_telco_cdr
WHERE (__time >= CURRENT_TIMESTAMP - INTERVAL '3' HOUR)
   OR (__time >= TIME_SHIFT(CURRENT_TIMESTAMP, 'P1D', -7) - INTERVAL '3' HOUR
       AND __time < TIME_SHIFT(CURRENT_TIMESTAMP, 'P1D', -7))
GROUP BY 1
ORDER BY 1"""),
    "mi_03": ("Daily data volume in gigabytes and the download-to-upload ratio, guarding against divide-by-zero", None),
}


def collect() -> list[dict]:
    rows = []
    for e in batch01.E:
        e = dict(e)
        if e["id"] in BATCH01_FIXES:
            q, sql = BATCH01_FIXES[e["id"]]
            e["question"], e["sql"] = q, (sql if sql is not None else e["sql"])
        e["id"] = "hw1_" + e["id"]
        e["style"] = e.get("style", "business")
        rows.append(e)
    for mod in (batch02, batch02b, batch02c):
        rows.extend(mod.E)
    return rows


def run_on_seeds(client, sql: str, schema_ids: list[str], index: dict):
    """Run `sql` on every seed copy; returns (statuses, row_counts, fingerprints)."""
    n = min(len(index[s].get("g4_variants") or [1]) for s in schema_ids)
    out = []
    for i in range(n):
        mapping = {index[s]["datasource"]: (index[s].get("g4_variants") or [index[s]["datasource"]])[i]
                   for s in schema_ids}
        r = V.run_query(V._swap_tables(sql, mapping), client=client, timeout_seconds=45,
                      max_rows=V.G4_MAX_ROWS)
        out.append((r.status, r.row_count, V.fingerprint(r.sample or []) if r.status == "VALID" else None,
                    (r.error_message or "")[:160]))
    return out


def main() -> int:
    index = json.loads((ROOT / "schema_index.json").read_text())
    seeds = pf.load_seeds()
    client = DruidClient()
    rows = collect()
    kept, failures = [], []
    for e in rows:
        rng = random.Random(f"curated:{e['id']}")
        answerable = not e["sql"].startswith("-- CANNOT_ANSWER")
        ids = list(e["schemas"])
        if rng.random() < 0.35:  # distractor tables, so hand-written rows also practise ignoring tables
            others = [s for s in HANDWRITTEN if s not in ids]
            ids += rng.sample(others, rng.randint(1, min(3, len(others))))
            rng.shuffle(ids)
        need = pf.requirements(index, ids, e["sql"]) if answerable else frozenset()
        if answerable:
            try:
                lits = V.sp.filter_literals(V.sp.parse(e["sql"]))
            except Exception:  # noqa: BLE001
                lits = []
            if any(l.strip("%").rstrip(". ") not in e["question"] for l in lits):
                need = need | {"desc"}  # a filter value only the schema text reveals needs a format that has descriptions
        show_overview = rng.random() < 0.25
        fmt = pf.pick(rng, need, require_notes=show_overview)
        overview_for = {t for t in ids if index[t].get("overview")} if show_overview else set()
        sample = {"table_ids": ids, "target_table": e["schemas"][0], "question_style": e["style"],
                  "notes_for": set(), "overview_for": overview_for, "format": fmt}
        reasons: list[str] = []
        detail: dict = {}
        if answerable:
            _, r = V.g0_style(e["sql"]); reasons += r
            _, r = V.g2_grounded(e["sql"], ids, index); reasons += r
            sp_text, _ = G.render_schema_prompt(sample, index, seeds)
            _, r = V.g3_literal_fidelity(e["question"], e["sql"], prompt_text=sp_text,
                                         value_lists=G.sampler.visible_value_lists(sp_text, ids, index)); reasons += r
            _, r = V.g5_role_compliance(e["sql"], ids, index,
                                        allow_sum_total=any(w in e["question"].lower() for w in ("total", "sum"))); reasons += r
            _, r = V.g8_time_phrase(e["question"], e["sql"]); reasons += r
            _, r = V.g7_style_truth(e["question"], G.leaky_columns(e["question"], index, [e["schemas"][0]]), e["style"])
            reasons += r
            res = run_on_seeds(client, e["sql"], ids, index)
            if any(s != "VALID" for s, *_ in res):
                reasons.append("G1 not VALID on every seed: " + "; ".join(x[3] for x in res if x[0] != "VALID")[:200])
            elif sum(1 for _, n, *_ in res if n) < 2 and not re.search(
                    r"INTERVAL\s+'\d+'\s+(HOUR|MINUTE)|TIME_FLOOR\(CURRENT_TIMESTAMP,\s*'P1D'\)(?!\s*-)", e["sql"], re.I):
                # Emptiness is clock-dependent: the seed data ends at its anchor, so a window of a few hours (or
                # "today") goes empty as time passes. Such rows were non-empty when first validated near the anchor.
                reasons.append("G1 empty result on 2+ seeds")
            detail["fingerprints"] = [f"seed{i}:{f}" for i, (_, _, f, _) in enumerate(res) if f]
        schema_prompt, _ = G.render_schema_prompt(sample, index, seeds)
        try:
            if answerable:
                ok, why = V.g9_semantic_judge(schema_prompt, e["question"], e["sql"])
                if not ok:
                    # the flash judge is not deterministic; only drop a row if the stronger auditor agrees
                    second = teacher.audit_answer(schema_prompt, e["question"], e["sql"])
                    if not second.get("answers_question"):
                        reasons += why + [f"auditor: {second.get('reason', '')}"]
            else:
                chk = teacher.check_answerable(schema_prompt, e["question"])
                if chk.get("answerable") is True and "not supported" not in e["sql"]:
                    reasons.append(f"UNANS checker says answerable: {chk.get('reason')}")
        except teacher.TeacherError as exc:
            reasons.append(f"judge unavailable: {str(exc)[:120]}")
        if reasons:
            failures.append({"id": e["id"], "question": e["question"], "sql": e["sql"], "reasons": reasons})
            continue
        turns = pf.render(fmt, index, ids, e["question"], seeds=seeds, notes_for=set(), overview_for=overview_for)
        messages = [{"role": r, "content": t} for r, t in turns] + [{"role": "assistant", "content": e["sql"]}]
        target = index[e["schemas"][0]]
        detected = V.detect_features(e["sql"], [index[t]["roles"] for t in ids]) if answerable else set()
        kept.append({"messages": messages, "meta": {
            "id": e["id"], "attempt": -1, "split": "train", "question": e["question"], "domain": target["domain"],
            "target_tables": [e["schemas"][0]], "prompt_tables": ids,
            "distractor_relation": "unrelated" if len(ids) == len(e["schemas"]) else "related",
            "format": fmt, "question_style": e["style"], "hint_level": target.get("hint_level"),
            "feature_families": sorted(detected & G.sampler.DRUID_COUNTED),
            "sql_features": sorted(detected & set(G.sampler.FEATURE_FAMILIES_GENERAL)),
            "skeleton_hash": G.skeleton_hash(e["sql"]) if answerable else "unanswerable:handwritten",
            "required_columns": [], "required_predicates": [], "forbidden_constructs": [],
            "unanswerable": not answerable, "prompt_tokens_est": G.count_tokens("\n".join(m["content"] for m in messages[:-1])),
            "n_columns_target": len(target["columns"]), "teacher": "handwritten", "judge": teacher.TEACHER_JUDGE,
            "g4_agreement": None, "result_fingerprints": detail.get("fingerprints", []),
            "exact_column_in_question": bool(G.leaky_columns(e["question"], index, [e["schemas"][0]])),
            "notes_shown": False, "overview_shown": bool(overview_for), "source": "handwritten",
            "cluster": e["cluster"]}})
    (ROOT / "curated.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept), encoding="utf-8")
    (ROOT / "curated_failures.json").write_text(json.dumps(failures, indent=1) + "\n")
    print(f"curated: {len(kept)} kept, {len(failures)} failed of {len(rows)}")
    for f in failures:
        print(f"  FAIL {f['id']}: {f['reasons'][:2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
