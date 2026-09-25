#!/usr/bin/env python3
"""Expand domain families into datasource specs, seed rows and a role index.

Each family in families.py becomes two or three datasources that differ in
naming convention, column subset, secondary-time encoding and enrichment shape.
The nine hand-written schemas from build_schemas.py are re-emitted alongside
them against the same time anchor, so every datasource's rows span the same
30 days ending now and CURRENT_TIMESTAMP-relative queries return rows.

The output that matters downstream is schema_index.json: besides the columns it
records a `roles` block naming which column plays which part (the time column,
the low-cardinality dimensions, the measures, the multi-value dimension, the
JSON string, the lookup key, the epoch and string time columns, the join key).
Query templates bind to roles, never to column names, which is what lets one
template render against sixty different schemas.

Not AI training or inference code: this only produces Druid fixtures.
"""
from __future__ import annotations

import json
import random
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import build_schemas as legacy
from families import DIMS, FAMILIES
from instructions import _NOUN_SINGULAR_OVERRIDE, make_glossary, make_instructions, make_overview
from roles import classify_metric, nullable_columns, blank_description_columns
from schema_view import NOUNS
from widen import widen

ROOT = Path(__file__).resolve().parent
for _f in FAMILIES:  # nouns for the overview prose of the v2 families
    if _f.get("noun"):
        NOUNS[_f["key"]] = _f["noun"]
        _NOUN_SINGULAR_OVERRIDE[_f["noun"]] = _f["noun_singular"]
SPECS, SEEDS = ROOT / "specs", ROOT / "seeds"
NOW = legacy.NOW
SPAN_DAYS = legacy.SPAN_DAYS
N_G4_SEEDS = 3  # independently-seeded datasource copies for G4 result-agreement
NULL_ROW_RATE = 0.12  # share of rows that get NULL in a nullable column

# Width tiers, plan P0-5: 7-25 (40%), 26-80 (40%), 81-200 (20%). v1 and the
# hand-written schemas stay narrow by construction (they land in 7-25 without
# help); v0 and v2 variants, plus three of the five dimension tables, are
# widened toward the other two tiers.
WIDEN_WIDE_DIMS = {"dim_stores", "dim_warehouses", "dim_carriers"}


def width_target_total(kind: str, rng: random.Random) -> int | None:
    if kind == "v0":
        # Most v0 variants (already the "full" variant) go very wide; the
        # rest land in the middle tier, which is how the 81-200 slice reaches
        # its ~20% share without every schema needing 100+ columns.
        return rng.randint(85, 180) if rng.random() < 0.72 else rng.randint(26, 78)
    if kind == "v2":
        return rng.randint(26, 78)
    if kind == "dim_wide":
        return rng.randint(28, 60)
    return None

# Shared identifier pools. Fact and dimension tables draw join keys from the
# same pool so that a join actually matches rows.
ID_POOLS: dict[str, list[str]] = {
    "hex10": [f"{i:010x}" for i in range(400)],
    "hex12": [f"{i:012x}" for i in range(400)],
    "hex16": [f"{i:016x}" for i in range(600)],
    "pod": [f"pod-{i:04d}" for i in range(120)],
    "host": [f"srv-{i:03d}" for i in range(60)],
    "driver": [f"drv-{i:03d}" for i in range(60)],
    "hub": [f"hub-{i:02d}" for i in range(24)],
    "sku": [f"SKU-{i:04d}" for i in range(120)],
    "bin": [f"{400000 + i * 137:06d}" for i in range(60)],
    "ip": [f"10.{i // 256 % 256}.{i % 256}.{(i * 7) % 254 + 1}" for i in range(300)],
    "bed": [f"bed-{i:03d}" for i in range(40)],
}

