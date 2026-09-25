"""Prompt/schema renderers.

The model must learn Druid SQL, not the shape of one prompt. Every training
example is rendered through one of the formats below, sampled by weight, so the
schema block's serialization, the preamble, the turn structure and the amount of
description all move independently of the SQL being taught.

Each renderer takes (index, schema_ids, question) and returns the list of
non-assistant messages. The assistant turn is appended by the caller and is
identical in every format: bare SQL, no fence, no prose. Only the input varies.

`FORMATS` maps id -> (weight, renderer). Weights are relative.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SQL_TYPE = {"long": "BIGINT", "double": "DOUBLE", "float": "FLOAT",
            "string": "VARCHAR", "array<string>": "VARCHAR"}
DRUID_TYPE = {"long": "LONG", "double": "DOUBLE", "float": "FLOAT",
              "string": "STRING", "array<string>": "STRING"}


# --------------------------------------------------------------- column view
class Col:
    __slots__ = ("name", "sql", "druid", "desc", "is_time", "is_mvd", "is_json")

    def __init__(self, name, ctype, desc, is_time):
        self.name = "__time" if is_time else name
        self.sql = "TIMESTAMP" if is_time else SQL_TYPE[ctype]
        self.druid = "TIMESTAMP" if is_time else DRUID_TYPE[ctype]
        self.desc = desc
        self.is_time = is_time
        self.is_mvd = ctype == "array<string>"
        self.is_json = name.endswith("_json")

    def short(self) -> str:
        """Trimmed description for terse formats.

        MVD and JSON-string columns keep their full description: both report as
        VARCHAR, so the prose is the only thing telling the model what they are.
        """
        if self.is_mvd or self.is_json:
            return self.desc.rstrip(".")
        return self.desc.split(". ")[0].rstrip(".")


_order_rng: random.Random | None = None


def cols(d: dict) -> list[Col]:
    blank = set(d.get("blank_desc_columns") or ())
    out = [Col(n, t, "" if n in blank else s, n == d["time_col"]) for n, t, s in d["columns"]]
    if _order_rng is not None:
        # Real catalogues do not list the timestamp first and the rest in ingest
        # order. Shuffle so neither position teaches the model anything.
        _order_rng.shuffle(out)
        if _order_rng.random() < 0.5:
            out.sort(key=lambda c: not c.is_time)
    return out


def tables(index: dict, schema_ids: list[str]):
    for sid in schema_ids:
        d = index[sid]
        yield d, cols(d)


def raw_cols(d: dict) -> list[Col]:
    """Columns with no blank-description overlay and no reordering.

    Used only by `f_md_sections`: `prompt.py`'s `schema_block()` (the actual
    serving-time renderer) reads `d["columns"]` directly with neither effect,
    and this format must stay byte-identical to it (see module docstring).
    """
    return [Col(n, t, s, n == d["time_col"]) for n, t, s in d["columns"]]


def raw_tables(index: dict, schema_ids: list[str]):
    for sid in schema_ids:
        d = index[sid]
        yield d, raw_cols(d)


def notes_block(d: dict, overview: bool = True, rules: bool = True) -> str:
    """Overview prose (P0-5) and/or instructions + glossary (P0-7) for one datasource."""
    parts = []
    if overview and d.get("overview"):
        parts.append(d["overview"])
    if rules:
        for instr in d.get("instructions") or []:
            parts.append(f"- {instr['text']}")
        for g in d.get("glossary") or []:
            parts.append(f"- {g['definition_text']}")
    return "\n".join(parts)


# Per-render context, set by `render()`: a notes_v3.NotesStyle (varied rule wording, layouts and glossary
# patterns, addendum F1/F2) and a {table id: nullable column names} display override (addendum F5).
# With neither set the output is exactly what v2 shipped, which the tests pin.
_notes_style = None
_nullable_override = None


def notes_text(d: dict, notes_for, overview_for) -> str:
    """What to print for one table: rules only for tables in `notes_for`; the overview
    for tables in `notes_for` or `overview_for`. Nothing for tables in neither."""
    sid = d.get("id")
    overview = (sid in (notes_for or ())) or (sid in (overview_for or ()))
    rules = sid in (notes_for or ())
    if _notes_style is not None:
        return _notes_style.text(d, overview, rules)
    return notes_block(d, overview=overview, rules=rules)


def nullable_columns(d: dict) -> set:
    if _nullable_override is not None and d.get("id") in _nullable_override:
        return set(_nullable_override[d["id"]])
    return set(d.get("nullable_columns") or ())


def _lookup_lines(d: dict, style: str = "prose") -> list[str]:
    out = []
    for lname, lcol, ldesc in d.get("lookups") or []:
        if style == "prose":
            out.append(f"`{lname}`: keyed by `{lcol}`. {ldesc} Use LOOKUP({lcol}, '{lname}').")
        elif style == "comment":
            out.append(f"-- lookup {lname}: LOOKUP({lcol}, '{lname}') -> {ldesc}")
        else:
            out.append(f"{lname} keyed by {lcol} - {ldesc}")
    return out


# ------------------------------------------------------------------ formats
def f_md_sections(index, ids, q, notes_for=None, overview_for=None):
    """F01 - the original. Markdown headings, backticked names, full descriptions.

    Must stay byte-identical to `prompt.py`'s `schema_block()`: uses
    `raw_tables()`, not `tables()`, and ignores `notes_for` (kept as a
    parameter only so `render()`'s uniform call signature still works)."""
    p = ["# Database Schema"]
    for d, cs in raw_tables(index, ids):
        p.append(f"\n## Table: `{d['datasource']}`")
        p.append("### Columns:")
        for c in cs:
            p.append(f"`{c.name}` ({c.sql}): {c.desc}")
        lk = _lookup_lines(d)
        if lk:
            p.append("\n### Lookups:")
            p.extend(lk)
    sys = "You write Apache Druid SQL.\nReturn only the query.\n\n" + "\n".join(p)
    return [("system", sys), ("user", q)]


def f_ddl(index, ids, q, notes_for=None, overview_for=None):
    """F02 - CREATE TABLE DDL with trailing line comments."""
    p = []
    for d, cs in tables(index, ids):
        nb = notes_text(d, notes_for, overview_for)
        if nb:
            p.append("-- " + nb.replace("\n", "\n-- "))
        p.append(f"CREATE TABLE {d['datasource']} (")
        w = max(len(c.name) for c in cs)
        for i, c in enumerate(cs):
            comma = "" if i == len(cs) - 1 else ","
            p.append(f"  {c.name.ljust(w)}  {(c.sql + comma).ljust(11)} -- {c.desc}")
        p.append(");")
        p.extend(_lookup_lines(d, "comment"))
        p.append("")
    sys = ("You are a text-to-SQL engine for Apache Druid 35.0.0.\n"
           "Schema:\n\n" + "\n".join(p).rstrip() +
           "\n\nRespond with one Druid SQL query and nothing else.")
    return [("system", sys), ("user", q)]


def f_compact(index, ids, q, notes_for=None, overview_for=None):
    """F03 - one line per table, types only, no descriptions."""
    p = []
    for d, cs in tables(index, ids):
        sig = ", ".join(f"{c.name}:{c.sql}" for c in cs)
        nb = notes_text(d, notes_for, overview_for)
        if nb:
            p.append("-- " + nb.replace("\n", "\n-- "))
        p.append(f"{d['datasource']}({sig})")
        for lname, lcol, _ in d.get("lookups") or []:
            p.append(f"lookup {lname} on {lcol}")
    return [("system", "Apache Druid SQL. Output the query only.\n\n" + "\n".join(p)),
            ("user", q)]


def f_yaml(index, ids, q, notes_for=None, overview_for=None):
    """F04 - YAML."""
    p = ["dialect: apache-druid", "tables:"]
    for d, cs in tables(index, ids):
        p.append(f"  - name: {d['datasource']}")
        p.append(f"    domain: {d['domain']}")
        nb = notes_text(d, notes_for, overview_for)
        if nb:
            p.append("    notes: |")
            p.extend("      " + line for line in nb.split("\n"))
        p.append("    columns:")
        for c in cs:
            p.append(f"      - name: {c.name}")
            p.append(f"        type: {c.sql}")
            p.append(f'        description: "{c.desc}"')
        if d.get("lookups"):
            p.append("    lookups:")
            for lname, lcol, ldesc in d["lookups"]:
                p.append(f"      - name: {lname}")
                p.append(f"        key: {lcol}")
                p.append(f'        description: "{ldesc}"')
    sys = "Write Apache Druid SQL for the user's question using this schema.\n\n" + "\n".join(p)
    return [("system", sys), ("user", q)]


def f_json(index, ids, q, notes_for=None, overview_for=None):
    """F05 - JSON blob, the shape a programmatic caller would inject."""
    obj = {"dialect": "druid", "tables": []}
    for d, cs in tables(index, ids):
        t = {"table": d["datasource"],
             "columns": [{"name": c.name, "type": c.sql, "description": c.desc} for c in cs]}
        nb = notes_text(d, notes_for, overview_for)
        if nb:
            t["notes"] = nb
        if d.get("lookups"):
            t["lookups"] = [{"name": a, "key": b, "description": c} for a, b, c in d["lookups"]]
        obj["tables"].append(t)
    sys = "Given a schema, emit a single Apache Druid SQL query. No explanation."
    return [("system", sys), ("user", json.dumps(obj, indent=2) + f"\n\n{q}")]


def f_pipe_table(index, ids, q, notes_for=None, overview_for=None):
    """F06 - markdown pipe table, the shape a wiki page or dbt doc gets pasted in as."""
    p = []
    for d, cs in tables(index, ids):
        p.append(f"**{d['datasource']}** ({d['domain']}, {d['rows']} rows)\n")
        nb = notes_text(d, notes_for, overview_for)
        if nb:
            p.append(nb + "\n")
        p.append("| Column | Type | Notes |")
        p.append("| --- | --- | --- |")
        for c in cs:
            p.append(f"| {c.name} | {c.sql} | {c.desc} |")
        for lname, lcol, ldesc in d.get("lookups") or []:
            p.append(f"| LOOKUP({lcol}, '{lname}') | VARCHAR | {ldesc} |")
        p.append("")
    sys = "You answer questions with Apache Druid SQL. Return the bare query."
    return [("system", sys), ("user", "\n".join(p).rstrip() + f"\n\n{q}")]


def f_no_system(index, ids, q, notes_for=None, overview_for=None):
    """F07 - two turns. Everything in the user message, schema before question."""
    p = []
    for d, cs in tables(index, ids):
        p.append(f"Table {d['datasource']}:")
        nb = notes_text(d, notes_for, overview_for)
        if nb:
            p.append("Notes: " + nb.replace("\n", "\n  "))
        for c in cs:
            p.append(f"  - {c.name} ({c.sql}) - {c.short()}")
        for lname, lcol, ldesc in d.get("lookups") or []:
            p.append(f"  - lookup {lname} keyed by {lcol}")
        p.append("")
    body = "\n".join(p).rstrip()
    return [("user", f"{body}\n\nWrite a Druid SQL query: {q}")]


def f_question_first(index, ids, q, notes_for=None, overview_for=None):
    """F08 - question ahead of the schema, so position is not a cue."""
    p = []
    for d, cs in tables(index, ids):
        p.append(f"{d['datasource']}")
        nb = notes_text(d, notes_for, overview_for)
        if nb:
            p.append("  -- " + nb.replace("\n", "\n  -- "))
        for c in cs:
            p.append(f"  {c.name} {c.sql}   {c.short()}")
        for lname, lcol, _ in d.get("lookups") or []:
            p.append(f"  LOOKUP({lcol}, '{lname}')")
        p.append("")
    sys = "Apache Druid SQL assistant. Reply with SQL only."
    return [("system", sys),
            ("user", f"{q}\n\nSchema:\n{chr(10).join(p).rstrip()}")]


def f_verbose_rules(index, ids, q, notes_for=None, overview_for=None):
    """F09 - the heavily prompt-engineered system message a careful team ships."""
    p = ["## Available tables"]
    for d, cs in tables(index, ids):
        p.append(f"\n### {d['datasource']} - {d['domain']}")
        nb = notes_text(d, notes_for, overview_for)
        if nb:
            p.append(nb)
        for c in cs:
            tag = ""
            if c.is_mvd:
                tag = " [multi-value dimension]"
            elif c.is_json:
                tag = " [JSON held as a string]"
            p.append(f"- {c.name} :: {c.sql}{tag} - {c.desc}")
        lk = _lookup_lines(d, "plain")
        if lk:
            p.append("Lookups: " + "; ".join(lk))
    sys = ("You are a senior analytics engineer who writes Apache Druid SQL.\n\n"
           "Rules:\n"
           "1. Return exactly one SQL query. No prose, no markdown fences, no trailing semicolon.\n"
           "2. `__time` is the only TIMESTAMP column. All time filtering goes through it.\n"
           "3. Double-quote every output alias.\n"
           "4. GROUP BY and ORDER BY refer to output positions by ordinal.\n"
           "5. Relative dates resolve against CURRENT_TIMESTAMP.\n"
           "6. Prefer Druid's approximate aggregators over exact ones.\n\n"
           + "\n".join(p))
    return [("system", sys), ("user", q)]


def f_bare(index, ids, q):
    """F10 - names only. No types, no descriptions, no rules."""
    p = []
    for d, cs in tables(index, ids):
        p.append(f"{d['datasource']}: " + ", ".join(c.name for c in cs))
    return [("system", "Druid SQL.\n" + "\n".join(p)), ("user", q)]


def _fmt_time(v):
    """Seeds hold business time in whatever unit each source uses; the prompt
    declares __time as TIMESTAMP, so the sample must look like one."""
    import datetime as _dt
    if isinstance(v, str):
        return v.replace("T", " ").replace("Z", "")
    ms = v if v > 10 ** 12 else v * 1000
    return _dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def f_sample_rows(index, ids, q, seeds=None, notes_for=None, overview_for=None):
    """F11 - schema plus a couple of real rows, the Spider/BIRD convention."""
    p = []
    for d, cs in tables(index, ids):
        p.append(f"Table: {d['datasource']}")
        nb = notes_text(d, notes_for, overview_for)
        if nb:
            p.append("Notes: " + nb.replace("\n", "\n  "))
        p.append("Columns: " + ", ".join(f"{c.name} {c.sql}" for c in cs))
        rows = (seeds or {}).get(d["datasource"], [])[:2]
        if rows:
            p.append("Sample rows:")
            for r in rows:
                cells = []
                for c in cs:
                    v = r.get(d["time_col"] if c.is_time else c.name)
                    if c.is_time:
                        v = _fmt_time(v)
                    if isinstance(v, list):
                        v = ",".join(v)
                    v = str(v)
                    cells.append(v if len(v) <= 28 else v[:25] + "...")
                p.append("  " + " | ".join(cells))
        for lname, lcol, ldesc in d.get("lookups") or []:
            p.append(f"Lookup: {lname} keyed by {lcol} - {ldesc}")
        p.append("")
    sys = "Translate the question into one Apache Druid SQL query. Output SQL only."
    return [("system", sys), ("user", "\n".join(p).rstrip() + f"\n\nQuestion: {q}")]


def f_enterprise_pipe(index, ids, q, notes_for=None, overview_for=None):
    """F13 - enterprise datasource doc export: heading, overview/instructions
    prose, then a COLUMN_NAME | DATA_TYPE | IS_NULLABLE | DESCRIPTION table
    (plan P0-5 format (a)). Structurally close to the DCE production prompt."""
    p = []
    for d, cs in tables(index, ids):
        nullable = nullable_columns(d)
        p.append(f"# Datasource: {d['datasource']}")
        nb = notes_text(d, notes_for, overview_for)
        if nb:
            p.append(nb)
        p.append("")
        p.append("| COLUMN_NAME | DATA_TYPE | IS_NULLABLE | DESCRIPTION |")
        p.append("| --- | --- | --- | --- |")
        for c in cs:
            base_name = c.name if c.name != "__time" else d["time_col"]
            is_null = "YES" if base_name in nullable else "NO"
            p.append(f"| {c.name} | {c.sql} | {is_null} | {c.desc} |")
        for lname, lcol, ldesc in d.get("lookups") or []:
            p.append(f"| LOOKUP({lcol}, '{lname}') | VARCHAR | NO | {ldesc} |")
        p.append("")
    sys = ("You write Apache Druid SQL against the datasource(s) below. Return only the "
          "query, no prose, no markdown fences.\n\n" + "\n".join(p).rstrip())
    return [("system", sys), ("user", q)]


def f_enterprise_backtick(index, ids, q, notes_for=None, overview_for=None):
    """F14 - same enterprise structure as F13, but columns as a backtick list
    with inline nullability instead of a pipe table (plan P0-5 format (b))."""
    p = []
    for d, cs in tables(index, ids):
        nullable = nullable_columns(d)
        p.append(f"## Table: `{d['datasource']}`")
        nb = notes_text(d, notes_for, overview_for)
        if nb:
            p.append(nb)
        p.append("### Columns:")
        for c in cs:
            base_name = c.name if c.name != "__time" else d["time_col"]
            null_txt = "NULLABLE" if base_name in nullable else "NOT NULL"
            p.append(f"`{c.name}` ({c.sql}, {null_txt}): {c.desc}")
        lk = _lookup_lines(d)
        if lk:
            p.append("### Lookups:")
            p.extend(lk)
        p.append("")
    sys = ("You are a Druid SQL assistant. Answer with exactly one Apache Druid 35.0.0 "
          "query and nothing else.\n\n# Database Schema\n\n" + "\n".join(p).rstrip())
    return [("system", sys), ("user", q)]


def f_druid_native(index, ids, q, notes_for=None, overview_for=None):
    """F12 - Druid's own type vocabulary, the shape the web console shows."""
    p = []
    for d, cs in tables(index, ids):
        p.append(f"datasource: {d['datasource']}")
        nb = notes_text(d, notes_for, overview_for)
        if nb:
            p.append("# " + nb.replace("\n", "\n# "))
        for c in cs:
            t = c.druid
            if c.is_mvd:
                t = "STRING (multi-value)"
            p.append(f"  {c.name.ljust(24)} {t}")
        for lname, lcol, _ in d.get("lookups") or []:
            p.append(f"  {('LOOKUP ' + lname).ljust(24)} keyed by {lcol}")
        p.append("")
    sys = ("Apache Druid 35.0.0. Column types below are Druid native types "
           "(LONG/STRING/DOUBLE map to BIGINT/VARCHAR/DOUBLE in SQL).\n"
           "Answer with a single query.\n\n" + "\n".join(p).rstrip())
    return [("system", sys), ("user", q)]


# What each format is capable of expressing. An example that needs a capability
# no format supplies would be unanswerable from its own prompt, so the sampler
# only draws from formats whose capabilities cover the example.
#   desc      - per-column prose
#   lookup    - lookup name + key column
#   json_keys - the key names inside a JSON-as-string column
#   mvd       - marks a multi-value dimension as multi-value
ALL = frozenset({"desc", "lookup", "json_keys", "mvd"})
# Formats whose renderer accepts `notes_for` (table overview/instructions/
# glossary prose, plan P0-7). The sampler only routes instruction-bearing
# examples to one of these. `md_sections` is deliberately excluded even
# though its renderer accepts the parameter: prompt.py's `schema_block()` is
# the actual serving-time renderer and has no notes support, and its
# docstring requires this format to stay byte-identical to that. Adding
# notes here would train the model on a system-prompt shape production
# never sends.
NOTES_CAPABLE = frozenset({"ddl", "pipe_table", "verbose_rules", "enterprise_pipe", "enterprise_backtick",
                          "yaml", "json", "compact", "no_system", "question_first", "sample_rows",
                          "druid_native"})

# Weights are close to equal (Section 7: "roughly balanced", no-system ~8%). md_sections is
# a little heavier because it is the format `prompt.py` serves.
FORMATS = {
    "md_sections":   (16, f_md_sections, ALL),
    "ddl":           (7, f_ddl, ALL),
    "compact":       (6, f_compact, frozenset({"lookup"})),
    "yaml":          (7, f_yaml, ALL),
    "json":          (7, f_json, ALL),
    "pipe_table":    (7, f_pipe_table, ALL),
    "no_system":     (8, f_no_system, ALL),
    "question_first": (7, f_question_first, ALL),
    "verbose_rules": (7, f_verbose_rules, ALL),
    "bare":          (8, f_bare, frozenset()),
    # sample rows show the MVD contents and the JSON keys as literal data
    "sample_rows":   (7, f_sample_rows, frozenset({"lookup", "json_keys", "mvd"})),
    "druid_native":  (6, f_druid_native, frozenset({"lookup", "mvd"})),
    "enterprise_pipe":     (8, f_enterprise_pipe, ALL),
    "enterprise_backtick": (8, f_enterprise_backtick, ALL),
}


def requirements(index: dict, schema_ids: list[str], sql: str) -> frozenset:
    """What an example's prompt must carry for its SQL to be derivable."""
    need = set()
    for sid in schema_ids:
        d = index[sid]
        for c in cols(d):
            if c.is_json and c.name in sql:
                need |= {"json_keys", "desc"}
            if c.is_mvd and c.name in sql:
                need.add("mvd")
        for lname, _, _ in d.get("lookups") or []:
            if lname in sql:
                need.add("lookup")
    return frozenset(need)


def render(fmt: str, index, ids, q, seeds=None, order_seed=None, notes_for=None, overview_for=None,
           notes_style=None, nullable_override=None):
    global _order_rng, _notes_style, _nullable_override
    _order_rng = random.Random(order_seed) if order_seed is not None else None
    _notes_style, _nullable_override = notes_style, nullable_override
    fn = FORMATS[fmt][1]
    try:
        if fmt == "sample_rows":
            return fn(index, ids, q, seeds=seeds, notes_for=notes_for, overview_for=overview_for)
        if fmt in NOTES_CAPABLE:
            return fn(index, ids, q, notes_for=notes_for, overview_for=overview_for)
        return fn(index, ids, q)
    finally:
        _order_rng = _notes_style = _nullable_override = None


def pick(rng: random.Random, need: frozenset = frozenset(), require_notes: bool = False,
         multipliers: dict | None = None) -> str:
    names = [n for n in FORMATS if need <= FORMATS[n][2]
            and (not require_notes or n in NOTES_CAPABLE)]
    if not names:
        names = [n for n in FORMATS if need <= FORMATS[n][2]]
    m = multipliers or {}
    return rng.choices(names, weights=[FORMATS[n][0] * m.get(n, 1.0) for n in names])[0]


def load_seeds() -> dict:
    out = {}
    for f in sorted((ROOT / "seeds").glob("*.json")):
        out[f.stem] = [json.loads(l) for l in f.read_text().splitlines()[:3] if l.strip()]
    return out
