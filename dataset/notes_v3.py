"""Renders a table's overview, rules and glossary with varied wording and layout (addendum F1, F2).

`NotesStyle` is what `prompt_formats.render(..., notes_style=...)` consults for every table. One style
object belongs to one prompt: the layout is drawn once per prompt (as in a real prompt, where one author
formats all the tables), the wording of each rule and the glossary pattern once per table, all from a fixed
seed, so re-rendering a row reproduces it exactly.

Definition patterns (>= 8 wanted, 14 here) render a glossary either as sentences ("X means ...",
"We treat a row as X when ...", "Count something as X when ...") or as structures (a `Term | Definition`
table, a `**Glossary**` block, a `### Glossary` list ...). Some patterns only fit a definition that is a
row filter; they are drawn only when every shown term is one.

Not AI training or inference code.
"""
from __future__ import annotations

import random

import rule_bank as RB
from glossary_terms import article


def cap(s: str) -> str:
    return s[0].upper() + s[1:] if s else s


def _art(term: str) -> str:
    return article(term)


# (id, weight, kind, renderer(items) -> lines).  items: [{"term","cond","phrase"}]
def _p_means(it):     return [f"{cap(i['term'])} means {i['phrase']}." for i in it]
def _p_treat(it):     return [f"We treat a row as {_art(i['term'])} {i['term']} when {i['cond']}." for i in it]
def _p_colon(it):     return [f"{i['term']}: {i['phrase']}" for i in it]
def _p_count(it):     return [f"Count something as {_art(i['term'])} {i['term']} when {i['cond']}." for i in it]
def _p_table(it):     return ["| Term | Definition |", "| --- | --- |"] + [f"| {i['term']} | {cap(i['phrase'])} |" for i in it]
def _p_bold(it):      return ["**Glossary**"] + [f"- **{i['term']}**: {i['phrase']}" for i in it]
def _p_heading(it):   return ["### Glossary"] + [f"* {i['term']} = {i['phrase']}" for i in it]
def _p_anyrec(it):    return [f"{cap(_art(i['term']))} {i['term']} is any record where {i['cond']}." for i in it]
def _p_arrow(it):     return [f"\"{i['term']}\" -> {i['phrase']}" for i in it]
def _p_says(it):      return [f"If someone says \"{i['term']}\", they mean {i['phrase']}." for i in it]
def _p_inline(it):    return ["Definitions: " + "; ".join(f"{i['term']} is {i['phrase']}" for i in it) + "."]
def _p_business(it):  return ["Business terms:"] + [f"- {i['term']} - {i['phrase']}" for i in it]
def _p_bywe(it):      return [f"By {i['term']} we mean {i['phrase']}." for i in it]
def _p_paren(it):     return [f"{cap(i['term'])} (glossary): {i['phrase']}." for i in it]


DEF_PATTERNS = [
    ("means", 1.0, "any", _p_means), ("treat", 1.0, "filter", _p_treat), ("colon", 1.0, "any", _p_colon),
    ("count_as", 1.0, "filter", _p_count), ("table", 1.0, "any", _p_table), ("bold_block", 1.0, "any", _p_bold),
    ("heading_list", 1.0, "any", _p_heading), ("any_record", 1.0, "filter", _p_anyrec), ("arrow", 1.0, "any", _p_arrow),
    ("says", 1.0, "any", _p_says), ("inline", 1.0, "any", _p_inline), ("business_terms", 1.0, "any", _p_business),
    ("by_we_mean", 1.0, "any", _p_bywe), ("glossary_paren", 1.0, "any", _p_paren),
]
_PATTERN_FN = {p[0]: p for p in DEF_PATTERNS}


