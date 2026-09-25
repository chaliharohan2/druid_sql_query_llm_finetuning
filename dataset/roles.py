"""Semantic roles and polarity for numeric columns (plan P0-2).

`schema_view.py`'s roles block already tells a template *what kind of column*
something is structurally (a dimension, a high-cardinality id, a metric, the
multi-value dimension, the JSON string...). What it does not carry is what a
*metric* means: whether summing it produces a meaningful number, and whether a
bigger value is good, bad or neither. That is what v2's role catalog
(druid_sql_dataset_v2_plan.md P0-2) adds, and it is what G5 checks against.

Roles, matching the plan's table:
  measure_additive     SUM / AVG / MIN / MAX all make sense.
  measure_nonadditive   AVG / MIN / MAX / quantiles; SUM only if the question
                        explicitly asks for a total.
  flag_01               a 0/1 indicator column: SUM-as-count, ratio, FILTER.
  category_numeric      a numeric column that is really an enumerated code
                        (status_code, dst_port): filter/group only, no math.
                        Not in the plan's table verbatim, but the same idea as
                        `category(values=[...])` applied to a numeric column.

Polarity (higher_is_worse / higher_is_better / neutral) resolves "worst",
"best" and "slowest" style questions. `category_numeric` and unresolved
metrics default to neutral, which the sampler must treat as "no worst/best
question about this column".

Not AI training or inference code: this only classifies Druid fixtures.
"""
from __future__ import annotations

ADDITIVE = "measure_additive"
NONADDITIVE = "measure_nonadditive"
FLAG = "flag_01"
CATEGORY_NUMERIC = "category_numeric"

WORSE = "higher_is_worse"
BETTER = "higher_is_better"
NEUTRAL = "neutral"