# Secondary time columns (P0-6). Three things vary independently so that no
# single name, encoding or hint string becomes the cue for "this needs
# converting": the column NAME (arbitrary realistic names; a `_ms`/`_epoch_s`
# suffix on at most ~25% of epoch columns), the ENCODING (six of them) and the
# HINT LEVEL of the description (none / semantic / explicit).
ENC = {
    "epoch_ms": dict(
        type="long", sem="stored as epoch milliseconds",
        expl="as epoch MILLISECONDS. Not a TIMESTAMP -- convert with MILLIS_TO_TIMESTAMP before using time functions."),
    "epoch_s": dict(
        type="long", sem="stored as epoch seconds (not milliseconds)",
        expl="as epoch SECONDS. Not a TIMESTAMP -- multiply by 1000 and convert with MILLIS_TO_TIMESTAMP."),
    "str_space": dict(
        type="string", sem="held as text like '2026-03-14 08:00:00'",
        expl="held as a string, format 'yyyy-MM-dd HH:mm:ss'. Parse with TIME_PARSE before using time functions."),
    "str_iso": dict(
        type="string", sem="held as an ISO-8601 string like '2026-03-14T08:00:00Z'",
        expl="held as an ISO-8601 string, format \"yyyy-MM-dd'T'HH:mm:ss'Z'\". Parse with TIME_PARSE and an explicit format."),
    "str_date": dict(
        type="string", sem="held as date-only text like '2026-03-14'",
        expl="held as a date string, format 'yyyy-MM-dd'. Parse with TIME_PARSE before using time functions."),
    "str_dmy": dict(
        type="string", sem="held as day-first text like '14/03/2026'",
        expl="held as a date string, format 'dd/MM/yyyy'. Parse with TIME_PARSE and an explicit format."),
}
SEC_ENCODINGS = list(ENC)
# (name, what it is). Neutral names carry no encoding suffix.
TIME_NAMES = [
    ("end_time", "End of the activity"), ("answer_time", "When the request was answered"),
    ("closed_on", "When the record was closed"), ("action_date", "When the action happened"),
    ("evt_ts", "Event timestamp from the source system"), ("created", "Creation time of the record"),
    ("last_seen", "Most recent time the entity was seen"), ("dispatch_at", "When the item was dispatched"),
    ("recorded_at", "Source-system record time"), ("modified_on", "Last modification time"),
    ("logged_at", "When the source logged the event"), ("received", "Upstream receive time"),
    ("queued", "When the item was queued"), ("settled_on", "When the item was settled"),
]
SUFFIX_NAMES = {  # only ever paired with the matching encoding
    "epoch_ms": [("received_at_ms", "Upstream receive time"), ("closed_at_ms", "When the record was closed")],
    "epoch_s": [("queued_at_epoch_s", "Enqueue time"), ("created_epoch_s", "Creation time of the record")],
}
# start/end pairs for duration questions ("average handling time"): plain
# arithmetic on two epoch columns, no TIMESTAMPDIFF. (start, end, what)
PAIR_NAMES = [
    ("answered_at", "ended_at", "the interaction"), ("start_time", "end_time", "the activity"),
    ("opened_on", "closed_on", "the case"), ("dispatch_at", "arrived_at", "the dispatch"),
    ("picked_up", "delivered", "the delivery"), ("queued", "completed", "the job"),
    ("submitted_ts", "resolved_ts", "the request"), ("begin_ts", "finish_ts", "the run"),
]

# Generic JSON payload columns for wide tables that have none of their own, so
# JSON extraction is practised on many more schemas than the three families
# that ship one. (name, description with keys, {key: values})
JSON_BANK = [
    ("request_context_json", "Per-request context. Keys: tenant, region, client_version.",
     {"tenant": ["acme", "globex", "initech", "umbrella"], "region": ["eu", "us", "apac"],
      "client_version": ["3.1", "3.2", "4.0"]}),
    ("extra_attrs_json", "Free-form attributes attached by the source. Keys: source_app, priority, is_manual.",
     {"source_app": ["portal", "api", "batch", "mobile"], "priority": ["low", "normal", "urgent"],
      "is_manual": ["true", "false"]}),
    ("device_info_json", "Client device details. Keys: os, browser, form_factor.",
     {"os": ["ios", "android", "windows", "macos"], "browser": ["chrome", "safari", "edge", "firefox"],
      "form_factor": ["phone", "tablet", "desktop"]}),
    ("audit_meta_json", "Audit metadata. Keys: change_reason, approver_group, ticket_type.",
     {"change_reason": ["correction", "migration", "customer_request", "policy"],
      "approver_group": ["ops", "finance", "security"], "ticket_type": ["incident", "change", "task"]}),
    ("routing_json", "Routing decisions. Keys: queue, tier, fallback.",
     {"queue": ["q_fast", "q_bulk", "q_manual"], "tier": ["t1", "t2", "t3"], "fallback": ["true", "false"]}),
    ("experiment_json", "Experiment assignment. Keys: cohort, variant, holdout.",
     {"cohort": ["c_a", "c_b", "c_c"], "variant": ["control", "treatment"], "holdout": ["true", "false"]}),
]

HINT_LEVELS = (("none", 0.30), ("semantic", 0.55), ("explicit", 0.15))


def _pick_weighted(r: float, items):
    acc = 0.0
    for v, w in items:
        acc += w
        if r < acc:
            return v
    return items[-1][0]


def pick_hint_level(schema_id: str) -> str:
    return _pick_weighted(random.Random(zlib.crc32(f"{schema_id}_hint".encode())).random(), HINT_LEVELS)


