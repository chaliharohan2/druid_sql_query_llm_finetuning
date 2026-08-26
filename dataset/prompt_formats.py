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
    out = [Col(n, t, s, n == d["time_col"]) for n, t, s in d["columns"]]
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
def f_md_sections(index, ids, q):
    """F01 - the original. Markdown headings, backticked names, full descriptions."""
    p = ["# Database Schema"]
    for d, cs in tables(index, ids):
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


def f_ddl(index, ids, q):
    """F02 - CREATE TABLE DDL with trailing line comments."""
    p = []
    for d, cs in tables(index, ids):
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


def f_compact(index, ids, q):
    """F03 - one line per table, types only, no descriptions."""
    p = []
    for d, cs in tables(index, ids):
        sig = ", ".join(f"{c.name}:{c.sql}" for c in cs)
        p.append(f"{d['datasource']}({sig})")
        for lname, lcol, _ in d.get("lookups") or []:
            p.append(f"lookup {lname} on {lcol}")
    return [("system", "Apache Druid SQL. Output the query only.\n\n" + "\n".join(p)),
            ("user", q)]


def f_yaml(index, ids, q):
    """F04 - YAML."""
    p = ["dialect: apache-druid", "tables:"]
    for d, cs in tables(index, ids):
        p.append(f"  - name: {d['datasource']}")
        p.append(f"    domain: {d['domain']}")
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


def f_json(index, ids, q):
    """F05 - JSON blob, the shape a programmatic caller would inject."""
    obj = {"dialect": "druid", "tables": []}
    for d, cs in tables(index, ids):
        t = {"table": d["datasource"],
             "columns": [{"name": c.name, "type": c.sql, "description": c.desc} for c in cs]}
        if d.get("lookups"):
            t["lookups"] = [{"name": a, "key": b, "description": c} for a, b, c in d["lookups"]]
        obj["tables"].append(t)
    sys = "Given a schema, emit a single Apache Druid SQL query. No explanation."
    return [("system", sys), ("user", json.dumps(obj, indent=2) + f"\n\n{q}")]


def f_pipe_table(index, ids, q):
    """F06 - markdown pipe table, the shape a wiki page or dbt doc gets pasted in as."""
    p = []
    for d, cs in tables(index, ids):
        p.append(f"**{d['datasource']}** ({d['domain']}, {d['rows']} rows)\n")
        p.append("| Column | Type | Notes |")
        p.append("| --- | --- | --- |")
        for c in cs:
            p.append(f"| {c.name} | {c.sql} | {c.desc} |")
        for lname, lcol, ldesc in d.get("lookups") or []:
            p.append(f"| LOOKUP({lcol}, '{lname}') | VARCHAR | {ldesc} |")
        p.append("")
    sys = "You answer questions with Apache Druid SQL. Return the bare query."
    return [("system", sys), ("user", "\n".join(p).rstrip() + f"\n\n{q}")]


def f_no_system(index, ids, q):
    """F07 - two turns. Everything in the user message, schema before question."""
    p = []
    for d, cs in tables(index, ids):
        p.append(f"Table {d['datasource']}:")
        for c in cs:
            p.append(f"  - {c.name} ({c.sql}) - {c.short()}")
        for lname, lcol, ldesc in d.get("lookups") or []:
            p.append(f"  - lookup {lname} keyed by {lcol}")
        p.append("")
    body = "\n".join(p).rstrip()
    return [("user", f"{body}\n\nWrite a Druid SQL query: {q}")]


def f_question_first(index, ids, q):
    """F08 - question ahead of the schema, so position is not a cue."""
    p = []
    for d, cs in tables(index, ids):
        p.append(f"{d['datasource']}")
        for c in cs:
            p.append(f"  {c.name} {c.sql}   {c.short()}")
        for lname, lcol, _ in d.get("lookups") or []:
            p.append(f"  LOOKUP({lcol}, '{lname}')")
        p.append("")
    sys = "Apache Druid SQL assistant. Reply with SQL only."
    return [("system", sys),
            ("user", f"{q}\n\nSchema:\n{chr(10).join(p).rstrip()}")]


def f_verbose_rules(index, ids, q):
    """F09 - the heavily prompt-engineered system message a careful team ships."""
    p = ["## Available tables"]
    for d, cs in tables(index, ids):
        p.append(f"\n### {d['datasource']} - {d['domain']}")
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


def f_sample_rows(index, ids, q, seeds=None):
    """F11 - schema plus a couple of real rows, the Spider/BIRD convention."""
    p = []
    for d, cs in tables(index, ids):
        p.append(f"Table: {d['datasource']}")
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


def f_druid_native(index, ids, q):
    """F12 - Druid's own type vocabulary, the shape the web console shows."""
    p = []
    for d, cs in tables(index, ids):
        p.append(f"datasource: {d['datasource']}")
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

FORMATS = {
    "md_sections":   (22, f_md_sections, ALL),
    "ddl":           (12, f_ddl, ALL),
    "compact":        (7, f_compact, frozenset({"lookup"})),
    "yaml":           (8, f_yaml, ALL),
    "json":           (7, f_json, ALL),
    "pipe_table":     (9, f_pipe_table, ALL),
    "no_system":      (8, f_no_system, ALL),
    "question_first": (6, f_question_first, ALL),
    "verbose_rules": (10, f_verbose_rules, ALL),
    "bare":           (4, f_bare, frozenset()),
    # sample rows show the MVD contents and the JSON keys as literal data
    "sample_rows":    (7, f_sample_rows, frozenset({"lookup", "json_keys", "mvd"})),
    "druid_native":   (6, f_druid_native, frozenset({"lookup", "mvd"})),
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


def render(fmt: str, index, ids, q, seeds=None, order_seed=None):
    global _order_rng
    _order_rng = random.Random(order_seed) if order_seed is not None else None
    fn = FORMATS[fmt][1]
    try:
        if fmt == "sample_rows":
            return fn(index, ids, q, seeds=seeds)
        return fn(index, ids, q)
    finally:
        _order_rng = None


def pick(rng: random.Random, need: frozenset = frozenset()) -> str:
    names = [n for n in FORMATS if need <= FORMATS[n][2]]
    return rng.choices(names, weights=[FORMATS[n][0] for n in names])[0]


def load_seeds() -> dict:
    out = {}
    for f in sorted((ROOT / "seeds").glob("*.json")):
        out[f.stem] = [json.loads(l) for l in f.read_text().splitlines()[:3] if l.strip()]
    return out
