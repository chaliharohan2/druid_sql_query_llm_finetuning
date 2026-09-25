"""Domain-specific glossary terms and richer definition shapes (addendum F2, F3).

v2 gave every table ONE glossary term, always "<flagged|priority|notable|key> <noun>" defined as "a value
list on one column". A model can learn that those four words trigger an arbitrary filter. This module
builds, per datasource, a glossary of several terms:

  * terms are domain phrases written per family ("rush shipment", "VIP booking", "at-risk account" ...);
  * definitions come in eight logical shapes: value_list, or_cols, not_in, like_prefix, threshold, mixed,
    time_active, derived (plus multi-term glossaries, which every glossary now is).

The FIRST entry keeps the original predicate of the v2 primary term (only its name changes) so every
existing gold SQL still satisfies it; the extras are new and are only ever *applied* by new rows (F3).

`build_all(index)` is deterministic. `python3 glossary_terms.py --write` rewrites schema_index.json with the
new glossaries (the pre-addendum file is kept as schema_index.pre_addendum.json).

Not AI training or inference code.
"""
from __future__ import annotations

import json
import math
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# --------------------------------------------------------------------------- term vocabulary
# Eight domain phrases per family, written for the domain. Term = "<modifier> <singular noun>".
FAMILY_MODIFIERS: dict[str, list[str]] = {
    "ad_impressions": ["viewable", "retargeted", "above-fold", "skippable", "cross-device", "brand-safe", "low-quality", "sponsored"],
    "airline_flights": ["problem", "long-haul", "red-eye", "codeshare", "diverted", "overbooked", "irregular", "hub-bound"],
    "api_gateway": ["abusive", "throttled", "legacy-client", "partner-tier", "burst", "cache-miss", "slow-path", "internal-only"],
    "app_logs": ["noisy", "user-facing", "stack-trace", "paged", "audit-relevant", "deploy-window", "suspect", "health-check"],
    "atm_network": ["off-us", "after-hours", "high-denomination", "foreign-card", "declined-retry", "cash-out", "suspicious", "balance-only"],
    "cdn_edge": ["cold-cache", "origin-shielded", "bot-like", "mobile-heavy", "range", "purged", "regional-spike", "slow-start"],
    "cell_tower_kpis": ["congested", "degraded", "rural", "handover-heavy", "backhaul-limited", "low-signal", "edge-of-coverage", "overloaded"],
    "ci_pipeline": ["hotfix", "release-candidate", "flaky", "nightly", "cold-start", "cache-busted", "gated", "long-queue"],
    "clinical_telemetry": ["borderline", "unstable", "post-op", "ward-level", "alarm-triggering", "manually-entered", "stale", "high-acuity"],
    "content_moderation": ["escalated", "appealed", "auto-flagged", "borderline", "repeat-offender", "viral", "time-sensitive", "policy-gap"],
    "crypto_trades": ["wash-suspect", "large-block", "maker", "cross-margin", "after-hours", "stablecoin", "fat-finger", "high-leverage"],
    "email_campaigns": ["engaged", "cold", "reactivation", "promo-heavy", "mobile-open", "soft-bounced", "list-fatigue", "holdout"],
    "energy_meter": ["peak-hour", "estimated", "tamper-suspect", "industrial", "net-metered", "zero-usage", "off-peak", "spike"],
    "event_ticketing": ["presale", "resale", "group", "last-minute", "comp", "VIP", "bulk", "floor-section"],
    "factory_quality": ["rework", "first-pass", "borderline", "line-stop", "supplier-caused", "audit", "spot-check", "major-defect"],
    "farm_sensors": ["drought-stress", "frost-risk", "irrigated", "edge-field", "calibration-drift", "night", "waterlogged", "sparse"],
    "fin_txn": ["chargeback-prone", "cross-border", "high-ticket", "recurring", "first-time", "manual-review", "sanctions-screened", "micro"],
    "fitness_app": ["personal-best", "outdoor", "interval", "recovery", "streak", "wearable-tracked", "gym", "short-burst"],
    "fleet_gps": ["speeding", "idle", "off-route", "night-run", "harsh-braking", "geofence-breach", "low-fuel", "overnight"],
    "food_delivery": ["rush", "late-night", "group", "repeat", "refund-prone", "scheduled", "high-basket", "promo"],
    "game_sessions": ["whale", "new-player", "rage-quit", "tournament", "cheat-suspect", "AFK", "co-op", "marathon"],
    "hospital_admissions": ["readmission", "overnight", "transfer-in", "surgical", "elective-track", "long-stay", "high-acuity", "weekend"],
    "hotel_bookings": ["VIP", "group", "last-minute", "non-refundable", "corporate", "extended-stay", "suite", "no-show-risk"],
    "insurance_claims": ["litigated", "fast-track", "fraud-suspect", "catastrophe", "subrogation", "high-severity", "reopened", "total-loss"],
    "inventory": ["cycle-count", "write-off", "inter-site", "bulk", "returns", "cross-dock", "adjustment", "expedited"],
    "iot_readings": ["out-of-band", "drifting", "stuck", "battery-low", "spiking", "noisy", "flatline", "calibrated"],
    "legal_matters": ["pro-bono", "write-off", "billable-plus", "block-billed", "partner-level", "travel", "overrun", "disputed"],
    "library_loans": ["overdue", "renewed", "reserved", "reference-desk", "children's", "interlibrary", "lost-risk", "new-release"],
    "logistics": ["misrouted", "held", "cold-chain", "international", "oversize", "signature-required", "damaged", "missent"],
    "ml_inference": ["low-confidence", "drifted", "shadow-mode", "canary", "timeout-prone", "fallback", "batch", "cold-model"],
    "network_flows": ["elephant", "scan-like", "east-west", "exfil-suspect", "long-lived", "encrypted", "blocked", "asymmetric"],
    "orders": ["expedite", "backordered", "gift", "wholesale", "split-shipment", "abandoned-cart", "international", "discounted"],
    "payment_gateway": ["soft-decline", "3DS-challenged", "retry", "high-risk", "recurring", "cross-border", "tokenised", "fallback-route"],
    "podcast_analytics": ["binge", "skip-heavy", "completed", "trailer", "offline", "new-listener", "commute-time", "ad-supported"],
    "products": ["clearance", "bestseller", "seasonal", "bundle", "low-margin", "new-arrival", "private-label", "long-tail"],
    "real_estate_listings": ["price-drop", "open-house", "stale", "luxury", "foreclosure", "new-build", "pending", "off-market"],
    "recruiting_pipeline": ["referred", "stalled", "fast-tracked", "offer-stage", "re-engaged", "campus", "boomerang", "withdrawn"],
    "restaurant_reservations": ["walk-in", "large-party", "holiday", "waitlisted", "prepaid", "no-show", "late-seating", "chef's-table"],
    "retail_pos": ["markdown", "voided", "loyalty", "bulk-buy", "price-override", "bundled", "staff-discount", "impulse"],
    "ride_hailing": ["surge", "airport", "shared", "long-distance", "late-night", "cancelled-late", "corporate", "first-ride"],
    "sec_alerts": ["lateral-movement", "noisy", "suppressed", "insider-risk", "phishing", "C2-suspect", "repeat", "after-hours"],
    "service_desk": ["VIP", "escalated", "SLA-risk", "reopened", "aging", "self-service", "major-incident", "vendor-owned"],
    "streaming_media": ["buffering", "offline-sync", "kids", "4K", "binge", "trial", "autoplay", "rebuffer"],
    "support_tickets": ["escalated", "reopened", "SLA-breach", "enterprise", "chatty", "stuck", "first-touch", "VIP"],
    "telco_cdr": ["roaming", "dropped", "international", "premium-rate", "short-call", "off-net", "peak", "fraud-suspect"],
    "university_enrollment": ["late-add", "waitlisted", "audit-only", "honors", "transfer-credit", "overload", "probation", "scholarship"],
    "vehicle_maintenance": ["warranty", "recall", "unscheduled", "overdue", "brake-related", "rework", "deferred", "breakdown"],
    "warranty_claims": ["repeat", "out-of-window", "fraud-suspect", "water-damage", "goodwill", "dead-on-arrival", "high-value", "express-swap"],
    "water_utility": ["leak-suspect", "high-consumption", "estimated", "tamper", "night-flow", "backflow", "zero-flow", "seasonal"],
    "web_events": ["bounce", "checkout", "campaign-tagged", "bot-like", "returning", "mobile", "deep-scroll", "rage-click"],
    "wind_turbines": ["derated", "icing", "curtailed", "high-wind", "yaw-error", "gearbox-alert", "idle", "cut-in"],
    "workforce": ["overtime", "split", "holiday", "night", "short-notice", "swap", "understaffed", "trainee"],
}
# used when a family runs out of its own phrases (a family has up to three datasources)
SPARE_MODIFIERS = ["dormant", "lopsided", "one-off", "back-office", "seasonal-peak", "manual", "bulk-upload", "legacy",
                   "pilot-cohort", "flagship", "trial-tier", "stopgap", "rework-loop", "fringe", "core-hours", "sandbox"]