def time_desc(what: str, enc: str, level: str) -> str:
    if level == "none":
        return f"{what}."
    if level == "semantic":
        return f"{what}, {ENC[enc]['sem']}."
    return f"{what} {ENC[enc]['expl']}" if enc.startswith("epoch") else f"{what}, {ENC[enc]['expl']}"


def pick_secondary(schema_id: str, taken: set[str]) -> tuple[str, str, str]:
    """(encoding, column name, what-it-is), deterministic per schema id."""
    rng = random.Random(zlib.crc32(f"{schema_id}_sec".encode()))
    enc = rng.choice(SEC_ENCODINGS)
    if enc in SUFFIX_NAMES and rng.random() < 0.25:
        pool = SUFFIX_NAMES[enc]
    else:
        pool = TIME_NAMES
    pool = [p for p in pool if p[0] not in taken] or TIME_NAMES
    name, what = rng.choice(pool)
    return enc, name, what


def pick_pair(schema_id: str, taken: set[str]):
    """~35% of generated fact schemas get an epoch start/end pair. Returns None or
    (start, end, unit, what)."""
    rng = random.Random(zlib.crc32(f"{schema_id}_pair".encode()))
    if rng.random() >= 0.35:
        return None
    options = [p for p in PAIR_NAMES if p[0] not in taken and p[1] not in taken]
    if not options:
        return None
    start, end, what = rng.choice(options)
    return start, end, ("ms" if rng.random() < 0.7 else "s"), what


def camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in rest)


def ts_millis(rng: random.Random) -> int:
    return legacy.ts_millis(rng)


def numeric(spec, rng: random.Random, ctype: str):
    kind = spec[0]
    if kind == "uni":
        v = rng.uniform(spec[1], spec[2])
    elif kind == "logn":
        v = rng.lognormvariate(spec[1], spec[2])
    else:
        v = rng.choice(spec[1])
    return int(v) if ctype == "long" else round(float(v), 3)


# --------------------------------------------------------------- variant build
VARIANTS = [
    # name suffix, datasource pattern, case fn, dim slice, id slice, metric slice,
    # keep mvd, keep json, keep lookup, keep partner, secondary encoding
    dict(tag="v0", ds="ds_{key}", case=str, dims=slice(None), ids=slice(None),
         metrics=slice(None), mvd=True, jsn=True, lookup=True, partner=False, sec="epoch_ms"),
    dict(tag="v1", ds="{camel}Raw", case=camel, dims=slice(0, 3), ids=slice(0, 1),
         metrics=slice(0, 3), mvd=False, jsn=True, lookup=False, partner=False, sec="str_iso"),
    dict(tag="v2", ds="{key}_daily", case=str, dims=slice(1, None), ids=slice(None),
         metrics=slice(1, None), mvd=True, jsn=False, lookup=True, partner=True, sec="str_space"),
]
# Two families ship only two variants, to land the total inside the 60-70 the
# schema budget allows.
TWO_ONLY = {"clinical_telemetry", "workforce"}


VALUE_LIST_STYLES = ["one of:", "possible values:", "values:"]


def list_allowed_values(sid: str, cols: list, roles: dict, pools: dict, blank: set):
    """Put the allowed values into the description of ~55% of low-cardinality dimensions
    (plan P0-5: "descriptions that list allowed values"). They go INSIDE the first sentence, so
    the terse formats that keep only the first sentence still carry them. Blanked descriptions
    never get a list (the blank overlay would hide it). Returns (columns, names_with_a_list)."""
    from widen import is_filler
    dims = set(roles.get("dims", []))
    shown, out = [], []
    for name, ctype, desc in cols:
        pool = list(dict.fromkeys(str(x) for x in pools.get(name, []))) if name in dims else []
        r = random.Random(zlib.crc32(f"{sid}:{name}:values".encode()))
        if (pool and 2 <= len(pool) <= 14 and name not in blank and not is_filler(name)
                and desc and r.random() < 0.55):
            style = r.choice(VALUE_LIST_STYLES)
            desc = f"{desc.rstrip('.')} ({style} {', '.join(pool)})."
            shown.append(name)
        out.append((name, ctype, desc))
    return out, shown


