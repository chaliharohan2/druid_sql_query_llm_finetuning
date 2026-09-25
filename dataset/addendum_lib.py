"""Shared by addendum_apply.py (existing rows) and generate_v2.py (new rows): F5 nullability.

Not AI training or inference code.
"""
from __future__ import annotations

import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import validators as V  # noqa: E402
from validators import sqlparse as sp  # noqa: E402


# SQL or Druid syntax inside a question: backticks, function names and calls, upper-case SQL keywords and
# aggregate names. A bare `__time` is a column name, not a SQL fragment, and stays.
SQLISH = re.compile(
    r"`|\b(?:TIME_FLOOR|TIME_SHIFT|TIME_EXTRACT|TIME_FORMAT|TIME_PARSE|MILLIS_TO_TIMESTAMP|APPROX_\w+|MV_\w+|JSON_VALUE|"
    r"PARSE_JSON|TRY_PARSE_JSON|UNNEST|CURRENT_TIMESTAMP|LOOKUP|GROUP BY|ORDER BY|SELECT|HAVING|COALESCE|CAST|INTERVAL|"
    r"NULLIF|WHERE|DISTINCT|COUNT|SUM|AVG|MIN|MAX|LATEST|EARLIEST)\b|\b\w+\([^()]*\)")


def sql_of(rec: dict) -> str:
    return rec["meta"].get("gold_sql") or rec["messages"][-1]["content"]


def clean_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.replace('"', ""))


def used_datasources(sql: str) -> set[str]:
    try:
        return set(sp.table_aliases(sp.parse(sql)).values())
    except Exception:  # noqa: BLE001
        return set()


def nullable_override(rec: dict, index: dict, rng: random.Random, ref_weight: float) -> tuple[dict, dict]:
    """{table id: nullable column names} plus counts for the report. Never turns a NULLABLE column into
    NOT NULL, never touches the time column, and leaves columns alone that the gold SQL uses in epoch /
    duration arithmetic without an IS NOT NULL guard (CONVENTIONS.md: such a column must not be
    declared nullable behind the query's back)."""
    m = rec["meta"]
    sql = sql_of(rec) if not m.get("unanswerable") else ""
    try:
        refs = {c for _, c in sp.referenced_columns(sp.parse(sql))} if sql else set()
    except Exception:  # noqa: BLE001
        refs = set()
    guarded = {c for c in re.findall(r"(\w+)\s+IS\s+NOT\s+NULL", clean_sql(sql), re.I)}
    used_ds = used_datasources(sql) if sql else set()
    override, stat = {}, Counter()
    for t in m["prompt_tables"]:
        d = index[t]
        nontime = [c[0] for c in d["columns"] if c[0] != d["time_col"]]
        base = set(d.get("nullable_columns") or []) & set(nontime)
        protected = (V.epoch_arith_columns(sql, [t], index) - guarded) if sql else set()
        eligible = [c for c in nontime if c not in base and c not in protected]
        share = rng.uniform(0.60, 0.80)
        need = min(len(eligible), max(0, round(share * len(nontime)) - len(base)))
        weights = [ref_weight if c in refs else 1.0 for c in eligible]
        flips: set[str] = set()
        pool, w = eligible[:], weights[:]
        for _ in range(need):
            if not pool:
                break
            i = rng.choices(range(len(pool)), weights=w)[0]
            flips.add(pool.pop(i))
            w.pop(i)
        nullable = base | flips
        override[t] = sorted(nullable)
        if d["datasource"] in used_ds:
            for c in nontime:
                key = "ref" if c in refs else "unref"
                stat[key + "_n"] += 1
                stat[key + "_nullable"] += c in nullable
        stat["all_n"] += len(nontime)
        stat["all_nullable"] += len(nullable)
        stat["before_nullable"] += len(base)
    return override, stat