# Explicit classification for every metric name in families.py, build_schemas.py
# and DIMS. Column names are reused verbatim (and with identical meaning)
# across families/variants, so one name -> one entry is unambiguous.
METRIC_META: dict[str, dict[str, str]] = {
    # -- additive, neutral --------------------------------------------------
    "amount_minor": {"role": ADDITIVE, "polarity": NEUTRAL},
    "amountMinor": {"role": ADDITIVE, "polarity": NEUTRAL},
    "basket_total": {"role": ADDITIVE, "polarity": NEUTRAL},
    "bytes": {"role": ADDITIVE, "polarity": NEUTRAL},
    "bytes_served": {"role": ADDITIVE, "polarity": NEUTRAL},
    "bytes_sent": {"role": ADDITIVE, "polarity": NEUTRAL},
    "bytes_transferred": {"role": ADDITIVE, "polarity": NEUTRAL},
    "bytes_up": {"role": ADDITIVE, "polarity": NEUTRAL},
    "bytes_down": {"role": ADDITIVE, "polarity": NEUTRAL},
    "response_bytes": {"role": ADDITIVE, "polarity": NEUTRAL},
    "checkout_lanes": {"role": ADDITIVE, "polarity": NEUTRAL},
    "declared_value_eur": {"role": ADDITIVE, "polarity": NEUTRAL},
    "delivery_fee": {"role": ADDITIVE, "polarity": NEUTRAL},
    "discount_usd": {"role": ADDITIVE, "polarity": NEUTRAL},
    "distance_km": {"role": ADDITIVE, "polarity": NEUTRAL},
    "leg_distance_km": {"role": ADDITIVE, "polarity": NEUTRAL},
    "dock_doors": {"role": ADDITIVE, "polarity": NEUTRAL},
    "fare_local": {"role": ADDITIVE, "polarity": NEUTRAL},
    "fee_quote": {"role": ADDITIVE, "polarity": NEUTRAL},
    "floor_sqm": {"role": ADDITIVE, "polarity": NEUTRAL},
    "input_tokens": {"role": ADDITIVE, "polarity": NEUTRAL},
    "items_count": {"role": ADDITIVE, "polarity": NEUTRAL},
    "cart_items": {"role": ADDITIVE, "polarity": NEUTRAL},
    "kwh_consumed": {"role": ADDITIVE, "polarity": NEUTRAL},
    "kwh_exported": {"role": ADDITIVE, "polarity": NEUTRAL},
    "lifetime_trips": {"role": ADDITIVE, "polarity": NEUTRAL},
    "line_quantity": {"role": ADDITIVE, "polarity": NEUTRAL},
    "line_total": {"role": ADDITIVE, "polarity": NEUTRAL},
    "member_count": {"role": ADDITIVE, "polarity": NEUTRAL},
    "packets": {"role": ADDITIVE, "polarity": NEUTRAL},
    "pallet_capacity": {"role": ADDITIVE, "polarity": NEUTRAL},
    "quantity": {"role": ADDITIVE, "polarity": NEUTRAL},
    "quantity_base": {"role": ADDITIVE, "polarity": NEUTRAL},
    "rated_cost_usd": {"role": ADDITIVE, "polarity": NEUTRAL},
    "reply_count": {"role": ADDITIVE, "polarity": NEUTRAL},
    "scheduled_minutes": {"role": ADDITIVE, "polarity": NEUTRAL},
    "worked_minutes": {"role": ADDITIVE, "polarity": NEUTRAL},
    "watch_seconds": {"role": ADDITIVE, "polarity": NEUTRAL},
    "weight_kg": {"role": ADDITIVE, "polarity": NEUTRAL},
    "weight_grams": {"role": ADDITIVE, "polarity": NEUTRAL},
    "duration_seconds": {"role": ADDITIVE, "polarity": NEUTRAL},
    "xp_earned": {"role": ADDITIVE, "polarity": NEUTRAL},
    "win_price_usd": {"role": ADDITIVE, "polarity": NEUTRAL},
    # -- additive, has a direction ------------------------------------------
    "attributed_revenue": {"role": ADDITIVE, "polarity": BETTER},
    "revenue_usd": {"role": ADDITIVE, "polarity": BETTER},
    "overtime_minutes": {"role": ADDITIVE, "polarity": WORSE},
    # -- nonadditive, neutral -------------------------------------------------
    "engine_rpm": {"role": NONADDITIVE, "polarity": NEUTRAL},
    "link_position": {"role": NONADDITIVE, "polarity": NEUTRAL},
    "odometer_km": {"role": NONADDITIVE, "polarity": NEUTRAL},
    "price": {"role": NONADDITIVE, "polarity": NEUTRAL},
    "bid_cpm_usd": {"role": NONADDITIVE, "polarity": NEUTRAL},
    "rate_limit_remaining": {"role": NONADDITIVE, "polarity": BETTER},
    "sla_hours": {"role": NONADDITIVE, "polarity": NEUTRAL},
    "slippage_bps": {"role": NONADDITIVE, "polarity": NEUTRAL},
    "speed_kph": {"role": NONADDITIVE, "polarity": NEUTRAL},
    "unit_cost": {"role": NONADDITIVE, "polarity": WORSE},
    "unit_price_usd": {"role": NONADDITIVE, "polarity": NEUTRAL},
    "list_price_usd": {"role": NONADDITIVE, "polarity": NEUTRAL},
    "fxRate": {"role": NONADDITIVE, "polarity": NEUTRAL},
    "value": {"role": NONADDITIVE, "polarity": NEUTRAL},
    "voltage_v": {"role": NONADDITIVE, "polarity": NEUTRAL},
    # -- nonadditive, higher is worse ----------------------------------------
    "duration_ms": {"role": NONADDITIVE, "polarity": WORSE},
    "dwell_minutes": {"role": NONADDITIVE, "polarity": WORSE},
    "gateway_latency_ms": {"role": NONADDITIVE, "polarity": WORSE},
    "upstream_latency_ms": {"role": NONADDITIVE, "polarity": WORSE},
    "gc_pause_ms": {"role": NONADDITIVE, "polarity": WORSE},
    "heap_used_mb": {"role": NONADDITIVE, "polarity": WORSE},
    "inference_ms": {"role": NONADDITIVE, "polarity": WORSE},
    "queue_ms": {"role": NONADDITIVE, "polarity": WORSE},
    "open_latency_s": {"role": NONADDITIVE, "polarity": WORSE},
    "origin_fetch_ms": {"role": NONADDITIVE, "polarity": WORSE},
    "prep_minutes": {"role": NONADDITIVE, "polarity": WORSE},
    "processing_ms": {"role": NONADDITIVE, "polarity": WORSE},
    "rebuffer_ms": {"role": NONADDITIVE, "polarity": WORSE},
    "risk_score": {"role": NONADDITIVE, "polarity": WORSE},
    "riskScore": {"role": NONADDITIVE, "polarity": WORSE},
    "cost_index": {"role": NONADDITIVE, "polarity": WORSE},
    "startup_ms": {"role": NONADDITIVE, "polarity": WORSE},
    "ttfb_ms": {"role": NONADDITIVE, "polarity": WORSE},
    "latency_ms": {"role": NONADDITIVE, "polarity": WORSE},
    "setup_latency_ms": {"role": NONADDITIVE, "polarity": WORSE},
    # -- nonadditive, higher is better ---------------------------------------
    "acceptance_rate": {"role": NONADDITIVE, "polarity": BETTER},
    "battery_pct": {"role": NONADDITIVE, "polarity": BETTER},
    "bitrate_kbps": {"role": NONADDITIVE, "polarity": BETTER},
    "confidence": {"role": NONADDITIVE, "polarity": BETTER},
    "csat_score": {"role": NONADDITIVE, "polarity": BETTER},
    "driver_rating": {"role": NONADDITIVE, "polarity": BETTER},
    "fuel_level_pct": {"role": NONADDITIVE, "polarity": BETTER},
    "hourly_rate": {"role": NONADDITIVE, "polarity": BETTER},
    "margin_pct": {"role": NONADDITIVE, "polarity": BETTER},
    "power_factor": {"role": NONADDITIVE, "polarity": BETTER},
    "shelf_life_days": {"role": NONADDITIVE, "polarity": BETTER},
    "signal_quality": {"role": NONADDITIVE, "polarity": BETTER},
    "signal_dbm": {"role": NONADDITIVE, "polarity": BETTER},
    "target_open_rate": {"role": NONADDITIVE, "polarity": BETTER},
    # -- flags ----------------------------------------------------------------
    "was_clicked": {"role": FLAG, "polarity": NEUTRAL},
    "isRecurring": {"role": FLAG, "polarity": NEUTRAL},
    "dropped_flag": {"role": FLAG, "polarity": NEUTRAL},
    # -- numeric codes that behave like categories -----------------------------
    "status_code": {"role": CATEGORY_NUMERIC, "polarity": NEUTRAL},
    "dst_port": {"role": CATEGORY_NUMERIC, "polarity": NEUTRAL},
    "dest_port": {"role": CATEGORY_NUMERIC, "polarity": NEUTRAL},
}