THRESHOLD_HIGH = ["high-value", "heavy", "oversized", "large", "big-ticket", "hefty", "outsized", "top-tier",
                  "bulk-size", "extreme", "major", "peak-level"]
THRESHOLD_LOW = ["low-value", "light", "small", "modest", "marginal", "minor", "undersized", "tiny", "below-par",
                 "entry-level"]
THRESHOLD_MODIFIERS = THRESHOLD_HIGH + THRESHOLD_LOW
ACTIVE_MODIFIERS = ["active", "engaged", "live", "current", "recent", "regular", "recurring", "ongoing"]
DERIVED_TIME_NAMES = ["handling time", "turnaround time", "cycle time", "dwell time", "lead time", "processing time",
                      "elapsed time", "time to resolve", "time in queue", "service duration"]
DERIVED_RATIO_NAMES = ["yield", "unit rate", "hit rate", "conversion", "utilisation", "load factor", "efficiency",
                       "density", "throughput ratio", "intensity"]

SHAPES = ["value_list", "or_cols", "not_in", "like_prefix", "threshold", "mixed", "time_active", "derived"]
_BAD_ENTITY = {"created_by", "updated_by", "trace", "request", "txn", "transaction", "order", "ticket", "message", "claim",
               "booking", "reservation", "content", "commit", "batch", "alert", "host", "ip", "object", "route", "item",
               "event", "record", "checkout", "basket", "parcel", "listing", "mls", "product", "episode", "case",
               "tracking", "part", "psp", "src", "dst", "baseband", "controller", "gateway", "collector", "endpoint",
               "bin", "matter", "policy", "pod", "upstream", "runner", "replica", "hub", "mac", "sku", "code",
               "encounter", "bed", "cell_site", "assigned", "candidate", "issuer", "recipient", "operator", "owner"}