def build_variant(f: dict, v: dict) -> dict:
    cf = v["case"]
    key = f["key"]
    ds = v["ds"].format(key=key, camel=camel(key))
    cols: list[tuple[str, str, str]] = []
    pools: dict[str, list] = {}
    roles: dict = {"dims": [], "hi_card": [], "metrics": []}

    tname = cf(f["time_name"])
    cols.append((tname, "long", f["time_desc"] + " Becomes `__time`, the only TIMESTAMP column."))
    roles["time"] = tname

    ename, edesc, epool = f["entity"]
    ename = cf(ename)
    cols.append((ename, "string", edesc))
    pools[ename] = list(epool)
    roles["entity"] = ename
    roles["dims"].append(ename)

    for name, desc, pool in f["dims"][v["dims"]]:
        name = cf(name)
        cols.append((name, "string", desc))
        pools[name] = list(pool)
        roles["dims"].append(name)

    for name, desc, kind in f["ids"][v["ids"]]:
        name = cf(name)
        cols.append((name, "string", desc))
        pools[name] = ID_POOLS[kind]
        roles["hi_card"].append(name)

    for name, ctype, desc, gen in f["metrics"][v["metrics"]]:
        name = cf(name)
        cols.append((name, ctype, desc))
        roles["metrics"].append({"name": name, "type": ctype, "gen": list(gen)})

    if f["mvd"] and v["mvd"]:
        name, desc, pool = f["mvd"]
        name = cf(name)
        cols.append((name, "array<string>", "Multi-value dimension. " + desc))
        pools[name] = list(pool)
        roles["mvd"] = name

    hint_level = pick_hint_level(f"{key}_{v['tag']}")
    sid = f"{key}_{v['tag']}"
    json_dirty = False
    if f["jsn"] and v["jsn"]:
        name, desc, keys = f["jsn"]
        name = cf(name)
        json_dirty = random.Random(zlib.crc32(f"{sid}_jsondirty".encode())).random() < 0.30
        prefix = {"none": "", "semantic": "JSON payload held as text. ",
                  "explicit": "JSON object stored as a string. Parse with PARSE_JSON before JSON_VALUE. "}[hint_level]
        dirt = " A few rows hold malformed JSON." if json_dirty else ""
        cols.append((name, "string", prefix + desc + dirt))
        pools[name] = keys
        roles["json"] = name
        roles["json_keys"] = {k: list(vals) for k, vals in keys.items()}
        roles["json_dirty"] = json_dirty

    taken = {c[0] for c in cols}
    enc, sname, swhat = pick_secondary(sid, taken)
    sname = cf(sname)
    cols.append((sname, ENC[enc]["type"], time_desc(swhat, enc, hint_level)))
    roles[enc] = sname
    roles["epoch_cols"] = [sname] if enc.startswith("epoch") else []

    pair = pick_pair(sid, {c[0] for c in cols})
    pair_nullable: set[str] = set()
    if pair:
        pstart, pend, punit, pwhat = pair
        pstart, pend = cf(pstart), cf(pend)
        penc = "epoch_ms" if punit == "ms" else "epoch_s"
        cols.append((pstart, "long", time_desc(f"Start of {pwhat}", penc, hint_level)))
        cols.append((pend, "long", time_desc(f"End of {pwhat}", penc, hint_level)))
        roles["epoch_pairs"] = [[pstart, pend, punit]]
        roles["epoch_cols"] += [pstart, pend]
        pair_nullable.add(pend)  # activities that never finished have no end time

    lookups = []
    if f["lookup"] and v["lookup"]:
        lname, lcol, ldesc, lmap = f["lookup"]
        lcol = cf(lcol)
        lookups.append([lname, lcol, ldesc])
        roles["lookup"] = [lname, lcol]

    partners = []
    if f["partner"] and v["partner"]:
        dim_key, local, remote = f["partner"]
        local = cf(local)
        partners.append([dim_key, local, remote])
        roles["partner"] = {"schema": dim_key, "local": local, "remote": remote}

    for m in roles["metrics"]:
        meta = classify_metric(m["name"], m["type"], "", m["gen"])
        m["role"], m["polarity"] = meta["role"], meta["polarity"]
    roles.setdefault("flags", [])

    wrng = random.Random(zlib.crc32(f"{key}_{v['tag']}_widen".encode()))
    existing_names = {c[0] for c in cols}
    total_target = width_target_total(v["tag"], wrng)
    widen_groups: list[str] = []
    if total_target:
        extra = widen(existing_names, max(0, total_target - len(cols)), wrng)
        cols.extend(extra["columns"])
        pools.update(extra["pools"])
        roles["dims"].extend(extra["dims"])
        roles["hi_card"].extend(extra["hi_card"])
        for m in extra["metrics"]:
            meta = classify_metric(m["name"], m["type"], "", m["gen"])
            m["role"], m["polarity"] = meta["role"], meta["polarity"]
        roles["metrics"].extend(extra["metrics"])
        roles["flags"].extend(extra["flags"])
        widen_groups = extra["groups_used"]

    jrng = random.Random(zlib.crc32(f"{sid}_jsonbank".encode()))
    if not roles.get("json") and total_target and jrng.random() < 0.55:
        jname, jdesc, jkeys = jrng.choice(JSON_BANK)
        jname = cf(jname)
        if jname not in {c[0] for c in cols}:
            json_dirty = jrng.random() < 0.30
            prefix = {"none": "", "semantic": "JSON payload held as text. ",
                      "explicit": "JSON object stored as a string. Parse with PARSE_JSON before JSON_VALUE. "}[hint_level]
            cols.append((jname, "string", prefix + jdesc + (" A few rows hold malformed JSON." if json_dirty else "")))
            pools[jname] = jkeys
            roles["json"] = jname
            roles["json_keys"] = {k: list(vals) for k, vals in jkeys.items()}
            roles["json_dirty"] = json_dirty

    non_time = [c[0] for c in cols if c[0] != tname]
    nrng = random.Random(zlib.crc32(f"{key}_{v['tag']}_null".encode()))
    brng = random.Random(zlib.crc32(f"{key}_{v['tag']}_blank".encode()))
    nullable = nullable_columns(f"{key}_{v['tag']}", non_time, nrng) | pair_nullable
    blank = blank_description_columns(f"{key}_{v['tag']}", non_time, brng)
    cols, values_shown = list_allowed_values(sid, cols, roles, pools, blank)

    result = dict(id=f"{key}_{v['tag']}", datasource=ds, domain=f["domain"],
                  purpose=f["purpose"], time_col=tname, columns=cols, rows=legacy.FACT_ROWS,
                  lookups=lookups, partners=partners, pools=pools, roles=roles,
                  family=key, variant=v["tag"], generated=True,
                  nullable_columns=nullable, blank_desc_columns=blank, values_shown=values_shown,
                  widen_groups=widen_groups, noun=NOUNS.get(key, "records"),
                  entity_word=roles["entity"], hint_level=hint_level)
    orng = random.Random(zlib.crc32(f"{key}_{v['tag']}_ovr".encode()))
    result["overview"] = make_overview(key, f["domain"], f["purpose"], result["noun"], orng)
    irng = random.Random(zlib.crc32(f"{key}_{v['tag']}_instr".encode()))
    result["instructions"] = make_instructions(result, irng, n=2)
    grng = random.Random(zlib.crc32(f"{key}_{v['tag']}_gloss".encode()))
    result["glossary"] = make_glossary(result, grng, n=1)
    return result


