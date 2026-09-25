"""Table overview prose, table-level instructions, and a business glossary (P0-5, P0-7).

v1 has zero examples where an instruction changes the correct SQL. This module
generates, per schema:

  - `overview`     one paragraph: what the table is for, when to use it over
                    a sibling (P0-5, required in >=40% of multi-table prompts).
  - `instructions` a short list of rules. Each carries the structured
                    constraint it imposes (`required_predicate` /
                    `forbidden_construct`) so G6 can check a generated
                    example actually obeyed it. >=70% of a schema's
                    instructions must be capable of changing a correct
                    answer -- these are, because they gate a predicate the
                    naive query would otherwise omit.
  - `glossary`      business terms that resolve to a predicate on a real
                    column, e.g. "a churn risk case" -> `risk_tier = 'high'`.

Instructions are schema-level; whether a *specific* generated example is
actually bound by one (and so must satisfy G6) is decided by the sampler,
which is recorded per example in `required_predicates` / `forbidden_constructs`.

Not AI training or inference code: this only produces Druid fixtures.
"""
from __future__ import annotations

import random

OVERVIEW_TEMPLATES = [
    "{purpose} Use this table for questions scoped to {domain_lc}; it is the "
    "system of record for individual {noun}, not a daily rollup.",
    "{purpose} Each row is one {noun_singular}. Prefer this table whenever the "
    "question is about {domain_lc} activity rather than a pre-aggregated summary.",
    "{purpose} This is the event-level table for {domain_lc}: high volume, one "
    "row per {noun_singular}. A question asking for a running total or a "
    "current snapshot may be better served by a dimension table instead.",
]

_NOUN_SINGULAR_OVERRIDE = {
    "log records": "log record", "API requests": "API request", "edge requests": "edge request",
    "playback heartbeats": "playback heartbeat", "trips": "trip", "orders": "order",
    "parcel scans": "parcel scan", "meter readings": "meter reading", "observations": "observation",
    "shifts": "shift", "email events": "email event", "stock movements": "stock movement",
    "trades": "trade", "ticket updates": "ticket update", "vehicle samples": "vehicle sample",
    "authorisation attempts": "authorisation attempt", "predictions": "prediction",
    "till lines": "till line", "network flows": "network flow", "events": "event",
    "impressions": "impression", "sensor readings": "sensor reading", "products": "product",
    "transactions": "transaction", "alerts": "alert", "sessions": "session",
    "call records": "call record", "drivers": "driver", "carriers": "carrier",
    "warehouses": "warehouse", "segments": "segment", "stores": "store", "records": "record",
}


def make_overview(family_key: str, domain: str, purpose: str, noun: str, rng: random.Random) -> str:
    singular = _NOUN_SINGULAR_OVERRIDE.get(noun, noun.rstrip("s"))
    tpl = rng.choice(OVERVIEW_TEMPLATES)
    return tpl.format(purpose=purpose, domain_lc=domain[0].lower() + domain[1:],
                      noun=noun, noun_singular=singular)


# Instruction generators. Each takes the schema dict (post role/nullability
# assignment) and returns None (not applicable) or a dict:
#   {"kind", "text", "binding_column", "applies_to"}
# `kind` is what validators.g6_instructions keys on: whether an instruction
# BINDS a given SQL query, and whether the query obeys it, is decided from the
# SQL itself, so the constraint metadata never depends on what a teacher claims.
def _instr_nullable_metric(s: dict, rng: random.Random):
    nullable = s.get("nullable_columns") or set()
    metrics = [m["name"] for m in s["roles"]["metrics"]]
    candidates = [c for c in nullable if c in metrics]
    if not candidates:
        return None
    col = rng.choice(sorted(candidates))
    return {"kind": "nullable_metric", "binding_column": col, "applies_to": "metric",
            "text": f"When aggregating `{col}`, only consider rows where `{col}` IS NOT NULL; "
                    f"unresolved rows should not be silently coerced into the average."}