def head_word(term: str) -> str:
    return term.split()[0].lower()


def article(word: str) -> str:
    w = word.strip("`'\"*")
    if not w:
        return "a"
    first = w.split()[0]
    if first.isupper() and len(first) > 1 and re.fullmatch(r"[A-Z0-9-]+", first):  # acronym: spelled out
        return "an" if first[0] in "AEFHILMNORSX" else "a"
    low = w.lower()
    if low.startswith(("one", "uni", "use", "eu", "3", "4k")) and not low.startswith(("unpaid", "unusual", "unresolved", "unscheduled")):
        return "a" if not low.startswith("4k") else "a"
    if low.startswith(("hour", "honest", "unpaid", "unresolved", "unscheduled", "unusual")):
        return "an"
    return "an" if low[0] in "aeiou" else "a"


def _q(v) -> str:
    return f"`{v}`"


def _or_join(vals, tick=True) -> str:
    q = [_q(v) if tick else str(v) for v in vals]
    return q[0] if len(q) == 1 else ", ".join(q[:-1]) + " or " + q[-1]


def _list_join(vals) -> str:
    return ", ".join(_q(v) for v in vals)


def _sql_list(vals) -> str:
    return ", ".join("'" + str(v) + "'" for v in vals)


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def _nice(x: float, lo: float, hi: float) -> float:
    """A round threshold inside (lo, hi)."""
    span = max(hi - lo, 1e-9)
    if span >= 200:
        step = 50
    elif span >= 60:
        step = 10
    elif span >= 20:
        step = 5
    elif span >= 8:
        step = 1
    else:
        step = 0.5 if span >= 3 else 0.1
    v = round(x / step) * step
    if not (lo < v < hi):
        v = x
    return round(v, 2)


OPS = [(">", ["greater than", "above", "over"]), (">=", ["at least", "no less than"]),
       ("<", ["below", "under", "less than"]), ("<=", ["at most", "no more than"])]


