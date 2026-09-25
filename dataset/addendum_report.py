"""Addendum report sections (F1-F6), computed from the FINAL split files only.

`compute()` returns a dict, `markdown()` renders it; `make_report.py` embeds both. Run this file directly to
print the numbers without regenerating the whole report.

Not AI training or inference code.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import random  # noqa: E402

import term_names  # noqa: E402
import validators as V  # noqa: E402
from glossary_terms import article, head_word  # noqa: E402

OLD_HEADS = {"flagged", "priority", "notable", "key"}
SIX_WORDS = ["key", "priority", "flagged", "notable", "critical", "premium"]
SPLITS = ["train", "val_heldout_domain", "test_heldout_shape", "test_prodlike"]
DEFN_SHAPES = ["or_cols", "not_in", "like_prefix", "threshold", "mixed", "time_active", "derived", "multi_term"]


def _pct(n, d) -> float:
    return round(100 * n / d, 1) if d else 0.0


def _share_table(counter: Counter, top: int = 5) -> list:
    tot = sum(counter.values())
    return [(k, v, _pct(v, tot)) for k, v in counter.most_common(top)]


_VOWEL_A_OK = ("uni", "use", "usa", "usu", "uti", "one", "eu", "ura", "ubi", "uk", "ur", "ut")
_CONS_AN_OK = ("hour", "honest", "honor", "heir", "mba", "sql", "sla", "html", "http", "fbi", "xml", "x-", "mri", "nfc")


def text_bugs(rows: dict[str, list[dict]]) -> dict:
    """Counts of the four F7 text bugs on the FINAL rows (all must be zero), with the first few offenders."""
    out = {"doubled_backticks": 0, "bad_plurals": 0, "article_before_backticked_name": 0, "article_in_question": 0}
    ex: dict[str, list] = {k: [] for k in out}
    plural_rx = re.compile(r"\b\w*(?:sh|ch|x|z)s\b")  # hashs, batchs, boxs
    tick_rx = re.compile(r"\b(an?) `([A-Za-z_]\w*)`")
    q_rx = re.compile(r"(?<![-\w])([Aa]n?) ([A-Za-z][\w'-]*)")
    for s in SPLITS:
        for r in rows.get(s, []):
            rid = r["meta"]["id"]
            prompt = "\n".join(x["content"] for x in r["messages"][:-1])
            if re.search(r"(?<!`)``(?!`)", prompt):  # not the ```sql fence of a wrapper instruction
                out["doubled_backticks"] += 1
                ex["doubled_backticks"].append(rid)
            if plural_rx.search(prompt):
                out["bad_plurals"] += 1
                ex["bad_plurals"].append((rid, plural_rx.search(prompt).group(0)))
            for art, name in tick_rx.findall(prompt):
                first = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name.replace("_", " ")).lower().split()[0]
                if art != article(first):
                    out["article_before_backticked_name"] += 1
                    ex["article_before_backticked_name"].append((rid, art, name))
                    break
            q = r["meta"]["question"] + " " + (r["meta"].get("prior_question") or "")
            for art, word in q_rx.findall(q):
                w = word.lower()
                if word.isupper() and len(word) > 1:
                    continue
                vowel = w[0] in "aeiou"
                bad = (art.lower() == "a" and vowel and not w.startswith(_VOWEL_A_OK)) or \
                      (art.lower() == "an" and not vowel and not w.startswith(_CONS_AN_OK))
                if bad:
                    out["article_in_question"] += 1
                    ex["article_in_question"].append((rid, art, word))
                    break
    out["examples"] = {k: v[:5] for k, v in ex.items() if v}
    return out


def term_checks(index: dict, rows: dict[str, list[dict]]) -> dict:
    """F7: every term versus its definition."""
    verbatim, stop, n = [], [], 0
    for sid, d in index.items():
        for e in d.get("glossary") or []:
            n += 1
            t = e["term"].lower()
            for v in list(e.get("values") or []) + [str(x) for x in e.get("numbers") or []]:
                if len(str(v)) >= 3 and str(v).lower() in t:
                    verbatim.append((e["term"], v))
            noun = (d["glossary"][0].get("gen_term") or "").split(" ", 1)[-1].lower()  # the table's own word ("email event")
            mod = t[: -len(noun) - 1] if noun and t.endswith(" " + noun) else t
            vt = set()
            for v in e.get("values") or []:
                vt |= term_names._tokens(str(v))
            if vt & term_names._tokens(mod):
                verbatim.append((e["term"], sorted(vt & term_names._tokens(mod))))
            toks = set(re.split(r"[-\s/]+", mod)) & term_names.STOP
            own = {w for c in e.get("columns", []) for w in term_names.words(c)}
            if toks - own:
                stop.append((e["term"], sorted(toks - own)))
    shown: dict[str, tuple] = {}
    for s in SPLITS:
        for r in rows.get(s, []):
            for t in r["meta"].get("glossary_terms_shown", []):
                shown.setdefault(t, ())
    pool = [(sid, e) for sid, d in index.items() for e in d.get("glossary") or [] if e["term"] in shown]
    rng = random.Random(20260926)
    pairs = [{"term": e["term"], "shape": e["shape"], "table": sid, "definition": e["phrases"][0], "previous_name": e.get("gen_term")}
             for sid, e in rng.sample(pool, min(30, len(pool)))]
    return {"terms_defined": n, "renamed_terms": sum(1 for d in index.values() for e in d.get("glossary") or []
                                                      if e.get("gen_term") and e["term"] != e["gen_term"]),
            "primaries_renamed_vs_v2": sum(1 for d in index.values() if d.get("glossary") and d["glossary"][0].get("gen_term")),
            "terms_containing_a_definition_value": len(verbatim), "terms_with_a_stop_word": len(stop),
            "value_examples": verbatim[:5], "stop_examples": stop[:5], "sample_pairs": pairs}


def compute(rows: dict[str, list[dict]], index: dict, extra: dict | None = None) -> dict:
    out: dict = {}
    allrows = [r for s in SPLITS for r in rows.get(s, [])]
    all_terms = {e["term"] for d in index.values() for e in d.get("glossary") or []}

    # ---------------------------------------------------------------- F1
    per_kind: dict[str, Counter] = {}
    layouts: Counter = Counter()
    n_rules_prompts = 0
    for r in allrows:
        st = r["meta"].get("notes_style")
        if not st:
            continue
        keys = [k for t in st["tables"].values() for k in t.get("rule_keys", [])]
        if keys:
            n_rules_prompts += 1
            layouts[st["layout"] + (":" + st["heading_case"] if st["layout"] == "instructions" else "")] += 1
        for k in keys:
            kind = k.split(":")[0]
            per_kind.setdefault(kind, Counter())[k.replace(":c", ":clause")] += 1
    f1 = {"rules_prompts": n_rules_prompts, "layouts": {k: {"n": v, "pct": _pct(v, n_rules_prompts)} for k, v in layouts.most_common()}, "phrasings": {}}
    layout_main = Counter()
    for k, v in layouts.items():
        layout_main[k.split(":")[0]] += v
    f1["layout_pct"] = {k: _pct(v, n_rules_prompts) for k, v in layout_main.most_common()}
    for kind, c in per_kind.items():
        tot = sum(c.values())
        top = c.most_common(1)[0]
        f1["phrasings"][kind] = {"occurrences": tot, "distinct": len(c), "top_phrasing": top[0], "top_share_pct": _pct(top[1], tot)}
    out["f1"] = f1

    # ---------------------------------------------------------------- F2
    occ_heads, dist_heads = Counter(), Counter()
    pattern_c: Counter = Counter()
    terms_shown: set[str] = set()
    occ_terms = 0
    n_gl_prompts = 0
    unknown_used = 0
    for r in allrows:
        st = r["meta"].get("notes_style")
        if not st:
            continue
        had = False
        for t in st["tables"].values():
            if t.get("gloss_pattern"):
                pattern_c[t["gloss_pattern"]] += len(t["terms"])
                for term in t["terms"]:
                    occ_heads[head_word(term)] += 1
                    occ_terms += 1
                    terms_shown.add(term)
                had = True
        n_gl_prompts += had
        # every glossary term that a question uses must be defined in that prompt
        q = " ".join(x for x in (r["meta"]["question"], r["meta"].get("prior_question") or "") if x).lower()
        shown = {x.lower() for t in st["tables"].values() for x in t.get("terms", [])}
        for term in all_terms:
            if term.lower() not in shown and len(term.split()) >= 2 and re.search(
                    rf"\b{re.escape(term.lower())}(?:e?s)?(?:'s)?(?!\w)", q):
                unknown_used += 1
                break
    for term in terms_shown:
        dist_heads[head_word(term)] += 1
    old_heads_occ = sum(v for k, v in occ_heads.items() if k in OLD_HEADS)
    f2 = {"distinct_terms_shown": len(terms_shown), "distinct_terms_defined": len(all_terms), "definition_occurrences": occ_terms,
          "head_words_distinct": len(dist_heads), "top_head_share_pct_occurrences": _share_table(occ_heads),
          "top_head_share_pct_distinct": _share_table(dist_heads),
          "old_four_heads_pct_occurrences": _pct(old_heads_occ, occ_terms),
          "old_four_heads_pct_distinct": _pct(sum(v for k, v in dist_heads.items() if k in OLD_HEADS), len(terms_shown)),
          "pattern_pct": {k: _pct(v, occ_terms) for k, v in pattern_c.most_common()},
          "rows_using_undefined_term": unknown_used}
    out["f2"] = f2

    # ---------------------------------------------------------------- F3
    shapes = Counter()
    for r in rows.get("train", []):
        d = r["meta"].get("defn_shape")
        if d:
            shapes[d] += 1
    ev = Counter()
    for s in SPLITS[1:]:
        for r in rows.get(s, []):
            if r["meta"].get("defn_shape"):
                ev[s] += 1
    out["f3"] = {"train": dict(shapes), "train_total": sum(shapes.values()), "eval": dict(ev)}

    # ---------------------------------------------------------------- F4
    decoys = [r for r in rows.get("train", []) if r["meta"].get("decoy")]
    real_col = [r for r in decoys if r["meta"]["decoy"].get("mode") == "real_column"]
    other_term = [r for r in decoys if r["meta"].get("notes_shown") and r["meta"].get("glossary_terms_shown")]
    words_rx = re.compile(r"\b(" + "|".join(SIX_WORDS) + r")\b", re.I)
    with_word = [r for r in rows.get("train", []) if words_rx.search(r["meta"]["question"] + " " + (r["meta"].get("prior_question") or ""))]
    matching = 0
    for r in with_word:
        st = r["meta"].get("notes_style") or {"tables": {}}
        q = (r["meta"]["question"] + " " + (r["meta"].get("prior_question") or "")).lower()
        for t in st["tables"].values():
            if any(term.lower() in q and words_rx.search(term) for term in t.get("terms", [])):
                matching += 1
                break
    bad_decoy = 0
    for r in decoys:
        sql = r["meta"].get("gold_sql") or r["messages"][-1]["content"]
        clean = re.sub(r"\s+", " ", sql.replace('"', ""))
        for term in r["meta"]["decoy"].get("glossary_terms", []):
            for d in index.values():
                for e in d.get("glossary") or []:
                    if e["term"] == term and V.glossary_applied(e, clean)[0]:
                        bad_decoy += 1
    out["f4"] = {"decoy_rows": len(decoys), "real_column_rows": len(real_col), "other_term_rows": len(other_term),
                 "train_questions_with_six_words": len(with_word), "with_matching_definition": matching,
                 "matching_pct": _pct(matching, len(with_word)), "decoys_applying_a_glossary_filter": bad_decoy}

    # ---------------------------------------------------------------- F5
    tot = Counter()
    for r in allrows:
        na = r["meta"].get("nullable_annotations")
        if na:
            tot.update({k: v for k, v in na.items() if isinstance(v, (int, float))})
    out["f5"] = {"rows_with_nullability": sum(1 for r in allrows if r["meta"].get("nullable_annotations")),
                 "referenced_nullable_pct": _pct(tot["ref_nullable"], tot["ref_n"]),
                 "unreferenced_nullable_pct": _pct(tot["unref_nullable"], tot["unref_n"]),
                 "all_columns_nullable_pct": _pct(tot["all_nullable"], tot["all_n"]),
                 "before_addendum_pct": _pct(tot["before_nullable"], tot["all_n"])}
    out["f5"]["gap_points"] = round(abs(out["f5"]["referenced_nullable_pct"] - out["f5"]["unreferenced_nullable_pct"]), 1)

    out["f7"] = term_checks(index, rows)
    out["f7"]["text_bugs"] = text_bugs(rows)
    if extra:
        out["f6_and_cost"] = extra
    return out


def markdown(m: dict) -> str:
    L = ["", "## Addendum (F1-F6)", ""]
    f1 = m["f1"]
    L += [f"### F1 rule phrasing and layout ({f1['rules_prompts']} prompts show rules)", "",
          "| layout | prompts | share |", "|---|---:|---:|"]
    for k, v in f1["layouts"].items():
        L.append(f"| {k} | {v['n']} | {v['pct']}% |")
    L += ["", "| rule type | occurrences | distinct phrasings | most common phrasing | its share |", "|---|---:|---:|---|---:|"]
    for k, v in f1["phrasings"].items():
        L.append(f"| {k} | {v['occurrences']} | {v['distinct']} | `{v['top_phrasing']}` | {v['top_share_pct']}% |")
    f2 = m["f2"]
    L += ["", "### F2 glossary terms and definition patterns", "",
          f"- distinct terms shown in prompts: **{f2['distinct_terms_shown']}** (defined in the schemas: {f2['distinct_terms_defined']})",
          f"- definition occurrences: {f2['definition_occurrences']}; distinct head words: {f2['head_words_distinct']}",
          f"- most common head words (share of occurrences): {f2['top_head_share_pct_occurrences']}",
          f"- most common head words (share of distinct terms): {f2['top_head_share_pct_distinct']}",
          f"- flagged/priority/notable/key together: {f2['old_four_heads_pct_occurrences']}% of occurrences, {f2['old_four_heads_pct_distinct']}% of distinct terms",
          f"- rows whose question uses a glossary term that its prompt does not define: **{f2['rows_using_undefined_term']}**",
          "", "| definition pattern | share of definitions |", "|---|---:|"]
    for k, v in f2["pattern_pct"].items():
        L.append(f"| {k} | {v}% |")
    f3 = m["f3"]
    L += ["", "### F3 richer definition shapes (new train rows)", "", f"total: **{f3['train_total']}**; eval pools: {f3['eval']}", "",
          "| shape | rows |", "|---|---:|"]
    for k in DEFN_SHAPES:
        L.append(f"| {k} | {f3['train'].get(k, 0)} |")
    f4 = m["f4"]
    L += ["", "### F4 decoys", "",
          f"- decoy rows: **{f4['decoy_rows']}** (word refers to a real column: **{f4['real_column_rows']}**; a glossary that defines other terms is shown: {f4['other_term_rows']})",
          f"- train questions containing key/priority/flagged/notable/critical/premium: {f4['train_questions_with_six_words']}; "
          f"with a matching definition in the prompt: {f4['with_matching_definition']} (**{f4['matching_pct']}%**, must be <= 60%)",
          f"- decoy rows whose gold SQL applies a glossary filter: **{f4['decoys_applying_a_glossary_filter']}**"]
    f5 = m["f5"]
    L += ["", "### F5 nullability annotations (enterprise formats)", "",
          f"- rows showing nullability: {f5['rows_with_nullability']}; NULLABLE share of non-time columns: {f5['all_columns_nullable_pct']}% (v2 had {f5['before_addendum_pct']}%)",
          f"- referenced by the gold SQL: **{f5['referenced_nullable_pct']}%**, unreferenced: **{f5['unreferenced_nullable_pct']}%** (gap {f5['gap_points']} points, must be <= 5)"]
    f7 = m["f7"]
    tb = f7["text_bugs"]
    L += ["", "### F7 term names and text fixes", "",
          f"- glossary terms: **{f7['terms_defined']}**, each named from its definition (the F2 name is kept as `gen_term`); "
          f"renamed: **{f7['renamed_terms']}** ({f7['terms_defined'] - f7['renamed_terms']} `time_active` terms, e.g. \"engaged student\", already had the right form); all {f7['primaries_renamed_vs_v2']} v2 primary terms are new",
          f"- terms that contain a value (or a word of a value) their definition filters on: **{f7['terms_containing_a_definition_value']}**; "
          f"terms with a when/where/how word their definition does not filter on: **{f7['terms_with_a_stop_word']}**",
          f"- text bugs left in the final rows (must be 0): doubled backticks **{tb['doubled_backticks']}**, bad plurals "
          f"**{tb['bad_plurals']}**, wrong article before a backticked name **{tb['article_before_backticked_name']}**, "
          f"wrong article in a question **{tb['article_in_question']}**", "",
          "30 random (term, definition) pairs for review:", "", "| term | shape | definition | was (F2 name) |", "|---|---|---|---|"]
    for p in f7["sample_pairs"]:
        L.append(f"| {p['term']} | {p['shape']} | {p['definition']} | {p['previous_name']} |")
    if "f6_and_cost" in m:
        L += ["", "### F6 cleanup and this pass", "", "```json", json.dumps(m["f6_and_cost"], indent=1), "```"]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    index = json.loads((ROOT / "schema_index.json").read_text())
    rows = {s: [json.loads(l) for l in (ROOT / "v2" / f"{s}.jsonl").read_text().splitlines() if l.strip()] for s in SPLITS}
    print(markdown(compute(rows, index)))