class NotesStyle:
    """Formatting decisions for one prompt. Deterministic in (seed, table id)."""

    def __init__(self, seed: str, exclude_terms=(), force_terms=(), min_terms: int = 0, layout: str | None = None,
                 fixed_entries: dict[str, list[int]] | None = None):
        self.seed = str(seed)
        self.fixed = fixed_entries or {}  # table id -> indices into its glossary that MUST be printed (re-rendering)
        self.exclude = {t.lower() for t in exclude_terms}
        self.force = {t.lower() for t in force_terms}
        self.min_terms = min_terms
        r = random.Random(f"{self.seed}|layout")
        self.layout = layout or RB.weighted(r, RB.LAYOUTS)
        self.heading_case = r.choice(["upper", "title"])
        self.shown: dict[str, dict] = {}
        self.picked: dict[str, list[dict]] = {}  # table id -> glossary entries actually printed

    def _rng(self, tid: str, tag: str) -> random.Random:
        return random.Random(f"{self.seed}|{tid}|{tag}")

    # ---- glossary
    def pick_entries(self, d: dict) -> list[dict]:
        entries = d.get("glossary") or []
        if not entries:
            return []
        if d["id"] in self.fixed:
            return [entries[i] for i in self.fixed[d["id"]]]
        rng = self._rng(d["id"], "terms")
        primary, rest = entries[0], entries[1:]
        keep = [e for e in rest if e["term"].lower() not in self.exclude or e["term"].lower() in self.force]
        forced = [e for e in keep if e["term"].lower() in self.force]
        optional = [e for e in keep if e not in forced]
        k = rng.choice([1, 2, 3])
        rng.shuffle(optional)
        need = max(0, k - len(forced), self.min_terms - 1 - len(forced))
        chosen = forced + optional[:need]
        out = ([primary] if (primary["term"].lower() not in self.exclude or primary["term"].lower() in self.force) else []) + chosen
        return sorted(out, key=lambda e: entries.index(e))

    def glossary_lines(self, d: dict, entries: list[dict]) -> tuple[list[str], str | None]:
        if not entries:
            return [], None
        rng = self._rng(d["id"], "gloss")
        all_filter = all(e.get("conds") for e in entries)
        pats = [p for p in DEF_PATTERNS if p[2] == "any" or all_filter]
        pid, _, _, fn = pats[rng.randrange(len(pats))]
        items = []
        for e in entries:
            j = rng.randrange(len(e["phrases"]))
            items.append({"term": e["term"], "phrase": e["phrases"][j],
                          "cond": e["conds"][j] if e.get("conds") else None})
        return fn(items), pid

    # ---- one table
    def text(self, d: dict, overview: bool, rules: bool) -> str:
        tid = d["id"]
        ov = d.get("overview") if overview else ""
        info: dict = {"layout": self.layout}
        lines: list[str]
        if not rules:
            lines = [ov] if ov else []
        else:
            sentences, keys = RB.render_rules(d.get("instructions") or [], d, self._rng(tid, "rules"))
            entries = self.pick_entries(d)
            self.picked[tid] = entries
            gl, pid = self.glossary_lines(d, entries)
            lines = RB.layout_lines(self.layout, ov or "", sentences, self.heading_case) + gl
            info.update({"rule_keys": keys, "gloss_pattern": pid, "terms": [e["term"] for e in entries],
                         "folded": len(sentences) < len(d.get("instructions") or [])})
        self.shown[tid] = info
        return "\n".join(lines)

    def meta(self) -> dict:
        return {"layout": self.layout, "heading_case": self.heading_case, "tables": self.shown}


def colliding_terms(d: dict, texts: list[str]) -> set[str]:
    """Glossary terms (other than the primary) whose words show up in a question: they would read as
    references to the definition even though the gold SQL never applies it, so they are left out."""
    import re
    low = " ".join(texts).lower()
    out = set()
    for e in (d.get("glossary") or [])[1:]:
        t = e["term"].lower()
        words = [w for w in re.split(r"[\s/]+", t) if w]
        head = words[0]
        if t in low or re.search(rf"\b{re.escape(head)}\b", low):
            out.add(e["term"])
    return out