def _instr_unique_by(s: dict, rng: random.Random):
    hi = s["roles"].get("hi_card") or []
    if not hi:
        return None
    col = rng.choice(hi)
    return {"kind": "unique_by", "binding_column": col, "applies_to": "count",
            "text": f"Records are unique per `{col}`; do not use DISTINCT when counting "
                    f"{s.get('noun', 'records')} by it."}


def _instr_exact_distinct(s: dict, rng: random.Random):
    hi = s["roles"].get("hi_card") or []
    if not hi:
        return None
    col = rng.choice(hi)
    return {"kind": "exact_distinct", "binding_column": col, "applies_to": "count",
            "text": f"Use an exact `COUNT(DISTINCT {col})` for reporting on unique "
                    f"{col.replace('_', ' ')}s; approximate counts are not acceptable for this field."}


def _instr_exclude_flag(s: dict, rng: random.Random):
    flags = [f for f in (s["roles"].get("flags") or []) if f in ("is_test_record", "is_deleted")]
    if not flags:
        return None
    col = rng.choice(sorted(flags))
    what = "test records" if col == "is_test_record" else "soft-deleted records"
    return {"kind": "exclude_flag", "binding_column": col, "applies_to": "any",
            "text": f"Reports on this table always exclude {what}: keep only rows where `{col}` = 0."}


def _instr_duration_end(s: dict, rng: random.Random):
    pairs = s["roles"].get("epoch_pairs") or []
    if not pairs:
        return None
    start, end, _ = pairs[0]
    return {"kind": "duration_end", "binding_column": end, "applies_to": "metric",
            "text": f"For duration questions, only consider rows where `{end}` IS NOT NULL "
                    f"(an activity with no end time has not finished)."}


INSTRUCTION_FNS = [_instr_nullable_metric, _instr_unique_by, _instr_exact_distinct,
                   _instr_exclude_flag, _instr_duration_end]


def make_instructions(s: dict, rng: random.Random, n: int = 2) -> list[dict]:
    out, seen, cols_used = [], set(), set()
    fns = INSTRUCTION_FNS[:]
    rng.shuffle(fns)
    for fn in fns:
        if len(out) >= n:
            break
        instr = fn(s, rng)
        if not instr or instr["text"] in seen:
            continue
        # two rules about the same column would be contradictory (e.g. "exact
        # distinct" vs "no DISTINCT"), so each column is bound at most once
        if instr["binding_column"] in cols_used:
            continue
        cols_used.add(instr["binding_column"])
        seen.add(instr["text"])
        out.append(instr)
    return out


def make_glossary(s: dict, rng: random.Random, n: int = 1) -> list[dict]:
    """A business term resolving to a predicate on one of the domain's OWN
    categorical columns (never widening filler such as `locale_code`)."""
    from widen import is_filler
    dims = [d for d in s["roles"].get("dims", [])
            if not is_filler(d) and s["pools"].get(d) and len(s["pools"][d]) >= 3
            and all(v != "" for v in s["pools"][d])]
    if not dims:
        return []
    out = []
    for _ in range(min(n, len(dims))):
        col = rng.choice(dims)
        pool = s["pools"][col]
        k = rng.randint(1, min(3, len(pool)))
        values = rng.sample(pool, k)
        noun = s.get("noun", "records")
        singular = _NOUN_SINGULAR_OVERRIDE.get(noun, noun.rstrip("s"))
        term = f"{rng.choice(['priority', 'flagged', 'notable', 'key'])} {singular}"
        vlist = ", ".join(f"`{v}`" for v in values)
        pred = " OR ".join(f"{col} = '{v}'" for v in values) if len(values) > 1 else f"{col} = '{values[0]}'"
        out.append({"term": term,
                    "definition_text": f"A *{term}* is one where `{col}` is {vlist}.",
                    "predicate": pred, "binding_column": col, "values": values})
    return out
