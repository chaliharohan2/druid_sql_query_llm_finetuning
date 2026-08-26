"""The serving-time prompt template.

This is `md_sections` in prompt_formats.py, and it must stay byte-identical to
whatever inference.py sends. Training renders through all twelve formats there;
this one is the heaviest in the mix precisely because it is the one served.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREAMBLE = "You write Apache Druid SQL.\nReturn only the query."
SQL_TYPE = {"long": "BIGINT", "double": "DOUBLE", "float": "FLOAT",
            "string": "VARCHAR", "array<string>": "VARCHAR"}


def load_index() -> dict:
    return json.loads((ROOT / "schema_index.json").read_text())


def schema_block(index: dict, schema_ids: list[str]) -> str:
    parts = ["# Database Schema"]
    for sid in schema_ids:
        d = index[sid]
        parts.append(f"\n## Table: `{d['datasource']}`")
        parts.append("### Columns:")
        for name, ctype, desc in d["columns"]:
            shown = "__time" if name == d["time_col"] else name
            sql_t = "TIMESTAMP" if name == d["time_col"] else SQL_TYPE[ctype]
            parts.append(f"`{shown}` ({sql_t}): {desc}")
        if d.get("lookups"):
            parts.append("\n### Lookups:")
            for lname, lcol, ldesc in d["lookups"]:
                parts.append(f"`{lname}`: keyed by `{lcol}`. {ldesc} Use LOOKUP({lcol}, '{lname}').")
    return "\n".join(parts)


def system_prompt(index: dict, schema_ids: list[str]) -> str:
    return f"{PREAMBLE}\n\n{schema_block(index, schema_ids)}"