def _dims(d: dict) -> list[str]:
    from widen import is_filler
    out = []
    for c in d["roles"].get("dims", []):
        pool = d["pools"].get(c) or []
        if is_filler(c) or len(pool) < 3 or any(not isinstance(v, str) or v == "" or "'" in v or "`" in v for v in pool):
            continue
        out.append(c)
    return out


def _numeric_metrics(d: dict) -> list[dict]:
    out = []
    for m in d["roles"].get("metrics", []):
        g = m.get("gen") or []
        if not g or m.get("role") == "flag_01" or g[0] not in ("uni", "logn"):
            continue
        out.append(m)
    return out


def _threshold_for(m: dict, rng: random.Random) -> tuple[str, float, str]:
    """(op, value, wording) that keeps a healthy share of the rows either side of the threshold."""
    g = m["gen"]
    if g[0] == "uni":
        lo, hi = float(g[1]), float(g[2])
        q = rng.choice([0.3, 0.4, 0.6, 0.7])
        t = _nice(lo + q * (hi - lo), lo, hi)
    else:
        mu, sigma = float(g[1]), float(g[2])
        q = rng.choice([-0.3, 0.3, 0.6])
        t = float(f"{math.exp(mu + q * sigma):.2g}")
        t = round(t) if t >= 10 else round(t, 1)
    if m["type"] == "long":
        t = int(round(t))
    op = rng.choice([">", ">=", "<"]) if q >= 0 else rng.choice(["<", "<="])
    if q >= 0 and op == "<":
        op = ">"
    return op, t, rng.choice(dict(OPS)[op])


def _like_prefix(d: dict, rng: random.Random):
    """(column, prefix) where the prefix matches 8-45% of the column's pool values, or None."""
    cands = []
    for c in _dims(d) + list(d["roles"].get("hi_card") or []):
        pool = d["pools"].get(c) or []
        if len(pool) < 6 or any(not isinstance(v, str) or "'" in v or "%" in v or "_" == v[:1] for v in pool[:20]):
            continue
        for L in range(3, 9):
            groups = Counter(v[:L] for v in pool if len(v) > L)
            for pref, n in groups.items():
                if 0.08 <= n / len(pool) <= 0.45 and (pref[-1] in "-_/:" or pref[-1].isdigit() or L >= 4):
                    cands.append((c, pref, n / len(pool)))
    if not cands:
        return None
    # prefer natural prefixes: dimension columns over ids, boundary-ending prefixes first
    cands.sort(key=lambda t: (t[0] in (d["roles"].get("hi_card") or []), not t[1][-1] in "-_/:", abs(t[2] - 0.25)))
    c, pref, _ = cands[rng.randrange(min(4, len(cands)))]
    return c, pref