def build_dim(d: dict) -> dict:
    cols = [(d["time_name"], "long", d["time_desc"] + " Becomes `__time`.")]
    pools: dict[str, list] = {}
    roles = {"time": d["time_name"], "dims": [], "hi_card": [], "metrics": []}

    ename, edesc, epool = d["entity"]
    entity_values = ID_POOLS[epool] if isinstance(epool, str) else list(epool)
    cols.append((ename, "string", edesc))
    pools[ename] = entity_values
    roles["entity"] = ename
    roles["dims"].append(ename)
    roles["dim_key"] = ename

    for name, desc, pool in d["dims"]:
        cols.append((name, "string", desc))
        pools[name] = list(pool)
        roles["dims"].append(name)
    for name, ctype, desc, gen in d["metrics"]:
        cols.append((name, ctype, desc))
        meta = classify_metric(name, ctype, desc, list(gen))
        roles["metrics"].append({"name": name, "type": ctype, "gen": list(gen),
                                 "role": meta["role"], "polarity": meta["polarity"]})
    roles["flags"] = []

    key = d["key"]
    widen_groups: list[str] = []
    if key in WIDEN_WIDE_DIMS:
        wrng = random.Random(zlib.crc32(f"{key}_dim_widen".encode()))
        existing_names = {c[0] for c in cols}
        total_target = width_target_total("dim_wide", wrng)
        extra = widen(existing_names, max(0, total_target - len(cols)), wrng)
        cols.extend(extra["columns"])
        pools.update(extra["pools"])
        roles["dims"].extend(extra["dims"])
        roles["hi_card"].extend(extra["hi_card"])
        for m in extra["metrics"]:
            meta = classify_metric(m["name"], m["type"], "", m["gen"])
            m["role"], m["polarity"] = meta["role"], meta["polarity"]
        roles["metrics"].extend(extra["metrics"])
        roles["flags"].extend(extra["flags"])
        widen_groups = extra["groups_used"]

    non_time = [c[0] for c in cols if c[0] != d["time_name"]]
    nrng = random.Random(zlib.crc32(f"{key}_dim_null".encode()))
    brng = random.Random(zlib.crc32(f"{key}_dim_blank".encode()))
    nullable = nullable_columns(key, non_time, nrng)
    blank = blank_description_columns(key, non_time, brng)

    result = dict(id=key, datasource=key, domain=d["domain"], purpose=d["purpose"],
                  time_col=d["time_name"], columns=cols, rows=len(entity_values),
                  lookups=[], partners=[], pools=pools, roles=roles,
                  family=key, variant="dim", generated=True, is_dim=True,
                  nullable_columns=nullable, blank_desc_columns=blank,
                  widen_groups=widen_groups, noun=NOUNS.get(key, "records"),
                  entity_word=roles["entity"])
    orng = random.Random(zlib.crc32(f"{key}_dim_ovr".encode()))
    result["overview"] = make_overview(key, d["domain"], d["purpose"], result["noun"], orng)
    result["instructions"] = []  # slowly-changing dims aren't where business rules bite
    result["glossary"] = []
    return result


