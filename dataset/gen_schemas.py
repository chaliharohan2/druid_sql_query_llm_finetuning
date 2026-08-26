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

ROOT = Path(__file__).resolve().parent
SPECS, SEEDS = ROOT / "specs", ROOT / "seeds"
NOW = legacy.NOW
SPAN_DAYS = legacy.SPAN_DAYS

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

# Secondary time encodings. One is attached per variant so that no single
# encoding (or column name) becomes the cue for "this needs converting".
SECONDARY = {
    "epoch_ms": ("received_at_ms", "long",
                 "Upstream receive time as epoch MILLISECONDS. Not a TIMESTAMP -- "
                 "convert with MILLIS_TO_TIMESTAMP before using time functions."),
    "epoch_s": ("queued_at_epoch_s", "long",
                "Enqueue time as epoch SECONDS. Not a TIMESTAMP -- multiply by 1000 "
                "and convert with MILLIS_TO_TIMESTAMP."),
    "str_space": ("recorded_at", "string",
                  "Source-system time held as a string, format 'yyyy-MM-dd HH:mm:ss'. "
                  "Parse with TIME_PARSE before using time functions."),
    "str_iso": ("recordedAt", "string",
                "Source-system time held as an ISO-8601 string, format "
                "\"yyyy-MM-dd'T'HH:mm:ss'Z'\". Parse with TIME_PARSE and an explicit format."),
}


def camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in rest)


def ts_millis(rng: random.Random) -> int:
    start = NOW - timedelta(days=SPAN_DAYS)
    return int((start + timedelta(seconds=rng.uniform(0, SPAN_DAYS * 86400))).timestamp() * 1000)


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

    if f["jsn"] and v["jsn"]:
        name, desc, keys = f["jsn"]
        name = cf(name)
        cols.append((name, "string",
                     "JSON object stored as a string. Parse with PARSE_JSON before JSON_VALUE. " + desc))
        pools[name] = keys
        roles["json"] = name
        roles["json_keys"] = {k: list(vals) for k, vals in keys.items()}

    sname, stype, sdesc = SECONDARY[v["sec"]]
    sname = cf(sname)
    cols.append((sname, stype, sdesc))
    roles[v["sec"]] = sname

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

    return dict(id=f"{key}_{v['tag']}", datasource=ds, domain=f["domain"],
                purpose=f["purpose"], time_col=tname, columns=cols, rows=900,
                lookups=lookups, partners=partners, pools=pools, roles=roles,
                family=key, variant=v["tag"], generated=True)


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
        roles["metrics"].append({"name": name, "type": ctype, "gen": list(gen)})

    return dict(id=d["key"], datasource=d["key"], domain=d["domain"], purpose=d["purpose"],
                time_col=d["time_name"], columns=cols, rows=len(entity_values),
                lookups=[], partners=[], pools=pools, roles=roles,
                family=d["key"], variant="dim", generated=True, is_dim=True)


# ------------------------------------------------------------------ row builder
def gen_rows(s: dict, rng: random.Random) -> list[dict]:
    cols = s["columns"]
    pools, roles = s["pools"], s["roles"]
    gen_by_name = {m["name"]: m["gen"] for m in roles["metrics"]}
    types = {n: t for n, t, _ in cols}
    n_rows = s["rows"]
    entity_values = pools.get(roles.get("entity"), [])
    rows = []
    for i in range(n_rows):
        t = ts_millis(rng)
        row: dict = {}
        for name, ctype, _ in cols:
            if name == s["time_col"]:
                row[name] = t
            elif s.get("is_dim") and name == roles.get("dim_key"):
                # one row per key: the dimension table must not multiply the join
                row[name] = entity_values[i % len(entity_values)]
            elif s.get("is_dim") and name in pools and types[name] == "string":
                pool = pools[name]
                row[name] = pool[i % len(pool)]
            elif name == roles.get("mvd"):
                row[name] = rng.sample(pools[name], rng.randint(1, min(3, len(pools[name]))))
            elif name == roles.get("json"):
                row[name] = json.dumps({k: _json_val(rng.choice(v)) for k, v in pools[name].items()})
            elif name == roles.get("epoch_ms"):
                row[name] = t - rng.randint(40, 6000)
            elif name == roles.get("epoch_s"):
                row[name] = (t - rng.randint(1000, 40000)) // 1000
            elif name == roles.get("str_space"):
                row[name] = _fmt(t - rng.randint(1000, 90000), "%Y-%m-%d %H:%M:%S")
            elif name == roles.get("str_iso"):
                row[name] = _fmt(t - rng.randint(1000, 90000), "%Y-%m-%dT%H:%M:%SZ")
            elif name in gen_by_name:
                row[name] = numeric(gen_by_name[name], rng, ctype)
            elif name in pools:
                row[name] = rng.choice(pools[name])
            else:
                row[name] = f"{name}_{rng.randint(0, 5)}"
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
            roles["metrics"].append({"name": name, "type": ctype, "gen": []})
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
    return dict(id=d["id"], datasource=d["datasource"], domain=d["domain"],
                purpose=d.get("purpose", ""), time_col=d["time_col"], rows=d["rows"],
                partners=partners, lookups=[list(l) for l in d.get("lookups", [])],
                columns=[list(c) for c in d["columns"]], pools=pools, roles=roles,
                family=d["id"], variant="handwritten", generated=False)


# ----------------------------------------------------------------------- emit
def write_schema(s: dict) -> None:
    seed = SEEDS / f"{s['datasource']}.json"
    rng = random.Random(zlib.crc32(s["id"].encode()))
    with seed.open("w", encoding="utf-8") as fh:
        for row in gen_rows(s, rng):
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    spec = {"name": s["datasource"],
            "columns": [{"name": n, "type": t, **({"is_time": True} if n == s["time_col"] else {})}
                        for n, t, _ in s["columns"]],
            "seed": {"mode": "file", "path": f"../seeds/{s['datasource']}.json", "format": "json"}}
    (SPECS / f"{s['datasource']}.json").write_text(json.dumps(spec, indent=2) + "\n")


def main() -> None:
    SPECS.mkdir(parents=True, exist_ok=True)
    SEEDS.mkdir(parents=True, exist_ok=True)
    legacy.main()  # re-emits the nine hand-written schemas against this anchor
    index = {d["id"]: legacy_index_entry(d) for d in legacy.SCHEMAS}

    generated: list[dict] = []
    for f in FAMILIES:
        variants = VARIANTS[:2] if f["key"] in TWO_ONLY else VARIANTS
        for v in variants:
            if v["mvd"] and not f["mvd"] and v["tag"] == "v2" and not f["partner"]:
                pass  # variant still valid without the optional pieces
            generated.append(build_variant(f, v))
    generated.extend(build_dim(d) for d in DIMS)

    for s in generated:
        write_schema(s)
        index[s["id"]] = {k: s[k] for k in
                          ("id", "datasource", "domain", "purpose", "time_col", "rows",
                           "partners", "lookups", "columns", "pools", "roles",
                           "family", "variant", "generated")}
        index[s["id"]]["partners"] = s["partners"]

    (ROOT / "schema_index.json").write_text(json.dumps(index, indent=1) + "\n")
    facts = [s for s in index.values() if not s["id"].startswith("dim_")]
    print(f"anchor {NOW.isoformat()}  span {SPAN_DAYS}d")
    print(f"{len(index)} datasources: {len(facts)} fact, {len(index) - len(facts)} dimension")
    print(f"  hand-written {sum(1 for s in index.values() if not s['generated'])}, "
          f"generated {sum(1 for s in index.values() if s['generated'])}")


if __name__ == "__main__":
    main()
