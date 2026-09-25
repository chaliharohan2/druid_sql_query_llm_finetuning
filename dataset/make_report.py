#!/usr/bin/env python3
"""dataset_report.md / dataset_report.json (plan Section 10), regenerated from the files.

Reads v2/*.jsonl (+ the pool *_rejects.jsonl logs) and writes, next to them,
counts per split, every Section 7 axis as target vs actual, skeleton statistics,
gate rejection rates, the "must be zero" checks recomputed from the final rows,
and whether the harness accepts window functions on Druid 35.0.0.

Usage: make_report.py [--pools train_pool,val_pool,test_shape_pool,prodlike_pool]
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "druid-harness"))

import validators as V  # noqa: E402
import sampler  # noqa: E402

OUT = ROOT / "v2"
SPLITS = ["train", "val_heldout_domain", "test_heldout_shape", "test_prodlike"]
FORMATS_ALL = 14


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def pct(n, d):
    return round(100 * n / d, 1) if d else 0.0


def band(actual, target, tol=5.0):
    return "ok" if abs(actual - target) <= tol else "OFF"


def gold_sql(r):
    return r["meta"].get("gold_sql") or r["messages"][-1]["content"]


def axis_rows(recs: list[dict], index: dict) -> dict:
    n = len(recs)
    m = [r["meta"] for r in recs]
    ans = [x for x in m if not x.get("unanswerable")]
    styles = Counter(x["question_style"] for x in m)
    fam_n = Counter(min(3, len([f for f in x["feature_families"]])) for x in ans)
    tables = Counter("1" if len(x["prompt_tables"]) == 1 else "2-3" if len(x["prompt_tables"]) <= 3 else "4-8" for x in m)
    multi = [x for x in m if len(x["prompt_tables"]) > 1]
    width = Counter("7-25" if x["n_columns_target"] <= 25 else "26-80" if x["n_columns_target"] <= 80 else "81-200" for x in m)
    toks = [x["prompt_tokens_est"] for x in m]
    tok_b = Counter("<1k" if t < 1000 else "1-4k" if t < 4000 else "4-12k" if t < 12000 else "12-24k" for t in toks)
    hint = Counter(x.get("hint_level") for x in m if x.get("hint_level"))
    fmt = Counter(x["format"] for x in m)
    notes = [x for x in m if x.get("notes_shown")]
    return {
        "n": n,
        "question_style_pct": {k: pct(v, n) for k, v in styles.most_common()},
        "exact_column_in_question_pct": pct(sum(x.get("exact_column_in_question", False) for x in m), n),
        "druid_family_count_pct": {k: pct(fam_n.get(k, 0), len(ans)) for k in (0, 1, 2, 3)},
        "tables_per_prompt_pct": {k: pct(tables.get(k, 0), n) for k in ("1", "2-3", "4-8")},
        "related_or_same_domain_distractor_pct_of_multi_table": pct(
            sum(1 for x in multi if x["distractor_relation"] in ("same_domain", "related")), len(multi)),
        "target_width_pct": {k: pct(width.get(k, 0), n) for k in ("7-25", "26-80", "81-200")},
        "prompt_tokens_pct": {k: pct(tok_b.get(k, 0), n) for k in ("<1k", "1-4k", "4-12k", "12-24k")},
        "prompt_tokens": {"p50": sorted(toks)[n // 2] if toks else 0, "p95": sorted(toks)[int(n * .95)] if toks else 0,
                          "max": max(toks) if toks else 0},
        "hint_level_pct": {k: pct(hint.get(k, 0), sum(hint.values())) for k in ("none", "semantic", "explicit")},
        "notes_shown_pct": pct(len(notes), n),
        "notes_change_the_answer_pct": pct(sum(1 for x in notes if x.get("instructions_changing", 0) > 0), len(notes)),
        "overview_shown_pct_of_multi_table": pct(sum(1 for x in multi if x.get("overview_shown")), len(multi)),
        "epoch_arith_pct": pct(sum("epoch_arith" in x["feature_families"] for x in ans), n),
        "calendar_window_pct": pct(sum("calendar_window" in x["feature_families"] for x in ans), n),
        "unanswerable_pct": pct(sum(x.get("unanswerable", False) for x in m), n),
        "wrapper_pct": pct(sum(1 for x in m if x.get("wrapper")), n),
        "multi_turn_pct": pct(sum(1 for x in m if x.get("turns", 1) > 1), n),
        "handwritten_rows": sum(1 for x in m if x.get("source") == "handwritten"),
        "formats": dict(fmt.most_common()),
        "no_system_pct": pct(fmt.get("no_system", 0), n),
        "formats_used": len(fmt),
    }


TARGETS = {
    "question_style_pct": {"business": 55, "vocab_gap": 15, "terse": 10},
    "druid_family_count_pct": {0: 20, 1: 40, 2: 28, 3: 12},
    "tables_per_prompt_pct": {"1": 30, "2-3": 40, "4-8": 30},
    "target_width_pct": {"7-25": 40, "26-80": 40, "81-200": 20},
    "prompt_tokens_pct": {"<1k": 30, "1-4k": 35, "4-12k": 25, "12-24k": 10},
    "hint_level_pct": {"none": 30, "semantic": 55, "explicit": 15},
}
SCALAR_TARGETS = {"notes_shown_pct": 35, "unanswerable_pct": 4, "wrapper_pct": 10, "multi_turn_pct": 5,
                  "no_system_pct": 8}
AT_LEAST = {"epoch_arith_pct": 3, "calendar_window_pct": 8,
            "related_or_same_domain_distractor_pct_of_multi_table": 50,
            "notes_change_the_answer_pct": 70, "overview_shown_pct_of_multi_table": 40}


def zero_checks(recs: list[dict], index: dict) -> dict:
    """Recompute the 'must be zero' checks on the FINAL rows."""
    import generate_v2 as gen  # noqa: E402 -- leaky_columns for G7
    lit = role = ungrounded = style = g6 = g7 = g10 = 0
    bad_ids = []
    for r in recs:
        m = r["meta"]
        if m.get("unanswerable") or not m.get("question"):
            continue
        sql, q = gold_sql(r), m["question"]
        if m.get("prior_question"):  # follow-up: the earlier turn supplies the literals / "total" wording
            q = m["prior_question"] + " " + q
        prompt_text = "\n".join(x["content"] for x in r["messages"][:-1])
        value_lists = sampler.visible_value_lists(prompt_text, m["prompt_tables"], index)
        shown_terms = set(m.get("glossary_terms_shown") or [])
        allowed = set()
        shown_rules = []  # tables whose rules the prompt printed, glossary reduced to the printed entries
        rule_tables = {t for t, v in (m.get("notes_style") or {}).get("tables", {}).items() if "rule_keys" in v}
        for t in m["prompt_tables"]:
            d = index[t]
            entries = [g for g in d.get("glossary") or [] if not shown_terms or g["term"] in shown_terms]
            for g in entries:
                allowed.update(str(v) for v in g.get("values") or [])
                allowed.update(re.findall(r"'([^']{1,60})'", g.get("predicate", "")))
            for i in d.get("instructions") or []:
                allowed.update(re.findall(r"'([^']{1,60})'", i.get("required_predicate") or ""))
            if t in rule_tables:
                dd = dict(d)
                dd["glossary"] = entries
                shown_rules.append(dd)
        if not V.g3_literal_fidelity(q, sql, allowed_extra=allowed, prompt_text=prompt_text, value_lists=value_lists)[0]:
            lit += 1; bad_ids.append((m["id"], "G3"))
        if not V.g5_role_compliance(sql, m["prompt_tables"], index,
                                    allow_sum_total=any(w in q.lower() for w in ("total", "sum")))[0]:
            role += 1; bad_ids.append((m["id"], "G5"))
        if not V.g2_grounded(sql, m["prompt_tables"], index)[0]:
            ungrounded += 1; bad_ids.append((m["id"], "G2"))
        if not V.g0_style(sql)[0]:
            style += 1; bad_ids.append((m["id"], "G0"))
        if shown_rules and not V.g6_instructions(sql, q, shown_rules)[0]:
            g6 += 1; bad_ids.append((m["id"], "G6"))
        if not V.g10_epoch_unit(sql, prompt_text, m["prompt_tables"], index)[0]:
            g10 += 1; bad_ids.append((m["id"], "G10"))
        if m.get("source") != "handwritten" and m.get("turns", 1) <= 1:
            cols = [c.rsplit(".", 1)[-1] for c in m.get("required_columns", [])]
            cols += gen.leaky_columns(m["question"], index, [m["target_tables"][0]])
            if not V.g7_style_truth(m["question"], sorted(set(cols)), m["question_style"])[0]:
                g7 += 1; bad_ids.append((m["id"], "G7"))
    return {"literal_mismatch": lit, "role_violation": role, "ungrounded_column": ungrounded,
            "output_style_violation": style, "instruction_violation": g6, "style_truth_violation": g7,
            "hidden_epoch_unit": g10, "examples": bad_ids[:20]}


def window_support() -> str:
    try:
        from harness.api import run_query
        from harness.client import DruidClient
        c = DruidClient()
        if not c.health():
            return "not tested (Druid down)"
        r = run_query('SELECT "h", "n", ROW_NUMBER() OVER (ORDER BY "n" DESC) AS "rk" FROM '
                      '(SELECT hub_code AS "h", COUNT(*) AS "n" FROM "logistics_daily" GROUP BY 1) LIMIT 5', client=c)
        return f"ACCEPTED on Druid 35.0.0 ({r.status})" if r.status == "VALID" else f"REJECTED: {r.error_message[:100]}"
    except Exception as exc:  # noqa: BLE001
        return f"not tested ({type(exc).__name__})"


def time_col_names(recs: list[dict], index: dict) -> dict:
    names, suffixed, epoch = Counter(), 0, 0
    seen = set()
    for r in recs:
        t = r["meta"]["target_tables"][0]
        if t in seen:
            continue
        seen.add(t)
        roles = index[t]["roles"]
        for k in ("epoch_ms", "epoch_s", "str_space", "str_iso", "str_date", "str_dmy"):
            if roles.get(k):
                names[roles[k]] += 1
                if k.startswith("epoch"):
                    epoch += 1
                    suffixed += roles[k].lower().endswith(("_ms", "_epoch_s", "ms", "epochs"))
        for s, e, _ in roles.get("epoch_pairs") or []:
            names[s] += 1; names[e] += 1
    return {"distinct_names": len(names), "top": names.most_common(12),
            "epoch_secondary_columns": epoch, "epoch_with_unit_suffix_pct": pct(suffixed, epoch)}


def addendum_extras() -> dict:
    """F6 drop counts plus what the addendum pass cost (`add_*_usage.json`, written by generate_v2 runs)."""
    import teacher
    log_path = OUT / "addendum_apply_log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else {}
    usage: dict = {}
    calls = 0
    for f in sorted(ROOT.glob("add_*_usage.json")):
        u = json.loads(f.read_text())
        for model, c in u.items():
            agg = usage.setdefault(model, {})
            for k, v in c.items():
                agg[k] = agg.get(k, 0) + v
            calls += c.get("calls", 0)
    rows = {s: len(load(OUT / f"{s}.jsonl")) for s in SPLITS}
    new_rows = sum(v for k, v in (log.get("new_rows") or {}).items())
    return {"rows_before": log.get("rows_in"), "rows_after": rows, "dropped_in_code": log.get("drops"),
            "rows_rewritten_in_code": {"prompts_re_rendered": sum(rows.values()) - new_rows,
                                       "questions_with_a_glossary_term_renamed": log.get("f2_rows_rewritten")},
            "rows_rewritten_by_gemini": 0, "new_rows_written_by_gemini": log.get("new_rows"),
            "gemini_calls": calls, "gemini_cost_usd_estimate": round(teacher.estimated_cost_usd(usage), 2) if usage else 0,
            "nullable_share": log.get("nullable_share")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pools", default="train_pool,val_pool,test_shape_pool,prodlike_pool")
    args = ap.parse_args()
    index = json.loads((ROOT / "schema_index.json").read_text())
    data = {s: load(OUT / f"{s}.jsonl") for s in SPLITS}
    report: dict = {"splits": {}, "targets": TARGETS}
    for s, recs in data.items():
        report["splits"][s] = axis_rows(recs, index) if recs else {"n": 0}

    train = data["train"]
    sk = Counter(r["meta"]["skeleton_hash"] for r in train if not r["meta"].get("unanswerable"))
    n_ans = sum(sk.values())
    report["skeletons"] = {
        "train_answerable_rows": n_ans, "train_distinct": len(sk),
        "distinct_per_6000": round(len(sk) * 6000 / max(1, len(train))),
        "target_distinct_per_6000": 1500,
        "top20_share_pct": [round(100 * v / max(1, len(train)), 2) for _, v in sk.most_common(20)],
        "max_share_pct": round(100 * sk.most_common(1)[0][1] / max(1, len(train)), 2) if sk else 0,
        "cap_pct": 0.5,
    }
    train_sk = set(sk)
    report["skeleton_overlap_with_train_pct"] = {}
    for s in SPLITS[1:]:
        other = {r["meta"]["skeleton_hash"] for r in data[s] if not r["meta"].get("unanswerable")}
        report["skeleton_overlap_with_train_pct"][s] = pct(len(other & train_sk), len(other))
    pair = Counter()
    for r in train:
        pair.update(itertools.combinations(sorted(r["meta"]["feature_families"]), 2))
    report["family_pairs"] = {"max_pair_share_pct": round(100 * max(pair.values(), default=0) / max(1, len(train)), 2),
                              "cap_pct": 3.0, "top": [[list(k), v] for k, v in pair.most_common(5)]}

    rej: dict = {}
    for pool in args.pools.split(","):
        rows = load(ROOT / f"{pool}_rejects.jsonl")
        kept = len(load(ROOT / f"{pool}.jsonl"))
        c = Counter(g for r in rows for g in r["gates"])
        tot = kept + len(rows)
        rej[pool] = {"attempts": tot, "kept": kept, "keep_rate_pct": pct(kept, tot),
                     "by_gate_pct_of_attempts": {g: pct(n, tot) for g, n in c.most_common()}}
    report["gate_rejections"] = rej

    report["zero_checks"] = {s: zero_checks(recs, index) for s, recs in data.items() if recs}
    report["window_functions"] = window_support()
    report["time_columns"] = time_col_names(train, index) if train else {}
    report["domains"] = {s: len({index[r["meta"]["target_tables"][0]]["family"] for r in recs}) for s, recs in data.items() if recs}
    import addendum_report  # noqa: E402
    report["addendum"] = addendum_report.compute(data, index, extra=addendum_extras())
    (OUT / "dataset_report.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    (OUT / "dataset_report.md").write_text(render_md(report) + addendum_report.markdown(report["addendum"]), encoding="utf-8")
    print(f"wrote {OUT/'dataset_report.md'}")
    return 0


def render_md(rep: dict) -> str:
    L = ["# Dataset v2 report", "", "Regenerated by `make_report.py` from `v2/*.jsonl`. "
         "`ok`/`OFF` uses the plan's +/-5 point band; `>=` rows show a floor.", ""]
    L += ["## Splits", "", "| split | rows | domains | handwritten | unanswerable | wrapped | max prompt tokens |", "|---|---:|---:|---:|---:|---:|---:|"]
    for s, d in rep["splits"].items():
        if d["n"]:
            L.append(f"| {s} | {d['n']} | {rep['domains'].get(s, '')} | {d['handwritten_rows']} | {d['unanswerable_pct']}% | {d['wrapper_pct']}% | {d['prompt_tokens']['max']} |")
    tr = rep["splits"]["train"]
    if tr["n"]:
        L += ["", "## Train: target vs actual (Section 7)", "", "| axis | bucket | target | actual | |", "|---|---|---:|---:|---|"]
        for axis, tg in TARGETS.items():
            for k, v in tg.items():
                a = tr[axis].get(k, tr[axis].get(str(k), 0))
                L.append(f"| {axis} | {k} | {v} | {a} | {band(a, v)} |")
        for k, v in SCALAR_TARGETS.items():
            L.append(f"| {k} | ~ | {v} | {tr[k]} | {band(tr[k], v)} |")
        for k, v in AT_LEAST.items():
            L.append(f"| {k} | >= | {v} | {tr[k]} | {'ok' if tr[k] >= v else 'BELOW'} |")
        ex = tr["exact_column_in_question_pct"]
        L.append(f"| exact_column_in_question_pct | <= | 20 | {ex} | {'ok' if ex <= 20 else 'ABOVE'} |")
        L += ["", f"Formats used: {tr['formats_used']} of {FORMATS_ALL}. Counts: `{tr['formats']}`"]
    sk = rep["skeletons"]
    L += ["", "## Skeletons", "", f"- distinct in train: **{sk['train_distinct']}** (about {sk['distinct_per_6000']} per 6,000 rows; target >= {sk['target_distinct_per_6000']})",
          f"- largest skeleton share: **{sk['max_share_pct']}%** (cap {sk['cap_pct']}%)",
          f"- top-20 shares (%): {sk['top20_share_pct']}",
          f"- largest family-pair share: **{rep['family_pairs']['max_pair_share_pct']}%** (cap {rep['family_pairs']['cap_pct']}%)",
          "- overlap with train (% of a split's skeletons also in train): " + ", ".join(f"{k} {v}%" for k, v in rep["skeleton_overlap_with_train_pct"].items()) + "  (test_heldout_shape must be < 10%)"]
    L += ["", "## Gate rejections (% of attempts)", "", "| pool | attempts | kept | keep rate | by gate |", "|---|---:|---:|---:|---|"]
    for p, d in rep["gate_rejections"].items():
        L.append(f"| {p} | {d['attempts']} | {d['kept']} | {d['keep_rate_pct']}% | {d['by_gate_pct_of_attempts']} |")
    L += ["", "## Must be zero (recomputed on final rows)", "", "| split | literal mismatch | role violation | ungrounded column | style violation | rule/glossary violation (G6) | style truth (G7) | hidden epoch unit (G10) |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for s, z in rep["zero_checks"].items():
        L.append(f"| {s} | {z['literal_mismatch']} | {z['role_violation']} | {z['ungrounded_column']} | {z['output_style_violation']} | "
                 f"{z['instruction_violation']} | {z['style_truth_violation']} | {z['hidden_epoch_unit']} |")
    tc = rep.get("time_columns") or {}
    L += ["", "## Other", "", f"- window functions: **{rep['window_functions']}**",
          f"- time-column names in train targets: {tc.get('distinct_names')} distinct; epoch columns with a unit suffix: {tc.get('epoch_with_unit_suffix_pct')}% (target <= 30%); top: {tc.get('top')}"]
    for s in SPLITS:
        d = rep["splits"].get(s)
        if d and d["n"]:
            L += ["", f"### {s}", "", "```json", json.dumps({k: v for k, v in d.items() if k != 'formats'}, indent=1), "```"]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