# ------------------------------------------------------------------ row builder
def gen_rows(s: dict, rng: random.Random) -> list[dict]:
    cols = s["columns"]
    pools, roles = s["pools"], s["roles"]
    gen_by_name = {m["name"]: m["gen"] for m in roles["metrics"]}
    types = {n: t for n, t, _ in cols}
    n_rows = s["rows"]
    entity_values = pools.get(roles.get("entity"), [])
    nullable = s.get("nullable_columns") or set()
    rows = []
    pairs = roles.get("epoch_pairs") or []
    pair_names = {n for p in pairs for n in p[:2]}
    for i in range(n_rows):
        t = ts_millis(rng)
        row: dict = {}
        pair_vals: dict[str, int] = {}
        for pstart, pend, punit in pairs:
            begin = t - rng.randint(5_000, 900_000)
            finish = begin + rng.randint(20_000, 5_400_000)
            div = 1000 if punit == "s" else 1
            pair_vals[pstart], pair_vals[pend] = begin // div, finish // div
        for name, ctype, _ in cols:
            if name == s["time_col"]:
                row[name] = t
            elif name in pair_names:
                row[name] = pair_vals[name]
            elif s.get("is_dim") and name == roles.get("dim_key"):
                # one row per key: the dimension table must not multiply the join
                row[name] = entity_values[i % len(entity_values)]
            elif s.get("is_dim") and name in pools and types[name] == "string":
                pool = pools[name]
                row[name] = pool[i % len(pool)]
            elif name == roles.get("mvd"):
                # ~8% empty: real MVD columns hold empty arrays, and MV_LENGTH /
                # MV_CONTAINS must handle them (plan Section 5 step 2)
                row[name] = ([] if rng.random() < 0.08 else
                             rng.sample(pools[name], rng.randint(1, min(3, len(pools[name])))))
            elif name == roles.get("json"):
                txt = json.dumps({k: _json_val(rng.choice(v)) for k, v in pools[name].items()})
                row[name] = txt[: len(txt) // 2] if roles.get("json_dirty") and rng.random() < 0.04 else txt
            elif name == roles.get("epoch_ms"):
                row[name] = t - rng.randint(40, 6000)
            elif name == roles.get("epoch_s"):
                row[name] = (t - rng.randint(1000, 40000)) // 1000
            elif name == roles.get("str_space"):
                row[name] = _fmt(t - rng.randint(1000, 90000), "%Y-%m-%d %H:%M:%S")
            elif name == roles.get("str_iso"):
                row[name] = _fmt(t - rng.randint(1000, 90000), "%Y-%m-%dT%H:%M:%SZ")
            elif name == roles.get("str_date"):
                row[name] = _fmt(t - rng.randint(1000, 90000), "%Y-%m-%d")
            elif name == roles.get("str_dmy"):
                row[name] = _fmt(t - rng.randint(1000, 90000), "%d/%m/%Y")
            elif name in gen_by_name:
                row[name] = numeric(gen_by_name[name], rng, ctype)
            elif name in pools:
                row[name] = rng.choice(pools[name])
            else:
                row[name] = f"{name}_{rng.randint(0, 5)}"
            if name in nullable and name != s["time_col"] and rng.random() < NULL_ROW_RATE:
                row[name] = None
        rows.append(row)
    return rows


def _json_val(v):
    if v == "true":
        return True
    if v == "false":
        return False
    return v


def _fmt(ms: int, pattern: str) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(pattern)


# --------------------------------------------------------------- legacy roles
# The nine hand-written schemas predate the role model, so their roles are
# inferred from the columns themselves rather than restated by hand.
SECONDARY_BY_NAME = {"request_started_at_ms": "epoch_ms", "served_at_epoch_s": "epoch_s",
                     "reading_taken_at": "str_space", "settledAt": "str_iso"}
LEGACY_JSON_KEYS = {
    "attrs_json": {"campaign": ["spring", "holiday", "evergreen", "none"],
                   "tier": ["free", "plus", "pro"], "is_bot": ["yes", "no"]},
    "enrichment_json": {"asset_owner": ["platform", "payments", "data", "corp-it"],
                        "env": ["prod", "staging", "dev"],
                        "patch_level": ["current", "n-1", "n-2", "unknown"]},
}
LEGACY_DIM_KEY = {"products": "sku"}
LEGACY_JOIN = {"orders": {"schema": "products", "local": "product_sku", "remote": "sku"}}
EXTRA_POOLS = {"experiment_tags": None, "creative_tags": None, "alert_tags": None,
               "host_id": None, "product_sku": None, "sku": None}


def legacy_index_entry(d: dict) -> dict:
    """Infer a role block from a hand-written schema's actual columns."""
    extra = {"experiment_tags": legacy.EXPERIMENT_TAGS, "creative_tags": legacy.CREATIVE_TAGS,
             "alert_tags": legacy.ALERT_TAGS, "host_id": legacy.HOSTS,
             "product_sku": legacy.SKUS, "sku": legacy.SKUS}
    roles: dict = {"time": d["time_col"], "dims": [], "hi_card": [], "metrics": []}
    pools: dict = {}
    for name, ctype, _ in d["columns"]:
        if name == d["time_col"]:
            continue
        if name in SECONDARY_BY_NAME:
            roles[SECONDARY_BY_NAME[name]] = name
            continue
        if ctype == "array<string>":
            roles["mvd"] = name
            pools[name] = list(extra[name])
            continue
        if name.endswith("_json"):
            roles["json"] = name
            roles["json_keys"] = LEGACY_JSON_KEYS[name]
            continue
        if ctype in ("long", "double", "float"):
            meta = classify_metric(name, ctype, "", [])
            roles["metrics"].append({"name": name, "type": ctype, "gen": [],
                                     "role": meta["role"], "polarity": meta["polarity"]})
            continue
        pool = legacy.POOLS.get(name) or extra.get(name)
        if pool is None:
            roles["hi_card"].append(name)
            continue
        pools[name] = list(pool)
        (roles["dims"] if len(pool) <= 20 else roles["hi_card"]).append(name)
    roles["entity"] = roles["dims"][0] if roles["dims"] else roles["hi_card"][0]
    if d.get("lookups"):
        lname, lcol, _ = d["lookups"][0]
        roles["lookup"] = [lname, lcol]
    if d["id"] in LEGACY_DIM_KEY:
        roles["dim_key"] = LEGACY_DIM_KEY[d["id"]]
    partners = []
    if d["id"] in LEGACY_JOIN:
        j = LEGACY_JOIN[d["id"]]
        roles["partner"] = j
        partners.append([j["schema"], j["local"], j["remote"]])
    roles["flags"] = []

    non_time = [c[0] for c in d["columns"] if c[0] != d["time_col"]]
    brng = random.Random(zlib.crc32(f"{d['id']}_blank".encode()))
    # Hand-written schemas keep their own row builder (legacy.build_row),
    # which predates the nullability model, so no column here is ever
    # actually written as NULL. Marking one nullable in the schema without
    # real nulls in the data would make an IS NOT NULL instruction vacuous.
    nullable: set[str] = set()
    blank = blank_description_columns(d["id"], non_time, brng)

    result = dict(id=d["id"], datasource=d["datasource"], domain=d["domain"],
                  purpose=d.get("purpose", ""), time_col=d["time_col"], rows=d["rows"],
                  partners=partners, lookups=[list(l) for l in d.get("lookups", [])],
                  columns=[list(c) for c in d["columns"]], pools=pools, roles=roles,
                  family=d["id"], variant="handwritten", generated=False,
                  nullable_columns=nullable, blank_desc_columns=blank, widen_groups=[],
                  noun=NOUNS.get(d["id"], "records"), entity_word=roles["entity"])
    orng = random.Random(zlib.crc32(f"{d['id']}_ovr".encode()))
    result["overview"] = make_overview(d["id"], d["domain"], d.get("purpose", ""), result["noun"], orng)
    irng = random.Random(zlib.crc32(f"{d['id']}_instr".encode()))
    result["instructions"] = make_instructions(result, irng, n=2)
    grng = random.Random(zlib.crc32(f"{d['id']}_gloss".encode()))
    result["glossary"] = make_glossary(result, grng, n=1)
    return result


# ----------------------------------------------------------------------- emit
INDEX_FIELDS = ("id", "datasource", "domain", "purpose", "time_col", "rows",
                "partners", "lookups", "columns", "pools", "roles",
                "family", "variant", "generated", "nullable_columns",
                "blank_desc_columns", "values_shown", "widen_groups", "noun", "entity_word",
                "overview", "instructions", "glossary", "hint_level")


def _col_spec(n: str, t: str, s: dict) -> dict:
    d = {"name": n, "type": t}
    if n == s["time_col"]:
        d["is_time"] = True
    elif n in (s.get("nullable_columns") or ()):
        d["nullable"] = True
    return d


def _write_variant(datasource: str, s: dict, rng: random.Random) -> None:
    """Write one independently-seeded copy of a datasource, under its own name.

    The primary (unsuffixed) copy is what prompts reference and what the
    model is taught against. The suffixed `__g4b` / `__g4c` copies are loaded
    for G4 (result agreement across independently seeded data) only -- they
    never appear in a prompt.
    """
    seed = SEEDS / f"{datasource}.json"
    with seed.open("w", encoding="utf-8") as fh:
        for row in gen_rows(s, rng):
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    spec = {"name": datasource,
            "columns": [_col_spec(n, t, s) for n, t, _ in s["columns"]],
            "seed": {"mode": "file", "path": f"../seeds/{datasource}.json", "format": "json"}}
    (SPECS / f"{datasource}.json").write_text(json.dumps(spec, indent=2) + "\n")


def write_schema(s: dict) -> list[str]:
    """Write the primary datasource plus N_G4_SEEDS-1 extra seeds for G4.

    Returns the full list of loaded datasource names (primary first).
    """
    names = [s["datasource"]] + [f"{s['datasource']}__g4{c}" for c in "bcdefgh"[:N_G4_SEEDS - 1]]
    for i, name in enumerate(names):
        rng = random.Random(zlib.crc32(f"{s['id']}:{i}".encode()))
        _write_variant(name, s, rng)
    return names


def _write_legacy_g4(d: dict) -> list[str]:
    """G4 seed variants for a hand-written schema, via legacy.build_row.

    legacy.main() already wrote the primary (unsuffixed) seed/spec; this adds
    N_G4_SEEDS-1 more independently-seeded copies under suffixed names.
    """
    names = [d["datasource"]] + [f"{d['datasource']}__g4{c}" for c in "bcdefgh"[:N_G4_SEEDS - 1]]
    for i, name in enumerate(names[1:], start=1):
        rng = random.Random(zlib.crc32(f"{d['id']}:{i}".encode()))
        rows = [legacy.build_row(d["id"], d["columns"], rng, d["time_col"]) for _ in range(d["rows"])]
        seed_path = SEEDS / f"{name}.json"
        with seed_path.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, separators=(",", ":")) + "\n")
        spec = {"name": name,
                "columns": [{"name": n, "type": t, **({"is_time": True} if n == d["time_col"] else {})}
                            for n, t, _ in d["columns"]],
                "seed": {"mode": "file", "path": f"../seeds/{name}.json", "format": "json"}}
        (SPECS / f"{name}.json").write_text(json.dumps(spec, indent=2) + "\n")
    return names


