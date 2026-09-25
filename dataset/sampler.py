"""Per-example sampler (plan Section 5.3 / Appendix B / P0-4 / P0-5 / P1-1).

Draws everything the generation loop hands to the teacher for one candidate:
which table(s) -- sized to hit a prompt-length bucket and biased toward
same/related-domain distractors --, a skill spec of feature families that the
target schema can actually support, a question style, and a prompt format
(with or without table notes).

Not AI training or inference code: this only decides what to *ask* a teacher
LLM to generate.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import prompt_formats as pf

ROOT = Path(__file__).resolve().parent
RESERVED = set(json.loads((ROOT / "reserved_columns.json").read_text())) \
    if (ROOT / "reserved_columns.json").exists() else set()

# rolling_window / calendar_window are not assigned as families: the time window comes from
# TIME_SCOPES below, which is controlled independently. They are still *detected* in the SQL.
FEATURE_FAMILIES_DRUID = [
    "time_bucket", "time_shift",
    "time_extract_format", "epoch_convert", "epoch_arith", "string_time_parse",
    "json_value", "mvd", "lookup", "approx", "latest_earliest", "filtered_agg",
    "reserved_identifier", "order_by_rule", "join",
]
TIME_SCOPES = [("none", 0.30), ("rolling", 0.32), ("day", 0.09), ("this_period", 0.08),
               ("last_period", 0.15), ("explicit_range", 0.06)]
COMPLEXITY = [("simple", 0.35), ("moderate", 0.45), ("complex", 0.20)]
TIME_SCOPE_BRIEF = {
    "none": "Do not restrict the time range: no condition on __time in WHERE at all (time may still be "
            "used for bucketing or extraction).",
    "rolling": "Use a ROLLING window with its size in the question, e.g. 'past 7 days', 'last 24 hours' "
               "(do not write 'last week/month/quarter/year' -- those mean calendar periods).",
    "day": "Use the words 'today' or 'yesterday'.",
    "this_period": "Use a to-date calendar window: 'this week / month / quarter / year (so far)'.",
    "last_period": "Use a completed calendar period: 'last week / month / quarter / year'.",
    "explicit_range": "Use an explicit date range between two dates that both lie within the last 12 months; "
                      "state both dates in the question (the end date is inclusive).",
    "definition": "The only time window is the one inside the glossary definition: do not name any window in the "
                  "question and add no other time condition.",
}
COMPLEXITY_BRIEF = {
    "simple": "Keep the question simple: one measure, at most one grouping and one filter. At most 18 words.",
    "moderate": "A moderate question: up to two measures or a couple of conditions. At most 32 words.",
    "complex": "A more involved question is fine, but it must stay ONE coherent analyst question with exactly one "
               "correct answer. At most 55 words.",
}
# Hard caps enforced by generate_v2 (real analyst questions are short; the plan's own example
# is "average handling time of calls per agent"). Terse questions are capped lower still.
WORD_CAPS = {"simple": 20, "moderate": 36, "complex": 60}
TERSE_WORD_CAP = 14
# Families counted in a row's meta: everything assignable plus the two window kinds the time scope
# produces (Appendix B lists them as Druid-specific families).
DRUID_COUNTED = set(FEATURE_FAMILIES_DRUID) | {"rolling_window", "calendar_window"}
FEATURE_FAMILIES_GENERAL = [
    "case", "null_handling", "having", "subquery", "cte", "ratio",
    "top_n_per_group", "multi_metric", "exact_distinct", "string_ops", "window",
]
# Section 7: 0 Druid families 20%, 1: 40%, 2: 28%, 3: 12%.
# Assigned mix (the plan's Section 7 target); the report measures what the SQL actually contains,
# counting the time-window kinds as families too.
DRUID_COUNT_DIST = [(0, 0.34), (1, 0.36), (2, 0.20), (3, 0.10)]
# Rarer syntax gets a higher draw weight so common families don't crowd it out.
FAMILY_WEIGHT = {"time_bucket": 1.0, "rolling_window": 1.0, "calendar_window": 1.2, "time_shift": 1.4,
                 "time_extract_format": 1.3, "epoch_convert": 1.3, "epoch_arith": 1.5,
                 "string_time_parse": 1.3, "json_value": 1.6, "mvd": 1.6, "lookup": 1.8, "approx": 1.0,
                 "latest_earliest": 1.6, "filtered_agg": 1.2, "reserved_identifier": 1.6,
                 "order_by_rule": 1.2, "join": 1.5}
QUESTION_STYLES = [("business", 0.53), ("vocab_gap", 0.19), ("exact_column", 0.17), ("terse", 0.11)]
TABLE_COUNT_BUCKETS = [("1", 0.30), ("2-3", 0.40), ("4-8", 0.30)]
# Joint of table count and prompt-length bucket (P1-1). Chosen so the marginals
# come out at <1k 28%, 1-4k 38%, 4-12k 25%, 12-24k 9% against the plan's
# 30/35/25/10 -- a single table cannot honestly reach 12k tokens.
LENGTH_BY_TABLES = {
    "1": [("<1k", 0.55), ("1-4k", 0.40), ("4-12k", 0.05)],
    "2-3": [("<1k", 0.30), ("1-4k", 0.45), ("4-12k", 0.25)],
    "4-8": [("1-4k", 0.25), ("4-12k", 0.45), ("12-24k", 0.30)],
}
BUCKET_RANGE = {"<1k": (150, 1000), "1-4k": (1000, 4000), "4-12k": (4000, 12000), "12-24k": (12000, 24000)}

STYLE_BRIEF = {
    "business": "Phrase the question the way a business analyst who has never seen the "
               "schema would ask it. Do not use any column name verbatim; describe what "
               "you want in plain business language.",
    "vocab_gap": "Phrase the question using a natural word for what you want (e.g. 'agent', "
                "'handling time') that is NOT itself a column name, even though a "
                "differently-named column answers it.",
    "exact_column": "It is fine to name the exact column(s) involved in the question.",
    "terse": "Phrase the question tersely/casually, the way someone would type it into a "
            "chat box in a hurry (abbreviations, no punctuation are fine). Do not use exact "
            "column names.",
}


def _weighted(rng: random.Random, items):
    total = sum(w for _, w in items)
    r = rng.random() * total
    acc = 0.0
    for v, w in items:
        acc += w
        if r < acc:
            return v
    return items[-1][0]


def related_score(index: dict, a: str, b: str) -> int:
    ca = {c[0] for c in index[a]["columns"]}
    cb = {c[0] for c in index[b]["columns"]}
    return len(ca & cb)


_tok_cache: dict[str, int] = {}


def table_tokens(index: dict, sid: str) -> int:
    """Rough token cost of one table in a prompt (chars/4, matching prompt_tokens_est)."""
    if sid not in _tok_cache:
        d = index[sid]
        chars = sum(len(c[0]) + len(c[2]) + 14 for c in d["columns"]) + 60
        chars += len(d.get("overview") or "") + sum(len(i["text"]) for i in d.get("instructions") or [])
        _tok_cache[sid] = chars // 4
    return _tok_cache[sid]


def applicable_druid_families(d: dict) -> list[str]:
    r = d["roles"]
    has_epoch = bool(r.get("epoch_ms") or r.get("epoch_s") or r.get("epoch_pairs"))
    has_str_time = any(r.get(k) for k in ("str_space", "str_iso", "str_date", "str_dmy"))
    ok = {
        "epoch_convert": has_epoch, "epoch_arith": bool(r.get("epoch_pairs")),
        "string_time_parse": has_str_time, "json_value": bool(r.get("json")),
        "mvd": bool(r.get("mvd")), "lookup": bool(r.get("lookup")), "join": bool(r.get("partner")),
        "reserved_identifier": any(c[0] in RESERVED for c in d["columns"]),
    }
    return [f for f in FEATURE_FAMILIES_DRUID if ok.get(f, True)]


def pick_skill_spec(rng: random.Random, d: dict, forced: str | None):
    avail = applicable_druid_families(d)
    n = _weighted(rng, DRUID_COUNT_DIST)
    if forced:
        n = max(n, 1)
    chosen: list[str] = [forced] if forced else []
    pool = [f for f in avail if f not in chosen]
    while len(chosen) < n and pool:
        f = _weighted(rng, [(x, FAMILY_WEIGHT[x]) for x in pool])
        chosen.append(f)
        pool.remove(f)
    n_general = _weighted(rng, [(0, 0.5), (1, 0.35), (2, 0.15)])
    general = rng.sample(FEATURE_FAMILIES_GENERAL, n_general)
    return chosen, general


def _adjust(items, mult: dict | None):
    return [(k, w * (mult or {}).get(k, 1.0)) for k, w in items]


def pick_style(rng: random.Random, mult: dict | None = None) -> str:
    return _weighted(rng, _adjust(QUESTION_STYLES, mult))


# Weighted above the 40/40/20 target for wide tables because the prompt-length buckets
# already exclude wide targets from every "<1k" prompt.
WIDTH_TIERS = [("7-25", 0.33), ("26-80", 0.40), ("81-200", 0.27)]


def width_tier(index: dict, sid: str) -> str:
    n = len(index[sid]["columns"])
    return "7-25" if n <= 25 else "26-80" if n <= 80 else "81-200"


MAX_PROMPT_TOKENS = 25000


def pick_tables(rng: random.Random, index: dict, fact_ids: list[str], n: int, bucket: str,
                target_pool: list[str]) -> tuple[str, list[str]]:
    """(target, [target] + distractors) sized toward `bucket`.

    The target's column-width tier follows Section 7 (40/40/20) among the tables that fit the
    bucket; at least one same/related-domain distractor is guaranteed most of the time (>=50%
    of multi-table prompts, P0-5); the total is capped so no prompt runs past 25k tokens.
    """
    lo, hi = BUCKET_RANGE[bucket]
    room = hi - (n - 1) * 250
    fits = [s for s in target_pool if table_tokens(index, s) <= max(room, 300)] or target_pool
    by_tier: dict[str, list[str]] = {}
    for sid in fits:
        by_tier.setdefault(width_tier(index, sid), []).append(sid)
    tiers = [(t, w) for t, w in WIDTH_TIERS if t in by_tier]
    target = rng.choice(by_tier[_weighted(rng, tiers)])
    chosen = [target]
    used = table_tokens(index, target)
    mid = (lo + hi) / 2
    while len(chosen) < n:
        pool = [s for s in fact_ids if s not in chosen]
        want = max(150.0, (mid - used) / (n - len(chosen)))
        related = [s for s in pool if related_score(index, target, s) >= 5]
        force_related = len(chosen) == 1 and related and rng.random() < 0.70
        scored = []
        for s in (related if force_related else pool):
            score = abs(table_tokens(index, s) - want) + rng.random() * want * 0.5
            if not force_related and related_score(index, target, s) >= 5 and rng.random() < 0.6:
                score *= 0.35
            scored.append((score, s))
        scored.sort()
        pick = rng.choice([s for _, s in scored[:4]])
        chosen.append(pick)
        used += table_tokens(index, pick)
    while used > MAX_PROMPT_TOKENS and len(chosen) > 1:
        drop = max(chosen[1:], key=lambda x: table_tokens(index, x))
        chosen.remove(drop)
        used -= table_tokens(index, drop)
    return target, chosen


def pick_format(rng: random.Random, need: frozenset, require_notes: bool, mult: dict | None = None) -> str:
    return pf.pick(rng, need, require_notes=require_notes, multipliers=mult)


def sample_candidate(rng: random.Random, index: dict, fact_ids: list[str], profile: str = "standard",
                     adapt: dict | None = None, target_ids: list[str] | None = None) -> dict:
    """`adapt` maps an attribute to per-category weight multipliers that undo selection bias:
    categories the gates reject more often are drawn more often (see generate_v2.Run.adapt)."""
    adapt = adapt or {}
    prodlike = profile == "prodlike"
    forced = "epoch_arith" if rng.random() < 0.05 else None
    # `fact_ids` supplies distractors; `target_ids` (default: the same list) restricts the target.
    # The prod-like split draws targets only from its held-out families but distractors from every
    # table, as real enterprise prompts carry many unrelated tables.
    base_targets = target_ids or fact_ids
    target_pool = base_targets
    if forced == "epoch_arith":
        target_pool = [s for s in base_targets if index[s]["roles"].get("epoch_pairs")] or base_targets
        if target_pool is base_targets:
            forced = None

    tcount = _weighted(rng, TABLE_COUNT_BUCKETS)
    n_tables = {"1": 1, "2-3": rng.randint(2, 3), "4-8": rng.randint(4, 8)}[tcount]
    bucket = _weighted(rng, _adjust(LENGTH_BY_TABLES[tcount], adapt.get("length_bucket")))
    if prodlike:  # wide multi-table enterprise-style prompts, 10-25k tokens where the pool allows
        tcount, n_tables = "4-8", rng.randint(4, min(8, len(fact_ids)))
        bucket = _weighted(rng, [("4-12k", 0.45), ("12-24k", 0.55)])
    target, table_ids = pick_tables(rng, index, fact_ids, n_tables, bucket, target_pool)
    style = _weighted(rng, [("business", 0.6), ("vocab_gap", 0.4)]) if prodlike else pick_style(rng, adapt.get("style"))
    complexity = (_weighted(rng, [("moderate", 0.4), ("complex", 0.6)]) if prodlike
                  else _weighted(rng, _adjust(COMPLEXITY, adapt.get("complexity"))))
    druid_families, general_features = pick_skill_spec(rng, index[target], forced)
    # A time window is itself a Druid family (rolling / calendar), so a question meant to use no Druid
    # feature is mostly given no time window either; otherwise plain-SQL answers would be too rare.
    scope_w = _adjust(TIME_SCOPES, adapt.get("time_scope"))
    if not druid_families:
        scope_w = [(k, w * (2.4 if k == "none" else 1.0)) for k, w in scope_w]
    time_scope = _weighted(rng, scope_w)

    partner = index[target]["roles"].get("partner")
    if "join" in druid_families and partner and partner["schema"] not in table_ids:
        table_ids.append(partner["schema"])  # a join needs its partner table in the prompt

    has_notes = any(index[t].get("instructions") or index[t].get("glossary") for t in table_ids)
    show_notes = bool(has_notes and rng.random() < (0.85 if prodlike else 0.40))  # ~35% of prompts, plan P0-7
    notes_must_apply = bool(show_notes and rng.random() < 0.80)  # plan: >=70% must change the SQL

    # overview prose: >=40% of multi-table prompts (P0-5); rules+glossary already carry it
    show_overview = bool(not show_notes and rng.random() < (1.0 if prodlike else (0.5 if len(table_ids) > 1 else 0.2)))
    fmt = pick_format(rng, frozenset(), require_notes=show_notes or show_overview, mult=adapt.get("format"))
    if prodlike:
        fmt = rng.choice(["enterprise_pipe", "enterprise_backtick", "enterprise_pipe", "pipe_table"])
    notes_for = {t for t in table_ids if show_notes} if show_notes else set()
    overview_for = {t for t in table_ids if index[t].get("overview")} if show_overview else set()
    others = [t for t in table_ids if t != target]
    related = any(related_score(index, target, t) >= 5 for t in others)
    trap = bool(others and related and style in ("business", "vocab_gap") and rng.random() < 0.4)

    return {
        "target_table": target,
        "table_ids": table_ids,
        "length_bucket_planned": bucket,
        "distractor_relation": ("unrelated" if not others else
                                ("same_domain" if any(index[t]["family"] == index[target]["family"]
                                                      for t in others)
                                 else ("related" if related else "unrelated"))),
        "question_style": style,
        "time_scope": time_scope,
        "complexity": complexity,
        "feature_families": druid_families,
        "sql_features": general_features,
        "format": fmt,
        "notes_for": notes_for,
        "overview_for": overview_for,
        "show_notes": show_notes,
        "notes_must_apply": notes_must_apply,
        "wrong_table_trap": trap,
    }


def visible_value_lists(prompt_text: str, table_ids: list[str], index: dict) -> list[list[str]]:
    """For each low-cardinality column whose allowed values are ALL present in the rendered prompt,
    that list of values (so a paraphrase of a listed value can be told from an invented one)."""
    out = []
    for sid in table_ids:
        for col, pool in (index[sid].get("pools") or {}).items():
            vals = list(dict.fromkeys(str(v) for v in pool)) if isinstance(pool, list) else []
            if 2 <= len(vals) <= 40 and all(v in prompt_text for v in vals):
                out.append(vals)
    return out


def value_hints(d: dict, prompt_text: str = "", max_dims: int = 12) -> str:
    """Real values that occur in the data, for the TEACHER only. Each is tagged by whether the
    schema text the model will see actually lists it: a value the model cannot recover from the
    prompt may only be filtered on if the question quotes it verbatim, or the label is unlearnable."""
    r, pools, lines, seen = d["roles"], d.get("pools") or {}, [], set()
    for col in [r.get("entity")] + list(r.get("dims", [])):
        pool = pools.get(col)
        if col and pool and col not in seen and len(lines) < max_dims:
            seen.add(col)
            vals = list(dict.fromkeys(str(v) for v in pool))
            shown = all(v in prompt_text for v in vals)
            sample = ", ".join(repr(v) for v in vals[:6])
            if shown:
                lines.append(f"- {col}: the schema text lists its values ({sample}); use them exactly")
            else:
                lines.append(f"- {col}: real values include {sample}, but the schema text does NOT list them -- "
                             f"if you filter on this column, write the value verbatim, in quotes, in the question")
    if r.get("mvd") and pools.get(r["mvd"]):
        vals = [str(v) for v in pools[r["mvd"]]]
        tag = "listed in the schema text" if all(v in prompt_text for v in vals) else "NOT listed in the schema text (quote verbatim in the question if used)"
        lines.append(f"- {r['mvd']} (multi-value): tags include {', '.join(repr(v) for v in vals[:6])} -- {tag}")
    if r.get("json") and pools.get(r["json"]):
        keys = "; ".join(f"{k}: {', '.join(repr(str(x)) for x in v[:4])}" for k, v in pools[r["json"]].items())
        lines.append(f"- {r['json']} keys and values that exist: {keys} (quote any value you filter on verbatim in the question)")
    return "\n".join(lines)


def column_semantics(d: dict) -> str:
    """Role/polarity/encoding facts about the target table, for the teacher only."""
    r = d["roles"]
    lines = []
    for m in r.get("metrics", []):
        role, pol = m.get("role"), m.get("polarity")
        if role in ("category_numeric",):
            lines.append(f"- {m['name']}: a numeric code; filter/group only, no arithmetic")
        elif role == "flag_01":
            lines.append(f"- {m['name']}: 0/1 flag; SUM/COUNT it, or use it in FILTER")
        else:
            direction = {"higher_is_worse": "higher is worse", "higher_is_better": "higher is better"}.get(pol, "no good/bad direction")
            add = "additive (SUM is meaningful)" if role == "measure_additive" else "non-additive (use AVG/MIN/MAX/quantiles; SUM only if the question asks for a total)"
            lines.append(f"- {m['name']}: {add}; {direction}")
    for c in r.get("hi_card", []):
        lines.append(f"- {c}: identifier; COUNT DISTINCT / APPROX_COUNT_DISTINCT, never SUM/AVG")
    enc = {"epoch_ms": "epoch milliseconds", "epoch_s": "epoch seconds", "str_space": "text 'yyyy-MM-dd HH:mm:ss'",
           "str_iso": "text ISO-8601 with Z", "str_date": "text 'yyyy-MM-dd'", "str_dmy": "text 'dd/MM/yyyy'"}
    for k, label in enc.items():
        if r.get(k):
            lines.append(f"- {r[k]}: secondary time column stored as {label}")
    for start, end, unit in r.get("epoch_pairs") or []:
        lines.append(f"- {start} / {end}: epoch {'milliseconds' if unit == 'ms' else 'seconds'}; "
                     f"a duration is plain arithmetic on the two (end may be NULL)")
    if r.get("json_dirty"):
        lines.append(f"- {r['json']}: some rows hold malformed JSON")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# Addendum F3 / F4: rows about glossary definitions and about glossary-like words that define nothing.
# ---------------------------------------------------------------------------------------------
DEFN_SHAPES = ["or_cols", "not_in", "like_prefix", "threshold", "mixed", "time_active", "derived", "multi_term"]
DEFN_TARGET_PER_SHAPE = 66
DECOY_WORDS = [("key", 0.17), ("priority", 0.2), ("flagged", 0.13), ("notable", 0.13), ("critical", 0.2), ("premium", 0.17)]
# how a decoy word can refer to a REAL column (train families only are ever sampled from this)
DECOY_WORD_HINT = {
    "key": "as ordinary English ('the key drivers', 'key trends', 'the key takeaway')",
    "notable": "as ordinary English ('a notable spike', 'any notable changes')",
    "flagged": "as ordinary English ('anything that stood out', 'flagged for a follow-up' used loosely) -- never "
               "as a filter on rows",
    "priority": "as ordinary English ('our top priority', 'priority order') OR as the name of a real column",
    "critical": "as ordinary English ('the critical path') OR as a real value of a column",
    "premium": "as ordinary English ('premium pricing') OR as a real value of a column",
}


def defn_pairs(index: dict, ids: list[str], shape: str) -> list[tuple[str, dict]]:
    """(schema id, glossary entry) for every entry a row of this F3 shape can be about."""
    out = []
    for sid in ids:
        for i, e in enumerate(index[sid].get("glossary") or []):
            if shape == "multi_term":
                ok = e["shape"] == "value_list" and len(index[sid]["glossary"]) >= 3
            else:
                ok = e["shape"] == shape
            if ok:
                out.append((sid, e))
    return out


def decoy_real_column_combos(index: dict, ids: list[str]) -> list[dict]:
    """A decoy word that names a real column (`priority`) or a real value of one (`critical`, `premium`)."""
    import re
    combos = []
    for sid in ids:
        d = index[sid]
        for col, pool in (d["pools"] or {}).items():
            if not isinstance(pool, list):
                continue
            for v in pool[:300]:
                if isinstance(v, str) and v.lower() in ("critical", "premium"):
                    combos.append({"sid": sid, "word": v.lower(), "col": col, "value": v})
        for c in d["columns"]:
            if re.fullmatch(r"priority(?:_?[Ll]evel)?", c[0]) and d["pools"].get(c[0]):
                combos.append({"sid": sid, "word": "priority", "col": c[0], "value": None})
    return combos


def sample_addendum(rng: random.Random, index: dict, fact_ids: list[str], mode: str, have, uses, profile: str = "standard",
                    adapt: dict | None = None, all_ids: list[str] | None = None) -> dict:
    """A candidate for F3 (`mode="defn"`) or F4 (`mode="decoy"`). Everything else -- tables, style, time scope,
    complexity, prompt format -- is drawn as usual, then the notes are forced on for the target."""
    prodlike = profile == "prodlike"
    plan: dict = {}
    if mode == "defn":
        shape = _weighted(rng, [(s, (max(0, DEFN_TARGET_PER_SHAPE - have.get(s, 0)) + 2) ** 2) for s in DEFN_SHAPES])
        pairs = defn_pairs(index, fact_ids, shape) or defn_pairs(index, fact_ids, "value_list")
        sid, entry = rng.choices(pairs, weights=[1.0 / (1 + uses.get(e["term"], 0)) ** 1.5 for _, e in pairs])[0]
        plan = {"shape": shape, "term": entry["term"], "entry_shape": entry["shape"]}
        target = sid
    else:
        word = _weighted(rng, DECOY_WORDS)
        combos = [c for c in decoy_real_column_combos(index, fact_ids)]
        want_real = rng.random() < 0.46 and combos
        if want_real:
            c = rng.choices(combos, weights=[1.0 / (1 + uses.get(c["sid"] + c["col"], 0)) ** 1.2 for c in combos])[0]
            plan = {"mode": "real_column", "word": c["word"], "col": c["col"], "value": c["value"]}
            target = c["sid"]
        else:
            plan = {"mode": "english", "word": word}
            target = rng.choice([s for s in fact_ids if index[s].get("instructions") or index[s].get("glossary")] or fact_ids)
    sample = sample_candidate(rng, index, all_ids or fact_ids, profile=profile, adapt=adapt, target_ids=[target])
    tables = sample["table_ids"]
    if mode == "defn" or rng.random() < (0.85 if prodlike else 0.55):
        sample["notes_for"] = {target} | {t for t in tables if t != target and (prodlike or rng.random() < 0.5)}
    else:
        sample["notes_for"] = set()
    sample["overview_for"] = {t for t in tables if index[t].get("overview")} if (prodlike or len(tables) > 1) else set()
    sample["show_notes"] = bool(sample["notes_for"])
    sample["notes_must_apply"] = mode == "defn"
    if sample["format"] not in pf.NOTES_CAPABLE and (sample["notes_for"] or sample["overview_for"]):
        sample["format"] = pf.pick(rng, frozenset(), require_notes=True)
    if mode == "defn":
        sample["defn"] = plan
        sample["notes_style_kwargs"] = {"force_terms": [plan["term"]], "min_terms": 3 if plan["shape"] == "multi_term" else 0}
        if plan["shape"] == "time_active":  # the definition carries its own time window
            sample["time_scope"] = "definition"
            sample["feature_families"] = [f for f in sample["feature_families"] if f not in ("rolling_window", "calendar_window")]
    else:
        sample["decoy"] = plan
        sample["notes_style_kwargs"] = {}
        if plan["mode"] == "real_column" and plan["word"] == "priority":
            sample["question_style"] = "exact_column"  # the word IS the column's name
    return sample