_WORSE_KW = ("latency", "_ms", "error", "pause", "drop", "risk", "dwell",
             "bounce", "fail", "cost", "age_", "_age")
_BETTER_KW = ("rating", "confidence", "acceptance", "uptime", "satisfaction",
              "quality", "signal", "csat")
_ADDITIVE_KW = ("bytes", "count", "quantity", "amount", "revenue", "weight",
                "distance", "fee", "packets", "minutes", "seconds", "tokens",
                "capacity", "spend", "total")
_FLAG_PREFIX = ("is_", "was_", "has_")
_FLAG_SUFFIX = ("_flag",)
_CATEGORY_SUFFIX = ("_code", "_status", "port")


def classify_metric(name: str, ctype: str = "long", desc: str = "",
                    gen: list | tuple | None = None) -> dict[str, str]:
    """Role + polarity for a metric column, falling back to a keyword heuristic.

    The explicit table above covers every column in the current schema
    factory. The heuristic exists for columns a teacher-generated schema
    invents at generation time (P0-3), which the explicit table cannot know
    about in advance.
    """
    meta = METRIC_META.get(name)
    if meta:
        return dict(meta)
    lname, ldesc = name.lower(), (desc or "").lower()
    if gen and len(gen) >= 2 and gen[0] == "pick" and sorted(set(gen[1])) == [0, 1]:
        return {"role": FLAG, "polarity": NEUTRAL}
    if lname.startswith(_FLAG_PREFIX) or lname.endswith(_FLAG_SUFFIX) or \
            "1 if" in ldesc or "0 if" in ldesc or "true/false" in ldesc:
        return {"role": FLAG, "polarity": NEUTRAL}
    if lname.endswith(_CATEGORY_SUFFIX) and gen and gen[0] == "pick":
        return {"role": CATEGORY_NUMERIC, "polarity": NEUTRAL}
    polarity = NEUTRAL
    if any(k in lname or k in ldesc for k in _WORSE_KW):
        polarity = WORSE
    elif any(k in lname or k in ldesc for k in _BETTER_KW):
        polarity = BETTER
    role = ADDITIVE if any(k in lname for k in _ADDITIVE_KW) else NONADDITIVE
    # A "worse"/"better" direction on a per-record rate (pct, score, ms, rate,
    # ratio) almost always means the column shouldn't be blindly summed.
    if role == ADDITIVE and any(k in lname for k in ("_pct", "pct_", "rate", "score", "_ms", "ratio")):
        role = NONADDITIVE
    return {"role": role, "polarity": polarity}


# --------------------------------------------------------------- nullability
# P0-5: 10-30% of columns blank/nullable in about 40% of tables. Applied
# deterministically per schema id so regeneration is reproducible.
def nullable_columns(schema_id: str, column_names: list[str], rng) -> set[str]:
    """Which non-time, non-id columns are nullable in this schema.

    Roughly 40% of schemas get *any* nullable columns; those that do make
    10-30% of their columns nullable. `rng` should be seeded per-schema so a
    regeneration reproduces the same set.
    """
    if rng.random() >= 0.40:
        return set()
    share = rng.uniform(0.10, 0.30)
    k = max(1, round(len(column_names) * share))
    return set(rng.sample(column_names, min(k, len(column_names))))


def blank_description_columns(schema_id: str, column_names: list[str], rng) -> set[str]:
    """Which columns get their description blanked out (P0-5)."""
    if rng.random() >= 0.40:
        return set()
    share = rng.uniform(0.10, 0.30)
    k = max(1, round(len(column_names) * share))
    return set(rng.sample(column_names, min(k, len(column_names))))