def main() -> None:
    SPECS.mkdir(parents=True, exist_ok=True)
    SEEDS.mkdir(parents=True, exist_ok=True)
    legacy.main()  # re-emits the nine hand-written schemas against this anchor
    index = {d["id"]: legacy_index_entry(d) for d in legacy.SCHEMAS}
    for d in legacy.SCHEMAS:
        index[d["id"]]["g4_variants"] = _write_legacy_g4(d)

    generated: list[dict] = []
    for f in FAMILIES:
        variants = VARIANTS[:2] if f["key"] in TWO_ONLY else VARIANTS
        for v in variants:
            if v["mvd"] and not f["mvd"] and v["tag"] == "v2" and not f["partner"]:
                pass  # variant still valid without the optional pieces
            generated.append(build_variant(f, v))
    generated.extend(build_dim(d) for d in DIMS)

    for s in generated:
        index[s["id"]] = s
        index[s["id"]]["g4_variants"] = write_schema(s)

    for sid, s in index.items():
        entry = {k: s[k] for k in INDEX_FIELDS if k in s}
        entry["partners"] = s.get("partners", [])
        entry["g4_variants"] = s["g4_variants"]
        entry["nullable_columns"] = sorted(entry.get("nullable_columns") or [])
        entry["blank_desc_columns"] = sorted(entry.get("blank_desc_columns") or [])
        index[sid] = entry

    (ROOT / "schema_index.json").write_text(json.dumps(index, indent=1) + "\n")
    facts = [s for s in index.values() if not s["id"].startswith("dim_")]
    print(f"anchor {NOW.isoformat()}  span {SPAN_DAYS}d")
    print(f"{len(index)} datasources: {len(facts)} fact, {len(index) - len(facts)} dimension")
    print(f"  hand-written {sum(1 for s in index.values() if not s['generated'])}, "
          f"generated {sum(1 for s in index.values() if s['generated'])}")


if __name__ == "__main__":
    main()
