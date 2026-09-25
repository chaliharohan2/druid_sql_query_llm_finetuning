#!/usr/bin/env python3
"""Apply the addendum's code-only fixes to the assembled v2 splits (no Gemini, no Druid).

  F1  rules re-worded (rule_bank.py) and laid out in five layouts
  F2  glossary terms renamed to domain phrases (glossary_terms.py), definitions re-worded in 14 patterns,
      and the question text updated to the new term (dropped when it cannot be done cleanly)
  F5  NULLABLE annotations re-rendered so 60-80% of non-time columns read NULLABLE
  F6  cleanup: questions that contain SQL or backticks, exact-duplicate questions, and rows whose gold SQL
      assumes the unit of a BIGINT time column that the prompt never states (gate G10)

Input is always `v2_pre_addendum/` (a copy of the assembled files, made on the first run), so the script
is idempotent; output is `v2/`. Row ids never change. SQL never changes, so G1/G4/G9 are not re-run.
The prompts are rebuilt with `prompt_formats.render`, which reproduces v2's prompts byte for byte when no
style is given (checked below for every row before anything is rewritten).

  addendum_apply.py [--ref-weight 1.0] [--extra defn_pool decoy_pool ...]

Not AI training or inference code.
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "druid-harness"))

import notes_v3  # noqa: E402
from addendum_lib import SQLISH, clean_sql, nullable_override, sql_of, used_datasources  # noqa: E402
import prompt_formats as pf  # noqa: E402
import validators as V  # noqa: E402
from validators import sqlparse as sp  # noqa: E402

SPLITS = ["train", "val_heldout_domain", "test_heldout_shape", "test_prodlike"]
OUT, PRE = ROOT / "v2", ROOT / "v2_pre_addendum"

ENTERPRISE = ("enterprise_pipe", "enterprise_backtick")


# ----------------------------------------------------------------------------------------- helpers
def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


_HASHS = re.compile(r"(?i)\b(\w*hash)s\b")


def fix_plurals(text: str) -> str:
    """"recipientHashs" / "hashs" -> "recipientHashes" / "hashes" (v2 questions pluralised a name ending in -sh with -s)."""
    return _HASHS.sub(lambda m: m.group(1) + "es", text)


def norm_q(q: str) -> str:
    return re.sub(r"\s+", " ", q.strip().lower())


def term_regex(term: str, partial: bool = False) -> re.Pattern:
    """The old term as it can appear in a question: "priority warranty claim(s)", possessives included. With
    `partial`, also its clipped forms -- head + the last word(s) of the noun ("priority claims")."""
    words = term.split()
    forms = [words] + ([[words[0]] + words[-k:] for k in range(len(words) - 1, 0, -1)] if partial and len(words) > 2 else [])
    body = "|".join(r"\s+".join(re.escape(w) for w in f) for f in forms)
    return re.compile(rf"\b(?P<core>{body})(?P<suf>(?:e?s)?(?:'s)?)(?!\w)", re.I)


def substitute(text: str, old_term: str, new_term: str) -> tuple[str, int]:
    """Replace the old term (plural, possessive and clipped forms included) and fix the article in front of it:
    "a priority trip" -> "an estimated trip" would otherwise read "a estimated trip"."""
    from glossary_terms import article

    def repl(m: re.Match) -> str:
        core = m.group("core")
        new = new_term[0].upper() + new_term[1:] if core[0].isupper() else new_term
        art = m.group("art")
        if art:
            a = article(new_term)
            art = (a.capitalize() if art[0].isupper() else a) + " "
        return (art or "") + new + m.group("suf")
    rx = term_regex(old_term, partial=True)
    rx = re.compile(r"(?P<art>\b(?:[Aa]n?)\s+)?" + rx.pattern, re.I)
    return rx.subn(repl, text)


def detect_notes(rec: dict, old_index: dict) -> tuple[set, set]:
    """Which tables showed rules / only the overview in a v2 prompt (recovered from the prompt text)."""
    txt = "\n".join(x["content"] for x in rec["messages"][:-1])
    nf, ov = set(), set()
    for t in rec["meta"]["prompt_tables"]:
        d = old_index[t]
        rules = [i["text"] for i in d.get("instructions") or []] + [g["definition_text"] for g in d.get("glossary") or []]
        if rules and any(x in txt for x in rules):
            nf.add(t)
        elif d.get("overview") and d["overview"] in txt:
            ov.add(t)
    return nf, ov


# ----------------------------------------------------------------------------------------- one row
class Dropped(Exception):
    pass


def process(rec: dict, old_index: dict, new_index: dict, seeds: dict, ref_weight: float, gen) -> tuple[dict, dict]:
    m0 = rec["meta"]
    stats: dict = {}
    unans = bool(m0.get("unanswerable"))
    sql = "" if unans else sql_of(rec)
    ids, fmt = m0["prompt_tables"], m0["format"]
    q, prior = m0["question"], m0.get("prior_question") or ""
    q_first = prior or q

    # ---- F6: SQL or backticks in the question
    if SQLISH.search(q) or (prior and SQLISH.search(prior)):
        raise Dropped("f6_sql_in_question")

    # ---- F6 / G10: hidden epoch unit
    prompt_old = "\n".join(x["content"] for x in rec["messages"][:-1])
    if not unans:
        ok, why = V.g10_epoch_unit(sql, prompt_old, ids, old_index)
        if not ok:
            raise Dropped("f6_hidden_epoch_unit")

    # ---- rebuild the v2 prompt exactly, to be sure we are rewriting what we think we are
    nf, ov = detect_notes(rec, old_index)
    old_turns = pf.render(fmt, old_index, ids, q_first, seeds=seeds, notes_for=nf, overview_for=ov)
    msgs = copy.deepcopy(rec["messages"])
    for i, (role, text) in enumerate(old_turns):
        cur = msgs[i]["content"]
        if msgs[i]["role"] != role or not cur.startswith(text.rstrip()):
            raise RuntimeError(f"{m0['id']}: re-render of the v2 prompt does not match message {i}")

    # ---- F2: rename the term the question used
    clean = clean_sql(sql)
    used_ds = used_datasources(sql)
    subs = []
    for t in sorted(nf):
        og = (old_index[t].get("glossary") or [])
        if not og:
            continue
        o = og[0]
        applied = old_index[t]["datasource"] in used_ds and V.glossary_applied(o, clean)[0] and not unans
        hit = any(term_regex(o["term"], partial=True).search(x) for x in (q, prior) if x)
        head = o["term"].split()[0]
        if applied and hit:
            subs.append((t, o["term"], new_index[t]["glossary"][0]["term"]))
        elif applied and any(re.search(rf"\b{re.escape(head)}\b", x, re.I) for x in (q, prior) if x):
            raise Dropped("f2_term_not_substitutable")
    q_new, prior_new = q, prior
    for t, old_t, new_t in subs:
        q_new, n1 = substitute(q_new, old_t, new_t)
        prior_new, n2 = substitute(prior_new, old_t, new_t) if prior_new else (prior_new, 0)
        head = old_t.split()[0]
        if re.search(rf"\b{re.escape(head)}\b", q_new + " " + prior_new, re.I):
            raise Dropped("f2_term_not_substitutable")
    q_new, prior_new = fix_plurals(q_new), fix_plurals(prior_new)
    stats["f2_substituted"] = len(subs)

    # ---- F1/F2 style and F5 nullability
    texts = [x for x in (q_new, prior_new) if x]
    exclude = set()
    for t in nf:
        exclude |= notes_v3.colliding_terms(new_index[t], texts)
    style = notes_v3.NotesStyle(f"{m0['id']}|addendum", exclude_terms=exclude)
    rng = random.Random(f"{m0['id']}|nullable")
    nb, nstat = nullable_override(rec, new_index, rng, ref_weight) if fmt in ENTERPRISE else (None, Counter())
    q_first_new = prior_new or q_new
    new_turns = pf.render(fmt, new_index, ids, q_first_new, seeds=seeds, notes_for=nf, overview_for=ov,
                          notes_style=style, nullable_override=nb)
    for i, (role, text) in enumerate(new_turns):
        cur, old_text = msgs[i]["content"], old_turns[i][1]
        suffix = cur[len(old_text.rstrip()):]  # an output-wrapper instruction appended by assemble.py
        msgs[i]["content"] = text.rstrip() + suffix if suffix.strip() else text
    if prior:  # the follow-up turn's own question
        j = len(new_turns) + 1
        if msgs[j]["role"] != "user" or q not in msgs[j]["content"]:
            raise RuntimeError(f"{m0['id']}: follow-up message not found")
        msgs[j]["content"] = msgs[j]["content"].replace(q, q_new)

    # ---- G6 on the rewritten row: every shown definition and rule still obeyed by the unchanged SQL
    if not unans:
        shown = []
        for t in nf:
            dd = dict(new_index[t])
            dd["glossary"] = style.picked.get(t, [])
            shown.append(dd)
        ok, reasons, *_ = V.g6_instructions(sql, (prior_new + " " + q_new).strip(), shown)
        if not ok:
            raise Dropped("g6_after_rewrite:" + reasons[0][:60])

    # ---- G7 on the new question text (a term such as "low-confidence" can spell out a column name)
    if not unans and m0.get("source") != "handwritten" and m0.get("turns", 1) <= 1 and q_new != q:
        cols = [c.rsplit(".", 1)[-1] for c in m0.get("required_columns", [])]
        cols += gen.leaky_columns(q_new, new_index, [m0["target_tables"][0]])
        ok, reasons = V.g7_style_truth(q_new, sorted(set(cols)), m0["question_style"])
        if not ok:
            raise Dropped("g7_after_rewrite")

    rec2 = {"messages": msgs, "meta": copy.deepcopy(m0)}
    m = rec2["meta"]
    m["question"] = q_new
    if prior:
        m["prior_question"] = prior_new
    m["notes_style"] = style.meta()
    m["glossary_terms_shown"] = sorted({e["term"] for es in style.picked.values() for e in es})
    m["addendum"] = {"terms_renamed": [[a, b] for _, a, b in subs]}
    if nb is not None:
        m["nullable_annotations"] = dict(nstat)
    m["prompt_tokens_est"] = gen.count_tokens("\n".join(x["content"] for x in msgs[:-1]))
    stats["nullable"] = nstat
    return rec2, stats


ADD_SEED = 20260925  # the --seed the F3/F4 pools were generated with (notes style and nullability seeds derive from it)


def rerender_extra(rec: dict, new_index: dict, seeds: dict, gen) -> dict:
    """Rebuild a new F3/F4 row's prompt from the CURRENT glossary, F1 wording fixes and F7 term names.

    The pools were generated when glossary terms had their F2 names (`gen_term`), so the same style seed, the same
    printed entries (found by gen_term) and the same nullability draw reproduce the prompt the writer saw, now with
    the renamed terms and corrected wording. The SQL is unchanged."""
    m = rec["meta"]
    ids, fmt, attempt = m["prompt_tables"], m["format"], m["attempt"]
    tables = m["notes_style"]["tables"]
    prompt_old = "\n".join(x["content"] for x in rec["messages"][:-1])
    nf = {t for t, v in tables.items() if "rule_keys" in v}
    ov = {t for t in ids if t not in nf and new_index[t].get("overview") and new_index[t]["overview"] in prompt_old}
    fixed = {}
    for t in nf:
        gl = new_index[t].get("glossary") or []
        fixed[t] = [i for term in tables[t].get("terms", []) for i, e in enumerate(gl) if e["gen_term"] == term]
        if len(fixed[t]) != len(tables[t].get("terms", [])):
            raise RuntimeError(f"{m['id']}: a printed glossary term is no longer in the schema")
    q = fix_plurals(m["question"])
    if m.get("defn_term"):
        tgt = m["target_tables"][0]
        new_term = next(e["term"] for e in new_index[tgt]["glossary"] if e["gen_term"] == m["defn_term"])
        q, n = substitute(q, m["defn_term"], new_term)
        if not n:
            raise Dropped("f7_term_not_in_question")
    style = notes_v3.NotesStyle(f"{ADD_SEED}:{attempt}|notes", fixed_entries=fixed)
    sql = sql_of(rec)
    nb = None
    if fmt in ENTERPRISE:
        nb, nstat = nullable_override({"meta": {"prompt_tables": ids, "unanswerable": False}, "messages": [{"content": sql}]},
                                      new_index, random.Random(f"{ADD_SEED}:{attempt}|nullable"), 1.0)
    turns = pf.render(fmt, new_index, ids, q, seeds=seeds, notes_for=nf, overview_for=ov, notes_style=style, nullable_override=nb)
    msgs = [{"role": r, "content": t} for r, t in turns] + [rec["messages"][-1]]
    shown = []
    for t in nf:
        dd = dict(new_index[t])
        dd["glossary"] = style.picked.get(t, [])
        shown.append(dd)
    ok, reasons, *_ = V.g6_instructions(sql, q, shown)
    if not ok:
        raise Dropped("g6_after_rewrite")
    if m.get("source") != "handwritten" and q != m["question"]:
        cols = [c.rsplit(".", 1)[-1] for c in m.get("required_columns", [])]
        cols += gen.leaky_columns(q, new_index, [m["target_tables"][0]])
        if not V.g7_style_truth(q, sorted(set(cols)), m["question_style"])[0]:
            raise Dropped("g7_after_rewrite")
    meta = copy.deepcopy(m)
    meta["question"] = q
    if m.get("defn_term"):
        meta["defn_term"] = new_term
    meta["notes_style"] = style.meta()
    meta["glossary_terms_shown"] = sorted({e["term"] for es in style.picked.values() for e in es})
    if meta.get("decoy"):
        meta["decoy"]["glossary_terms"] = meta["glossary_terms_shown"]
    if nb is not None:
        meta["nullable_annotations"] = dict(nstat)
    meta["prompt_tokens_est"] = gen.count_tokens("\n".join(x["content"] for x in msgs[:-1]))
    return {"messages": msgs, "meta": meta}


# ------------------------------------------------------------------------------------------ driver
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref-weight", type=float, default=1.0,
                    help="relative chance that a referenced column is flipped to NULLABLE (calibrates F5)")
    ap.add_argument("--extra", nargs="*", default=[], help="pools of new rows (F3/F4) to append with fresh ids")
    args = ap.parse_args()

    import generate_v2 as gen  # noqa: E402 -- token counting; imports nothing that talks to Druid on import

    if not PRE.exists():
        PRE.mkdir()
        for s in SPLITS:
            shutil.copy(OUT / f"{s}.jsonl", PRE / f"{s}.jsonl")
        shutil.copy(OUT / "assemble_log.json", PRE / "assemble_log.json")
        print(f"saved the pre-addendum splits to {PRE.name}/")
    old_index = json.loads((ROOT / "schema_index.pre_addendum.json").read_text())
    new_index = json.loads((ROOT / "schema_index.json").read_text())
    seeds = pf.load_seeds()

    report: dict = {"drops": {}, "rows_in": {}, "rows_out": {}, "f2_rows_rewritten": 0, "f2_rows_dropped": 0}
    final: dict[str, list[dict]] = {}
    nstat_total: Counter = Counter()
    for split in SPLITS:
        rows = load(PRE / f"{split}.jsonl")
        report["rows_in"][split] = len(rows)
        kept, drops = [], Counter()
        for rec in rows:
            try:
                rec2, st = process(rec, old_index, new_index, seeds, args.ref_weight, gen)
            except Dropped as d:
                reason = str(d).split(":")[0]
                drops[reason] += 1
                continue
            report["f2_rows_rewritten"] += bool(st["f2_substituted"])
            nstat_total.update(st["nullable"])
            kept.append(rec2)
        final[split] = kept
        report["drops"][split] = dict(drops)
        print(f"{split:20} in {len(rows):5}  kept {len(kept):5}  drops {dict(drops)}")

    # ---- F6: exact-duplicate questions (kept: the first). Compared inside a split and against train.
    dup = Counter()
    seen_train: dict[str, str] = {}
    for split in SPLITS:
        seen: set[str] = set()
        out = []
        for rec in final[split]:
            key = norm_q(rec["meta"]["question"])
            if split != "train" and key in seen_train:
                dup[split] += 1
                continue
            if key in seen:
                dup[split] += 1
                continue
            seen.add(key)
            out.append(rec)
        final[split] = out
        if split == "train":
            seen_train = {k: "" for k in seen}
    report["drops"]["exact_duplicate_questions"] = dict(dup)

    # ---- new rows from Gemini (F3/F4): appended with fresh ids, after the same duplicate check
    new_counts, audit_dropped = Counter(), Counter()
    for pool in args.extra:
        audit_path = ROOT / f"{pool}_audit.jsonl"  # audit_pool.py: second opinion from the stronger model
        flagged = {json.loads(l)["attempt"] for l in audit_path.read_text().splitlines()
                   if l.strip() and not json.loads(l)["ok"]} if audit_path.exists() else set()
        for rec in load(ROOT / f"{pool}.jsonl"):
            split = rec["meta"]["split"]
            if rec["meta"]["attempt"] in flagged:
                audit_dropped[pool] += 1
                continue
            try:
                rec = rerender_extra(rec, new_index, seeds, gen)
            except Dropped as d:
                audit_dropped[f"{pool}:{d}"] += 1
                continue
            key = norm_q(rec["meta"]["question"])
            if any(norm_q(r["meta"]["question"]) == key for r in final[split]):
                continue
            n_split = max((int(r["meta"]["id"].rsplit("_", 1)[1]) for r in final[split]), default=-1) + 1
            n_pre = max((int(r["meta"]["id"].rsplit("_", 1)[1]) for r in load(PRE / f"{split}.jsonl")), default=-1) + 1
            rec["meta"]["id"] = f"v2_{split}_{max(n_split, n_pre):05d}"
            final[split].append(rec)
            new_counts[(pool, split)] += 1
    report["new_rows"] = {f"{p}:{s}": n for (p, s), n in new_counts.items()}
    report["new_rows_dropped_by_audit"] = dict(audit_dropped)

    for split in SPLITS:
        (OUT / f"{split}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in final[split]), encoding="utf-8")
        report["rows_out"][split] = len(final[split])
    report["nullable"] = dict(nstat_total)
    ref = nstat_total["ref_nullable"] / max(1, nstat_total["ref_n"])
    unref = nstat_total["unref_nullable"] / max(1, nstat_total["unref_n"])
    report["nullable_share"] = {"referenced": round(ref, 3), "unreferenced": round(unref, 3),
                                "all_columns": round(nstat_total["all_nullable"] / max(1, nstat_total["all_n"]), 3),
                                "before": round(nstat_total["before_nullable"] / max(1, nstat_total["all_n"]), 3)}
    (OUT / "addendum_apply_log.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "nullable"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