def _entity(d: dict):
    for c in d["roles"].get("hi_card") or []:
        low = c.lower()
        m = re.fullmatch(r"(.+?)(?:_id|id|_ref|ref|_uid|uid|_hash|hash)", low) if not low.startswith(("created_by", "updated_by")) else None
        if not m:
            continue
        root = re.sub(r"[^a-z]+", " ", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", c[:len(m.group(1))]).lower()).strip()
        if root and root.replace(" ", "_") not in _BAD_ENTITY and root.split()[-1] not in _BAD_ENTITY:
            return c, root
    return None


# --------------------------------------------------------------------------- entry builders
def _entry(term, shape, predicate, cols, values, conds, phrases, **extra):
    e = {"term": term, "shape": shape, "predicate": predicate, "binding_column": cols[0], "columns": cols,
         "values": [str(v) for v in values], "conds": conds, "phrases": phrases,
         "definition_text": f"{term[0].upper() + term[1:]} means {phrases[0]}."}
    e.update(extra)
    return e


def _cond_phrases(conds: list[str]) -> list[str]:
    lead = ["rows where ", "records where ", "any row in which "]
    return [lead[i % 3] + c for i, c in enumerate(conds)]


def make_value_list(cols_c, values, term) -> dict:
    c = cols_c
    pred = " OR ".join(f"{c} = '{v}'" for v in values) if len(values) > 1 else f"{c} = '{values[0]}'"
    if len(values) == 1:
        conds = [f"`{c}` is `{values[0]}`", f"`{c}` equals `{values[0]}`", f"`{c}` = '{values[0]}'"]
    else:
        conds = [f"`{c}` is one of {_list_join(values)}", f"`{c}` is {_or_join(values)}", f"`{c}` is in ({_sql_list(values)})"]
    return _entry(term, "value_list", pred, [c], values, conds, _cond_phrases(conds))


def build_or_cols(d, rng, term):
    dims = _dims(d)
    if len(dims) < 2:
        return None
    a, b = rng.sample(dims, 2)
    x, y = rng.choice(d["pools"][a]), rng.choice(d["pools"][b])
    nums = [m for m in d["roles"].get("metrics", []) if (m.get("gen") or [""])[0] == "pick"
            and all(isinstance(v, int) for v in m["gen"][1]) and len(m["gen"][1]) <= 10]
    if nums and rng.random() < 0.6:
        m = rng.choice(nums)
        n = rng.choice(m["gen"][1])
        conds = [f"`{a}` is `{x}` or `{m['name']}` is {n}", f"either `{a}` = '{x}' or `{m['name']}` = {n}",
                 f"`{a}` is `{x}`, or else `{m['name']}` is {n}"]
        pred, cols, vals = f"{a} = '{x}' OR {m['name']} = {n}", [a, m["name"]], [x]
        return _entry(term, "or_cols", pred, cols, vals, conds, _cond_phrases(conds), numbers=[n])
    conds = [f"`{a}` is `{x}` or `{b}` is `{y}`", f"either `{a}` = '{x}' or `{b}` = '{y}'", f"`{a}` is `{x}`, or else `{b}` is `{y}`"]
    return _entry(term, "or_cols", f"{a} = '{x}' OR {b} = '{y}'", [a, b], [x, y], conds, _cond_phrases(conds))


def build_not_in(d, rng, term):
    dims = [c for c in _dims(d) if len(d["pools"][c]) >= 4]
    if not dims:
        return None
    c = rng.choice(dims)
    vals = rng.sample(d["pools"][c], rng.choice([1, 2]))
    conds = ([f"`{c}` is not `{vals[0]}`", f"`{c}` is anything other than `{vals[0]}`", f"`{c}` <> '{vals[0]}'"] if len(vals) == 1
             else [f"`{c}` is not one of {_list_join(vals)}", f"`{c}` is anything other than {_or_join(vals)}",
                   f"`{c}` is not in ({_sql_list(vals)})"])
    pred = f"{c} <> '{vals[0]}'" if len(vals) == 1 else f"{c} NOT IN ({_sql_list(vals)})"
    return _entry(term, "not_in", pred, [c], vals, conds, _cond_phrases(conds))


def build_like(d, rng, term):
    hit = _like_prefix(d, rng)
    if not hit:
        return None
    c, p = hit
    conds = [f"`{c}` starts with `{p}`", f"`{c}` begins with '{p}'", f"`{c}` matches the prefix pattern '{p}%'"]
    return _entry(term, "like_prefix", f"{c} LIKE '{p}%'", [c], [p], conds, _cond_phrases(conds))


def threshold_spec(d, rng):
    ms = _numeric_metrics(d)
    if not ms:
        return None
    m = rng.choice(ms)
    return (m,) + _threshold_for(m, rng)


def build_threshold(d, rng, term, spec=None):
    spec = spec or threshold_spec(d, rng)
    if not spec:
        return None
    m, op, t, word = spec
    n = m["name"]
    words = dict(OPS)[op]
    other = rng.choice([w for w in words if w != word] or words)
    conds = [f"`{n}` is {word} {_num(t)}", f"`{n}` {op} {_num(t)}", f"`{n}` is {other} {_num(t)}"]
    return _entry(term, "threshold", f"{n} {op} {_num(t)}", [n], [], conds, _cond_phrases(conds), numbers=[t], op=op)


def build_mixed(d, rng, term):
    dims, ms = _dims(d), _numeric_metrics(d)
    if len(dims) < 2 or not ms:
        return None
    a, b = rng.sample(dims, 2)
    x, y = rng.choice(d["pools"][a]), rng.choice(d["pools"][b])
    m = rng.choice(ms)
    op, t, word = _threshold_for(m, rng)
    n = m["name"]
    conds = [f"`{a}` is `{x}` and either `{n}` is {word} {_num(t)} or `{b}` is `{y}`",
             f"`{a}` = '{x}' AND (`{n}` {op} {_num(t)} OR `{b}` = '{y}')",
             f"both hold: `{a}` is `{x}`, and at least one of (`{n}` is {word} {_num(t)}, `{b}` is `{y}`)"]
    return _entry(term, "mixed", f"{a} = '{x}' AND ({n} {op} {_num(t)} OR {b} = '{y}')", [a, n, b], [x, y], conds,
                  _cond_phrases(conds), numbers=[t], op=op)


def build_time_active(d, rng, term):
    ent = _entity(d)
    if not ent:
        return None
    col, root = ent
    days = rng.choice([30, 60, 90])
    noun = (d.get("noun") or "records")
    first = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", col.replace("_", " ")).lower().split()[0]
    a = article(first)  # "a `user_id`" but "an `order_id`"
    phrases = [f"{a} `{col}` that has at least one row in the last {days} days",
               f"{a} distinct `{col}` seen at least once during the past {days} days" if a == "a" or True else "",
               f"{a} `{col}` with any activity in the last {days} days"]
    phrases[1] = f"a distinct `{col}` seen at least once during the past {days} days"
    return _entry(term, "time_active", f"COUNT(DISTINCT {col}) over rows with __time in the last {days} days", [col], [], [], phrases,
                  window_days=days, entity_col=col, noun=noun)


def _epoch_unit_name(u: str) -> str:
    return {"s": "seconds", "ms": "milliseconds"}.get(u, "seconds")


def build_derived(d, rng, name_pools):
    """('time' | 'ratio' | 'diff', entry-builder) options for a datasource; name chosen by caller."""
    opts = []
    for pair in d["roles"].get("epoch_pairs") or []:
        opts.append(("time", pair))
    ms = [m for m in _numeric_metrics(d) if m["role"] != "category_numeric"]
    pos = [m for m in ms if (m["gen"][0] == "logn" or float(m["gen"][1]) > 0)]
    if len(ms) >= 2 and pos:
        num = rng.choice(ms)
        den = rng.choice([m for m in pos if m["name"] != num["name"]] or pos)
        if num["name"] != den["name"]:
            opts.append(("ratio", (num, den)))
    return opts


def derived_entry(kind, spec, term, rng):
    if kind == "time":
        s, e, unit = spec
        tu = rng.choice(["seconds", "minutes"])
        div = {("s", "seconds"): 1, ("s", "minutes"): 60, ("ms", "seconds"): 1000, ("ms", "minutes"): 60000}[(unit, tu)]
        uname = _epoch_unit_name(unit)
        conv = "" if div == 1 else f" / {div}"
        phrases = [f"the elapsed time from `{s}` to `{e}` in {tu}: (`{e}` - `{s}`){conv}, where both columns are epoch {uname}",
                   f"`{e}` minus `{s}`, in {tu} (both are epoch {uname} columns)",
                   f"how long it took from `{s}` to `{e}`, expressed in {tu}; the columns hold epoch {uname}"]
        return _entry(term, "derived", f"({e} - {s}){conv}", [s, e], [], [], phrases, derived_kind="time", unit=unit,
                      target_unit=tu, divisor=div)
    if kind == "ratio":
        a, b = spec
        phrases = [f"total `{a['name']}` divided by total `{b['name']}`", f"the ratio of summed `{a['name']}` to summed `{b['name']}`",
                   f"SUM(`{a['name']}`) / SUM(`{b['name']}`)"]
        return _entry(term, "derived", f"SUM({a['name']}) / SUM({b['name']})", [a["name"], b["name"]], [], [], phrases,
                      derived_kind="ratio")
    raise ValueError(kind)


# --------------------------------------------------------------------------- assembling
def singular_of(old_term: str) -> str:
    return old_term.split(" ", 1)[1]


def build_all(index: dict, extras: int = 3, seed: int = 20260925, avoid_text: str = "") -> dict[str, list[dict]]:
    """schema id -> glossary (list of entries; entry 0 is the renamed v2 primary).

    `avoid_text` is every existing question, lower-cased and joined: a term that already occurs in some
    question (as a plain phrase such as "night shifts") is not used, or that question would read as using
    a definition its prompt does not carry."""
    rng = random.Random(seed)
    avoid = avoid_text.lower()
    used_terms: set[str] = set()
    head_uses: Counter = Counter()
    shape_uses: Counter = Counter()
    fam_used: dict[str, set[str]] = {}
    pools = {"thr": THRESHOLD_MODIFIERS[:], "act": ACTIVE_MODIFIERS[:], "spare": SPARE_MODIFIERS[:]}
    derived_used: Counter = Counter()
    out: dict[str, list[dict]] = {}

    def take(mods: list[str], noun_part: str, fam: str | None = None, max_head: int = 5, unique: bool = True) -> str | None:
        order = mods[:]
        rng.shuffle(order)
        for m in order:
            term = f"{m} {noun_part}".strip()
            if (unique and term.lower() in used_terms) or head_uses[head_word(term)] >= max_head:
                continue
            if unique and any(u in term.lower() or term.lower() in u for u in used_terms):
                continue  # terms must not contain one another ("estimated meter read" / "estimated meter reading")
            if avoid and re.search(rf"\b{re.escape(term.lower())}(?:e?s)?(?:'s)?(?!\w)", avoid):
                continue
            if fam and m in fam_used.setdefault(fam, set()):
                continue
            used_terms.add(term.lower())
            head_uses[head_word(term)] += 1
            if fam:
                fam_used[fam].add(m)
            return term
        return None

    for sid in sorted(index):
        d = index[sid]
        old = d.get("glossary") or []
        if not old:
            out[sid] = []
            continue
        fam = d["family"]
        sing = singular_of(old[0]["term"])
        mods = FAMILY_MODIFIERS[fam]
        term0 = take(mods, sing, fam) or take(SPARE_MODIFIERS, sing, fam)
        g0 = old[0]
        entries = [make_value_list(g0["binding_column"], g0["values"], term0)]
        # the primary keeps the v2 predicate byte for byte
        entries[0]["predicate"] = g0["predicate"]

        builders = {"or_cols": build_or_cols, "not_in": build_not_in, "like_prefix": build_like, "mixed": build_mixed,
                    "threshold": build_threshold, "time_active": build_time_active, "derived": None, "value_list": None}
        avail = []
        for shape in ("or_cols", "not_in", "like_prefix", "mixed", "threshold", "time_active", "derived", "value_list"):
            if shape == "derived":
                if build_derived(d, random.Random(0), None):
                    avail.append(shape)
            elif shape == "value_list":
                if len(_dims(d)) >= 1:
                    avail.append(shape)
            elif shape == "time_active":
                if _entity(d):
                    avail.append(shape)
            elif builders[shape](d, random.Random(0), "probe"):
                avail.append(shape)
        chosen: list[str] = []
        if "derived" in avail and rng.random() < 0.5:
            chosen.append("derived")
            shape_uses["derived"] += 1
        for _ in range(extras - len(chosen)):
            rest = [s for s in avail if s not in chosen]
            if not rest:
                break
            w = [(5.0 if s == 'derived' else 1.0) / (1 + shape_uses[s]) ** 1.5 for s in rest]
            s = rng.choices(rest, weights=w)[0]
            chosen.append(s)
            shape_uses[s] += 1
        for shape in chosen:
            e = None
            for _try in range(6):
                if shape == "threshold":
                    spec = threshold_spec(d, rng)
                    pool = THRESHOLD_HIGH if spec[1] in (">", ">=") else THRESHOLD_LOW
                    term = take(pool, sing, None, max_head=8)
                    e = build_threshold(d, rng, term, spec) if term else None
                elif shape == "time_active":
                    ent = _entity(d)
                    term = take(ACTIVE_MODIFIERS, ent[1], None, max_head=7) if ent else None
                    e = build_time_active(d, rng, term) if term else None
                elif shape == "derived":
                    opts = build_derived(d, rng, None)
                    if not opts:
                        continue
                    kind, spec = rng.choice(opts)
                    names = {"time": DERIVED_TIME_NAMES, "ratio": DERIVED_RATIO_NAMES}[kind]
                    term = take(names, "", None, max_head=6, unique=False)
                    e = derived_entry(kind, spec, term, rng) if term else None
                elif shape == "value_list":
                    dims = _dims(d)
                    c = rng.choice(dims)
                    vals = rng.sample(d["pools"][c], rng.randint(1, min(3, len(d["pools"][c]))))
                    term = take(mods, sing, fam) or take(SPARE_MODIFIERS, sing, fam)
                    e = make_value_list(c, vals, term) if term else None
                else:
                    term = take(mods, sing, fam) or take(SPARE_MODIFIERS, sing, fam)
                    e = builders[shape](d, rng, term) if term else None
                if e:
                    break
            if e and all(e["term"].lower() != x["term"].lower() for x in entries):
                entries.append(e)
        out[sid] = entries
    return out


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[-\s'_]+", s.lower()) if len(t) >= 4]


def conflicts(d: dict, e: dict) -> bool:
    """A value-based term whose modifier is another value of the same column ("children's loan" defined as
    audience `senior` or `adult`): the word would contradict its own definition."""
    if e["shape"] in ("derived", "time_active", "threshold"):
        return False
    sing = e["term"].split(" ", 1)[1] if " " in e["term"] else ""
    mod = e["term"][: -len(sing) - 1] if sing and e["term"].endswith(sing) else e["term"]
    mt = _tokens(mod)
    for c in e["columns"]:
        for v in d["pools"].get(c) or []:
            if isinstance(v, str) and v not in e["values"]:
                vt = _tokens(v)
                if any(t == u or t[:5] == u[:5] for t in mt for u in vt):
                    return True
    return False


def fix_conflicts(index: dict, built: dict, avoid_text: str) -> dict[str, str]:
    """Rename contradictory terms in `built` (in place); returns {old term: new term}."""
    used = {e["term"].lower() for es in built.values() for e in es}
    renames: dict[str, str] = {}
    for sid in sorted(built):
        for e in built[sid]:
            if not conflicts(index[sid], e):
                continue
            sing = e["term"].split(" ", 1)[1]
            for m in random.Random(sid).sample(SPARE_MODIFIERS, len(SPARE_MODIFIERS)):
                term = f"{m} {sing}"
                if term.lower() in used or any(u in term.lower() or term.lower() in u for u in used):
                    continue
                if avoid_text and re.search(rf"\b{re.escape(term.lower())}(?:e?s)?(?:'s)?(?!\w)", avoid_text.lower()):
                    continue
                probe = dict(e, term=term)
                if conflicts(index[sid], probe):
                    continue
                renames[e["term"]] = term
                used.add(term.lower())
                e["definition_text"] = e["definition_text"].replace(e["term"][0].upper() + e["term"][1:], term[0].upper() + term[1:], 1)
                e["term"] = term
                break
    return renames


def fix_overview(d: dict) -> str:
    """v2 lower-cased the first letter of the domain inside the overview, giving "aPI gateway traffic" and
    "cI/CD build pipeline runs". Restore the acronym."""
    dom, ov = d["domain"], d.get("overview") or ""
    first = re.split(r"[\s/]", dom)[0]
    if len(first) >= 2 and (first.isupper() or first[1:].isupper() or "/" in dom.split()[0]) or dom.startswith(("CI/CD", "IT and")):
        bad = dom[0].lower() + dom[1:]
        ov = ov.replace(bad, dom)
    return ov


def main() -> int:
    idx_path = ROOT / "schema_index.json"
    backup = ROOT / "schema_index.pre_addendum.json"
    if not backup.exists():
        backup.write_text(idx_path.read_text())
    index = json.loads(backup.read_text())  # always build from the pre-addendum file: idempotent
    pre = ROOT / "v2_pre_addendum"
    src = pre if pre.exists() else ROOT / "v2"
    texts = []
    for f in src.glob("*.jsonl"):
        for line in f.read_text().splitlines():
            m = json.loads(line)["meta"]
            texts += [m.get("question") or "", m.get("prior_question") or ""]
    avoid = " \n ".join(texts)
    built = build_all(index, avoid_text=avoid)  # generation-time (F2) names: the F3/F4 pools were written with these
    import term_names
    term_names.rename_all(index, built, avoid_text=avoid)  # F7: names that match the definitions
    (ROOT / "term_renames.json").unlink(missing_ok=True)
    for sid, entries in built.items():
        index[sid]["glossary_v2"] = index[sid].get("glossary") or []
        index[sid]["glossary"] = entries
        index[sid]["overview"] = fix_overview(index[sid])
    if "--write" in sys.argv:
        idx_path.write_text(json.dumps(index, indent=1) + "\n")
        print(f"wrote {idx_path.name}")
    terms = [e["term"] for es in built.values() for e in es]
    heads = Counter(head_word(t) for t in terms)
    print(f"{sum(1 for es in built.values() if es)} glossaries, {len(terms)} terms, {len(set(t.lower() for t in terms))} distinct, "
          f"{len(heads)} head words; top heads {heads.most_common(6)}")
    print("shapes:", Counter(e["shape"] for es in built.values() for e in es))
    print("glossary sizes:", Counter(len(es) for es in built.values()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
